import { createReminder } from '../reminders.js';

export const setReminderTool = {
    name: 'set_reminder',
    description:
        'Schedule a notification or reminder for the user at a specified time or after a certain delay in minutes. ' +
        'The bot will proactively send a message (and voice note if requested) to the user when the time arrives.',
    parameters: {
        type: 'object',
        properties: {
            title: {
                type: 'string',
                description: 'The reminder text, topic, or task to alert the user about.',
            },
            delayMinutes: {
                type: 'number',
                description: 'Relative delay in minutes from now (e.g. 15, 30, 60, 120). Use this for "after X minutes/hours".',
            },
            targetTime: {
                type: 'string',
                description: 'Absolute ISO timestamp (YYYY-MM-DDTHH:mm:ss) or time string for the reminder.',
            },
            withVoice: {
                type: 'boolean',
                description: 'Whether to also send an Arabic voice alert when the reminder triggers (default false).',
            },
        },
        required: ['title'],
    },
    execute: async (
        args: { title: string; delayMinutes?: number; targetTime?: string; withVoice?: boolean },
        context?: { userId?: number }
    ) => {
        const userId = context?.userId || 0;
        if (!userId) {
            return {
                success: false,
                message: '❌ لا يمكن جدولة التذكير بدون معرف مستخدم صالح.',
            };
        }

        const title = args.title.trim();
        const result = createReminder(userId, title, args.delayMinutes, args.targetTime, args.withVoice ?? false);

        const timeStr = result.remindAt.toLocaleTimeString('ar-EG', {
            hour: '2-digit',
            minute: '2-digit',
        });
        const dateStr = result.remindAt.toLocaleDateString('ar-EG', {
            day: 'numeric',
            month: 'long',
        });

        return {
            success: true,
            id: result.id,
            title: result.title,
            remindAt: result.remindAt.toISOString(),
            message: `⏰ تم ضبط التذكير بنجاح!\n\n🔔 *الموضوع:* ${title}\n📅 *الموعد:* ${dateStr} في تمام ${timeStr}${
                args.withVoice ? ' (مع رسالة صوتية)' : ''
            }`,
        };
    },
};
