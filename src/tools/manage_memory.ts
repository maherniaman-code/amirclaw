import { setMemoryDb, deleteMemoryDb, getMemoriesDb } from '../db/index.js';

export const manageMemoryTool = {
    name: 'manage_memory',
    description:
        'Save, retrieve, or delete long-term facts and memories about the user (e.g. their name, major, goals, current projects, exam dates, or personal preferences). ' +
        'These memories persist forever and are remembered across all future conversations.',
    parameters: {
        type: 'object',
        properties: {
            action: {
                type: 'string',
                enum: ['remember', 'forget', 'list'],
                description: 'The memory operation: "remember" to save a fact, "forget" to remove one, or "list" to view all stored facts.',
            },
            key: {
                type: 'string',
                description: 'Short descriptor of the memory (e.g. "user_name", "study_major", "learning_goal", "project_deadline").',
            },
            value: {
                type: 'string',
                description: 'The content or detail to remember (required when action is "remember").',
            },
        },
        required: ['action'],
    },
    execute: async (
        args: { action: 'remember' | 'forget' | 'list'; key?: string; value?: string },
        context?: { userId?: number }
    ) => {
        const userId = context?.userId || 0;
        if (!userId) {
            return {
                success: false,
                message: '❌ لا يمكن إدارة الذاكرة بدون معرف مستخدم صالح.',
            };
        }

        const { action, key, value } = args;

        if (action === 'remember') {
            if (!key || !value) {
                return { success: false, message: 'يجب تحديد key و value لحفظ المعلومة.' };
            }
            setMemoryDb(userId, key.trim(), value.trim());
            console.log(`🧠 Memorized for user ${userId}: [${key}] = "${value}"`);
            return {
                success: true,
                message: `🧠 تم حفظ هذه المعلومة في ذاكرتي الدائمة بنجاح:\n• **${key}:** ${value}`,
            };
        }

        if (action === 'forget') {
            if (!key) {
                return { success: false, message: 'يجب تحديد المفتاح key المراد حذفه.' };
            }
            deleteMemoryDb(userId, key.trim());
            return {
                success: true,
                message: `🗑️ تم مسح المعلومة [${key}] من ذاكرتي الدائمة.`,
            };
        }

        // action === 'list'
        const memories = getMemoriesDb(userId);
        if (memories.length === 0) {
            return {
                success: true,
                count: 0,
                message: '🧠 لا توجد معلومات شخصية محفوظة في ذاكرتك الدائمة بعد.',
            };
        }

        return {
            success: true,
            count: memories.length,
            memories,
            message: `🧠 إليك ما أتذكره عنك في الذاكرة الدائمة (${memories.length} معلومة):\n\n` +
                memories.map((m) => `• **${m.key}:** ${m.value}`).join('\n'),
        };
    },
};
