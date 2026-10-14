import Database from 'better-sqlite3';
import { initializeApp, cert, getApps } from 'firebase-admin/app';
import { getFirestore, FieldValue, type Firestore } from 'firebase-admin/firestore';
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';
import { config } from '../config.js';

export const db = new Database(config.dbPath);
let firestoreDb: Firestore | null = null;
let initialized = false;

// Ensure tables are always ready
initDb();

export function initDb() {
    if (initialized) return;
    initialized = true;

    // Local SQLite database
    db.exec(`
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            remind_at INTEGER NOT NULL,
            reply_voice INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, key)
        );
    `);

    // Cloud Firestore initialization
    initFirestore();
}

function initFirestore() {
    const credPath = resolve(process.cwd(), process.env.GOOGLE_APPLICATION_CREDENTIALS || './service-account.json');
    if (existsSync(credPath)) {
        try {
            const serviceAccount = JSON.parse(readFileSync(credPath, 'utf8'));
            if (!getApps().length) {
                initializeApp({
                    credential: cert(serviceAccount),
                });
            }
            firestoreDb = getFirestore();
            console.log('🔥 Firebase Cloud Firestore connected (Telegram chats, WhatsApp messages and backups sync to the cloud).');
        } catch (error: any) {
            console.error('⚠️ Failed to initialize Firebase:', error.message);
        }
    } else {
        console.log('ℹ️ Firebase credentials file (service-account.json) not found yet. Using local database until provided.');
    }
}

export function getFirestoreDb(): Firestore | null {
    return firestoreDb;
}

export function getMessages(userId: number, limit = config.conversationHistoryLimit ?? 50) {
    const stmt = db.prepare('SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?');
    const messages = stmt.all(userId, limit) as { role: 'user' | 'assistant' | 'system'; content: string }[];
    return messages.reverse(); // Return in chronological order
}

export function addMessage(userId: number, role: 'user' | 'assistant' | 'system', content: string) {
    // 1. Save to local SQLite
    const stmt = db.prepare('INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)');
    stmt.run(userId, role, content);

    // 2. Asynchronously sync to Cloud Firestore
    if (firestoreDb) {
        firestoreDb.collection('conversations')
            .doc(String(userId))
            .collection('messages')
            .add({
                role,
                content,
                createdAt: FieldValue.serverTimestamp(),
            })
            .catch((err: any) => {
                console.error('Error syncing message to Firebase Firestore:', err);
            });
    }
}

// ── Reminders Operations ──────────────────────────────────────────────

export function addReminderDb(userId: number, title: string, remindAtMs: number, replyVoice = false) {
    const stmt = db.prepare('INSERT INTO reminders (user_id, title, remind_at, reply_voice) VALUES (?, ?, ?, ?)');
    const info = stmt.run(userId, title, remindAtMs, replyVoice ? 1 : 0);
    return info.lastInsertRowid;
}

export function getDueRemindersDb() {
    const now = Date.now();
    const stmt = db.prepare('SELECT * FROM reminders WHERE completed = 0 AND remind_at <= ? ORDER BY remind_at ASC');
    return stmt.all(now) as { id: number; user_id: number; title: string; remind_at: number; reply_voice: number }[];
}

export function markReminderCompletedDb(id: number) {
    const stmt = db.prepare('UPDATE reminders SET completed = 1 WHERE id = ?');
    stmt.run(id);
}

export function getUserRemindersDb(userId: number) {
    const stmt = db.prepare('SELECT * FROM reminders WHERE user_id = ? AND completed = 0 ORDER BY remind_at ASC');
    return stmt.all(userId) as { id: number; user_id: number; title: string; remind_at: number }[];
}

// ── Long-term User Memories Operations ────────────────────────────────

export function setMemoryDb(userId: number, key: string, value: string) {
    const stmt = db.prepare(
        'INSERT INTO user_memories (user_id, key, value, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) ' +
        'ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP'
    );
    stmt.run(userId, key, value);
}

export function getMemoriesDb(userId: number) {
    const stmt = db.prepare('SELECT key, value, updated_at FROM user_memories WHERE user_id = ? ORDER BY key ASC');
    return stmt.all(userId) as { key: string; value: string; updated_at: string }[];
}

export function deleteMemoryDb(userId: number, key: string) {
    const stmt = db.prepare('DELETE FROM user_memories WHERE user_id = ? AND key = ?');
    stmt.run(userId, key);
}

export function clearMemoriesDb(userId: number) {
    const stmt = db.prepare('DELETE FROM user_memories WHERE user_id = ?');
    stmt.run(userId);
}
