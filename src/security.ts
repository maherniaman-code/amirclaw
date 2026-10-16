// Guards for what the AI assistant can reach on the server (security review, 16-10).
// The assistant reads untrusted text (web pages, uploaded files, WhatsApp group messages), so a
// message written by someone else could try to make it run code or read the bot's secrets.
import dns from 'dns/promises';
import net from 'net';
import path from 'path';
import { randomBytes } from 'crypto';

// ── Python code waits for the owner's tap ───────────────────────────────────
// Telegram updates are handled one at a time, so the code is not run inside the chat turn:
// it is shown with Run / Cancel buttons and runs from the button (once, by the same user).
export const PY_CB_PREFIX = 'py';
const APPROVAL_TTL_MS = 10 * 60 * 1000;
const pendingCode = new Map<string, { code: string; userId: number; createdMs: number }>();

export function requestPythonApproval(code: string, userId: number, nowMs = Date.now()): string {
    for (const [id, p] of pendingCode) if (nowMs - p.createdMs > APPROVAL_TTL_MS) pendingCode.delete(id);
    const id = randomBytes(6).toString('hex');
    pendingCode.set(id, { code, userId, createdMs: nowMs });
    return id;
}

/** The code waiting under id, for this user and not expired; it can be taken only once. */
export function takePythonApproval(id: string, userId: number, nowMs = Date.now()): string | null {
    const p = pendingCode.get(id);
    if (!p || p.userId !== userId) return null;
    pendingCode.delete(id);
    return nowMs - p.createdMs > APPROVAL_TTL_MS ? null : p.code;
}

/** Environment for code the assistant writes: no bot token, API keys or other secrets. */
export function safeChildEnv(env: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
    const keep = ['PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR'];
    const out: NodeJS.ProcessEnv = { MPLBACKEND: 'Agg' };
    for (const k of keep) if (env[k]) out[k] = env[k];
    return out;
}

// ── Files the assistant may read ────────────────────────────────────────────
// WhatsApp login keys, the message / ticket / memory databases and credentials stay out of reach.
const BLOCKED_PARTS = ['whatsapp_session', 'backups', 'restore'];
const BLOCKED_EXT = ['.db', '.db-wal', '.db-shm', '.sqlite', '.pem', '.key'];
const BLOCKED_NAMES = ['.env', 'service-account.json', 'creds.json'];

/** Absolute path inside root that the assistant may read, or null. */
export function allowedWorkspacePath(root: string, requested: string): string | null {
    const resolved = path.resolve(root, requested);
    const rel = path.relative(root, resolved);
    if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) return null;
    const parts = rel.split(path.sep).map((p) => p.toLowerCase());
    const name = parts[parts.length - 1];
    if (parts.some((p) => BLOCKED_PARTS.includes(p))) return null;
    if (BLOCKED_NAMES.includes(name) || name.startsWith('.env')) return null;
    if (BLOCKED_EXT.some((e) => name.endsWith(e))) return null;
    return resolved;
}

/** A file name from Telegram, safe to save in a folder (no folders, no hidden files). */
export function safeUploadName(name: string | undefined, fallback: string): string {
    const base = path.basename(String(name || '')).replace(/[\x00-\x1f]/g, '').replace(/^\.+/, '').trim();
    return base || fallback;
}

// ── Web pages: public internet only ─────────────────────────────────────────
// Blocks the server's own services and the cloud metadata address (169.254.169.254), which
// would hand out the server's cloud credentials.
export function isPrivateAddress(ip: string): boolean {
    if (net.isIPv4(ip)) {
        const [a, b] = ip.split('.').map(Number);
        return a === 0 || a === 10 || a === 127 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31)
            || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || a >= 224;
    }
    const v6 = ip.toLowerCase();
    if (v6.startsWith('::ffff:')) return isPrivateAddress(v6.slice(7));
    return v6 === '::' || v6 === '::1' || v6.startsWith('fc') || v6.startsWith('fd') || v6.startsWith('fe80');
}

export async function assertPublicUrl(raw: string): Promise<URL> {
    const url = new URL(raw);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error('Only http and https links are allowed');
    const host = url.hostname.replace(/^\[|\]$/g, '');
    const addrs = net.isIP(host) ? [host] : (await dns.lookup(host, { all: true })).map((a) => a.address);
    if (!addrs.length || addrs.some(isPrivateAddress)) throw new Error('Links to private or internal addresses are blocked');
    return url;
}
