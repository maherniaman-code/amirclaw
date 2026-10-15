import ExcelJS from 'exceljs';
import fs from 'fs';
import path from 'path';
import { FieldValue } from 'firebase-admin/firestore';
import { getFirestoreDb } from '../db/index.js';
import { config } from '../config.js';
import { isHrMessage } from '../privacy.js';

export const WHATSAPP_COLLECTION = 'whatsapp_messages';

export interface CloudWhatsAppMessage {
    messageId: string;
    groupJid?: string;
    groupName: string;
    sender: string;
    senderId?: string;
    text: string;
    mediaType: string;
    mediaFileName?: string;
    quotedMessageId?: string;
    rawTimestamp: string;
    timestampMs: number | null;
}

function docIdFor(messageId: string): string {
    return messageId.replace(/\//g, '_');
}

function toFirestoreFields(msg: CloudWhatsAppMessage): Record<string, unknown> {
    const fields: Record<string, unknown> = {
        ...msg,
        timestampIso: msg.timestampMs ? new Date(msg.timestampMs).toISOString() : null,
        syncedAt: FieldValue.serverTimestamp(),
    };
    for (const k of Object.keys(fields)) if (fields[k] === undefined) delete fields[k];
    return fields;
}

export function mirrorWhatsAppMessage(msg: CloudWhatsAppMessage): void {
    const fsdb = getFirestoreDb();
    if (!fsdb) return;
    fsdb.collection(WHATSAPP_COLLECTION)
        .doc(docIdFor(msg.messageId))
        .set({ ...toFirestoreFields(msg), capturedLive: true }, { merge: true })
        .catch((err: any) => console.error('Error mirroring WhatsApp message to Firestore:', err.message));
}

function tzOffsetMs(utcMs: number, timeZone: string): number {
    const name = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'longOffset' })
        .formatToParts(new Date(utcMs))
        .find((p) => p.type === 'timeZoneName')?.value ?? 'GMT';
    const m = name.match(/GMT([+-])(\d{2}):?(\d{2})?/);
    if (!m) return 0;
    return (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3] || 0)) * 60_000;
}

/**
 * Parses the timestamp strings stored in the WhatsApp Excel file.
 * Arabic-digit rows (ar-EG) are DD/MM; Western AM/PM rows come from toLocaleString('en-US') and are MM/DD.
 */
export function parseExcelTimestamp(raw: string, timeZone: string = config.timeZone): number | null {
    if (!raw) return null;
    const hasArabic = /[٠-٩]/.test(raw);
    const s = raw
        .replace(/[‎‏‪-‮﻿]/g, '')
        .replace(/[٠-٩]/g, (d) => String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)))
        .replace(/،/g, ',')
        .replace(/\s*ص\s*$/, ' AM')
        .replace(/\s*م\s*$/, ' PM')
        .replace(/\s+/g, ' ')
        .trim();
    const m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4}),?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?$/i);
    if (!m) return null;

    const ampm = m[7]?.toUpperCase();
    const monthFirst = !hasArabic && !!ampm;
    let month = Number(monthFirst ? m[1] : m[2]);
    let day = Number(monthFirst ? m[2] : m[1]);
    if (month > 12 && day <= 12) [month, day] = [day, month];
    let hour = Number(m[4]) % (ampm ? 12 : 24);
    if (ampm === 'PM') hour += 12;

    const localAsUtc = Date.UTC(Number(m[3]), month - 1, day, hour, Number(m[5]), Number(m[6] || 0));
    return localAsUtc - tzOffsetMs(localAsUtc, timeZone);
}

function cellText(v: ExcelJS.CellValue): string {
    if (v === null || v === undefined) return '';
    if (typeof v === 'object' && 'richText' in (v as any)) return (v as any).richText.map((r: any) => r.text).join('');
    if (typeof v === 'object' && 'text' in (v as any)) return String((v as any).text);
    return String(v);
}

export async function backfillFromExcel(excelPath: string): Promise<{ uploaded: number; skipped: number; unparsedDates: number }> {
    const fsdb = getFirestoreDb();
    if (!fsdb) throw new Error('Firestore is not initialized (service-account.json missing?)');

    const wb = new ExcelJS.Workbook();
    await wb.xlsx.readFile(excelPath);
    const sheet = wb.worksheets[0];

    let uploaded = 0, skipped = 0, unparsedDates = 0;
    let batch = fsdb.batch();
    let pending = 0;

    for (let r = 2; r <= sheet.rowCount; r++) {
        const row = sheet.getRow(r);
        const messageId = cellText(row.getCell(1).value).trim();
        if (!messageId) { skipped++; continue; }
        const rawTimestamp = cellText(row.getCell(2).value);
        const timestampMs = parseExcelTimestamp(rawTimestamp);
        if (timestampMs === null) unparsedDates++;
        const mediaType = cellText(row.getCell(6).value).replace(/^[^\p{L}]+/u, '').trim() || 'Text';
        const quoted = cellText(row.getCell(8).value).trim();

        const msg: CloudWhatsAppMessage = {
            messageId,
            groupName: cellText(row.getCell(3).value),
            sender: cellText(row.getCell(4).value),
            text: cellText(row.getCell(5).value),
            mediaType,
            quotedMessageId: quoted || undefined,
            rawTimestamp,
            timestampMs,
        };
        if (isHrMessage(msg.text)) { skipped++; continue; } // HR / leave messages stay on the server
        batch.set(fsdb.collection(WHATSAPP_COLLECTION).doc(docIdFor(messageId)), { ...toFirestoreFields(msg), backfilledFromExcel: true }, { merge: true });
        uploaded++;
        if (++pending === 400) {
            await batch.commit();
            batch = fsdb.batch();
            pending = 0;
        }
    }
    if (pending) await batch.commit();
    return { uploaded, skipped, unparsedDates };
}

export async function exportMessagesToExcel(outPath: string): Promise<number> {
    const fsdb = getFirestoreDb();
    if (!fsdb) throw new Error('Firestore is not initialized (service-account.json missing?)');

    const snap = await fsdb.collection(WHATSAPP_COLLECTION).get();
    const docs = snap.docs.map((d) => d.data()).sort((a, b) => (a.timestampMs ?? 0) - (b.timestampMs ?? 0));

    const wb = new ExcelJS.Workbook();
    const sheet = wb.addWorksheet('WhatsApp Messages');
    sheet.columns = [
        { header: 'Message ID', key: 'messageId', width: 22 },
        { header: 'Date & Time', key: 'rawTimestamp', width: 22 },
        { header: 'Group Name', key: 'groupName', width: 26 },
        { header: 'Sender', key: 'sender', width: 25 },
        { header: 'Message', key: 'text', width: 70 },
        { header: 'Type', key: 'mediaType', width: 15 },
        { header: 'Photo Preview', key: 'preview', width: 20 },
        { header: 'Quoted Message ID', key: 'quotedMessageId', width: 25 },
        { header: 'Timestamp (ISO, UTC)', key: 'timestampIso', width: 26 },
    ];
    sheet.views = [{ state: 'frozen', ySplit: 1 }];
    for (const d of docs) sheet.addRow({ ...d, preview: d.mediaFileName || '' });

    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    await wb.xlsx.writeFile(outPath);
    return docs.length;
}
