import { Bot } from 'grammy';
import { config } from '../config.js';

// Owner alerts on Telegram for failures that used to show up only in pm2 logs.
// Sent to ALLOWED_USER_IDS (the owners), never to REPORT_CHAT_IDS.

const ALERT_COOLDOWN_MS = Number(process.env.ALERT_COOLDOWN_MINUTES ?? 60) * 60_000;
const WA_DOWN_ALERT_MS = Number(process.env.WA_DOWN_ALERT_MINUTES ?? 10) * 60_000;
const SILENCE_ALERT_MS = Number(process.env.SILENCE_ALERT_HOURS ?? 3) * 3_600_000;
// Silence is only meaningful while people are on duty and posting (Dubai hours)
const SILENCE_WATCH_FROM_HOUR = Number(process.env.SILENCE_WATCH_FROM_HOUR ?? 8);
const SILENCE_WATCH_TO_HOUR = Number(process.env.SILENCE_WATCH_TO_HOUR ?? 22);

let alertBot: Bot | null = null;
const lastSent = new Map<string, number>();

// Latest real message time seen per group (message timestamp, so history sync of old
// messages never counts as fresh activity)
const lastGroupActivity = new Map<string, number>();
let watchStartedAt = Date.now();

export function initAlerts(bot: Bot): void {
    alertBot = bot;
    watchStartedAt = Date.now();
}

/** Send an owner alert, at most once per cooldown for the same key. Never throws. */
export async function alertOwner(key: string, text: string, opts: { force?: boolean } = {}): Promise<void> {
    const now = Date.now();
    if (!opts.force && now - (lastSent.get(key) ?? 0) < ALERT_COOLDOWN_MS) return;
    lastSent.set(key, now);
    console.error(`🚨 [alert:${key}] ${text.replace(/\n/g, ' | ')}`);
    if (!alertBot) return;
    for (const userId of config.allowedUserIds) {
        try {
            await alertBot.api.sendMessage(userId, `🚨 ${text}`);
        } catch (err: any) {
            console.error(`Could not send alert to ${userId}:`, err.message);
        }
    }
}

export function clearAlert(key: string): void {
    lastSent.delete(key);
}

export function recordGroupActivity(groupName: string, messageTimeMs: number): void {
    if (messageTimeMs > (lastGroupActivity.get(groupName) ?? 0)) {
        lastGroupActivity.set(groupName, messageTimeMs);
    }
}

function dubaiHour(d = new Date()): number {
    return Number(new Intl.DateTimeFormat('en-US', { timeZone: config.timeZone, hour: '2-digit', hourCycle: 'h23' }).format(d));
}

/** Pure check used by the watchdog (exported for tests). */
export function shouldAlertSilence(lastActivityMs: number, nowMs: number, startedMs: number, hour: number): boolean {
    if (hour < SILENCE_WATCH_FROM_HOUR || hour >= SILENCE_WATCH_TO_HOUR) return false;
    const since = Math.max(lastActivityMs, startedMs); // give a fresh restart time to catch up
    return nowMs - since > SILENCE_ALERT_MS;
}

/**
 * Watches WhatsApp connection and message flow once a minute.
 * - disconnected/connecting for > WA_DOWN_ALERT_MINUTES -> alert; "reconnected" when back
 * - logged out (needs QR) -> alert immediately
 * - no new group message at all for > SILENCE_ALERT_HOURS during working hours -> alert
 */
export function startWhatsAppWatchdog(
    getStatus: () => { status: string; statusSince?: number; loggedOut?: boolean },
): void {
    let downAlerted = false;
    setInterval(async () => {
        try {
            const st = getStatus();
            const now = Date.now();
            if (st.loggedOut) {
                await alertOwner('wa-logged-out', 'WhatsApp is logged out. Send /whatsapp to the bot and scan the new QR code, otherwise no messages are captured.');
            } else if (st.status !== 'connected' && st.statusSince && now - st.statusSince > WA_DOWN_ALERT_MS) {
                const mins = Math.round((now - st.statusSince) / 60_000);
                await alertOwner('wa-down', `WhatsApp has been disconnected for ${mins} min (status: ${st.status}). Messages are not being captured, so reports will be incomplete.`);
                downAlerted = true;
            } else if (st.status === 'connected' && downAlerted) {
                downAlerted = false;
                clearAlert('wa-down');
                await alertOwner('wa-up', '✅ WhatsApp is connected again.', { force: true });
            }

            if (st.status === 'connected') {
                const latest = Math.max(0, ...lastGroupActivity.values());
                if (shouldAlertSilence(latest, now, watchStartedAt, dubaiHour())) {
                    const hrs = Math.round((now - Math.max(latest, watchStartedAt)) / 3_600_000);
                    await alertOwner('wa-silence', `No WhatsApp group message for about ${hrs} h although the connection looks fine. Check the account is still in the groups.`);
                }
            }
        } catch (err: any) {
            console.error('WhatsApp watchdog error:', err.message);
        }
    }, 60_000);
}
