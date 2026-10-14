import { config } from '../config.js';

export const generateVideoTool = {
    name: 'generate_video',
    description: 'Generate a video clip based on a descriptive text prompt. Use this tool whenever the user asks to create or generate a video using Google Veo (Veo 2 / Veo 3.1 Fast).',
    parameters: {
        type: 'object',
        properties: {
            prompt: {
                type: 'string',
                description: 'Detailed description of the video to generate in English or Arabic.',
            },
        },
        required: ['prompt'],
    },
    execute: async (args: { prompt: string }) => {
        const prompt = args.prompt;
        console.log(`🎬 Processing video request for: "${prompt}"`);

        // Check if a valid Google Gemini API key exists
        if (!config.geminiApiKey) {
            return {
                success: false,
                error: 'MISSING_GEMINI_KEY',
                message: 'لم يتم إنشاء الفيديو لأن مفتاح GEMINI_API_KEY غير موجود في ملف .env.',
            };
        }

        try {
            // Initiate Veo 3.1 video generation operation (long-running)
            const res = await fetch(
                `https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning?key=${config.geminiApiKey}`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        instances: [{ prompt }],
                        parameters: {
                            sampleCount: 1,
                            durationSeconds: 4,
                        },
                    }),
                }
            );

            if (!res.ok) {
                const errorText = await res.text();
                console.error('Google Veo API error:', res.status, errorText);
                let userMessage = '';
                if (res.status === 402) {
                    userMessage = '💳 رصيد Google AI Studio نفد (Prepayment credits depleted). يرجى مراجعة خطة الدفع في https://ai.studio/projects أو شحن الرصيد.';
                } else if (res.status === 429) {
                    userMessage = '⚠️ انتهت الكوتة المجانية لتوليد الفيديوهات في Google Veo اليوم. تُجدد الكوتة كل 24 ساعة. يُرجى المحاولة لاحقاً أو الترقية إلى خطة مدفوعة في Google AI Studio.';
                } else if (res.status === 403) {
                    userMessage = '🔑 مفتاح Gemini API لا يمتلك صلاحية استخدام نموذج Veo لتوليد الفيديوهات. قد تحتاج إلى تفعيل الوصول من Google AI Studio.';
                } else {
                    userMessage = `❌ خطأ في Google Veo (${res.status}). يرجى المحاولة لاحقاً.`;
                }
                return {
                    success: false,
                    status: res.status,
                    message: userMessage,
                };
            }

            const initialData = await res.json();
            const operationName = initialData.name;

            if (!operationName) {
                return {
                    success: false,
                    message: 'لم يتم استلام معرف العملية من Google Veo.',
                };
            }

            console.log(`⏳ Video generation started (Operation: ${operationName}). Polling for result...`);

            // Poll operation for up to 60 seconds
            let attempts = 0;
            while (attempts < 12) {
                await new Promise((r) => setTimeout(r, 5000));
                attempts++;

                const pollRes = await fetch(
                    `https://generativelanguage.googleapis.com/v1beta/${operationName}?key=${config.geminiApiKey}`
                );

                if (pollRes.ok) {
                    const pollData = await pollRes.json();
                    if (pollData.done) {
                        const videoUri =
                            pollData.response?.predictions?.[0]?.videoUri ||
                            pollData.response?.predictions?.[0]?.url;

                        if (videoUri) {
                            return {
                                success: true,
                                type: 'video_url',
                                url: videoUri,
                                prompt,
                                message: 'تم إنشاء الفيديو بنجاح بواسطة Google Veo!',
                            };
                        }
                    }
                }
            }

            return {
                success: false,
                message: 'عملية توليد الفيديو تستغرق وقتاً أطول في المعالجة لدى Google Veo.',
            };
        } catch (err: any) {
            console.error('Error in generate_video:', err);
            return {
                success: false,
                message: `حدث خطأ أثناء الاتصال بخدمة Google Veo: ${err.message}`,
            };
        }
    },
};
