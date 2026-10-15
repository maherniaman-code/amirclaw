import { Keyboard, InlineKeyboard, InputFile } from 'grammy';
import path from 'path';
import fs from 'fs';
import { runReportEngine, saveCorrection, exportMessagesExcel, type OverdueTicket } from './bridge.js';
import { config } from '../config.js';
import { EXCEL_PATH, ensureExcelInitialized, flushWhatsAppMessages } from '../whatsapp/excel.js';

// Callback data prefix for date-picker inline buttons
export const RPT_CB_PREFIX = 'rpt';

function getDubaiDateStr(daysAgo = 0): string {
    const d = new Date(Date.now() - daysAgo * 86400000);
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Dubai',
        year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(d);
    const get = (t: string) => parts.find(p => p.type === t)?.value ?? '00';
    return `${get('day')}-${get('month')}-${get('year')}`;
}

function datePicker(reportType: string, _label: string): InlineKeyboard {
    const p = RPT_CB_PREFIX;
    return new InlineKeyboard()
        .text('📅 Today',       `${p}:${reportType}:today`)
        .text('📅 Yesterday',         `${p}:${reportType}:yesterday`)
        .row()
        .text('📆 Last 7 days',   `${p}:${reportType}:week`)
        .text('🗓️ This month',       `${p}:${reportType}:month`);
}

const REPORT_TYPE_LABELS: Record<string, string> = {
    maintenance:    '🔧 Maintenance',
    fire_safety:    '🧯 Fire & Safety',
    operations:     '🏢 Operations',
    violations:     '🚫 Parking Violations',
    permissions:    '📋 Vehicle Permissions',
    cleaning:       '🧹 Cleaning',
    parking:        '🅿️ Parking',
    parking_latest: '🅿️ Parking (latest)',
    parking_1000:   '🅿️ Parking 10:00',
    parking_1200:   '🅿️ Parking 12:00',
    parking_1400:   '🅿️ Parking 14:00',
    parking_today:  '📅 Parking today',
    parking_prev:   '⏮️ Previous period',
    parking_excel:  '📥 Parking Excel',
    parking_review: '🔍 Data review',
};

export const REPORT_BUTTONS = {
    parkingLatest: '🅿️ Parking Report — Latest',
    parking10: '🅿️ Parking Report — 10:00',
    parking12: '🅿️ Parking Report — 12:00',
    parking14: '🅿️ Parking Report — 14:00',
    parkingToday: '📅 Parking Report — Today',
    parkingPrev: '⏮️ Parking Report — Previous Period',
    downloadParkingExcel: '📥 Download Parking Excel',
    reviewConflicts: '🔍 Review Missing / Conflicting Data',
    correctClassification: '✏️ Correct Classification',
    maintenance: '🔧 Maintenance Report (Meydan FM)',
    fireSafety: '🧯 Fire & Safety Report',
    operations: '🏢 Operations Report',
    violations: '🚫 Parking Violations Report',
    permissions: '📋 Vehicle Permissions Report',
    cleaning: '🧹 Cleaning Report (Meydan)',
    dataQuality: '🩺 Data Quality — Last 7 Days',
    whatsappExcel: '📊 WhatsApp Messages Excel',
    ownerKpis: '📈 Weekly KPIs (Owner)',
    morningDigest: '🌅 Morning Digest',
    dashboard: '📊 Dashboard (Owner)',
};

export function getMeydanReportsKeyboard(): Keyboard {
    return new Keyboard()
        .text(REPORT_BUTTONS.parkingLatest)
        .row()
        .text(REPORT_BUTTONS.parking10)
        .text(REPORT_BUTTONS.parking12)
        .text(REPORT_BUTTONS.parking14)
        .row()
        .text(REPORT_BUTTONS.parkingToday)
        .text(REPORT_BUTTONS.parkingPrev)
        .row()
        .text(REPORT_BUTTONS.downloadParkingExcel)
        .text(REPORT_BUTTONS.reviewConflicts)
        .row()
        .text(REPORT_BUTTONS.maintenance)
        .text(REPORT_BUTTONS.fireSafety)
        .row()
        .text(REPORT_BUTTONS.operations)
        .text(REPORT_BUTTONS.violations)
        .row()
        .text(REPORT_BUTTONS.permissions)
        .row()
        .text(REPORT_BUTTONS.cleaning)
        .row()
        .text(REPORT_BUTTONS.dataQuality)
        .text(REPORT_BUTTONS.ownerKpis)
        .row()
        .text(REPORT_BUTTONS.morningDigest)
        .text(REPORT_BUTTONS.dashboard)
        .text(REPORT_BUTTONS.whatsappExcel)
        .row()
        .text(REPORT_BUTTONS.correctClassification)
        .resized();
}

const TELEGRAM_MAX_CHARS = 4000;

function splitLongMessage(text: string): string[] {
    if (text.length <= TELEGRAM_MAX_CHARS) return [text];
    const parts: string[] = [];
    const lines = text.split('\n');
    let chunk = '';
    for (const line of lines) {
        const added = chunk ? `${chunk}\n${line}` : line;
        if (added.length > TELEGRAM_MAX_CHARS) {
            if (chunk) parts.push(chunk);
            chunk = line;
        } else {
            chunk = added;
        }
    }
    if (chunk) parts.push(chunk);
    return parts;
}

async function replyReportTextAndExcel(ctx: any, text: string, excelPath?: string) {
    // 1. Send report text — split if > 4000 chars to stay within Telegram limit
    const parts = splitLongMessage(text);
    for (const part of parts) {
        try {
            await ctx.reply(part, { parse_mode: 'Markdown' });
        } catch (_) {
            try { await ctx.reply(part); } catch (_2) {}
        }
    }

    // 2. Send Excel workbook if generated
    if (excelPath && fs.existsSync(excelPath)) {
        try {
            await ctx.replyWithChatAction('upload_document');
        } catch (_) {}
        try {
            const filename = path.basename(excelPath);
            await ctx.replyWithDocument(new InputFile(excelPath, filename), {
                caption: `📊 Excel Workbook: ${filename}`,
            });
        } catch (docErr: any) {
            console.error('Failed to send Excel document:', docErr.message);
            await ctx.reply('⚠️ The Excel file was created but could not be sent (it may be too large).').catch(() => {});
        }
    }
}

// Sends the raw WhatsApp messages workbook (all captured groups). Used by the
// "📊 WhatsApp Messages Excel" button and the /whatsapp_excel command.
export async function sendWhatsAppExcel(ctx: any): Promise<void> {
    await ctx.replyWithChatAction('upload_document');

    // Built on demand from the SQLite store (always complete); the live workbook is the fallback
    await flushWhatsAppMessages();
    const exported = await exportMessagesExcel(path.join(process.cwd(), 'workspace', 'downloads', 'whatsapp_messages_export.xlsx'));
    let filePath = exported;
    if (!filePath) {
        await ensureExcelInitialized();
        filePath = EXCEL_PATH;
    }

    const stats = fs.statSync(filePath);
    const sizeKB = (stats.size / 1024).toFixed(1);

    await ctx.replyWithDocument(new InputFile(filePath, 'whatsapp_messages.xlsx'), {
        caption: `📊 WhatsApp messages Excel\nSize: ${sizeKB} KB\nLast updated: ${stats.mtime.toLocaleString('en-GB', { timeZone: config.timeZone })}`,
    });
}

// ── Quick correction buttons ────────────────────────────────────────────────
// Callback data: cor:<report m|f|o>:<action d|p|x|u>:<message id>  (< 64 bytes)
export const COR_CB_PREFIX = 'cor';
const COR_REPORTS: Record<string, { code: string; label: string; doneStatus: string; openStatus: string }> = {
    maintenance: { code: 'm', label: 'maintenance', doneStatus: 'Done', openStatus: 'Pending' },
    fire_safety: { code: 'f', label: 'fire_safety', doneStatus: 'Done', openStatus: 'Pending' },
    operations: { code: 'o', label: 'operations', doneStatus: 'Completed', openStatus: 'Reported' },
};
const COR_BY_CODE = Object.fromEntries(Object.entries(COR_REPORTS).map(([engine, v]) => [v.code, { engine, ...v }]));
const MAX_CORRECTION_ROWS = 25; // 4 buttons per row, Telegram allows 100 per message

export function buildCorrectionKeyboard(engine: string, items: { no: number; id: string }[]): InlineKeyboard | null {
    const r = COR_REPORTS[engine];
    if (!r || !items.length) return null;
    const kb = new InlineKeyboard();
    for (const it of items.slice(0, MAX_CORRECTION_ROWS)) {
        const base = `${COR_CB_PREFIX}:${r.code}`;
        kb.text(`#${it.no} ✅`, `${base}:d:${it.id}`)
            .text(`#${it.no} ↩️`, `${base}:p:${it.id}`)
            .text(`#${it.no} ❌`, `${base}:x:${it.id}`)
            .text(`#${it.no} 🔁`, `${base}:u:${it.id}`)
            .row();
    }
    return kb;
}

// Overdue reminder: one message listing tickets open > 48h, with ✅ fixed / ❌ not a ticket per item
export function buildOverdueMessage(items: OverdueTicket[]): { text: string; keyboard: InlineKeyboard } {
    const shown = items.slice(0, MAX_CORRECTION_ROWS);
    const kb = new InlineKeyboard();
    const lines = ['⏰ Tickets open for more than 48 hours:', ''];
    shown.forEach((it, i) => {
        const n = i + 1;
        const r = COR_REPORTS[it.report];
        const icon = it.report === 'fire_safety' ? '🧯' : '🔧';
        const days = (it.hours_open / 24).toFixed(1);
        lines.push(`#${n} ${icon} ${it.building ? `Building ${it.building} — ` : ''}${days} days — ${it.reported}`);
        lines.push(`   ${it.text}`);
        kb.text(`#${n} ✅ Fixed`, `${COR_CB_PREFIX}:${r.code}:d:${it.id}`)
            .text(`#${n} ❌ Not a ticket`, `${COR_CB_PREFIX}:${r.code}:x:${it.id}`)
            .row();
    });
    if (items.length > shown.length) lines.push('', `+${items.length - shown.length} more in the next reminder.`);
    lines.push('', 'Press ✅ if it is actually fixed, or ❌ if it was not a fault. Each ticket is reminded only once.');
    return { text: lines.join('\n'), keyboard: kb };
}

async function sendCorrectionButtons(ctx: any, engine: string, items?: { no: number; id: string }[]) {
    const kb = buildCorrectionKeyboard(engine, items || []);
    if (!kb) return;
    await ctx.reply(
        '✏️ Quick corrections — item numbers as in the report:\n' +
        '✅ fixed   ↩️ still open   ❌ not a ticket   🔁 duplicate\n' +
        'Corrections are saved and applied to every later report.',
        { reply_markup: kb },
    );
}

export async function handleCorrectionCallback(ctx: any, data: string): Promise<boolean> {
    const parts = data.split(':');
    if (parts[0] !== COR_CB_PREFIX || parts.length !== 4) return false;
    const [, code, action, messageId] = parts;
    const r = COR_BY_CODE[code];
    if (!r || !messageId) return false;

    const by = ctx.from?.username || ctx.from?.first_name || String(ctx.from?.id || 'telegram');
    let ok = false;
    let label = '';
    if (action === 'd') {
        ok = await saveCorrection(messageId, r.label, r.doneStatus, by, 'telegram: done');
        label = 'marked fixed';
    } else if (action === 'p') {
        ok = await saveCorrection(messageId, r.label, r.openStatus, by, 'telegram: still open');
        label = 'marked still open';
    } else if (action === 'x') {
        ok = await saveCorrection(messageId, 'ignore', undefined, by, 'telegram: not a ticket');
        label = 'removed (not a ticket)';
    } else if (action === 'u') {
        ok = await saveCorrection(messageId, 'ignore', undefined, by, 'telegram: duplicate');
        label = 'removed (duplicate)';
    } else {
        return false;
    }
    try {
        await ctx.answerCallbackQuery({
            text: ok ? `✔ Saved: ${label}. Request the report again to see it.` : '❌ Could not save the correction',
            show_alert: !ok,
        });
    } catch (_) {}
    // Keep a visible record on the message itself: the pressed item's buttons become "✔ #n <label>"
    if (ok) {
        try {
            const rows: any[][] = ctx.callbackQuery?.message?.reply_markup?.inline_keyboard || [];
            const updated = rows.map((row) => {
                if (!row.some((b: any) => b.callback_data === data)) return row;
                const num = String(row[0]?.text || '').split(' ')[0];
                return [{ text: `✔ ${num} ${label}`, callback_data: `${COR_CB_PREFIX}:noop` }];
            });
            if (rows.length) await ctx.editMessageReplyMarkup({ reply_markup: { inline_keyboard: updated } });
        } catch (_) {}
    }
    return true;
}

export async function handleReportAction(ctx: any, input: string): Promise<boolean> {
    const trimmed = (input || '').trim();

    // 1. Menu Command
    if (trimmed === '/reports' || trimmed === '/meydan') {
        await ctx.reply(
            '📊 *Meydan Reports Automation Menu*\n\n' +
            'Select any report below to generate it instantly on-demand with full data validation and Excel export:',
            {
                parse_mode: 'Markdown',
                reply_markup: getMeydanReportsKeyboard(),
            }
        );
        return true;
    }

    // 2. Parking Report - Latest
    if (trimmed === REPORT_BUTTONS.parkingLatest || trimmed === '/parking') {
        await ctx.reply('🅿️ Parking report (latest) — choose a date:', { reply_markup: datePicker('parking_latest', 'Parking') });
        return true;
    }

    // 3. Parking 10:00
    if (trimmed === REPORT_BUTTONS.parking10) {
        await ctx.reply('🅿️ Parking report 10:00 — choose a date:', { reply_markup: datePicker('parking_1000', 'Parking 10:00') });
        return true;
    }

    // 4. Parking 12:00
    if (trimmed === REPORT_BUTTONS.parking12) {
        await ctx.reply('🅿️ Parking report 12:00 — choose a date:', { reply_markup: datePicker('parking_1200', 'Parking 12:00') });
        return true;
    }

    // 5. Parking 14:00
    if (trimmed === REPORT_BUTTONS.parking14) {
        await ctx.reply('🅿️ Parking report 14:00 — choose a date:', { reply_markup: datePicker('parking_1400', 'Parking 14:00') });
        return true;
    }

    // 6. Parking - Today
    if (trimmed === REPORT_BUTTONS.parkingToday) {
        await ctx.reply('📅 Parking today — choose a date:', { reply_markup: datePicker('parking_today', 'Parking today') });
        return true;
    }

    // 7. Parking - Previous Period
    if (trimmed === REPORT_BUTTONS.parkingPrev) {
        await ctx.reply('⏮️ Previous parking period — choose a date:', { reply_markup: datePicker('parking_prev', 'Previous period') });
        return true;
    }

    // 8. Download Parking Excel
    if (trimmed === REPORT_BUTTONS.downloadParkingExcel) {
        await ctx.reply('📥 Parking Excel — choose a date:', { reply_markup: datePicker('parking_excel', 'Parking Excel') });
        return true;
    }

    // 9. Review Missing / Conflicting Data
    if (trimmed === REPORT_BUTTONS.reviewConflicts) {
        await ctx.reply('🔍 Data review — choose a date:', { reply_markup: datePicker('parking_review', 'Data review') });
        return true;
    }

    // 10. Correct Classification
    if (trimmed === REPORT_BUTTONS.correctClassification) {
        await ctx.reply(
            '✏️ Correct classification\n\n' +
            'Request a Maintenance, Fire & Safety or Operations report from the buttons. After the report you get buttons for every item:\n' +
            '✅ fixed   ↩️ still open   ❌ not a ticket   🔁 duplicate\n\n' +
            'Corrections are saved and applied to every later report.',
        );
        return true;
    }

    // 11. Maintenance Report (Meydan FM)
    if (trimmed === REPORT_BUTTONS.maintenance || trimmed === '/maintenance') {
        await ctx.reply('🔧 Maintenance report — choose a date:', { reply_markup: datePicker('maintenance', 'Maintenance') });
        return true;
    }

    // 12. Fire & Safety Report
    if (trimmed === REPORT_BUTTONS.fireSafety || trimmed === '/fire_safety') {
        await ctx.reply('🧯 Fire & Safety report — choose a date:', { reply_markup: datePicker('fire_safety', 'Fire & Safety') });
        return true;
    }

    // 13. Operations Report
    if (trimmed === REPORT_BUTTONS.operations || trimmed === '/operations') {
        await ctx.reply('🏢 Operations report — choose a date:', { reply_markup: datePicker('operations', 'Operations') });
        return true;
    }

    // 14. Parking Violations Report
    if (trimmed === REPORT_BUTTONS.violations || trimmed === '/violations') {
        await ctx.reply('🚫 Parking violations — choose a date:', { reply_markup: datePicker('violations', 'Violations') });
        return true;
    }

    // 15. Vehicle Permissions Report
    if (trimmed === REPORT_BUTTONS.permissions || trimmed === '/permissions') {
        await ctx.reply('📋 Vehicle permissions — choose a date:', { reply_markup: datePicker('permissions', 'Permissions') });
        return true;
    }

    // 16. Cleaning Report
    if (trimmed === REPORT_BUTTONS.cleaning || trimmed === '/cleaning') {
        await ctx.reply('🧹 Cleaning report — choose a date:', { reply_markup: datePicker('cleaning', 'Cleaning') });
        return true;
    }

    if (trimmed === REPORT_BUTTONS.morningDigest || trimmed === '/digest') {
        const res = await runReportEngine('morning_digest');
        if (res.success && res.text) {
            await replyReportTextAndExcel(ctx, res.text);
        } else {
            await ctx.reply(`❌ Morning digest failed: ${res.error || 'unknown error'}`);
        }
        return true;
    }

    if (trimmed === REPORT_BUTTONS.dashboard || trimmed === '/dashboard') {
        await ctx.reply('📊 Preparing the dashboard...');
        const res = await runReportEngine('dashboard');
        if (res.success && res.htmlPath && fs.existsSync(res.htmlPath)) {
            await ctx.replyWithDocument(new InputFile(res.htmlPath, path.basename(res.htmlPath)), { caption: res.text });
        } else {
            await ctx.reply(`❌ Dashboard failed: ${res.error || 'unknown error'}`);
        }
        return true;
    }

    if (trimmed === '/overdue') {
        const res = await runReportEngine('overdue');
        if (!res.success) {
            await ctx.reply(`❌ Overdue list failed: ${res.error || 'unknown error'}`);
        } else if (!res.overdue?.length) {
            await ctx.reply('✅ No tickets open for more than 48 hours.');
        } else {
            const { text, keyboard } = buildOverdueMessage(res.overdue);
            await ctx.reply(text, { reply_markup: keyboard });
        }
        return true;
    }

    if (trimmed === REPORT_BUTTONS.ownerKpis || trimmed === '/kpis') {
        await ctx.reply('📈 Preparing the weekly summary...');
        const res = await runReportEngine('owner_kpis');
        if (res.success && res.text) {
            await replyReportTextAndExcel(ctx, res.text);
        } else {
            await ctx.reply(`❌ Weekly summary failed: ${res.error || 'unknown error'}`);
        }
        return true;
    }

    if (trimmed === REPORT_BUTTONS.whatsappExcel) {
        await sendWhatsAppExcel(ctx);
        return true;
    }

    if (trimmed === REPORT_BUTTONS.dataQuality || trimmed === '/dataquality') {
        await ctx.reply('🩺 Checking data quality for the last 7 days...');
        const res = await runReportEngine('data_quality');
        if (res.success && res.text) {
            await replyReportTextAndExcel(ctx, res.text);
        } else {
            await ctx.reply(`❌ Data-quality report failed: ${res.error || 'unknown error'}`);
        }
        return true;
    }

    return false;
}

// Maps reportKey → { engineType, slot? }
const REPORT_KEY_MAP: Record<string, { engine: string; slot?: string; isExcel?: boolean; isReview?: boolean }> = {
    maintenance:      { engine: 'maintenance' },
    fire_safety:      { engine: 'fire_safety' },
    operations:       { engine: 'operations' },
    violations:       { engine: 'violations' },
    permissions:      { engine: 'permissions' },
    cleaning:         { engine: 'cleaning' },
    parking_latest:   { engine: 'parking', slot: 'latest' },
    parking_1000:     { engine: 'parking', slot: '10:00' },
    parking_1200:     { engine: 'parking', slot: '12:00' },
    parking_1400:     { engine: 'parking', slot: '14:00' },
    parking_today:    { engine: 'parking', slot: 'latest' },
    parking_prev:     { engine: 'parking', slot: '__prev__' },
    parking_excel:    { engine: 'parking', slot: 'latest', isExcel: true },
    parking_review:   { engine: 'parking', slot: 'latest', isReview: true },
};

// Handles callback_query from inline date-picker buttons (format: rpt:<reportKey>:<today|yesterday>)
export async function handleReportCallback(ctx: any, data: string): Promise<boolean> {
    const parts = data.split(':');
    if (parts[0] !== RPT_CB_PREFIX || parts.length !== 3) return false;

    const [, reportKey, dateKey] = parts;
    const mapping = REPORT_KEY_MAP[reportKey];
    if (!mapping) return false;

    const DATE_LABELS: Record<string, string> = {
        today: 'today', yesterday: 'yesterday', week: 'last 7 days', month: 'this month',
    };
    const dateLabel = DATE_LABELS[dateKey] ?? dateKey;

    // week/month are passed as special strings directly to Python; others as DD-MM-YYYY
    const date = dateKey === 'yesterday' ? getDubaiDateStr(1)
               : dateKey === 'today'     ? getDubaiDateStr(0)
               : dateKey; // 'week' or 'month' passed verbatim
    const label = REPORT_TYPE_LABELS[reportKey] || REPORT_TYPE_LABELS[mapping.engine] || reportKey;

    try { await ctx.answerCallbackQuery({ text: `⏳ Generating ${label} — ${dateLabel}...` }); } catch (_) {}

    // Resolve dynamic slot for parking_prev
    let slot = mapping.slot;
    if (slot === '__prev__') {
        const dubaiHour = parseInt(
            new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Dubai', hour: 'numeric', hour12: false }).format(new Date()), 10,
        );
        const slotHours = [10, 12, 14];
        const slots = ['10:00', '12:00', '14:00'];
        slot = '10:00';
        for (let i = slotHours.length - 1; i >= 0; i--) {
            if (dubaiHour > slotHours[i]) { slot = slots[i]; break; }
        }
    }

    try { await ctx.replyWithChatAction(mapping.isExcel ? 'upload_document' : 'typing'); } catch (_) {}

    const res = await runReportEngine(mapping.engine as any, { date, slot });

    // Special handling: Excel download
    if (mapping.isExcel) {
        if (res.success && res.excelPath && fs.existsSync(res.excelPath)) {
            try {
                const filename = path.basename(res.excelPath);
                await ctx.replyWithDocument(new InputFile(res.excelPath, filename), {
                    caption: `📥 Complete Meydan Parking Multi-Sheet Excel Workbook — ${dateLabel}`,
                });
            } catch (docErr: any) {
                console.error('Failed to send parking Excel:', docErr.message);
                await ctx.reply('⚠️ Could not send the Excel file (it may be too large).').catch(() => {});
            }
        } else {
            await ctx.reply('⚠️ The Excel file cannot be created right now.');
        }
        return true;
    }

    // Special handling: diagnostics review
    if (mapping.isReview) {
        const diagnostics = res.diagnostics || [];
        let diagMsg = `🔍 *Data review — ${dateLabel}:*\n\n`;
        if (diagnostics.length === 0) {
            diagMsg += '✅ All parking sources are complete and consistent, no conflicts.';
        } else {
            diagMsg += `⚠️ ${diagnostics.length} issue(s) found:\n\n`;
            diagnostics.forEach((d, idx) => { diagMsg += `• [${idx + 1}] ${d}\n`; });
        }
        try {
            await ctx.reply(diagMsg, { parse_mode: 'Markdown' });
        } catch (_) {
            await ctx.reply(diagMsg);
        }
        return true;
    }

    // Standard report text + Excel
    if (res.success && res.text) {
        await replyReportTextAndExcel(ctx, res.text, res.excelPath);
        await sendCorrectionButtons(ctx, mapping.engine, res.items);
    } else {
        await ctx.reply(`❌ Failed: ${res.error || 'unknown error'}`);
    }
    return true;
}
