import { Bot, InputFile } from 'grammy';
import Groq, { toFile } from 'groq-sdk';
import fs from 'fs';
import path from 'path';
import { config, validateConfig } from './config.js';
import { initDb, addMessage, getMessages, getMemoriesDb, clearMemoriesDb } from './db/index.js';
import { availableTools, executeTool } from './tools/index.js';
import { createChatCompletion, getActiveProviders } from './llm.js';
import { startWhatsAppClient, getWhatsAppStatus, getWhatsAppQrImage } from './whatsapp/client.js';
import { ensureExcelInitialized } from './whatsapp/excel.js';
import { textToSpeech, getUserVoiceSettings, setUserAlwaysVoice, setUserVoiceKey, ARABIC_VOICES } from './tts.js';
import { startReminderScheduler, listUserReminders } from './reminders.js';
import { startMeydanScheduler } from './reports/scheduler.js';
import { handleReportAction, handleReportCallback, handleCorrectionCallback, sendWhatsAppExcel } from './reports/telegram_handlers.js';
import { startCloudBackupScheduler } from './cloud/backup.js';
import { initAlerts, startWhatsAppWatchdog } from './monitoring/alerts.js';

// Validate configuration and initialize database
validateConfig();
initDb();
ensureExcelInitialized().catch(console.error);

// Auto-start WhatsApp if session exists
const whatsappCredsPath = path.join(process.cwd(), 'workspace', 'whatsapp_session', 'creds.json');
if (fs.existsSync(whatsappCredsPath)) {
    console.log('🔄 Existing WhatsApp session found, connecting client automatically...');
    startWhatsAppClient().catch(console.error);
}

const WORKSPACE_DIR = path.resolve(process.cwd(), 'workspace');
const DOWNLOADS_DIR = path.join(WORKSPACE_DIR, 'downloads');

// Ensure workspace directories exist
if (!fs.existsSync(DOWNLOADS_DIR)) {
    fs.mkdirSync(DOWNLOADS_DIR, { recursive: true });
}

const bot = new Bot(config.telegramBotToken);
// Owner alerts (report failures, WhatsApp down/logged out, no messages, Excel save errors)
initAlerts(bot);
startWhatsAppWatchdog(getWhatsAppStatus);
const groq = new Groq({ apiKey: config.groqApiKey || 'placeholder' });

// Start the proactive reminder scheduler
startReminderScheduler(bot);
// Start Meydan Reports Automation Scheduler (Dubai shifts & parking slots)
startMeydanScheduler(bot);
// Daily Firestore backup of session, databases and media
startCloudBackupScheduler();

// Map tools to standard OpenAI function calling format
const llmTools = availableTools.map((t) => ({
    type: 'function' as const,
    function: {
        name: t.name,
        description: t.description,
        parameters: t.parameters,
    },
}));

// Authorization middleware: only allow configured user IDs
bot.use(async (ctx, next) => {
    const userId = ctx.from?.id;
    if (!userId || !config.allowedUserIds.includes(userId)) {
        console.warn(`Unauthorized access attempt from user ID: ${userId} (@${ctx.from?.username || 'unknown'})`);
        if (ctx.chat?.type === 'private') {
            await ctx.reply('⚠️ Unauthorized: Your Telegram ID is not allowed to use this bot.');
        }
        return;
    }
    await next();
});

// /start command
bot.command('start', async (ctx) => {
    await ctx.reply(
        '👋 مرحباً! أنا amirClaw، مساعدك الشخصي الذكي الخارق.\n\n' +
        '🚀 *أبرز قدراتي:*\n' +
        '• 🔍 **البحث الحي في الويب:** البحث المباشر في الإنترنت وجلب أحدث المعلومات والمصادر.\n' +
        '• 🌐 **قراءة وسكراب الروابط والمقالات:** استخراج وتلخيص محتوى أي صفحة أو رابط ترسلها.\n' +
        '• 🎓 **مساعد المذاكرة والتعلم:** تبسيط المفاهيم الصعبة (طريقة فاينمان)، اختبارات تفاعلية، وتلخيص المحاضرات والـ PDF.\n' +
        '• 🐍 **تنفيذ أكواد بايثون كاملة:** كتابة وتشغيل برامج وسكربتات بايثون لحل العمليات المعقدة والبيانات والإكسل.\n' +
        '• 📁 **قراءة المستندات والملفات:** إرسال ملفات PDF، Excel، Word، نصوص، وصور لتحليلها مباشرة.\n' +
        '• 🎙️ **الرسائل الصوتية الذكية:** التحدث وسماع الردود بأصوات ونبرات عربية متنوعة.\n' +
        '• 📱 **ربط الواتساب (قراءة فقط):** سحب رسائل المجموعات والصور لملف إكسل منظم.\n\n' +
        '📌 *الأوامر السريعة:*\n' +
        '• /study - تفعيل دليل المساعد الدراسي ووضع المذاكرة\n' +
        '• /search <كلمة البحث> - بحث سريع في الويب\n' +
        '• /voice - إعدادات واختيار الأصوات العربية\n' +
        '• /status - عرض حالة مزودي الذكاء الاصطناعي\n' +
        '• /clear - مسح سياق المحادثة وبدء جلسة جديدة\n' +
        '• /whatsapp - ربط الواتساب\n' +
        '• /reports - Meydan Reports Automation Menu'
    );
});

// /status command to view active providers
bot.command('status', async (ctx) => {
    const active = getActiveProviders();
    let text = '⚙️ *حالة مزودي الذكاء الاصطناعي (LLM):*\n\n';

    text += `🔹 *OpenRouter:* ${config.openrouterApiKey ? `✅ مفعّل (\`${config.openrouterModel}\`)` : '❌ غير مفعّل'}\n`;
    text += `🔹 *AgentRouter:* ${config.agentrouterApiKey ? `✅ مفعّل (\`${config.agentrouterModel}\`)` : '❌ غير مفعّل'}\n`;
    text += `🔹 *NVIDIA NIM:* ${config.nvidiaApiKey ? `✅ مفعّل (\`${config.nvidiaModel}\`)` : '❌ غير مفعّل'}\n`;
    text += `🔹 *Google Gemini:* ${config.geminiApiKey ? `✅ مفعّل (\`${config.geminiModel}\`)` : '❌ غير مفعّل'}\n`;
    text += `🔹 *Groq:* ${config.groqApiKey ? `✅ مفعّل (\`${config.groqModel}\`)` : '❌ غير مفعّل'}\n\n`;

    if (active.length > 0) {
        text += `🎯 *المزود النشط حالياً بالأولوية:* ${active[0].name} (${active[0].model})\n`;
        text += `🔄 يدعم البوت التبديل التلقائي إذا فشل أي مزود.\n`;
        text += `🐍 بيئة Python 3 ومستقبل الملفات جاهزة ومفعلة!`;
    } else {
        text += `⚠️ لا يوجد أي مزود نشط! يرجى إضافة مفتاح API في ملف \`.env\`.`;
    }

    await ctx.reply(text, { parse_mode: 'Markdown' });
});

// /clear command to reset context
bot.command('clear', async (ctx) => {
    const userId = ctx.from?.id;
    if (userId) {
        addMessage(userId, 'system', 'Conversation context reset by user.');
        await ctx.reply('🧹 تم مسح الذاكرة وبدء محادثة جديدة.');
    }
});

// /study command - Study Assistant & Tutor Guide
bot.command('study', async (ctx) => {
    const studyGuide =
        '🎓 *مساعد المذاكرة والتعلم الذكي (Study Tutor):*\n\n' +
        'أنا جاهز لمساعدتك في فهم وحفظ أي مادة دراسية بأحدث أساليب التعلم الفعّال! إليك كيف يمكنك استغلالي:\n\n' +
        '1️⃣ 💡 *طريقة فاينمان (تبسيط أصعب المفاهيم):*\n' +
        '• اطلب مني: _"اشرح لي [اسم المفهوم/المعادلة] كأن عمري 10 سنوات وبأمثلة واقعية"_\n\n' +
        '2️⃣ ❓ *وضع الاختبار التفاعلي (Flashcards & Quiz):*\n' +
        '• اطلب مني: _"اختبرني في [الموضوع] سؤالاً بسؤال وانتظر إجابتي لتصححها وتدعمني"_\n\n' +
        '3️⃣ 📝 *تلخيص المحاضرات والمراجع والـ PDF:*\n' +
        '• أرسل لي أي ملف PDF أو مستند أو صفحة وقول: _"لخص أهم النقاط، القوانين، والمصطلحات الأساسية"_\n\n' +
        '4️⃣ 🗓️ *خطة وجدول دراسي مخصص:*\n' +
        '• اطلب مني: _"اعمل لي جدول مذاكرة لـ [المادة] مقسم إلى جلسات تركيز (Pomodoro) حتى موعد امتحاني"_\n\n' +
        '5️⃣ 🔍 *البحث عن شروحات ومصادر:*\n' +
        '• اطلب: _"ابحث لي في الإنترنت عن أفضل التمارين أو الشروحات لموضوع كذا"_\n\n' +
        '💬 *فقط اكتب سؤالك أو أرسل ملفك وسنبدأ المذاكرة معاً فوراً!*';

    await ctx.reply(studyGuide, { parse_mode: 'Markdown' });
});

// /search command - Quick Web Search
bot.command('search', async (ctx) => {
    const text = ctx.message?.text || '';
    const query = text.split(' ').slice(1).join(' ').trim();

    if (!query) {
        await ctx.reply('🔍 *طريقة الاستخدام:*\nاكتب `/search` متبوعاً بكلمة البحث.\n\n_مثال:_ `/search ما هو التعلم المعزز بالذكاء الاصطناعي`', {
            parse_mode: 'Markdown',
        });
        return;
    }

    const userId = ctx.from?.id;
    if (userId) {
        await processAgentPrompt(ctx, userId, `ابحث في الإنترنت عن: "${query}" وقدم لي ملخصاً وافياً مع المصادر والروابط.`);
    }
});

// /reminders command - List active scheduled reminders
bot.command('reminders', async (ctx) => {
    const userId = ctx.from?.id;
    if (!userId) return;
    const reminders = listUserReminders(userId);
    if (reminders.length === 0) {
        await ctx.reply('⏰ لا توجد تذكيرات نشطة مجدولة حالياً.\nاطلب مني في أي وقت: _"فكرني بعد ساعة بكذا"_ أو _"فكرني الساعة 5 بمراجعة الكود"_!', {
            parse_mode: 'Markdown',
        });
        return;
    }
    let msg = `⏰ *تذكيراتك القادمة (${reminders.length}):*\n\n`;
    for (const r of reminders) {
        const d = new Date(r.remind_at);
        msg += `• *${r.title}*\n📅 ${d.toLocaleDateString('ar-EG', { dateStyle: 'medium', timeZone: config.timeZone })} في تمام ${d.toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit', timeZone: config.timeZone })}\n\n`;
    }
    await ctx.reply(msg, { parse_mode: 'Markdown' });
});

// /memory command - View persistent personal knowledge
bot.command('memory', async (ctx) => {
    const userId = ctx.from?.id;
    if (!userId) return;
    const memories = getMemoriesDb(userId);
    if (memories.length === 0) {
        await ctx.reply('🧠 ذاكرتي الدائمة فارغة حالياً.\nأخبرني بمعلومات عنك (مثلاً: "احفظ أن اسمي ماهر وأدرس هندسة الذكاء الاصطناعي") وسأتذكرها دائماً!');
        return;
    }
    let msg = `🧠 *ما أتذكره عنك في الذاكرة الدائمة:*\n\n`;
    for (const m of memories) {
        msg += `• **${m.key}:** ${m.value}\n`;
    }
    msg += `\n_لمسح الذاكرة بالكامل اكتب:_ \`/clearmemory\``;
    await ctx.reply(msg, { parse_mode: 'Markdown' });
});

// /clearmemory command - Clear persistent memory
bot.command('clearmemory', async (ctx) => {
    const userId = ctx.from?.id;
    if (!userId) return;
    clearMemoriesDb(userId);
    await ctx.reply('🧹 تم مسح جميع الذكريات والمعلومات الشخصية المحفوظة عنك بنجاح.');
});

// /whatsapp_digest command - Smart AI summary of WhatsApp groups
bot.command('whatsapp_digest', async (ctx) => {
    const userId = ctx.from?.id;
    if (!userId) return;
    await ctx.reply('📊 جاري قراءة وتحليل رسائل مجموعات الواتساب لإعداد التقرير الذكي...');
    await processAgentPrompt(
        ctx,
        userId,
        'استخدم أداة summarize_whatsapp لقراءة رسائل مجموعات الواتساب الأخيرة، ثم قدم لي تقريراً شاملاً ومنسقاً يوضح: 1. أهم الإعلانات والقرارات 2. روابط Zoom أو المستندات المشاركة 3. ملخص النقاشات في كل مجموعة.'
    );
});

// ── WhatsApp Read-Only Commands ──────────────────────────────────────

// /whatsapp - Start WhatsApp connection and send QR code
bot.command('whatsapp', async (ctx) => {
    const status = getWhatsAppStatus();

    if (status.status === 'connected') {
        await ctx.reply(
            '✅ الواتساب متصل بالفعل!\n' +
            '📊 الرسائل تُسجَّل تلقائياً في ملف الإكسل.\n' +
            'استخدم /whatsapp\\_excel لتحميل الملف.'
        );
        return;
    }

    await ctx.reply('📱 جاري بدء اتصال الواتساب (وضع القراءة فقط)...\n⏳ انتظر ظهور رمز QR للمسح...');

    // Start the client (async, QR will appear shortly)
    startWhatsAppClient();

    // Wait a bit for QR to be generated, then send it
    let attempts = 0;
    const maxAttempts = 15;
    const checkInterval = 2000; // 2 seconds

    const sendQr = async () => {
        const qrImage = getWhatsAppQrImage();
        const currentState = getWhatsAppStatus();

        if (currentState.status === 'connected') {
            await ctx.reply('✅ تم ربط الواتساب بنجاح! الآن يستمع لرسائل المجموعات (قراءة فقط).');
            return;
        }

        if (qrImage) {
            await ctx.replyWithPhoto(new InputFile(qrImage, 'whatsapp_qr.png'), {
                caption:
                    '📱 *امسح رمز QR هذا بواتساب:*\n\n' +
                    '1️⃣ افتح واتساب على هاتفك\n' +
                    '2️⃣ اذهب إلى الإعدادات > الأجهزة المرتبطة\n' +
                    '3️⃣ اضغط "ربط جهاز" وامسح هذا الرمز\n\n' +
                    '⚠️ الربط للقراءة فقط - لن يتم إرسال أي رسائل.',
                parse_mode: 'Markdown',
            });
            return;
        }

        attempts++;
        if (attempts < maxAttempts) {
            setTimeout(sendQr, checkInterval);
        } else {
            await ctx.reply('⚠️ انتهت مهلة انتظار QR. حاول مرة أخرى بإرسال /whatsapp');
        }
    };

    setTimeout(sendQr, 3000); // Start checking after 3 seconds
});

// /whatsapp_status - Check WhatsApp connection status
bot.command('whatsapp_status', async (ctx) => {
    const status = getWhatsAppStatus();
    const statusEmoji: Record<string, string> = {
        connected: '✅ متصل',
        connecting: '🔄 جاري الاتصال...',
        disconnected: '❌ غير متصل',
    };

    let text = '📱 *حالة اتصال الواتساب:*\n\n';
    text += `الحالة: ${statusEmoji[status.status] || status.status}\n`;

    if (status.status === 'connected') {
        text += '\n📊 الرسائل تُسجَّل تلقائياً من المجموعات.\n';
        text += 'استخدم /whatsapp\\_excel لتحميل ملف الإكسل.';
    } else if (status.status === 'disconnected') {
        text += '\nاستخدم /whatsapp لبدء الاتصال.';
    }

    await ctx.reply(text, { parse_mode: 'Markdown' });
});

// /whatsapp_excel - Download the WhatsApp messages Excel file (last 30 days; "all" for everything)
bot.command('whatsapp_excel', async (ctx) => {
    await sendWhatsAppExcel(ctx, String(ctx.match || '').trim().toLowerCase() === 'all');
});

// /voice command - Manage Voice Responses (Arabic TTS)
bot.command('voice', async (ctx) => {
    const userId = ctx.from?.id;
    if (!userId) return;
    const text = ctx.message?.text || '';
    const args = text.split(' ').slice(1).join(' ').trim().toLowerCase();
    const settings = getUserVoiceSettings(userId);

    if (args === 'on') {
        setUserAlwaysVoice(userId, true);
        await ctx.reply('🎙️ *تم تفعيل الرد الصوتي دائماً!*\nسأقوم بالرد عليك بنص وتسجيل صوتي في جميع المحادثات.', { parse_mode: 'Markdown' });
        return;
    }

    if (args === 'off') {
        setUserAlwaysVoice(userId, false);
        await ctx.reply('🔇 *تم إيقاف الرد الصوتي الدائم.*\nسأرد صوتياً فقط عندما ترسل لي رسائل صوتية.', { parse_mode: 'Markdown' });
        return;
    }

    if (args && ARABIC_VOICES[args]) {
        setUserVoiceKey(userId, args);
        const v = ARABIC_VOICES[args];
        await ctx.reply(`✅ *تم تغيير الصوت إلى:* ${v.country} ${v.name}`, { parse_mode: 'Markdown' });
        return;
    }

    // Default info display
    const currentVoice = ARABIC_VOICES[settings.voiceKey] || ARABIC_VOICES['hamed'];
    let helpMsg = '🎙️ *إعدادات الردود الصوتية باللغة العربية (TTS):*\n\n';
    helpMsg += `• *الحالة الحالية:* ${settings.alwaysVoice ? '✅ مفعّل دائماً لجميع الرسائل' : '🎧 عند إرسال رسائل صوتية فقط'}\n`;
    helpMsg += `• *الصوت الحالي:* ${currentVoice.country} ${currentVoice.name}\n\n`;
    helpMsg += '📌 *الأوامر للتحكم:*\n';
    helpMsg += '• `/voice on` : تفعيل الرد الصوتي لجميع الرسائل.\n';
    helpMsg += '• `/voice off` : الرد صوتياً فقط عند استلام رسائل صوتية.\n\n';
    helpMsg += '🗣️ *الأصوات العربية المتوفرة (اكتب الأمر لتغيير الصوت):*\n';
    for (const [key, val] of Object.entries(ARABIC_VOICES)) {
        helpMsg += `• \`/voice ${key}\` : ${val.country} ${val.name}\n`;
    }

    await ctx.reply(helpMsg, { parse_mode: 'Markdown' });
});

// Helper to safely split and send long replies exceeding Telegram's 4096 character limit
async function sendSplitReply(ctx: any, text: string) {
    const maxLength = 3900;
    if (text.length <= maxLength) {
        return await ctx.reply(text);
    }

    let remaining = text;
    while (remaining.length > 0) {
        if (remaining.length <= maxLength) {
            await ctx.reply(remaining);
            break;
        }

        let splitIdx = remaining.lastIndexOf('\n', maxLength);
        if (splitIdx === -1 || splitIdx < 2000) {
            splitIdx = remaining.lastIndexOf(' ', maxLength);
        }
        if (splitIdx === -1 || splitIdx < 1000) {
            splitIdx = maxLength;
        }

        const chunk = remaining.slice(0, splitIdx).trim();
        if (chunk) {
            await ctx.reply(chunk);
        }
        remaining = remaining.slice(splitIdx).trim();
    }
}

// Helper to parse embedded tool calls when LLM outputs plaintext tags like <tool_call>...
function parseEmbeddedToolCalls(content: string) {
    if (!content || typeof content !== 'string') return [];
    const toolCalls: any[] = [];

    // Pattern 1: <tool_call>function_name<arg_key>k1</arg_key><arg_value>v1</arg_value>...</tool_call>
    const xmlRegex = /<tool_call>([a-zA-Z0-9_-]+)([\s\S]*?)<\/tool_call>/g;
    let match;
    while ((match = xmlRegex.exec(content)) !== null) {
        const toolName = match[1].trim();
        const body = match[2];
        const args: any = {};

        const argRegex = /<arg_key>([\s\S]*?)<\/arg_key>\s*<arg_value>([\s\S]*?)<\/arg_value>/g;
        let argMatch;
        let foundArgs = false;
        while ((argMatch = argRegex.exec(body)) !== null) {
            foundArgs = true;
            const key = argMatch[1].trim();
            let val = argMatch[2].trim();
            try {
                val = JSON.parse(val);
            } catch (_) {}
            args[key] = val;
        }

        if (!foundArgs) {
            try {
                const parsed = JSON.parse(body.trim());
                if (typeof parsed === 'object') Object.assign(args, parsed);
            } catch (_) {}
        }

        toolCalls.push({
            id: 'call_' + Math.random().toString(36).substring(7),
            type: 'function',
            function: {
                name: toolName,
                arguments: JSON.stringify(args),
            },
        });
    }

    // Pattern 2: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    if (toolCalls.length === 0) {
        const jsonRegex = /<tool_call>\s*(\{[\s\S]*?\})\s*<\/tool_call>/g;
        let jMatch;
        while ((jMatch = jsonRegex.exec(content)) !== null) {
            try {
                const obj = JSON.parse(jMatch[1]);
                if (obj.name) {
                    toolCalls.push({
                        id: 'call_' + Math.random().toString(36).substring(7),
                        type: 'function',
                        function: {
                            name: obj.name,
                            arguments: typeof obj.arguments === 'string' ? obj.arguments : JSON.stringify(obj.arguments || obj.parameters || {}),
                        },
                    });
                }
            } catch (_) {}
        }
    }

    return toolCalls;
}

// Core AI processing function for text, voice, documents, and photos
async function processAgentPrompt(ctx: any, userId: number, userPrompt: string, options?: { replyWithVoice?: boolean }) {
    await ctx.replyWithChatAction('typing');

    try {
        const wantsVoice = Boolean(
            options?.replyWithVoice ||
            /(تكلم|رد|اقرأ|انطق|جاوب).*(صوت|فويس)|(بصوتك|بالصوت|سجل فويس|رسالة صوتية)/i.test(userPrompt)
        );
        let voiceSentViaTool = false;

        // Save user message to database (SQLite & Firebase Firestore)
        addMessage(userId, 'user', userPrompt);

        // Load recent chat history
        const history = getMessages(userId, config.conversationHistoryLimit ?? 50);

        const now = new Date();
        const currentYear = now.getFullYear();
        const currentDateStr = now.toLocaleDateString('ar-EG', {
            weekday: 'long',
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            timeZone: config.timeZone,
        });

        const memories = getMemoriesDb(userId);
        let memorySection = '';
        if (memories.length > 0) {
            memorySection =
                `\n🧠 YOUR LONG-TERM KNOWLEDGE ABOUT THIS USER (Persists forever across conversations):\n` +
                memories.map((m) => `• [${m.key}]: ${m.value}`).join('\n') +
                `\nAlways respect and incorporate this user context naturally in your responses!\n\n`;
        }

        const messages: any[] = [
            {
                role: 'system',
                content:
                    'You are amirClaw, an advanced personal AI assistant on Telegram with FULL FILE READING, LIVE WEB SEARCH & SCRAPING, STUDY TUTORING, PYTHON 3, AND ARABIC VOICE CAPABILITIES.\n\n' +
                    `📅 CURRENT DATE & TIME CONTEXT:\n` +
                    `• Today's Date: ${currentDateStr}\n` +
                    `• Current Year: ${currentYear}\n` +
                    `• CRITICAL SEARCH RULE: You are living in ${currentYear}! When searching the web for "latest", "recent", "today", or "أخبار اليوم", NEVER use outdated years like 2024 or 2025 in your search query. Always search for current events of ${currentYear} or omit the year to get today's latest articles (e.g. use "أخبار الذكاء الاصطناعي اليوم" or "latest artificial intelligence news ${currentYear}").\n\n` +
                    memorySection +
                    'CRITICAL INSTRUCTIONS ABOUT YOUR CAPABILITIES:\n' +
                    '1. You CAN read and process files uploaded by the user directly in Telegram chat! Whenever the user uploads any document (.py, .xlsx, .csv, .txt, .pdf, .docx, or photo), our system immediately downloads it and provides its content or path to you.\n' +
                    '2. You have the "read_file" tool to read any file in workspace or downloads.\n' +
                    '3. You have the "search_web" tool to search the internet in real-time for live information, news, study materials, research papers, questions, and tutorials. Always use it when asked about current facts, latest news, or when searching.\n' +
                    '4. You have the "scrape_webpage" tool to fetch, scrape, and extract readable text from any specific URL or article shared by the user to summarize or analyze it.\n' +
                    '5. SMART REMINDERS & SCHEDULER:\n' +
                    '   - You have the "set_reminder" tool. When the user asks to be reminded ("فكرني بعد ساعة", "نبهني بكرة الساعة 5"), use "set_reminder" with delayMinutes or targetTime and clear topic title.\n' +
                    '6. LONG-TERM PERSONAL MEMORY:\n' +
                    '   - You have the "manage_memory" tool. Whenever the user tells you personal details (their name, study major, interests, current project, exam date, preferred tone) or says "احفظ أن...", call "manage_memory" with action="remember" so you remember them forever.\n' +
                    '7. WHATSAPP GROUP DIGEST:\n' +
                    '   - You have the "summarize_whatsapp" tool to read and aggregate captured WhatsApp group conversations from Excel to provide executive summaries of discussions, zoom links, and deadlines.\n' +
                    '8. DOCUMENT, PRESENTATION & SPREADSHEET GENERATION:\n' +
                    '   - With "execute_python", you have full access to python-pptx (PowerPoint slides), python-docx (Word documents), reportlab/pypdf (PDFs), openpyxl/pandas (Excel spreadsheets), and matplotlib (data charts). Whenever the user asks for a presentation (.pptx), report (.docx), PDF, or spreadsheet, write clean Python code to generate it in workspace and it will be sent automatically to the user!\n' +
                    '9. NEWS AND CURRENT EVENTS PRESENTATION:\n' +
                    '   - When asked for news (e.g. "أخبار الذكاء الاصطناعي اليوم" or latest updates): ALWAYS use "search_web" first to retrieve fresh headlines and summaries.\n' +
                    '   - Present news in a clean, polished, professional Arabic layout with:\n' +
                    '     • Catchy header (e.g. 📰 أهم أخبار الذكاء الاصطناعي اليوم:)\n' +
                    '     • Numbered items (1., 2., 3.)\n' +
                    '     • Clear headlines\n' +
                    '     • Concise 2-3 sentence summaries explaining the event\n' +
                    '     • Source attribution with link (e.g. 🔗 رويترز (Reuters) or original publisher)\n' +
                    '10. STUDY TUTOR & LEARNING MODE:\n' +
                    '   - When the user asks for help studying, learning, or revising: act as an inspiring, elite academic mentor.\n' +
                    '   - Use the Feynman technique (explain complex concepts intuitively with simple everyday analogies in Arabic).\n' +
                    '   - Provide interactive quizzes (Flashcards) one question at a time, check the user answer, and give gentle feedback and explanation.\n' +
                    '   - Summarize heavy books, lectures, or PDF slides into golden bullet points, essential formulas, and key vocabulary.\n' +
                    '   - Help create Pomodoro schedules and study roadmaps.\n' +
                    '11. You have the "generate_image" tool to generate images using Imagen AI model.\n' +
                    '12. You have the "generate_video" tool to generate short videos using Veo AI model.\n' +
                    '13. You have the "generate_voice" tool to generate spoken Arabic voice notes (TTS) using Microsoft Edge Neural TTS. Whenever the user asks to speak or hear voice, use "generate_voice". NEVER say you cannot speak or generate audio.\n' +
                    '14. NEVER output raw internal tags like <tool_call> or <arg_key> to the user. Always synthesize clean human responses in Arabic or English.',
            },
            ...history.map((msg) => ({
                role: msg.role,
                content:
                    typeof msg.content === 'string' && msg.content.length > 1000
                        ? msg.content.slice(0, 1000) + '... [مقتطع للاختصار]'
                        : msg.content,
            })),
        ];

        // Call multi-provider LLM service
        let completion = await createChatCompletion({
            messages,
            tools: llmTools.length > 0 ? llmTools : undefined,
            tool_choice: 'auto',
        });

        let responseMessage = completion.message;

        // Multi-round tool execution loop
        let toolRounds = 0;
        while (toolRounds < 4) {
            toolRounds++;

            // Extract tool calls from OpenAI structured tool_calls or fallback embedded text tags
            let toolCalls = responseMessage?.tool_calls;
            if ((!toolCalls || toolCalls.length === 0) && responseMessage?.content) {
                const parsed = parseEmbeddedToolCalls(responseMessage.content);
                if (parsed.length > 0) {
                    console.log(`🔧 Intercepted ${parsed.length} embedded text tool call(s) from LLM output!`);
                    toolCalls = parsed;
                    responseMessage.tool_calls = parsed;
                }
            }

            if (!toolCalls || toolCalls.length === 0) {
                break; // No tool calls to execute
            }

            messages.push(responseMessage);

            for (const toolCall of toolCalls) {
                const toolName = toolCall.function.name;
                let toolArgs: any = {};
                try {
                    toolArgs =
                        typeof toolCall.function.arguments === 'string'
                            ? JSON.parse(toolCall.function.arguments)
                            : toolCall.function.arguments || {};
                } catch (e) {
                    toolArgs = {};
                }

                console.log(`Executing tool: ${toolName} with args:`, toolArgs);
                const toolResult: any = await executeTool(toolName, toolArgs, { userId });

                // Handle media results directly on Telegram
                if (toolResult?.type === 'photo_url' && toolResult.url) {
                    await ctx.replyWithChatAction('upload_photo');
                    await ctx.replyWithPhoto(toolResult.url, { caption: `🎨 ${toolResult.prompt || ''}` });
                } else if (toolResult?.type === 'photo_base64' && toolResult.base64) {
                    await ctx.replyWithChatAction('upload_photo');
                    const buffer = Buffer.from(toolResult.base64, 'base64');
                    await ctx.replyWithPhoto(new InputFile(buffer, 'image.png'), { caption: `🎨 ${toolResult.prompt || ''}` });
                } else if (toolResult?.type === 'video_url' && toolResult.url) {
                    await ctx.replyWithChatAction('upload_video');
                    await ctx.replyWithVideo(toolResult.url, { caption: `🎬 ${toolResult.prompt || ''}` });
                } else if (toolResult?.type === 'voice_path' && toolResult.filePath) {
                    await ctx.replyWithChatAction('record_voice');
                    await ctx.replyWithVoice(new InputFile(toolResult.filePath));
                    fs.promises.unlink(toolResult.filePath).catch(() => {});
                    voiceSentViaTool = true;
                }

                // If execute_python produced any files (Excel, PDF, Word, images, etc.), send them to user
                if (toolResult?.generatedFiles && Array.isArray(toolResult.generatedFiles)) {
                    for (const f of toolResult.generatedFiles) {
                        const filePath = path.join(WORKSPACE_DIR, f);
                        if (fs.existsSync(filePath)) {
                            const ext = path.extname(f).toLowerCase();
                            if (['.png', '.jpg', '.jpeg'].includes(ext)) {
                                await ctx.replyWithChatAction('upload_photo');
                                await ctx.replyWithPhoto(new InputFile(filePath), { caption: `📊 ${f}` });
                            } else {
                                await ctx.replyWithChatAction('upload_document');
                                await ctx.replyWithDocument(new InputFile(filePath), { caption: `📁 تم إنشاء الملف: ${f}` });
                            }
                        }
                    }
                }

                messages.push({
                    role: 'tool',
                    tool_call_id: toolCall.id,
                    content: typeof toolResult === 'string' ? toolResult : JSON.stringify(toolResult),
                });
            }

            // Get next response after tool execution
            completion = await createChatCompletion({
                messages,
                tools: llmTools.length > 0 ? llmTools : undefined,
                tool_choice: 'auto',
            });

            responseMessage = completion.message;
        }

        let replyText = responseMessage?.content || 'تم تنفيذ طلبك بنجاح.';

        // Strip any residual raw <tool_call> tags if any remained in final text
        replyText = replyText.replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, '').trim();
        if (!replyText) {
            replyText = 'تم تنفيذ العملية بنجاح.';
        }

        // Save assistant response to DB
        addMessage(userId, 'assistant', replyText);

        await sendSplitReply(ctx, replyText);

        // Send Arabic voice reply if requested or alwaysVoice is enabled
        const voiceSettings = getUserVoiceSettings(userId);
        if (!voiceSentViaTool && (wantsVoice || voiceSettings.alwaysVoice)) {
            try {
                await ctx.replyWithChatAction('record_voice');
                const speechPath = await textToSpeech(replyText, userId);
                if (speechPath) {
                    await ctx.replyWithVoice(new InputFile(speechPath));
                    fs.promises.unlink(speechPath).catch(() => {});
                }
            } catch (vErr: any) {
                console.error('Error generating Arabic voice reply:', vErr);
            }
        }
    } catch (error: any) {
        console.error('Error handling message:', error);
        await ctx.reply(`❌ حدث خطأ أثناء المعالجة: ${error.message || 'خطأ غير معروف'}`);
    }
}

// Free-text guard: block AI from answering report/summary questions about WhatsApp data.
// English: only multi-word phrases or Meydan-specific terms (avoid blocking common words like "fire", "safety").
// Arabic: plain .includes() because \b does not work with non-ASCII in JS regex.
const REPORT_GUARD_ENGLISH_PHRASES = [
    'parking report', 'parking occupancy', 'parking summary',
    'maintenance report', 'fire safety report', 'fire & safety',
    'operations report', 'violations report', 'permissions report',
    'cleaning report', 'cleaning summary',
    'meydan report', 'fm report', 'shift report',
    'generate report', 'show report', 'get report',
];
const REPORT_GUARD_ARABIC_WORDS = [
    'تقرير', 'ملخص', 'صيانة', 'سلامة الحريق', 'عمليات ميدان', 'مخالفات', 'تصاريح', 'نظافة',
];
function isBlockedByReportGuard(text: string): boolean {
    const lower = text.toLowerCase().replace(/\s+/g, ' ');
    if (REPORT_GUARD_ENGLISH_PHRASES.some(kw => lower.includes(kw))) return true;
    if (REPORT_GUARD_ARABIC_WORDS.some(kw => text.includes(kw))) return true;
    return false;
}

// 0. Handle inline date-picker callbacks from report buttons
bot.on('callback_query:data', async (ctx) => {
    try {
        const data = ctx.callbackQuery.data || '';
        const handled = (await handleCorrectionCallback(ctx, data)) || (await handleReportCallback(ctx, data));
        if (!handled) {
            try { await ctx.answerCallbackQuery(); } catch (_) {}
        }
    } catch (err: any) {
        console.error('Callback query error:', err);
        try { await ctx.answerCallbackQuery({ text: '❌ حدث خطأ' }); } catch (_) {}
    }
});

// 1. Handle incoming text messages
bot.on('message:text', async (ctx) => {
    try {
        const text = ctx.message.text || '';
        // Check if input triggers any of the Meydan reports or buttons
        const handledByReports = await handleReportAction(ctx, text);
        if (handledByReports) {
            return;
        }

        // Free-text guard: never let AI generate report summaries
        if (isBlockedByReportGuard(text)) {
            await ctx.reply(
                'To generate Meydan reports, please use the report buttons:\n\n' +
                '• 🔧 Maintenance Report (Meydan FM)\n' +
                '• 🧯 Fire & Safety Report\n' +
                '• 🏢 Operations Report\n' +
                '• 🚫 Parking Violations Report\n' +
                '• 📋 Vehicle Permissions Report\n' +
                '• 🅿️ Parking Report — Latest\n\n' +
                'Type /reports to open the full menu.'
            );
            return;
        }

        const userId = ctx.from.id;
        await processAgentPrompt(ctx, userId, text);
    } catch (err: any) {
        console.error('Unhandled error in message:text handler:', err);
        try {
            await ctx.reply('❌ حدث خطأ غير متوقع. حاول مرة أخرى.');
        } catch (_) {}
    }
});

// 2. Handle incoming document and file uploads (.py, .txt, .csv, .json, .pdf, etc.)
bot.on('message:document', async (ctx) => {
    const userId = ctx.from.id;
    const doc = ctx.message.document;
    if (!doc) return;

    await ctx.replyWithChatAction('typing');

    try {
        console.log(`📁 Received document: ${doc.file_name} from user ${userId}`);
        const file = await ctx.api.getFile(doc.file_id);
        if (!file.file_path) {
            throw new Error('Could not retrieve file path from Telegram.');
        }

        const fileName = doc.file_name || `file_${Date.now()}`;
        const localPath = path.join(DOWNLOADS_DIR, fileName);

        const fileUrl = `https://api.telegram.org/file/bot${config.telegramBotToken}/${file.file_path}`;
        const res = await fetch(fileUrl);
        const arrayBuffer = await res.arrayBuffer();
        const buffer = Buffer.from(arrayBuffer);
        await fs.promises.writeFile(localPath, buffer);

        console.log(`✅ Saved document to ${localPath} (${buffer.length} bytes)`);

        const ext = path.extname(fileName).toLowerCase();
        const textExtensions = ['.py', '.txt', '.csv', '.json', '.js', '.ts', '.html', '.css', '.md', '.sh', '.yaml', '.yml', '.sql'];
        const caption = ctx.message.caption?.trim() || '';

        let promptText = '';
        if (['.xlsx', '.xls'].includes(ext)) {
            promptText =
                `[المستخدم قام برفع ملف إكسل: ${fileName} وحجمه: ${(buffer.length / 1024).toFixed(1)} KB]\n` +
                `المسار المحفوظ: workspace/downloads/${fileName}\n\n` +
                `يمكنك قراءة وتحليل ومعالجة هذا الملف في بايثون باستخدام pandas أو openpyxl:\n` +
                `\`import pandas as pd; df = pd.read_excel('downloads/${fileName}')\`\n\n` +
                `${caption ? `طلب المستخدم: ${caption}` : 'قم بفحص وتلخيص محتويات ملف الإكسل هذا وعرض الجداول وأهم الأرقام به.'}`;
        } else if (textExtensions.includes(ext) || doc.mime_type?.startsWith('text/')) {
            const content = buffer.toString('utf-8', 0, Math.min(buffer.length, 30000));
            promptText =
                `[المستخدم قام برفع ملف: ${fileName}]\n` +
                `المسار المحفوظ: workspace/downloads/${fileName}\n` +
                `محتوى الملف:\n\`\`\`${ext.replace('.', '')}\n${content}\n\`\`\`\n\n` +
                `${caption ? `طلب المستخدم بخصوص الملف: ${caption}` : 'يرجى مراجعة هذا الملف أو تشغيله بواسطة بايثون إذا طُلب ذلك.'}`;
        } else {
            promptText =
                `[المستخدم قام برفع ملف: ${fileName} وحجمه: ${(buffer.length / 1024).toFixed(1)} KB]\n` +
                `المسار على السيرفر: workspace/downloads/${fileName}\n\n` +
                `${caption ? `طلب المستخدم: ${caption}` : 'تم استلام الملف بنجاح. ماذا تريد مني أن أفعل به أو كيف أساعدك به؟'}`;
        }

        await processAgentPrompt(ctx, userId, promptText);
    } catch (err: any) {
        console.error('Error handling document upload:', err);
        await ctx.reply(`❌ خطأ أثناء استلام الملف: ${err.message || 'خطأ غير معروف'}`);
    }
});

// 3. Handle incoming photos
bot.on('message:photo', async (ctx) => {
    const userId = ctx.from.id;
    const photos = ctx.message.photo;
    if (!photos || photos.length === 0) return;

    await ctx.replyWithChatAction('typing');

    try {
        const photo = photos[photos.length - 1]; // Highest resolution
        const file = await ctx.api.getFile(photo.file_id);
        if (!file.file_path) {
            throw new Error('Could not retrieve photo path from Telegram.');
        }

        const fileName = `photo_${Date.now()}.jpg`;
        const localPath = path.join(DOWNLOADS_DIR, fileName);

        const fileUrl = `https://api.telegram.org/file/bot${config.telegramBotToken}/${file.file_path}`;
        const res = await fetch(fileUrl);
        const arrayBuffer = await res.arrayBuffer();
        const buffer = Buffer.from(arrayBuffer);
        await fs.promises.writeFile(localPath, buffer);

        console.log(`🖼️ Downloaded photo to ${localPath} (${buffer.length} bytes)`);

        const caption = ctx.message.caption?.trim() || '';
        const promptText =
            `[المستخدم أرسل صورة وتم حفظها في: workspace/downloads/${fileName}]\n` +
            `${caption ? `سؤال/طلب المستخدم عن الصورة: ${caption}` : 'أرسل المستخدم صورة. ساعده في تحليلها أو معالجتها بأكواد بايثون إذا طلب ذلك.'}`;

        await processAgentPrompt(ctx, userId, promptText);
    } catch (err: any) {
        console.error('Error handling photo:', err);
        await ctx.reply(`❌ خطأ أثناء استلام الصورة: ${err.message || 'خطأ غير معروف'}`);
    }
});

// 4. Handle incoming voice and audio messages with Groq Whisper
bot.on(['message:voice', 'message:audio'], async (ctx) => {
    const userId = ctx.from.id;
    const voice = ctx.message.voice || ctx.message.audio;
    if (!voice) return;

    if (!config.groqApiKey) {
        await ctx.reply('⚠️ ميزة تحويل الصوت إلى نص (Whisper) تتطلب إعداد GROQ_API_KEY في ملف .env.');
        return;
    }

    await ctx.replyWithChatAction('record_voice');

    try {
        console.log(`🎙️ Received voice message from user ${userId}, downloading...`);

        // 1. Get file information from Telegram
        const file = await ctx.api.getFile(voice.file_id);
        if (!file.file_path) {
            throw new Error('Could not retrieve file path from Telegram.');
        }

        const fileUrl = `https://api.telegram.org/file/bot${config.telegramBotToken}/${file.file_path}`;
        const audioResponse = await fetch(fileUrl);
        const arrayBuffer = await audioResponse.arrayBuffer();

        // 2. Transcribe using Groq Whisper (whisper-large-v3-turbo)
        const fileObj = await toFile(Buffer.from(arrayBuffer), 'voice.ogg', { type: 'audio/ogg' });
        console.log('Transcribing audio with Groq Whisper...');

        const transcription = await groq.audio.transcriptions.create({
            file: fileObj,
            model: 'whisper-large-v3-turbo',
        });

        const userPrompt = transcription.text?.trim();
        if (!userPrompt) {
            await ctx.reply('لم أتمكن من سماع أي صوت واضح في الرسالة. يرجى المحاولة مرة أخرى.');
            return;
        }

        console.log(`🗣️ Transcribed text: "${userPrompt}"`);
        await ctx.reply(`🎤 *سمعتك تقول:*\n"${userPrompt}"`, { parse_mode: 'Markdown' });

        // 3. Process transcribed prompt with AI agent and reply with voice
        await processAgentPrompt(ctx, userId, userPrompt, { replyWithVoice: true });
    } catch (error: any) {
        console.error('Error processing voice message:', error);
        await ctx.reply(`❌ خطأ في معالجة الرسالة الصوتية: ${error.message || 'خطأ غير متوقع'}`);
    }
});

// Error handling
bot.catch((err) => {
    console.error('❌ Error in telegram bot:', err.error || err);
});

// Start bot
console.log('🤖 amirClaw bot is starting...');
bot.start({
    onStart: (botInfo) => {
        const active = getActiveProviders();
        console.log(`✅ amirClaw is live as @${botInfo.username}`);
        console.log(`📡 Configured LLM Providers: ${active.map((a) => `${a.name} (${a.model})`).join(', ') || 'None'}`);
        console.log(`🐍 Python 3 execution & Document handling active.`);
    },
});
