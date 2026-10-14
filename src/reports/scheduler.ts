import { Bot, InputFile } from 'grammy';
import fs from 'fs';
import path from 'path';
import Database from 'better-sqlite3';
import { config } from '../config.js';
import { runReportEngine } from './bridge.js';

let _schedulerDb: any = null;

export function getSchedulerDb(customDbPath?: string): any {
    if (!_schedulerDb || customDbPath) {
        const dbPath = customDbPath || process.env.REPORTS_DB_PATH || path.join(process.cwd(), 'workspace', 'reports.db');
        const db = new Database(dbPath);
        db.exec(`
            CREATE TABLE IF NOT EXISTS sent_reports_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_key TEXT UNIQUE NOT NULL,
                sent_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS parking_poll_tracker (
                slot_key TEXT PRIMARY KEY,
                started_at INTEGER NOT NULL,
                attempts INTEGER DEFAULT 1
            );
        `);
        if (!customDbPath) {
            _schedulerDb = db;
        }
        return db;
    }
    return _schedulerDb;
}

export function hasReportBeenSent(reportKey: string, customDb?: any): boolean {
    const db = customDb || getSchedulerDb();
    const row = db.prepare('SELECT id FROM sent_reports_log WHERE report_key = ?').get(reportKey);
    return !!row;
}

export function markReportAsSent(reportKey: string, customDb?: any): void {
    const db = customDb || getSchedulerDb();
    try {
        db.prepare('INSERT OR IGNORE INTO sent_reports_log (report_key) VALUES (?)').run(reportKey);
    } catch (e: any) {
        console.error('Error marking report as sent:', e.message);
    }
}

export function getOrCreatePollTracker(slotKey: string, customDb?: any, simulatedNow?: number): { startedAt: number; attempts: number } {
    const db = customDb || getSchedulerDb();
    const existing = db.prepare('SELECT started_at, attempts FROM parking_poll_tracker WHERE slot_key = ?').get(slotKey);
    if (existing) {
        db.prepare('UPDATE parking_poll_tracker SET attempts = attempts + 1 WHERE slot_key = ?').run(slotKey);
        return { startedAt: existing.started_at, attempts: existing.attempts + 1 };
    }
    const now = simulatedNow !== undefined ? simulatedNow : Date.now();
    db.prepare('INSERT INTO parking_poll_tracker (slot_key, started_at, attempts) VALUES (?, ?, 1)').run(slotKey, now);
    return { startedAt: now, attempts: 1 };
}

export function clearPollTracker(slotKey: string, customDb?: any): void {
    const db = customDb || getSchedulerDb();
    try {
        db.prepare('DELETE FROM parking_poll_tracker WHERE slot_key = ?').run(slotKey);
    } catch (e: any) {
        console.error('Error clearing poll tracker:', e.message);
    }
}

export function startMeydanScheduler(bot: Bot) {
    console.log('⏰ Starting Meydan Reports Automation Scheduler (Asia/Dubai timezone)...');

    // Parse slot strings once at startup: [slotH, slotM, slotStr][]
    const parsedSlots: [number, number, string][] = ['10:00', '12:00', '14:00'].map((s) => {
        const [h, m] = s.split(':').map(Number);
        return [h, m, s];
    });

    // Polling tracker for parking report email wait
    const parkingPollStatus: Record<string, { startedAt: number; attempts: number }> = {};

    setInterval(async () => {
        try {
            if (!config.enableAutomatedReports) {
                // Automated sending disabled until confirmed by user
                return;
            }

            const now = new Date();
            // Format current time in Asia/Dubai
            const dubaiParts = new Intl.DateTimeFormat('en-US', {
                timeZone: config.timeZone,
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false,
            }).formatToParts(now);

            const getPart = (type: string) => dubaiParts.find((p) => p.type === type)?.value || '00';
            const year = getPart('year');
            const month = getPart('month');
            const day = getPart('day');
            const hour = parseInt(getPart('hour'), 10);
            const minute = parseInt(getPart('minute'), 10);

            const dateStr = `${day}-${month}-${year}`;
            const timeSlotStr = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;

            // 1. Shift Reports (Maintenance, Fire & Safety, Operations) at 05:00 and 17:00
            // Use ±2 minute window to absorb setInterval jitter; hasReportBeenSent prevents duplicates
            if ((hour === 5 && minute <= 2) || (hour === 17 && minute <= 2)) {
                const shiftName = hour === 17 ? 'Day' : 'Night';
                const shiftKey = `shift_${shiftName}_${dateStr}_${hour}`;

                if (!hasReportBeenSent(shiftKey)) {
                    console.log(`🚀 Triggering automated ${shiftName} Shift reports for ${dateStr} at ${timeSlotStr}...`);

                    const reportsToRun = ['maintenance', 'fire_safety', 'operations'] as const;
                    for (const rType of reportsToRun) {
                        try {
                            const res = await runReportEngine(rType);
                            if (res.success && res.text) {
                                for (const userId of config.reportChatIds) {
                                    await sendTelegramReport(bot, userId, res.text, res.excelPath);
                                }
                            }
                        } catch (rErr: any) {
                            console.error(`Error generating scheduled ${rType} report:`, rErr.message);
                        }
                    }
                    // Mark sent only after all reports attempted
                    markReportAsSent(shiftKey);
                }
            }

            // 2. Parking Report Slots: 10:00, 12:00, 14:00
            for (const [slotH, slotM, slot] of parsedSlots) {
                const slotKey = `parking_${dateStr}_${slot}`;

                if (hasReportBeenSent(slotKey)) {
                    continue;
                }

                // Check if current time is within slot window (slot to slot + 5 mins)
                const isSlotWindow = hour === slotH && minute >= slotM && minute <= slotM + 5;
                if (!isSlotWindow) {
                    continue;
                }

                // Get or initialize persistent poll tracker for this slot
                const tracker = getOrCreatePollTracker(slotKey);
                const elapsedMinutes = (Date.now() - tracker.startedAt) / 60000;

                console.log(`🔍 Polling parking report for slot ${slot} (${dateStr}), attempt ${tracker.attempts}...`);
                const res = await runReportEngine('parking', { slot, date: dateStr });

                // Check if email was found or if 5 minutes timeout elapsed
                const emailMissing = (res.diagnostics || []).some((d) => d.includes('email missing or unavailable'));
                const shouldSend = !emailMissing || elapsedMinutes >= 5;

                if (shouldSend) {
                    markReportAsSent(slotKey);
                    clearPollTracker(slotKey);
                    console.log(`📤 Sending scheduled Parking Report for slot ${slot} (${dateStr})...`);

                    if (res.success && res.text) {
                        for (const userId of config.reportChatIds) {
                            await sendTelegramReport(bot, userId, res.text, res.excelPath);
                        }
                    }
                }
            }
        } catch (err: any) {
            console.error('Error in Meydan scheduler tick:', err.message);
        }
    }, 60000); // Check every minute

    startWeeklyDataQualityReport(bot);
}

// Owner-only health report; separate from ENABLE_AUTOMATED_REPORTS, which governs operational reports.
function startWeeklyDataQualityReport(bot: Bot) {
    if (process.env.ENABLE_WEEKLY_QUALITY_REPORT === 'false') return;
    const sendDay = process.env.QUALITY_REPORT_WEEKDAY || 'Mon';
    const sendHour = Number(process.env.QUALITY_REPORT_HOUR ?? 9);

    setInterval(async () => {
        try {
            const parts = new Intl.DateTimeFormat('en-US', {
                timeZone: config.timeZone, weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23',
            }).formatToParts(new Date());
            const get = (t: string) => parts.find((p) => p.type === t)?.value || '';
            if (get('weekday') !== sendDay || Number(get('hour')) !== sendHour) return;

            const key = `data_quality_${get('day')}-${get('month')}-${get('year')}`;
            if (hasReportBeenSent(key)) return;
            markReportAsSent(key);

            const res = await runReportEngine('data_quality');
            if (!res.success || !res.text) {
                console.error('Weekly data quality report failed:', res.error);
                return;
            }
            for (const userId of config.allowedUserIds) {
                await sendTelegramReport(bot, userId, res.text);
            }
            console.log(`🩺 Weekly data quality report sent to ${config.allowedUserIds.length} owner(s).`);
        } catch (err: any) {
            console.error('Error in weekly data quality report:', err.message);
        }
    }, 5 * 60 * 1000);

    console.log(`🩺 Weekly data quality report scheduled (${sendDay} ${String(sendHour).padStart(2, '0')}:00 ${config.timeZone}, owner only).`);
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

export async function sendTelegramReport(bot: Bot, userId: number, text: string, excelPath?: string) {
    try {
        // Send report text in chunks if needed — Telegram limit is 4096 chars per message
        const parts = splitLongMessage(text);
        for (const part of parts) {
            try {
                await bot.api.sendMessage(userId, part, { parse_mode: 'Markdown' });
            } catch (_) {
                await bot.api.sendMessage(userId, part);
            }
        }

        // Send Excel file if generated and exists
        if (excelPath && fs.existsSync(excelPath)) {
            const fileName = path.basename(excelPath);
            await bot.api.sendDocument(userId, new InputFile(excelPath, fileName), {
                caption: `📊 Report Excel Workbook: ${fileName}`,
            });
        }
    } catch (e: any) {
        console.error(`Failed to send Telegram report to user ${userId}:`, e.message);
    }
}
