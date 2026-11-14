// Owner-only parking report at 10:00 / 12:00 / 14:00 (owner, 04-11). Sent to the owner on Telegram as
// soon as every part has arrived (from 30 minutes before the slot), or at the slot time with the parts
// still missing; then, for up to an hour, again each time a late part arrives (teams sometimes post after
// the slot). Nothing goes to WhatsApp until the owner presses "Send to Control Room", which always posts
// the newest version.
// Independent of ENABLE_AUTOMATED_REPORTS (which governs team-facing reports).
import { Bot, InlineKeyboard } from 'grammy';
import { config } from '../config.js';
import { runReportEngine, type ReportResult } from './bridge.js';
import { getSchedulerDb, hasReportBeenSent, markReportAsSent } from './scheduler.js';
import { alertOwner } from '../monitoring/alerts.js';
import { sendTextToGroup } from '../whatsapp/client.js';

export const PARKING_CB_PREFIX = 'pk';
const SLOTS = ['10:00', '12:00', '14:00'];
const EARLY_MINUTES = 30;       // start looking this long before the slot
const LATE_MINUTES = 15;        // still send after a restart this long after the slot
const FOLLOW_UP_MINUTES = 60;   // after an incomplete report, watch this long for the late parts
const POLL_EVERY_MS = 5 * 60 * 1000;

export function controlRoomGroup(): string {
    return process.env.CONTROL_ROOM_GROUP || 'Control Room Specialization';
}

// The 07:00 "Vehicles allowed today" list works the same way (owner, 14-11): it reaches the owner on
// Telegram and goes to the Parking Team WhatsApp group only when the owner presses Send.
export const VEHICLES_CB_PREFIX = 'vh';

export function parkingTeamGroup(): string {
    return process.env.PARKING_TEAM_GROUP || 'Parking Team at Meydan';
}

/** Today in Dubai as DD-MM-YYYY. */
export function todayDubai(): string {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: config.timeZone, day: '2-digit', month: '2-digit',
        year: 'numeric' }).formatToParts(new Date()).map((x) => [x.type, x.value]));
    return `${p.day}-${p.month}-${p.year}`;
}

/** "vehicles_14-11-2026" (the day's draft and callback key). */
export function vehiclesKey(date: string): string {
    return `vehicles_${date}`;
}

export function vehiclesKeyboard(key: string): InlineKeyboard {
    return new InlineKeyboard()
        .text('📤 Send to Parking Team', `${VEHICLES_CB_PREFIX}:s:${key}`)
        .text('🔄 Refresh', `${VEHICLES_CB_PREFIX}:r:${key}`);
}

/** Saves the day's list and sends it to the owner with the Send / Refresh buttons. */
export async function sendVehiclesDraft(bot: Bot, date: string, text: string): Promise<void> {
    const key = vehiclesKey(date);
    saveDraft(key, text);
    for (const userId of config.allowedUserIds) {
        try {
            await sendOrEdit((o) => bot.api.sendMessage(userId, text, o), { reply_markup: vehiclesKeyboard(key) });
        } catch (err: any) {
            console.error(`Failed to send the vehicles list to ${userId}:`, err.message);
        }
    }
}

export interface ParkingDraft {
    draft_key: string;
    text: string;
    sent_at: number | null;
    sent_by: string | null;
    sent_group: string | null;
}

function draftsDb(customDb?: any): any {
    const db = customDb || getSchedulerDb();
    db.exec(`CREATE TABLE IF NOT EXISTS parking_drafts (
        draft_key TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        sent_at INTEGER,
        sent_by TEXT,
        sent_group TEXT
    )`);
    return db;
}

/** "04-11-2026_1000" (the slot's draft and callback key). */
export function draftKey(date: string, slot: string): string {
    return `${date}_${slot.replace(':', '')}`;
}

function parseDraftKey(key: string): { date: string; slot: string } | null {
    const m = key.match(/^(\d{2}-\d{2}-\d{4})_(\d{2})(\d{2})$/);
    return m ? { date: m[1], slot: `${m[2]}:${m[3]}` } : null;
}

/** Saves the text the owner sees; a draft already sent is never changed. */
export function saveDraft(key: string, text: string, customDb?: any): void {
    draftsDb(customDb).prepare(`INSERT INTO parking_drafts (draft_key, text, created_at) VALUES (?, ?, ?)
        ON CONFLICT(draft_key) DO UPDATE SET text = excluded.text WHERE sent_at IS NULL`).run(key, text, Date.now());
}

export function getDraft(key: string, customDb?: any): ParkingDraft | null {
    return draftsDb(customDb).prepare('SELECT * FROM parking_drafts WHERE draft_key = ?').get(key) || null;
}

/** True for the one press that may send (a second press, or a double tap, gets false). */
export function claimDraft(key: string, by: string, customDb?: any): boolean {
    const r = draftsDb(customDb).prepare('UPDATE parking_drafts SET sent_at = ?, sent_by = ? WHERE draft_key = ? AND sent_at IS NULL')
        .run(Date.now(), by, key);
    return r.changes === 1;
}

export function finishDraft(key: string, group: string | null, customDb?: any): void {
    const db = draftsDb(customDb);
    if (group) db.prepare('UPDATE parking_drafts SET sent_group = ? WHERE draft_key = ?').run(group, key);
    else db.prepare('UPDATE parking_drafts SET sent_at = NULL, sent_by = NULL WHERE draft_key = ?').run(key);   // send failed
}

/**
 * What to do for one slot now: 'poll' (look for the parts, send if all are in), 'deadline' (send whatever
 * there is) or 'wait'. sentIncomplete: the slot's report already went out with parts missing.
 * Minutes are minutes of the Dubai day.
 */
export function slotAction(nowMinute: number, slotMinute: number, lastPollMs: number, nowMs: number,
    sentIncomplete = false): 'poll' | 'deadline' | 'wait' {
    const before = slotMinute - nowMinute;
    const due = nowMs - lastPollMs >= POLL_EVERY_MS;
    if (before > EARLY_MINUTES) return 'wait';
    if (sentIncomplete) return before >= -FOLLOW_UP_MINUTES && due ? 'poll' : 'wait';
    if (before < -LATE_MINUTES) return 'wait';
    if (before <= 0) return 'deadline';
    return due ? 'poll' : 'wait';
}

/** Fewer N/A lines than the version the owner has: a late part arrived (11-11: podium came at 14:08). */
export function hasNewFigures(newText: string, oldText: string): boolean {
    const na = (t: string) => (t.match(/N\/A/g) || []).length;
    return na(newText) < na(oldText);
}

export function draftKeyboard(key: string): InlineKeyboard {
    return new InlineKeyboard()
        .text('📤 Send to Control Room', `${PARKING_CB_PREFIX}:s:${key}`)
        .text('🔄 Refresh', `${PARKING_CB_PREFIX}:r:${key}`);
}

/** Telegram text: the report as it will be posted, plus what has not arrived (that note is not posted). */
export function draftMessage(res: ReportResult): string {
    const missing = res.missing || [];
    return missing.length ? `${res.text}\n\n⚠️ Not received yet: ${missing.join(', ')}` : String(res.text);
}

async function sendOrEdit(send: (opts: any) => Promise<any>, opts: any): Promise<void> {
    try {
        await send({ ...opts, parse_mode: 'Markdown' });
    } catch (_) {
        await send(opts);
    }
}

export async function sendParkingDraft(bot: Bot, date: string, slot: string, res: ReportResult, update = false): Promise<void> {
    const key = draftKey(date, slot);
    saveDraft(key, String(res.text));
    const header = !update ? '' : res.missing?.length ? '🔄 New figures arrived — updated report\n\n' : '✅ All parts received — updated report\n\n';
    const text = header + draftMessage(res);
    for (const userId of config.allowedUserIds) {
        try {
            await sendOrEdit((o) => bot.api.sendMessage(userId, text, o), { reply_markup: draftKeyboard(key) });
        } catch (err: any) {
            console.error(`Failed to send the parking draft to ${userId}:`, err.message);
        }
    }
    console.log(`🅿️ Parking ${slot} (${date}) sent to the owner${res.missing?.length ? `, missing: ${res.missing.join(', ')}` : ''}.`);
}

function timeLabel(ms: number): string {
    return new Date(ms).toLocaleTimeString('en-GB', { timeZone: config.timeZone, hour: '2-digit', minute: '2-digit' });
}

async function answer(ctx: any, text: string, alert = false): Promise<void> {
    try { await ctx.answerCallbackQuery({ text, show_alert: alert }); } catch (_) {}
}

export async function handleParkingDraftCallback(
    ctx: any, data: string, sendToGroup: (group: string, text: string) => Promise<string> = sendTextToGroup,
    customDb?: any,
): Promise<boolean> {
    const [prefix, action, key] = data.split(':');
    if ((prefix !== PARKING_CB_PREFIX && prefix !== VEHICLES_CB_PREFIX) || !key) return false;
    const vehicles = prefix === VEHICLES_CB_PREFIX;
    const target = vehicles ? { group: parkingTeamGroup(), label: 'Parking Team' } : { group: controlRoomGroup(), label: 'Control Room' };
    const draft = getDraft(key, customDb);
    if (!draft) {
        await answer(ctx, '❌ This report is no longer available. Request it again.', true);
        return true;
    }
    if (draft.sent_at) {
        await answer(ctx, `Already sent to ${draft.sent_group || 'the group'} at ${timeLabel(draft.sent_at)}.`, true);
        return true;
    }

    if (action === 's') {
        const by = ctx.from?.username || ctx.from?.first_name || String(ctx.from?.id || 'telegram');
        if (!claimDraft(key, by, customDb)) {
            await answer(ctx, 'Already being sent.');
            return true;
        }
        try {
            const group = await sendToGroup(target.group, draft.text);
            finishDraft(key, group, customDb);
            await answer(ctx, `✔ Sent to ${group}`);
            try {
                await ctx.editMessageReplyMarkup({ reply_markup: new InlineKeyboard()
                    .text(`✅ Sent to ${target.label} ${timeLabel(Date.now())}`, `${prefix}:d:${key}`) });
            } catch (_) {}
        } catch (err: any) {
            finishDraft(key, null, customDb);
            await answer(ctx, `❌ Not sent: ${err.message}`, true);
        }
        return true;
    }

    if (action === 'r' && vehicles) {
        const res = await runReportEngine('vehicles_today', { date: key.replace('vehicles_', '') });
        if (!res.success || !res.text) {
            await answer(ctx, `❌ Could not refresh: ${res.error || 'no text'}`, true);
            return true;
        }
        saveDraft(key, res.text, customDb);
        if (res.text === draft.text) {
            await answer(ctx, 'No change.');
            return true;
        }
        try {
            await sendOrEdit((o) => ctx.editMessageText(res.text, o), { reply_markup: vehiclesKeyboard(key) });
        } catch (_) {}
        await answer(ctx, '🔄 Updated with the latest requests.');
        return true;
    }

    if (action === 'r') {
        const when = parseDraftKey(key);
        if (!when) return false;
        const res = await runReportEngine('parking', { slot: when.slot, date: when.date });
        if (!res.success || !res.text) {
            await answer(ctx, `❌ Could not refresh: ${res.error || 'no text'}`, true);
            return true;
        }
        saveDraft(key, res.text, customDb);
        if (res.text === draft.text) {
            await answer(ctx, res.missing?.length ? `No new figures yet (missing: ${res.missing.join(', ')})` : 'No change.');
            return true;
        }
        try {
            await sendOrEdit((o) => ctx.editMessageText(draftMessage(res), o), { reply_markup: draftKeyboard(key) });
        } catch (_) {}
        await answer(ctx, '🔄 Updated with the latest figures.');
        return true;
    }

    await answer(ctx, 'Already sent.');
    return true;
}

export function startOwnerParkingReports(bot: Bot): void {
    if (process.env.ENABLE_OWNER_PARKING === 'false') return;
    const slots = SLOTS.map((s) => ({ slot: s, minute: Number(s.slice(0, 2)) * 60 + Number(s.slice(3)) }));
    const lastPoll = new Map<string, number>();
    let busy = false;

    setInterval(async () => {
        if (busy) return;
        busy = true;
        try {
            const parts = new Intl.DateTimeFormat('en-US', {
                timeZone: config.timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
            }).formatToParts(new Date());
            const get = (t: string) => parts.find((p) => p.type === t)?.value || '';
            const date = `${get('day')}-${get('month')}-${get('year')}`;
            const nowMinute = Number(get('hour')) * 60 + Number(get('minute'));

            for (const { slot, minute } of slots) {
                const sentKey = `owner_parking_${date}_${slot}`;
                const completeKey = `${sentKey}_complete`;
                if (hasReportBeenSent(completeKey)) continue;
                const sentIncomplete = hasReportBeenSent(sentKey);
                const action = slotAction(nowMinute, minute, lastPoll.get(sentKey) || 0, Date.now(), sentIncomplete);
                if (action === 'wait') continue;
                const draft = sentIncomplete ? getDraft(draftKey(date, slot)) : null;
                // The owner already posted the incomplete one: no update
                if (draft?.sent_at) {
                    markReportAsSent(completeKey);
                    continue;
                }
                lastPoll.set(sentKey, Date.now());

                const res = await runReportEngine('parking', { slot, date });
                const complete = res.success && !!res.text && Array.isArray(res.missing) && res.missing.length === 0;
                const newFigures = !!draft && !!res.text && hasNewFigures(res.text, draft.text);
                if (action === 'poll' && !complete && !newFigures) continue;
                markReportAsSent(sentKey);
                if (complete) markReportAsSent(completeKey);
                if (res.success && res.text) await sendParkingDraft(bot, date, slot, res, sentIncomplete);
                else await alertOwner('report-owner-parking', `Parking report ${slot} (${date}) failed: ${res.error || 'no text'}`);
            }
        } catch (err: any) {
            console.error('Error in owner parking reports:', err.message);
            await alertOwner('owner-parking', `Parking report scheduler error: ${err.message}`);
        } finally {
            busy = false;
        }
    }, 60 * 1000);

    console.log(`🅿️ Owner parking report at ${SLOTS.join(', ')} (${config.timeZone}): early when complete, `
        + `posted to "${controlRoomGroup()}" only after the owner presses Send.`);
}
