import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import WebSocket from 'ws';

const WORKSPACE_DIR = path.resolve(process.cwd(), 'workspace');
const TTS_DIR = path.join(WORKSPACE_DIR, 'tts_cache');

if (!fs.existsSync(TTS_DIR)) {
    fs.mkdirSync(TTS_DIR, { recursive: true });
}

export interface VoiceProfile {
    id: string;
    name: string;
    country: string;
    gender: 'male' | 'female';
    pitch?: string;
    rate?: string;
}

export const ARABIC_VOICES: Record<string, VoiceProfile> = {
    // 🇪🇬 لهجة مصرية
    shakir: { id: 'ar-EG-ShakirNeural', name: 'شاكر (مصري رجالي حيوي)', country: '🇪🇬', gender: 'male', pitch: '+4Hz', rate: '+8%' },
    salma: { id: 'ar-EG-SalmaNeural', name: 'سلمى (مصري نسائي ناعم)', country: '🇪🇬', gender: 'female', pitch: '+10Hz', rate: '+5%' },

    // 🇸🇦 لهجة سعودية
    hamed: { id: 'ar-SA-HamedNeural', name: 'حامد (سعودي رجالي وقور/عميق)', country: '🇸🇦', gender: 'male', pitch: '-6Hz', rate: '-4%' },
    zariyah: { id: 'ar-SA-ZariyahNeural', name: 'زارية (سعودي نسائي هادئ)', country: '🇸🇦', gender: 'female', pitch: '+12Hz', rate: '0%' },

    // 🇱🇧 لهجة شامية / لبنانية (مميزة جداً ومختلفة تماماً)
    layla: { id: 'ar-LB-LaylaNeural', name: 'ليلى (لبناني شامي رقيق)', country: '🇱🇧', gender: 'female', pitch: '+14Hz', rate: '+5%' },
    rami: { id: 'ar-LB-RamiNeural', name: 'رامي (لبناني شامي شبابي)', country: '🇱🇧', gender: 'male', pitch: '+2Hz', rate: '+6%' },

    // 🇦🇪 لهجة إماراتية خليجية
    hamdan: { id: 'ar-AE-HamdanNeural', name: 'حمدان (إماراتي خليجي هادئ)', country: '🇦🇪', gender: 'male', pitch: '-3Hz', rate: '-3%' },
    fatima: { id: 'ar-AE-FatimaNeural', name: 'فاطمة (إماراتي نسائي لطيف)', country: '🇦🇪', gender: 'female', pitch: '+10Hz', rate: '0%' },

    // 🇮🇶 لهجة عراقية
    bassel: { id: 'ar-IQ-BasselNeural', name: 'باسل (عراقي رجالي جهوري)', country: '🇮🇶', gender: 'male', pitch: '-8Hz', rate: '-2%' },
};

// User voice settings: userId -> { voiceKey: string, alwaysVoice: boolean }
const userVoiceSettings = new Map<number, { voiceKey: string; alwaysVoice: boolean }>();

export function getUserVoiceSettings(userId: number) {
    if (!userVoiceSettings.has(userId)) {
        userVoiceSettings.set(userId, { voiceKey: 'shakir', alwaysVoice: false });
    }
    return userVoiceSettings.get(userId)!;
}

export function setUserAlwaysVoice(userId: number, enabled: boolean) {
    const settings = getUserVoiceSettings(userId);
    settings.alwaysVoice = enabled;
    userVoiceSettings.set(userId, settings);
}

export function setUserVoiceKey(userId: number, voiceKey: string) {
    const settings = getUserVoiceSettings(userId);
    if (ARABIC_VOICES[voiceKey]) {
        settings.voiceKey = voiceKey;
        userVoiceSettings.set(userId, settings);
        return true;
    }
    return false;
}

/**
 * Clean text for natural speech synthesis:
 * Strips markdown code blocks, emojis, special characters, URLs, and keeps readable Arabic text.
 */
export function cleanTextForSpeech(text: string): string {
    return text
        // Remove code blocks
        .replace(/```[\s\S]*?```/g, 'تم تضمين كود برمجي.')
        // Remove inline code
        .replace(/`([^`]+)`/g, '$1')
        // Remove Markdown headers, bold, italics, links
        .replace(/^#+\s+/gm, '')
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/\*([^*]+)\*/g, '$1')
        .replace(/_([^_]+)_/g, '$1')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        // Remove URLs
        .replace(/https?:\/\/\S+/g, '')
        // Clean excessive whitespace and bullets
        .replace(/^[•\-\*]\s+/gm, '')
        .replace(/\n{2,}/g, '. ')
        .replace(/\n/g, ' ')
        .trim();
}

const CHROMIUM_FULL_VERSION = '143.0.3650.75';
const CHROMIUM_MAJOR_VERSION = '143';
const TRUSTED_CLIENT_TOKEN = '6A5AA1D4EAFF4E9FB37E23D68491D6F4';
const WIN_EPOCH = 11644473600;

function generateSecMsGec() {
    let ticks = Math.floor(Date.now() / 1000);
    ticks += WIN_EPOCH;
    ticks -= ticks % 300;
    const ticksBigInt = BigInt(ticks) * 10000000n;
    const strToHash = ticksBigInt.toString() + TRUSTED_CLIENT_TOKEN;
    return crypto.createHash('sha256').update(strToHash, 'ascii').digest('hex').toUpperCase();
}

function escapeXml(unsafe: string) {
    return unsafe.replace(/[<>&"']/g, (c) => {
        switch (c) {
            case '<':
                return '&lt;';
            case '>':
                return '&gt;';
            case '&':
                return '&amp;';
            case '"':
                return '&quot;';
            case "'":
                return '&apos;';
            default:
                return c;
        }
    });
}

/**
 * Generate Arabic speech from text using Microsoft Edge Neural TTS via native WebSocket.
 * Returns the local file path to the generated MP3.
 */
export async function textToSpeech(text: string, userId?: number, overrideVoiceKey?: string): Promise<string | null> {
    try {
        const cleaned = cleanTextForSpeech(text);
        if (!cleaned || cleaned.length < 2) return null;

        // Truncate to a reasonable speech length if very long (around 1200 chars)
        const speechText = cleaned.length > 1200 ? cleaned.slice(0, 1200) + '...' : cleaned;

        // Resolve voice profile
        let profile = ARABIC_VOICES['shakir'];
        if (overrideVoiceKey && ARABIC_VOICES[overrideVoiceKey]) {
            profile = ARABIC_VOICES[overrideVoiceKey];
        } else if (userId) {
            const userKey = getUserVoiceSettings(userId).voiceKey;
            profile = ARABIC_VOICES[userKey] || ARABIC_VOICES['shakir'];
        }

        const voiceId = profile.id;
        const locale = voiceId.slice(0, 5); // e.g. 'ar-EG', 'ar-SA', 'ar-LB'
        const pitch = profile.pitch || '0Hz';
        const rate = profile.rate || '0%';

        const fileName = `speech_${Date.now()}_${Math.random().toString(36).substring(7)}.mp3`;
        const filePath = path.join(TTS_DIR, fileName);

        return new Promise<string | null>((resolve) => {
            const connectionId = crypto.randomBytes(16).toString('hex');
            const muid = crypto.randomBytes(16).toString('hex').toUpperCase();
            const token = generateSecMsGec();

            const url =
                `wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?TrustedClientToken=${TRUSTED_CLIENT_TOKEN}` +
                `&ConnectionId=${connectionId}` +
                `&Sec-MS-GEC=${token}` +
                `&Sec-MS-GEC-Version=1-${CHROMIUM_FULL_VERSION}`;

            const headers = {
                'User-Agent': `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${CHROMIUM_MAJOR_VERSION}.0.0.0 Safari/537.36 Edg/${CHROMIUM_MAJOR_VERSION}.0.0.0`,
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'Accept-Language': 'en-US,en;q=0.9',
                Pragma: 'no-cache',
                'Cache-Control': 'no-cache',
                Cookie: `muid=${muid};`,
            };

            const ws = new WebSocket(url, {
                origin: 'chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold',
                headers,
                perMessageDeflate: true,
            });

            const audioChunks: Buffer[] = [];
            const requestId = crypto.randomBytes(16).toString('hex');

            const timeoutId = setTimeout(() => {
                try {
                    ws.close();
                } catch {}
                resolve(null);
            }, 15000);

            ws.on('open', () => {
                // 1. Send speech.config
                const configMessage =
                    'Content-Type:application/json; charset=utf-8\r\nPath:speech.config\r\n\r\n' +
                    JSON.stringify({
                        context: {
                            synthesis: {
                                audio: {
                                    metadataoptions: { sentenceBoundaryEnabled: 'false', wordBoundaryEnabled: 'true' },
                                    outputFormat: 'audio-24khz-48kbitrate-mono-mp3',
                                },
                            },
                        },
                    });
                ws.send(configMessage);

                // 2. Send SSML with prosody (pitch & rate) to emphasize distinctive personality
                const escapedText = escapeXml(speechText);
                const ssml =
                    `<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="${locale}">` +
                    `<voice name="${voiceId}">` +
                    `<prosody pitch="${pitch}" rate="${rate}">${escapedText}</prosody>` +
                    `</voice></speak>`;

                const ssmlMessage =
                    `X-RequestId:${requestId}\r\n` +
                    'Content-Type:application/ssml+xml\r\n' +
                    'Path:ssml\r\n\r\n' +
                    ssml;
                ws.send(ssmlMessage);
            });

            ws.on('message', (data: any, isBinary: boolean) => {
                if (isBinary) {
                    const buffer = Buffer.from(data);
                    const headerLength = buffer.readUInt16BE(0);
                    const audioData = buffer.subarray(headerLength + 2);
                    audioChunks.push(audioData);
                } else {
                    const str = data.toString();
                    if (str.includes('Path:turn.end')) {
                        clearTimeout(timeoutId);
                        try {
                            ws.close();
                        } catch {}
                        const finalAudio = Buffer.concat(audioChunks);
                        if (finalAudio.length > 100) {
                            fs.writeFileSync(filePath, finalAudio);
                            resolve(filePath);
                        } else {
                            resolve(null);
                        }
                    }
                }
            });

            ws.on('error', (err: any) => {
                console.error('Edge TTS WebSocket error:', err.message);
                clearTimeout(timeoutId);
                resolve(null);
            });
        });
    } catch (err: any) {
        console.error('Error generating Arabic speech:', err.message);
        return null;
    }
}
