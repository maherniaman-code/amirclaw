import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';
import sharp from 'sharp';

// SQLite copy of every captured WhatsApp group message. The reports engine reads this
// instead of re-parsing the whole Excel file on every report (the Excel file is still
// written for the Telegram download button and backups).
//
// Rows mirror the Excel columns exactly (same timestamp text, same "Type" badge), plus the
// row's thumbnail JPEG so reports keep their photos without the Excel drawing layer.

const WORKSPACE_DIR = path.resolve(process.cwd(), 'workspace');
export const MESSAGES_DB_PATH = process.env.MESSAGES_DB_PATH || path.join(WORKSPACE_DIR, 'whatsapp_messages.db');

const THUMB_W = 221; // same box as the Excel thumbnail (2.30" x 1.70")
const THUMB_H = 163;

let db: Database.Database | null = null;
let insertStmt: Database.Statement | null = null;

export const MESSAGES_SCHEMA = `
    CREATE TABLE IF NOT EXISTS messages (
        message_id        TEXT PRIMARY KEY,
        timestamp_text    TEXT NOT NULL,
        group_name        TEXT NOT NULL,
        sender            TEXT,
        text              TEXT,
        media_type        TEXT,
        quoted_message_id TEXT,   -- NULL = reply info not recorded, '' = not a reply
        thumb_jpeg        BLOB,
        media_file        TEXT,
        inserted_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        ts_utc_ms         INTEGER  -- exact message time; NULL for rows imported from Excel before it existed
    );
    CREATE INDEX IF NOT EXISTS ix_messages_group ON messages(group_name);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
`;

function getDb(): Database.Database {
    if (!db) {
        fs.mkdirSync(path.dirname(MESSAGES_DB_PATH), { recursive: true });
        db = new Database(MESSAGES_DB_PATH);
        db.pragma('journal_mode = WAL'); // reports read while the bot writes
        db.exec(MESSAGES_SCHEMA);
        // Databases created before ts_utc_ms existed get the column added in place
        const cols = (db.prepare('PRAGMA table_info(messages)').all() as any[]).map((c) => c.name);
        if (!cols.includes('ts_utc_ms')) db.exec('ALTER TABLE messages ADD COLUMN ts_utc_ms INTEGER');
        insertStmt = db.prepare(`
            INSERT OR IGNORE INTO messages
                (message_id, timestamp_text, group_name, sender, text, media_type, quoted_message_id, thumb_jpeg, media_file, ts_utc_ms)
            VALUES (@messageId, @timestamp, @groupName, @sender, @text, @mediaType, @quotedMessageId, @thumb, @mediaFile, @tsMs)
        `);
    }
    return db;
}

async function loadThumbnail(thumbnailPath?: string, mediaLocalPath?: string): Promise<Buffer | null> {
    try {
        if (thumbnailPath && fs.existsSync(thumbnailPath) && thumbnailPath !== mediaLocalPath) {
            return fs.readFileSync(thumbnailPath);
        }
        const src = thumbnailPath || mediaLocalPath;
        if (src && fs.existsSync(src)) {
            return await sharp(src).resize({ width: THUMB_W, height: THUMB_H, fit: 'inside', withoutEnlargement: true })
                .jpeg({ quality: 75 }).toBuffer();
        }
    } catch {
        // a missing/corrupt image must never block storing the message
    }
    return null;
}

export interface StoredMessage {
    messageId: string;
    timestamp: string;
    timestampMs?: number;
    groupName: string;
    sender: string;
    text: string;
    mediaBadge: string;          // Excel "Type" column text, e.g. "📷 Image"
    quotedMessageId?: string | null;
    thumbnailPath?: string;
    mediaLocalPath?: string;
    mediaFileName?: string;
}

export async function storeWhatsAppMessage(m: StoredMessage): Promise<void> {
    // Photos, and videos that got a frame / preview thumbnail
    const isImage = m.mediaBadge.includes('Image');
    const thumb = isImage || m.thumbnailPath ? await loadThumbnail(m.thumbnailPath, m.mediaLocalPath) : null;
    getDb();
    insertStmt!.run({
        messageId: m.messageId,
        timestamp: m.timestamp,
        groupName: m.groupName,
        sender: m.sender,
        text: m.text || '',
        mediaType: m.mediaBadge,
        quotedMessageId: m.quotedMessageId === undefined ? null : m.quotedMessageId,
        thumb,
        mediaFile: m.mediaFileName || null,
        tsMs: m.timestampMs ?? null,
    });
}

export function countStoredMessages(): number {
    return (getDb().prepare('SELECT COUNT(*) AS n FROM messages').get() as any).n;
}

// ── Old videos without a picture (recovered later from WhatsApp history) ──

export interface MissingVideo { messageId: string; groupName: string; tsMs: number | null }

export function missingVideoThumbs(): MissingVideo[] {
    return (getDb().prepare(
        `SELECT message_id AS messageId, group_name AS groupName, ts_utc_ms AS tsMs FROM messages
         WHERE media_type LIKE '%Video%' AND thumb_jpeg IS NULL ORDER BY ts_utc_ms`,
    ).all() as MissingVideo[]);
}

/** Saves a recovered picture; never overwrites one that is already there. */
export function setThumbnail(messageId: string, jpeg: Buffer): boolean {
    return getDb().prepare('UPDATE messages SET thumb_jpeg = ? WHERE message_id = ? AND thumb_jpeg IS NULL')
        .run(jpeg, messageId).changes > 0;
}

/** Newest recorded message of a group: history requests go back from it. */
export function newestMessageInGroup(groupName: string): { id: string; tsMs: number; fromMe: boolean } | null {
    const r = getDb().prepare(
        `SELECT message_id AS id, ts_utc_ms AS tsMs, sender FROM messages
         WHERE group_name = ? AND ts_utc_ms IS NOT NULL ORDER BY ts_utc_ms DESC LIMIT 1`,
    ).get(groupName) as any;
    return r ? { id: r.id, tsMs: r.tsMs, fromMe: String(r.sender || '').startsWith('Me (Bot Account)') } : null;
}

export function getMeta(key: string): string | null {
    const r = getDb().prepare('SELECT value FROM meta WHERE key = ?').get(key) as any;
    return r ? r.value : null;
}

export function setMeta(key: string, value: string): void {
    getDb().prepare('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)').run(key, value);
}
