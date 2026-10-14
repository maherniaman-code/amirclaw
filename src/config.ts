import dotenv from 'dotenv';
import { resolve } from 'path';

dotenv.config();

export const config = {
    telegramBotToken: process.env.TELEGRAM_BOT_TOKEN || '',
    allowedUserIds: (process.env.ALLOWED_USER_IDS || '').split(',').map(id => parseInt(id.trim())).filter(id => !isNaN(id)),
    
    // LLM Providers
    primaryProvider: process.env.PRIMARY_PROVIDER || 'auto', // 'openrouter' | 'nvidia' | 'gemini' | 'groq' | 'auto'

    // OpenRouter (Free models or custom)
    openrouterApiKey: process.env.OPENROUTER_API_KEY || '',
    openrouterModel: process.env.OPENROUTER_MODEL || 'openrouter/free',

    // NVIDIA NIM
    nvidiaApiKey: process.env.NVIDIA_API_KEY || '',
    nvidiaModel: process.env.NVIDIA_MODEL || 'meta/llama-3.3-70b-instruct',

    // Google Gemini
    geminiApiKey: process.env.GEMINI_API_KEY || '',
    geminiModel: process.env.GEMINI_MODEL || 'gemini-3.8-flash',

    // AgentRouter (agentrouter.org / OpenAI-compatible gateway)
    agentrouterApiKey: process.env.AGENTROUTER_API_KEY || '',
    agentrouterBaseUrl: process.env.AGENTROUTER_BASE_URL || 'https://co.agentrouter.org/v1',
    agentrouterModel: process.env.AGENTROUTER_MODEL || 'deepseek-chat',

    // Groq
    groqApiKey: process.env.GROQ_API_KEY || '',
    groqModel: process.env.GROQ_MODEL || 'qwen/qwen3.8-27b',

    dbPath: process.env.DB_PATH || './memory.db',
    timeZone: process.env.BOT_TIMEZONE || 'Asia/Dubai', // Default to Dubai (+04:00) or configure in .env

    // Python Reporting Engine & WhatsApp Extraction
    pythonPath: process.env.PYTHON_PATH || 'python3',
    enableQuotedMessageId: process.env.ENABLE_QUOTED_MESSAGE_ID !== 'false',

    // Mailbox Credentials for Parking Report IMAP
    imapHost: process.env.IMAP_HOST || '',
    imapPort: parseInt(process.env.IMAP_PORT || '993', 10),
    imapUser: process.env.IMAP_USER || '',
    imapPass: process.env.IMAP_PASS || '',
    imapTls: process.env.IMAP_TLS !== 'false',

    // Configurable recipient chat IDs for automated reports (defaults to allowedUserIds)
    reportChatIds: (process.env.REPORT_CHAT_IDS || process.env.ALLOWED_USER_IDS || '')
        .split(',')
        .map(id => parseInt(id.trim()))
        .filter(id => !isNaN(id)),

    // Automatic scheduler sending toggle (disabled by default until user confirms acceptance)
    enableAutomatedReports: process.env.ENABLE_AUTOMATED_REPORTS === 'true',

    // Maximum conversation history messages to load per user
    conversationHistoryLimit: parseInt(process.env.CONVERSATION_HISTORY_LIMIT || '50', 10),
};

export function validateConfig() {
    if (!config.telegramBotToken || config.telegramBotToken === 'REPLACE_WITH_YOURS') {
        throw new Error('TELEGRAM_BOT_TOKEN is missing or invalid in .env');
    }
    
    const hasAtLeastOneKey =
        config.openrouterApiKey ||
        config.agentrouterApiKey ||
        config.nvidiaApiKey ||
        config.geminiApiKey ||
        config.groqApiKey;

    if (!hasAtLeastOneKey) {
        throw new Error(
            'Missing LLM API keys. Please configure at least one in .env: OPENROUTER_API_KEY, AGENTROUTER_API_KEY, NVIDIA_API_KEY, GEMINI_API_KEY, or GROQ_API_KEY'
        );
    }

    if (config.allowedUserIds.length === 0) {
        console.warn('WARNING: ALLOWED_USER_IDS is empty. Nobody will be able to talk to the bot. Add your Telegram ID to .env');
    }
}
