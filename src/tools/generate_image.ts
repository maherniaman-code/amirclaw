import { config } from '../config.js';

export const generateImageTool = {
    name: 'generate_image',
    description: 'Generate an image based on a descriptive text prompt. Use this tool whenever the user asks to draw, generate, or create an image or photo (using Imagen 3 / Nano Banana).',
    parameters: {
        type: 'object',
        properties: {
            prompt: {
                type: 'string',
                description: 'Detailed description of the image to generate in English or Arabic.',
            },
            aspectRatio: {
                type: 'string',
                enum: ['1:1', '16:9', '9:16', '4:3', '3:4'],
                description: 'Aspect ratio of the generated image. Default is 1:1.',
            },
        },
        required: ['prompt'],
    },
    execute: async (args: { prompt: string; aspectRatio?: string }) => {
        const prompt = args.prompt;
        const aspectRatio = args.aspectRatio || '1:1';

        console.log(`🎨 Generating image for prompt: "${prompt}"`);

        // 1. Try Google Imagen 3 if Gemini API key is configured
        if (config.geminiApiKey) {
            try {
                const response = await fetch(
                    `https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key=${config.geminiApiKey}`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            instances: [{ prompt }],
                            parameters: {
                                sampleCount: 1,
                                aspectRatio: aspectRatio.replace(':', ':'),
                            },
                        }),
                    }
                );

                if (response.ok) {
                    const data = await response.json();
                    const base64 = data.predictions?.[0]?.bytesBase64Encoded;
                    if (base64) {
                        return {
                            type: 'photo_base64',
                            base64,
                            prompt,
                            message: `Image generated successfully using Google Imagen 3!`,
                        };
                    }
                } else {
                    console.warn('Google Imagen API call returned:', response.status, await response.text());
                }
            } catch (err: any) {
                console.warn('Error with Google Imagen 3, using high-speed fallback:', err.message);
            }
        }

        // 2. High-speed direct image generation (Instant fallback)
        const encodedPrompt = encodeURIComponent(prompt);
        const imageUrl = `https://image.pollinations.ai/prompt/${encodedPrompt}?nologo=true&enhance=true&width=1024&height=1024`;

        return {
            type: 'photo_url',
            url: imageUrl,
            prompt,
            message: `Image generated successfully!`,
        };
    },
};
