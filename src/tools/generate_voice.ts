import { textToSpeech } from '../tts.js';

export const generateVoiceTool = {
    name: 'generate_voice',
    description:
        'Generate spoken Arabic voice audio from text and send it as a voice note in Telegram. Supports multiple distinctive Arabic dialects and genders: Egyptian (shakir, salma), Saudi (hamed, zariyah), Lebanese (layla, rami), Emirati (hamdan, fatima), Iraqi (bassel).',
    parameters: {
        type: 'object',
        properties: {
            text: {
                type: 'string',
                description: 'The text in Arabic to be spoken naturally aloud.',
            },
            voice: {
                type: 'string',
                enum: ['shakir', 'salma', 'hamed', 'zariyah', 'layla', 'rami', 'hamdan', 'fatima', 'bassel'],
                description:
                    'Optional specific Arabic voice personality. shakir/salma (Egyptian), layla/rami (Lebanese), hamed/zariyah (Saudi), hamdan/fatima (Emirati), bassel (Iraqi).',
            },
        },
        required: ['text'],
    },
    execute: async (args: { text: string; voice?: string }, context?: { userId?: number }) => {
        const text = args.text;
        console.log(`🎙️ Generating voice note for: "${text.slice(0, 50)}..." [voice: ${args.voice || 'user preference'}]`);
        const filePath = await textToSpeech(text, context?.userId, args.voice);

        if (!filePath) {
            return {
                type: 'voice_error',
                message: 'Failed to generate voice audio.',
            };
        }

        return {
            type: 'voice_path',
            filePath,
            message: 'Voice message generated and sent successfully!',
        };
    },
};
