import { Keyboard, InlineKeyboard, InputFile } from 'grammy';
import path from 'path';
import fs from 'fs';
import { runReportEngine } from './bridge.js';

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
        .text('📅 اليوم',       `${p}:${reportType}:today`)
        .text('📅 أمس',         `${p}:${reportType}:yesterday`)
        .row()
        .text('📆 آخر أسبوع',   `${p}:${reportType}:week`)
        .text('🗓️ الشهر',       `${p}:${reportType}:month`);
}

const REPORT_TYPE_LABELS: Record<string, string> = {
    maintenance:    '🔧 الصيانة',
    fire_safety:    '🧯 الحريق والسلامة',
    operations:     '🏢 العمليات',
    violations:     '🚫 مخالفات المواقف',
    permissions:    '📋 تصاريح المركبات',
    cleaning:       '🧹 النظافة',
    parking:        '🅿️ المواقف',
    parking_latest: '🅿️ المواقف (آخر فترة)',
    parking_1000:   '🅿️ المواقف 10:00',
    parking_1200:   '🅿️ المواقف 12:00',
    parking_1400:   '🅿️ المواقف 14:00',
    parking_today:  '📅 مواقف اليوم',
    parking_prev:   '⏮️ الفترة السابقة',
    parking_excel:  '📥 إكسل المواقف',
    parking_review: '🔍 مراجعة البيانات',
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
            await ctx.reply('⚠️ Excel تم إنشاؤه لكن فشل إرساله (ربما حجمه كبير).').catch(() => {});
        }
    }
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
        await ctx.reply('🅿️ اختر تاريخ تقرير المواقف (آخر فترة):', { reply_markup: datePicker('parking_latest', 'المواقف') });
        return true;
    }

    // 3. Parking 10:00
    if (trimmed === REPORT_BUTTONS.parking10) {
        await ctx.reply('🅿️ اختر تاريخ تقرير المواقف — 10:00:', { reply_markup: datePicker('parking_1000', 'المواقف 10:00') });
        return true;
    }

    // 4. Parking 12:00
    if (trimmed === REPORT_BUTTONS.parking12) {
        await ctx.reply('🅿️ اختر تاريخ تقرير المواقف — 12:00:', { reply_markup: datePicker('parking_1200', 'المواقف 12:00') });
        return true;
    }

    // 5. Parking 14:00
    if (trimmed === REPORT_BUTTONS.parking14) {
        await ctx.reply('🅿️ اختر تاريخ تقرير المواقف — 14:00:', { reply_markup: datePicker('parking_1400', 'المواقف 14:00') });
        return true;
    }

    // 6. Parking - Today
    if (trimmed === REPORT_BUTTONS.parkingToday) {
        await ctx.reply('📅 اختر تاريخ تقرير مواقف اليوم:', { reply_markup: datePicker('parking_today', 'مواقف اليوم') });
        return true;
    }

    // 7. Parking - Previous Period
    if (trimmed === REPORT_BUTTONS.parkingPrev) {
        await ctx.reply('⏮️ اختر تاريخ الفترة السابقة:', { reply_markup: datePicker('parking_prev', 'الفترة السابقة') });
        return true;
    }

    // 8. Download Parking Excel
    if (trimmed === REPORT_BUTTONS.downloadParkingExcel) {
        await ctx.reply('📥 اختر تاريخ لتحميل ملف الإكسل:', { reply_markup: datePicker('parking_excel', 'إكسل المواقف') });
        return true;
    }

    // 9. Review Missing / Conflicting Data
    if (trimmed === REPORT_BUTTONS.reviewConflicts) {
        await ctx.reply('🔍 اختر تاريخ مراجعة التعارضات:', { reply_markup: datePicker('parking_review', 'مراجعة البيانات') });
        return true;
    }

    // 10. Correct Classification
    if (trimmed === REPORT_BUTTONS.correctClassification) {
        await ctx.reply(
            '✏️ *Manual Classification Correction (Rule Learning without AI)*\n\n' +
            'To correct any message classification, send:\n' +
            '`/correct <message_id> <target_report> [status]`\n\n' +
            '_Example:_\n' +
            '`/correct MSG_123 maintenance Done`\n' +
            '`/correct MSG_456 operations Reported`\n\n' +
            'Available target reports: `maintenance`, `fire_safety`, `operations`, `violations`, `permissions`, `ignore`.',
            { parse_mode: 'Markdown' }
        );
        return true;
    }

    // 11. Maintenance Report (Meydan FM)
    if (trimmed === REPORT_BUTTONS.maintenance || trimmed === '/maintenance') {
        await ctx.reply('🔧 اختر تاريخ تقرير الصيانة:', { reply_markup: datePicker('maintenance', 'الصيانة') });
        return true;
    }

    // 12. Fire & Safety Report
    if (trimmed === REPORT_BUTTONS.fireSafety || trimmed === '/fire_safety') {
        await ctx.reply('🧯 اختر تاريخ تقرير الحريق والسلامة:', { reply_markup: datePicker('fire_safety', 'الحريق') });
        return true;
    }

    // 13. Operations Report
    if (trimmed === REPORT_BUTTONS.operations || trimmed === '/operations') {
        await ctx.reply('🏢 اختر تاريخ تقرير العمليات:', { reply_markup: datePicker('operations', 'العمليات') });
        return true;
    }

    // 14. Parking Violations Report
    if (trimmed === REPORT_BUTTONS.violations || trimmed === '/violations') {
        await ctx.reply('🚫 اختر تاريخ تقرير المخالفات:', { reply_markup: datePicker('violations', 'المخالفات') });
        return true;
    }

    // 15. Vehicle Permissions Report
    if (trimmed === REPORT_BUTTONS.permissions || trimmed === '/permissions') {
        await ctx.reply('📋 اختر تاريخ تقرير التصاريح:', { reply_markup: datePicker('permissions', 'التصاريح') });
        return true;
    }

    // 16. Cleaning Report
    if (trimmed === REPORT_BUTTONS.cleaning || trimmed === '/cleaning') {
        await ctx.reply('🧹 اختر تاريخ تقرير النظافة:', { reply_markup: datePicker('cleaning', 'النظافة') });
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
        today: 'اليوم', yesterday: 'أمس', week: 'آخر أسبوع', month: 'الشهر',
    };
    const dateLabel = DATE_LABELS[dateKey] ?? dateKey;

    // week/month are passed as special strings directly to Python; others as DD-MM-YYYY
    const date = dateKey === 'yesterday' ? getDubaiDateStr(1)
               : dateKey === 'today'     ? getDubaiDateStr(0)
               : dateKey; // 'week' or 'month' passed verbatim
    const label = REPORT_TYPE_LABELS[reportKey] || REPORT_TYPE_LABELS[mapping.engine] || reportKey;

    try { await ctx.answerCallbackQuery({ text: `⏳ جاري إنتاج تقرير ${label} — ${dateLabel}...` }); } catch (_) {}

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
                await ctx.reply('⚠️ تعذّر إرسال ملف الإكسل (ربما حجمه كبير).').catch(() => {});
            }
        } else {
            await ctx.reply('⚠️ لا يمكن إنشاء ملف الإكسل في الوقت الحالي.');
        }
        return true;
    }

    // Special handling: diagnostics review
    if (mapping.isReview) {
        const diagnostics = res.diagnostics || [];
        let diagMsg = `🔍 *مراجعة البيانات — ${dateLabel}:*\n\n`;
        if (diagnostics.length === 0) {
            diagMsg += '✅ جميع مصادر المواقف مكتملة ومتطابقة رياضياً بدون تعارضات.';
        } else {
            diagMsg += `⚠️ وُجد ${diagnostics.length} ملاحظة:\n\n`;
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
    } else {
        await ctx.reply(`❌ فشل إنتاج تقرير ${label}: ${res.error || 'خطأ غير معروف'}`);
    }
    return true;
}
