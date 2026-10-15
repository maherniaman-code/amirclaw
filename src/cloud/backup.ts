import Database from 'better-sqlite3';
import crypto from 'crypto';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { FieldValue, type DocumentReference, type Firestore } from 'firebase-admin/firestore';
import { getFirestoreDb } from '../db/index.js';
import { HR_MARKERS } from '../privacy.js';
import { config } from '../config.js';

const execFileAsync = promisify(execFile);

const ROOT_DIR = process.cwd();
const WORKSPACE_DIR = path.join(ROOT_DIR, 'workspace');
const MEDIA_DIR = path.join(WORKSPACE_DIR, 'whatsapp_media');
const MEDIA_STATE_FILE = path.join(WORKSPACE_DIR, '.cloud_media_synced.json');

const BACKUPS_COLLECTION = 'backups';
const MEDIA_COLLECTION = 'backup_media';
// Firestore documents are capped at 1 MiB; keep each binary chunk well below it.
const CHUNK_BYTES = 900_000;
const KEEP_BACKUPS = Number(process.env.CLOUD_BACKUP_KEEP || 7);

// Paths are relative to the project root and keep the same layout inside the archive.
const SQLITE_FILES = [
    config.dbPath,
    'workspace/tickets.db',
    'workspace/reports_corrections.db',
    'workspace/reports.db',
    'workspace/whatsapp_messages.db', // SQLite message store read by the reports engine
];
// The messages workbook and the legacy JSON are not uploaded: the SQLite store is complete,
// the workbook can be rebuilt from it (messages_db --export-excel), and both files contain
// HR / leave messages that must stay on the server.
const PLAIN_FILES: string[] = [];
const DIRS = ['workspace/whatsapp_session'];

function requireFirestore(): Firestore {
    const fsdb = getFirestoreDb();
    if (!fsdb) throw new Error('Firestore is not initialized (service-account.json missing?)');
    return fsdb;
}

function sha256(buf: Buffer): string {
    return crypto.createHash('sha256').update(buf).digest('hex');
}

async function uploadChunks(parent: DocumentReference, buf: Buffer): Promise<number> {
    const count = Math.max(1, Math.ceil(buf.length / CHUNK_BYTES));
    for (let i = 0; i < count; i++) {
        await parent.collection('chunks').doc(String(i).padStart(4, '0'))
            .set({ data: buf.subarray(i * CHUNK_BYTES, (i + 1) * CHUNK_BYTES) });
    }
    return count;
}

async function downloadChunks(parent: DocumentReference): Promise<Buffer> {
    const snap = await parent.collection('chunks').get();
    const docs = [...snap.docs].sort((a, b) => a.id.localeCompare(b.id));
    return Buffer.concat(docs.map((d) => Buffer.from(d.get('data'))));
}

// Removes HR / leave messages from the staged (uploaded) copy only; the live database keeps them.
function scrubHrRows(stagedDbPath: string): void {
    const db = new Database(stagedDbPath);
    try {
        const where = HR_MARKERS.map(() => 'lower(text) LIKE ?').join(' OR ');
        db.prepare(`DELETE FROM messages WHERE ${where}`).run(...HR_MARKERS.map((m) => `%${m}%`));
        db.exec('VACUUM');
    } finally {
        db.close();
    }
}

async function stageFiles(stageDir: string): Promise<string[]> {
    const included: string[] = [];
    for (const rel of [...new Set(SQLITE_FILES.map((f) => path.normalize(f)))]) {
        const src = path.resolve(ROOT_DIR, rel);
        if (!fs.existsSync(src) || fs.statSync(src).size === 0) continue;
        const dest = path.join(stageDir, rel);
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        // Online backup API gives a consistent snapshot even while the bot is writing.
        const db = new Database(src, { readonly: true, fileMustExist: true });
        try {
            await db.backup(dest);
        } finally {
            db.close();
        }
        if (rel.endsWith('whatsapp_messages.db')) scrubHrRows(dest);
        included.push(rel);
    }
    for (const rel of PLAIN_FILES) {
        const src = path.join(ROOT_DIR, rel);
        if (!fs.existsSync(src)) continue;
        const dest = path.join(stageDir, rel);
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        fs.copyFileSync(src, dest);
        included.push(rel);
    }
    for (const rel of DIRS) {
        const src = path.join(ROOT_DIR, rel);
        if (!fs.existsSync(src)) continue;
        await fs.promises.cp(src, path.join(stageDir, rel), { recursive: true });
        included.push(rel + '/');
    }
    return included;
}

async function pruneOldBackups(fsdb: Firestore): Promise<number> {
    const refs = await fsdb.collection(BACKUPS_COLLECTION).listDocuments();
    const snaps = await Promise.all(refs.map((r) => r.get()));
    const complete = snaps.filter((s) => s.exists).sort((a, b) => b.get('createdAtMs') - a.get('createdAtMs'));
    const keep = new Set(complete.slice(0, KEEP_BACKUPS).map((s) => s.id));
    const dayAgo = Date.now() - 24 * 3600 * 1000;
    let deleted = 0;
    for (const s of snaps) {
        if (keep.has(s.id)) continue;
        // Chunks without a meta document are an interrupted upload; leave very recent ones alone.
        if (!s.exists && Number(new Date(s.id.replace(/T(\d\d)-(\d\d)-(\d\d)-(\d+)Z/, 'T$1:$2:$3.$4Z'))) > dayAgo) continue;
        await fsdb.recursiveDelete(s.ref);
        deleted++;
    }
    return deleted;
}

async function syncMedia(fsdb: Firestore): Promise<number> {
    if (!fs.existsSync(MEDIA_DIR)) return 0;
    let synced: Set<string>;
    try {
        synced = new Set(JSON.parse(fs.readFileSync(MEDIA_STATE_FILE, 'utf8')));
    } catch {
        synced = new Set((await fsdb.collection(MEDIA_COLLECTION).listDocuments()).map((r) => r.id));
    }

    let uploaded = 0;
    for (const name of fs.readdirSync(MEDIA_DIR)) {
        const full = path.join(MEDIA_DIR, name);
        if (synced.has(name) || !fs.statSync(full).isFile()) continue;
        const buf = fs.readFileSync(full);
        const ref = fsdb.collection(MEDIA_COLLECTION).doc(name);
        const chunkCount = await uploadChunks(ref, buf);
        await ref.set({ sizeBytes: buf.length, sha256: sha256(buf), chunkCount, uploadedAt: FieldValue.serverTimestamp() });
        synced.add(name);
        uploaded++;
        fs.writeFileSync(MEDIA_STATE_FILE, JSON.stringify([...synced]));
    }
    return uploaded;
}

let backupRunning = false;

export async function runCloudBackup(): Promise<{ id: string; sizeBytes: number; chunkCount: number; files: string[]; mediaUploaded: number; pruned: number }> {
    if (backupRunning) throw new Error('A cloud backup is already running');
    backupRunning = true;
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'amirclaw-backup-'));
    try {
        const fsdb = requireFirestore();
        const stageDir = path.join(tmp, 'stage');
        fs.mkdirSync(stageDir);
        const files = await stageFiles(stageDir);

        const archive = path.join(tmp, 'backup.tar.gz');
        await execFileAsync('tar', ['czf', archive, '-C', stageDir, '.']);
        const buf = fs.readFileSync(archive);

        const id = new Date().toISOString().replace(/[:.]/g, '-');
        const ref = fsdb.collection(BACKUPS_COLLECTION).doc(id);
        const chunkCount = await uploadChunks(ref, buf);
        // Meta is written last so a backup only counts as complete once every chunk is stored.
        await ref.set({
            createdAt: FieldValue.serverTimestamp(),
            createdAtMs: Date.now(),
            sizeBytes: buf.length,
            sha256: sha256(buf),
            chunkCount,
            files,
            host: os.hostname(),
        });

        const mediaUploaded = await syncMedia(fsdb);
        const pruned = await pruneOldBackups(fsdb);
        console.log(`☁️ Cloud backup ${id}: ${(buf.length / 1024).toFixed(0)} KB in ${chunkCount} chunk(s), ${mediaUploaded} new media file(s), ${pruned} old backup(s) pruned.`);
        return { id, sizeBytes: buf.length, chunkCount, files, mediaUploaded, pruned };
    } finally {
        backupRunning = false;
        fs.rmSync(tmp, { recursive: true, force: true });
    }
}

export async function listCloudBackups(): Promise<Array<{ id: string; createdAtMs: number; sizeBytes: number; files: string[] }>> {
    const snap = await requireFirestore().collection(BACKUPS_COLLECTION).orderBy('createdAtMs', 'desc').get();
    return snap.docs.map((d) => ({ id: d.id, createdAtMs: d.get('createdAtMs'), sizeBytes: d.get('sizeBytes'), files: d.get('files') }));
}

/** Restores a backup into outDir (never into the live workspace). */
export async function restoreCloudBackup(idOrLatest: string, outDir: string): Promise<{ id: string; files: string[]; mediaRestored: number }> {
    const fsdb = requireFirestore();
    const id = idOrLatest === 'latest' ? (await listCloudBackups())[0]?.id : idOrLatest;
    if (!id) throw new Error('No cloud backups found');

    const ref = fsdb.collection(BACKUPS_COLLECTION).doc(id);
    const meta = await ref.get();
    if (!meta.exists) throw new Error(`Backup ${id} not found or incomplete`);
    const buf = await downloadChunks(ref);
    if (sha256(buf) !== meta.get('sha256')) throw new Error(`Backup ${id} failed checksum verification`);

    fs.mkdirSync(outDir, { recursive: true });
    const archive = path.join(outDir, `backup-${id}.tar.gz`);
    fs.writeFileSync(archive, buf);
    await execFileAsync('tar', ['xzf', archive, '-C', outDir]);

    const mediaOut = path.join(outDir, 'workspace', 'whatsapp_media');
    fs.mkdirSync(mediaOut, { recursive: true });
    let mediaRestored = 0;
    for (const mref of await fsdb.collection(MEDIA_COLLECTION).listDocuments()) {
        const m = await mref.get();
        if (!m.exists) continue;
        const data = await downloadChunks(mref);
        if (sha256(data) !== m.get('sha256')) {
            console.warn(`Skipping media ${mref.id}: checksum mismatch`);
            continue;
        }
        fs.writeFileSync(path.join(mediaOut, mref.id), data);
        mediaRestored++;
    }
    return { id, files: meta.get('files'), mediaRestored };
}

function dubaiNow(): { day: string; hour: number } {
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: config.timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date());
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
    return { day: `${get('year')}-${get('month')}-${get('day')}`, hour: Number(get('hour')) };
}

export function startCloudBackupScheduler(): void {
    if (process.env.ENABLE_CLOUD_BACKUP === 'false' || !getFirestoreDb()) return;
    const backupHour = Number(process.env.CLOUD_BACKUP_HOUR ?? 3);
    let lastRunDay = '';

    const run = (reason: string) => {
        console.log(`☁️ Starting cloud backup (${reason})...`);
        runCloudBackup().catch((err) => console.error('☁️ Cloud backup failed:', err.message));
    };

    setInterval(() => {
        const { day, hour } = dubaiNow();
        if (hour === backupHour && day !== lastRunDay) {
            lastRunDay = day;
            run(`daily ${String(backupHour).padStart(2, '0')}:00`);
        }
    }, 10 * 60 * 1000);

    // Catch up after downtime: back up shortly after startup if the newest backup is over a day old.
    setTimeout(async () => {
        try {
            const latest = (await listCloudBackups())[0];
            if (!latest || Date.now() - latest.createdAtMs > 26 * 3600 * 1000) run('startup catch-up');
        } catch (err: any) {
            console.error('☁️ Could not check latest cloud backup:', err.message);
        }
    }, 2 * 60 * 1000);

    console.log(`☁️ Cloud backup scheduler started (daily at ${String(backupHour).padStart(2, '0')}:00 ${config.timeZone}, keeping ${KEEP_BACKUPS}).`);
}
