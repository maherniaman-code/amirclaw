import OpenAI from 'openai';
import { config } from './config.js';

export interface ProviderConfig {
    name: string;
    client: OpenAI;
    model: string;
    supportsTools: boolean;
}

// Build list of active LLM providers
export function getActiveProviders(): ProviderConfig[] {
    const providers: ProviderConfig[] = [];

    // 1. OpenRouter (Free models or custom model)
    if (config.openrouterApiKey) {
        providers.push({
            name: 'OpenRouter',
            client: new OpenAI({
                apiKey: config.openrouterApiKey,
                baseURL: 'https://openrouter.ai/api/v1',
                defaultHeaders: {
                    'HTTP-Referer': 'https://github.com/mahernoaman/amirClaw',
                    'X-Title': 'amirClaw Telegram Bot',
                },
            }),
            model: config.openrouterModel,
            supportsTools: true,
        });
    }

    // 2. AgentRouter (agentrouter.org gateway)
    if (config.agentrouterApiKey) {
        providers.push({
            name: 'AgentRouter',
            client: new OpenAI({
                apiKey: config.agentrouterApiKey,
                baseURL: config.agentrouterBaseUrl,
            }),
            model: config.agentrouterModel,
            supportsTools: true,
        });
    }

    // 3. NVIDIA NIM API
    if (config.nvidiaApiKey) {
        providers.push({
            name: 'NVIDIA NIM',
            client: new OpenAI({
                apiKey: config.nvidiaApiKey,
                baseURL: 'https://integrate.api.nvidia.com/v1',
            }),
            model: config.nvidiaModel,
            supportsTools: true,
        });
    }

    // 3. Google Gemini (via OpenAI compatibility endpoint)
    if (config.geminiApiKey) {
        providers.push({
            name: 'Google Gemini',
            client: new OpenAI({
                apiKey: config.geminiApiKey,
                baseURL: 'https://generativelanguage.googleapis.com/v1beta/openai/',
            }),
            model: config.geminiModel,
            supportsTools: true,
        });
    }

    // 4. Groq
    if (config.groqApiKey) {
        const groqClient = new OpenAI({
            apiKey: config.groqApiKey,
            baseURL: 'https://api.groq.com/openai/v1',
        });
        providers.push({
            name: 'Groq',
            client: groqClient,
            model: config.groqModel || 'qwen/qwen3.8-27b',
            supportsTools: true,
        });
        // Add backup model on Groq
        providers.push({
            name: 'Groq (Backup)',
            client: groqClient,
            model: 'openai/gpt-oss-120b',
            supportsTools: true,
        });
    }

    // If PRIMARY_PROVIDER is specified and matches a provider name, bring it to the front
    if (config.primaryProvider && config.primaryProvider !== 'auto') {
        const target = config.primaryProvider.toLowerCase();
        const primaryIdx = providers.findIndex((p) => p.name.toLowerCase().includes(target));
        if (primaryIdx > 0) {
            const [selected] = providers.splice(primaryIdx, 1);
            providers.unshift(selected);
        }
    }

    return providers;
}

export interface ChatOptions {
    messages: any[];
    tools?: any[];
    tool_choice?: any;
}

export async function createChatCompletion(options: ChatOptions): Promise<{
    message: any;
    provider: string;
    model: string;
}> {
    const providers = getActiveProviders();

    if (providers.length === 0) {
        throw new Error(
            'No LLM provider configured! Please set at least one of OPENROUTER_API_KEY, NVIDIA_API_KEY, GEMINI_API_KEY, or GROQ_API_KEY in .env.'
        );
    }

    let lastError: any = null;

    for (const provider of providers) {
        try {
            console.log(`🤖 Requesting LLM from [${provider.name}] using model [${provider.model}]...`);

            const requestParams: any = {
                model: provider.model,
                messages: options.messages,
            };

            // Groq free tier enforces a strict 1000 Output Tokens Per Minute (OTPM) limit.
            // Limiting max_tokens ensures requests are accepted without 429 OTPM errors.
            if (provider.name.includes('Groq')) {
                requestParams.max_tokens = 800;
            }

            if (options.tools && options.tools.length > 0 && provider.supportsTools) {
                requestParams.tools = options.tools;
                requestParams.tool_choice = options.tool_choice || 'auto';
            }

            const response = await provider.client.chat.completions.create(requestParams);
            const message = response.choices[0]?.message;

            if (message) {
                console.log(`✅ [${provider.name}] responded successfully.`);
                return {
                    message,
                    provider: provider.name,
                    model: provider.model,
                };
            }
        } catch (err: any) {
            lastError = err;
            console.warn(`⚠️ [${provider.name}] failed: ${err.message || err}. Trying next provider...`);
        }
    }

    throw new Error(`All LLM providers failed. Last error: ${lastError?.message || lastError}`);
}
