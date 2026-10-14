import { Bot, InputFile } from 'grammy';
import { addReminderDb, getDueRemindersDb, markReminderCompletedDb, getUserRemindersDb } from './db/index.js';
import { textToSpeech } from './tts.js';
import fs from 'fs';

let schedulerInterval: NodeJS.Timeout | null = null;

export function startReminderScheduler(bot: Bot) {
    if (schedulerInterval) return;

    console.log('⏰ Reminder scheduler started (checking every 15s)...');

    schedulerInterval = setInterval(async () => {
        try {
            const due = getDueRemindersDb();
            for (const reminder of due) {
                markReminderCompletedDb(reminder.id);

                const alertText = `⏰ *تنبيه من amirClaw!*\n\n🔔 *الموضوع:* ${reminder.title}`;

                await bot.api.sendMessage(reminder.user_id, alertText, {
                    parse_mode: 'Markdown',
                });

                // Send voice reminder if requested
                if (reminder.reply_voice) {
                    try {
                        const voiceFile = await textToSpeech(`تذكير: ${reminder.title}`, reminder.user_id);
                        if (voiceFile) {
                            await bot.api.sendVoice(reminder.user_id, new InputFile(voiceFile));
                            fs.promises.unlink(voiceFile).catch(() => {});
                        }
                    } catch (vErr) {
                        console.error('Failed to send voice reminder:', vErr);
                    }
                }
            }
        } catch (err: any) {
            console.error('Error in reminder scheduler tick:', err.message);
        }
    }, 15000);
}

export function createReminder(userId: number, title: string, delayMinutes?: number, targetIsoDate?: string, replyVoice = false) {
    let remindAtMs: number;

    if (delayMinutes && delayMinutes > 0) {
        remindAtMs = Date.now() + delayMinutes * 60 * 1000;
    } else if (targetIsoDate) {
        const parsed = new Date(targetIsoDate).getTime();
        remindAtMs = isNaN(parsed) ? Date.now() + 60 * 60 * 1000 : parsed;
    } else {
        remindAtMs = Date.now() + 30 * 60 * 1000; // default 30 mins
    }

    const id = addReminderDb(userId, title, remindAtMs, replyVoice);
    return {
        id,
        title,
        remindAt: new Date(remindAtMs),
        remindAtMs,
    };
}

export function listUserReminders(userId: number) {
    return getUserRemindersDb(userId);
}
