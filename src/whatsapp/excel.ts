import ExcelJS from 'exceljs';
import fs from 'fs';
import path from 'path';
import sharp from 'sharp';
import { config } from '../config.js';
import { alertOwner } from '../monitoring/alerts.js';
import { storeWhatsAppMessage } from './store.js';

const WORKSPACE_DIR = path.resolve(process.cwd(), 'workspace');
export const EXCEL_PATH = path.join(WORKSPACE_DIR, 'whatsapp_messages.xlsx');

export interface WhatsAppMessageEntry {
    messageId: string;
    timestamp: string;
    timestampMs?: number; // exact WhatsApp message time (UTC epoch ms); the text above is display only
    groupName: string;
    sender: string;
    text: string;
    mediaType: string;
    mediaFileName?: string;
    mediaLocalPath?: string;
    thumbnailPath?: string;
    quotedMessageId?: string;
}

// In-memory cache of known message IDs to avoid duplicates
const knownMessageIds = new Set<string>();
let _excelInitialized = false;

// Queue for batch processing
let pendingEntries: WhatsAppMessageEntry[] = [];
let flushTimeout: NodeJS.Timeout | null = null;
let isFlushing = false;

const SHEET_NAME = 'WhatsApp Messages';

// Color Palette
const COLORS = {
    headerBg: 'FF1E293B',    // Dark Slate
    headerText: 'FFFFFFFF',  // Pure White
    zebraLight: 'FFF8FAFC',  // Ultra-light Slate
    white: 'FFFFFFFF',
    border: 'FFE2E8F0',     // Soft Border
    primaryText: 'FF0F172A',
    secondaryText: 'FF475569',
    blueLink: 'FF2563EB',
    botBadge: 'FF1D4ED8',
};

function formatMediaTypeBadge(type: string): string {
    const t = (type || '').toLowerCase();
    if (t.includes('image') || t.includes('صورة')) return '📷 Image';
    if (t.includes('doc') || t.includes('مستند')) return '📄 Document';
    if (t.includes('video') || t.includes('فيديو')) return '🎬 Video';
    if (t.includes('audio') || t.includes('صوتي')) return '🎵 Audio';
    if (t.includes('sticker') || t.includes('ملصق')) return '🏷️ Sticker';
    if (t.includes('location') || t.includes('موقع')) return '📍 Location';
    if (t.includes('contact') || t.includes('جهة')) return '👤 Contact';
    return '💬 Text';
}

// Calculate appropriate row height based on text length
function calcRowHeight(text: string): number {
    const lines = (text || '').split('\n').length;
    const charsPerLine = 70; // approx characters visible per line at col width 70
    const wrappedLines = Math.ceil((text || '').length / charsPerLine);
    const totalLines = Math.max(lines, wrappedLines);
    return Math.min(Math.max(totalLines * 15, 24), 200); // min 24px, max 200px
}

const HEADER_LABELS = [
    'Message ID',
    'Date & Time',
    'Group Name',
    'Sender',
    'Message',
    'Type',
    'Photo Preview',
];

export function getHeaderLabels(): string[] {
    const labels = [...HEADER_LABELS];
    if (config.enableQuotedMessageId) {
        labels.push('Quoted Message ID');
    }
    return labels;
}

function applyHeaderStyle(headerRow: ExcelJS.Row): void {
    headerRow.height = 36;
    const labels = getHeaderLabels();

    labels.forEach((label, idx) => {
        const cell = headerRow.getCell(idx + 1);
        cell.value = label;
        cell.font = { bold: true, color: { argb: COLORS.headerText }, size: 11, name: 'Calibri' };
        cell.fill = {
            type: 'pattern',
            pattern: 'solid',
            fgColor: { argb: COLORS.headerBg },
        };
        cell.alignment = { vertical: 'middle', horizontal: 'center', wrapText: false };
        cell.border = {
            top: { style: 'medium', color: { argb: 'FF0F172A' } },
            bottom: { style: 'medium', color: { argb: 'FF0F172A' } },
            left: { style: 'thin', color: { argb: 'FF334155' } },
            right: { style: 'thin', color: { argb: 'FF334155' } },
        };
    });
}

/**
 * Initializes the Excel file with premium styling, frozen headers, and auto-filters.
 */
export async function ensureExcelInitialized(): Promise<void> {
    if (_excelInitialized) return;
    if (!fs.existsSync(WORKSPACE_DIR)) {
        fs.mkdirSync(WORKSPACE_DIR, { recursive: true });
    }

    const workbook = new ExcelJS.Workbook();
    const filterEnd = config.enableQuotedMessageId ? 'H1' : 'G1';

    if (fs.existsSync(EXCEL_PATH)) {
        try {
            await workbook.xlsx.readFile(EXCEL_PATH);
            const sheet = workbook.getWorksheet(SHEET_NAME) || workbook.worksheets[0];
            if (sheet) {
                sheet.name = SHEET_NAME;
                sheet.views = [
                    { state: 'frozen', xSplit: 0, ySplit: 1, activeCell: 'A2', rightToLeft: false },
                ];
                sheet.autoFilter = { from: 'A1', to: filterEnd };

                if (config.enableQuotedMessageId) {
                    const hCell8 = sheet.getRow(1).getCell(8);
                    if (!hCell8.value) {
                        applyHeaderStyle(sheet.getRow(1));
                        if ((sheet.getColumn(8).width || 0) < 25) {
                            sheet.getColumn(8).width = 25;
                        }
                        await workbook.xlsx.writeFile(EXCEL_PATH);
                    }
                }

                sheet.eachRow((row, rowNumber) => {
                    if (rowNumber > 1) {
                        const val = row.getCell(1).value;
                        if (val) knownMessageIds.add(String(val));
                    }
                });
            }
            return;
        } catch (err) {
            console.error('Error reading existing Excel file, will re-create:', err);
        }
    }

    const sheet = workbook.addWorksheet(SHEET_NAME, {
        views: [{ state: 'frozen', xSplit: 0, ySplit: 1, activeCell: 'A2', rightToLeft: false }],
    });

    const columns: any[] = [
        { header: 'Message ID', key: 'messageId', width: 22 },
        { header: 'Date & Time', key: 'timestamp', width: 22 },
        { header: 'Group Name', key: 'groupName', width: 26 },
        { header: 'Sender', key: 'sender', width: 25 },
        { header: 'Message', key: 'text', width: 70 },
        { header: 'Type', key: 'mediaType', width: 15 },
        { header: 'Photo Preview', key: 'preview', width: 20 },
    ];
    if (config.enableQuotedMessageId) {
        columns.push({ header: 'Quoted Message ID', key: 'quotedMessageId', width: 25 });
    }
    sheet.columns = columns;

    sheet.autoFilter = { from: 'A1', to: filterEnd };
    applyHeaderStyle(sheet.getRow(1));

    await workbook.xlsx.writeFile(EXCEL_PATH);
    console.log(`📊 Initialized polished English WhatsApp Excel file at: ${EXCEL_PATH}`);
    _excelInitialized = true;
}

/**
 * Flush all pending messages to the Excel file in a single batch.
 */
export async function flushWhatsAppMessages(): Promise<void> {
    if (flushTimeout) {
        clearTimeout(flushTimeout);
        flushTimeout = null;
    }

    if (pendingEntries.length === 0 || isFlushing) {
        return;
    }

    isFlushing = true;
    const batch = [...pendingEntries];
    pendingEntries = [];

    try {
        await ensureExcelInitialized();

        const workbook = new ExcelJS.Workbook();
        await workbook.xlsx.readFile(EXCEL_PATH);
        const sheet = workbook.getWorksheet(SHEET_NAME) || workbook.worksheets[0];
        sheet.name = SHEET_NAME;
        sheet.views = [
            { state: 'frozen', xSplit: 0, ySplit: 1, activeCell: 'A2', rightToLeft: false },
        ];
        const filterEnd = config.enableQuotedMessageId ? 'H1' : 'G1';
        sheet.autoFilter = { from: 'A1', to: filterEnd };

        // Ensure header row is polished
        applyHeaderStyle(sheet.getRow(1));

        let addedCount = 0;
        for (const entry of batch) {
            if (knownMessageIds.has(entry.messageId)) {
                continue;
            }

            knownMessageIds.add(entry.messageId);
            addedCount++;

            const badgeText = formatMediaTypeBadge(entry.mediaType);
            const isImage = badgeText.includes('Image');

            const rowValues = [
                entry.messageId,
                entry.timestamp,
                entry.groupName,
                entry.sender,
                entry.text || '',
                badgeText,
                '', // Col 7 placeholder for embedded thumbnail
            ];
            if (config.enableQuotedMessageId) {
                rowValues.push(entry.quotedMessageId || '');
            }

            const newRow = sheet.addRow(rowValues);

            const rowNum = newRow.number;
            const isZebra = rowNum % 2 === 1;
            const rowBg = isZebra ? COLORS.zebraLight : COLORS.white;

            // Embed compressed thumbnail if image
            const imgPath = entry.thumbnailPath || (isImage ? entry.mediaLocalPath : undefined);
            if (imgPath && fs.existsSync(imgPath)) {
                try {
                    const TARGET_W = Math.round(2.30 * 96); // 221 px
                    const TARGET_H = Math.round(1.70 * 96); // 163 px
                    const PICTURE_MAX_H = 260; // upright phone videos get a taller cell
                    const ROW_HEIGHT_PTS = Math.round(1.70 * 72); // 122 pt

                    let imgW = TARGET_W;
                    let imgH = TARGET_H;

                    try {
                        const meta = await sharp(imgPath).metadata();
                        if (meta.width && meta.height) {
                            // Video frames are stored larger than the cell: scale down to fit it
                            const scale = Math.min(1, TARGET_W / meta.width, PICTURE_MAX_H / meta.height);
                            imgW = Math.round(meta.width * scale);
                            imgH = Math.round(meta.height * scale);
                        }
                    } catch {}

                    const imageId = workbook.addImage({
                        filename: imgPath,
                        extension: 'jpeg',
                    });

                    sheet.addImage(imageId, {
                        tl: { col: 6 + 0.05, row: rowNum - 1 + 0.04 },
                        ext: { width: imgW, height: imgH },
                        editAs: 'oneCell',
                    });

                    newRow.height = Math.max(ROW_HEIGHT_PTS, Math.round(imgH * 0.75) + 6);

                    if ((sheet.getColumn(7).width || 0) < 28) {
                        sheet.getColumn(7).width = 28;
                    }
                } catch (imgErr) {
                    console.warn(`Could not embed image in Excel row: ${imgErr}`);
                    newRow.height = calcRowHeight(entry.text || '');
                }
            } else {
                // Auto-expand row height to fit full message text
                newRow.height = calcRowHeight(entry.text || '');
            }



            // Cell styling and typography
            newRow.eachCell({ includeEmpty: true }, (cell, colNum) => {
                // Background & border
                cell.fill = {
                    type: 'pattern',
                    pattern: 'solid',
                    fgColor: { argb: rowBg },
                };
                cell.border = {
                    top: { style: 'thin', color: { argb: COLORS.border } },
                    left: { style: 'thin', color: { argb: COLORS.border } },
                    bottom: { style: 'thin', color: { argb: COLORS.border } },
                    right: { style: 'thin', color: { argb: COLORS.border } },
                };

                // Alignment & Fonts per column
                if (colNum === 1) {
                    // Message ID
                    cell.font = { name: 'Courier New', size: 9, color: { argb: COLORS.secondaryText } };
                    cell.alignment = { vertical: 'middle', horizontal: 'center' };
                } else if (colNum === 2) {
                    // Date & Time
                    cell.font = { name: 'Calibri', size: 10, color: { argb: COLORS.secondaryText } };
                    cell.alignment = { vertical: 'middle', horizontal: 'center' };
                } else if (colNum === 3) {
                    // Group Name
                    cell.font = { bold: true, name: 'Calibri', size: 10, color: { argb: COLORS.primaryText } };
                    cell.alignment = { vertical: 'middle', horizontal: 'left', wrapText: true };
                } else if (colNum === 4) {
                    // Sender
                    const isBot = (entry.sender || '').includes('Bot');
                    cell.font = {
                        bold: isBot,
                        name: 'Calibri',
                        size: 10,
                        color: { argb: isBot ? COLORS.botBadge : COLORS.primaryText },
                    };
                    cell.alignment = { vertical: 'middle', horizontal: 'left', wrapText: true };
                } else if (colNum === 5) {
                    // Message
                    cell.font = { name: 'Calibri', size: 10, color: { argb: COLORS.primaryText } };
                    cell.alignment = { vertical: 'middle', horizontal: 'left', wrapText: true };
                } else if (colNum === 6) {
                    // Media Type Badge
                    cell.font = { bold: true, name: 'Calibri', size: 10, color: { argb: COLORS.primaryText } };
                    cell.alignment = { vertical: 'middle', horizontal: 'center' };
                } else if (colNum === 7) {
                    // Photo Preview
                    cell.alignment = { vertical: 'middle', horizontal: 'center' };
                } else if (colNum === 8) {
                    // Quoted Message ID
                    cell.font = { name: 'Courier New', size: 9, color: { argb: COLORS.secondaryText } };
                    cell.alignment = { vertical: 'middle', horizontal: 'center' };
                }
            });
        }

        if (addedCount > 0) {
            await workbook.xlsx.writeFile(EXCEL_PATH);
            console.log(`📊 Flushed ${addedCount} WhatsApp messages to polished Excel (Total: ${knownMessageIds.size})`);
        }
    } catch (err: any) {
        console.error('Error flushing WhatsApp messages to Excel:', err);
        alertOwner('excel-flush', `Saving WhatsApp messages to the Excel file failed: ${err?.message || err}. They are kept in memory and retried.`).catch(() => {});
        pendingEntries = [...batch, ...pendingEntries];
    } finally {
        isFlushing = false;
        if (pendingEntries.length > 0) {
            scheduleFlush();
        }
    }
}

function scheduleFlush(): void {
    if (flushTimeout) return;
    flushTimeout = setTimeout(() => {
        flushWhatsAppMessages().catch(console.error);
    }, 2000);
}

/**
 * Queue a WhatsApp message for batch insertion.
 */
export async function appendWhatsAppMessage(entry: WhatsAppMessageEntry): Promise<void> {
    // SQLite copy for the reports engine (INSERT OR IGNORE, so repeats are harmless)
    storeWhatsAppMessage({
        messageId: entry.messageId,
        timestamp: entry.timestamp,
        timestampMs: entry.timestampMs,
        groupName: entry.groupName,
        sender: entry.sender,
        text: entry.text,
        mediaBadge: formatMediaTypeBadge(entry.mediaType),
        // Same semantics as the Excel column: absent column = not recorded, '' = not a reply
        quotedMessageId: config.enableQuotedMessageId ? (entry.quotedMessageId || '') : null,
        thumbnailPath: entry.thumbnailPath,
        mediaLocalPath: entry.mediaLocalPath,
        mediaFileName: entry.mediaFileName,
    }).catch((err) => {
        console.error('Error storing WhatsApp message in SQLite:', err);
        alertOwner('sqlite-store', `Saving a WhatsApp message to the database failed: ${err?.message || err}`).catch(() => {});
    });

    // The SQLite store above is the source of truth; the live workbook can be switched off
    // (EXCEL_LIVE_WRITE=false) once on-demand export is trusted, saving a full rewrite per message.
    if (process.env.EXCEL_LIVE_WRITE === 'false') return;
    if (knownMessageIds.has(entry.messageId)) return;
    pendingEntries.push(entry);

    if (pendingEntries.length >= 10) {
        await flushWhatsAppMessages();
    } else {
        scheduleFlush();
    }
}
