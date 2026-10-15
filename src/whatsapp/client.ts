import makeWASocket, {
    DisconnectReason,
    useMultiFileAuthState,
    downloadMediaMessage,
    WASocket,
    proto,
} from '@whiskeysockets/baileys';
import pino from 'pino';
import qrcodeTerminal from 'qrcode-terminal';
import QRCode from 'qrcode';
import fs from 'fs';
import path from 'path';
import sharp from 'sharp';
import { appendWhatsAppMessage, ensureExcelInitialized, flushWhatsAppMessages, EXCEL_PATH } from './excel.js';
import { config } from '../config.js';
import { mirrorWhatsAppMessage } from '../cloud/messages.js';
import { recordGroupActivity } from '../monitoring/alerts.js';
import { isHrMessage } from '../privacy.js';
import { makeVideoThumbnail } from './video_thumb.js';

const WORKSPACE_DIR = path.resolve(process.cwd(), 'workspace');
const SESSION_DIR = path.join(WORKSPACE_DIR, 'whatsapp_session');
const MEDIA_DIR = path.join(WORKSPACE_DIR, 'whatsapp_media');

// Ensure directories exist
if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });
if (!fs.existsSync(MEDIA_DIR)) fs.mkdirSync(MEDIA_DIR, { recursive: true });

let waSocket: WASocket | null = null;
let currentStatus: 'disconnected' | 'connecting' | 'connected' = 'disconnected';
let statusSince = Date.now();
let loggedOut = false;

// statusSince = when we last went from connected to not-connected (or back). Reconnect
// attempts flip disconnected <-> connecting every few seconds and must not reset it.
function setStatus(next: 'disconnected' | 'connecting' | 'connected'): void {
    if ((next === 'connected') !== (currentStatus === 'connected')) statusSince = Date.now();
    currentStatus = next;
}
let currentQrBuffer: Buffer | null = null;
let currentQrText: string | null = null;
let groupMetadataCache: Map<string, string> = new Map();

export function getWhatsAppStatus() {
    return {
        status: currentStatus,
        statusSince,
        loggedOut,
        hasQr: !!currentQrBuffer,
        excelPath: EXCEL_PATH,
    };
}

export function getWhatsAppQrImage(): Buffer | null {
    return currentQrBuffer;
}

function unwrapMessage(msg: proto.IWebMessageInfo): any {
    if (!msg.message) return null;
    let m: any = msg.message;
    if (m.ephemeralMessage?.message) m = m.ephemeralMessage.message;
    if (m.viewOnceMessage?.message) m = m.viewOnceMessage.message;
    if (m.viewOnceMessageV2?.message) m = m.viewOnceMessageV2.message;
    if (m.documentWithCaptionMessage?.message) m = m.documentWithCaptionMessage.message;
    if (m.deviceSentMessage?.message) m = m.deviceSentMessage.message;
    if (m.botInvokeMessage?.message) m = m.botInvokeMessage.message;
    return m;
}

export function extractQuotedMessageId(m: any): string {
    if (!m) return '';
    const ctx =
        m.extendedTextMessage?.contextInfo ||
        m.imageMessage?.contextInfo ||
        m.videoMessage?.contextInfo ||
        m.documentMessage?.contextInfo ||
        m.audioMessage?.contextInfo ||
        m.stickerMessage?.contextInfo ||
        m.locationMessage?.contextInfo ||
        m.contactMessage?.contextInfo ||
        m.buttonsResponseMessage?.contextInfo ||
        m.listResponseMessage?.contextInfo ||
        m.templateButtonReplyMessage?.contextInfo;
    return (ctx && ctx.stanzaId) ? String(ctx.stanzaId) : '';
}

async function getGroupNameWithTimeout(jid: string): Promise<string> {
    const cached = groupMetadataCache.get(jid);
    if (cached) return cached;
    if (!waSocket) return 'مجموعة واتساب';

    try {
        const timeoutPromise = new Promise<null>((resolve) => setTimeout(() => resolve(null), 3000));
        const metaPromise = waSocket.groupMetadata(jid);
        const meta = await Promise.race([metaPromise, timeoutPromise]);
        if (meta && (meta as any).subject) {
            const subject = (meta as any).subject;
            groupMetadataCache.set(jid, subject);
            return subject;
        }
    } catch {
        // Group metadata failed or rate limited
    }
    return 'مجموعة واتساب';
}

async function processGroupMessage(msg: proto.IWebMessageInfo, downloadMedia: boolean = true): Promise<void> {
    try {
        const jid = msg.key.remoteJid;
        if (!jid) return;

        // Process only group messages (ends with @g.us)
        const isGroup = jid.endsWith('@g.us');
        if (!isGroup) return;

        const m = unwrapMessage(msg);
        if (!m) return;

        // Message timestamp (Safely convert number/Long and format with correct timezone)
        let tsSeconds = Math.floor(Date.now() / 1000);
        if (msg.messageTimestamp) {
            if (typeof msg.messageTimestamp === 'number') {
                tsSeconds = msg.messageTimestamp;
            } else if (typeof (msg.messageTimestamp as any).toNumber === 'function') {
                tsSeconds = (msg.messageTimestamp as any).toNumber();
            } else {
                const parsed = Number(msg.messageTimestamp);
                if (!isNaN(parsed) && parsed > 0) tsSeconds = parsed;
            }
        }

        const dateStr = new Date(tsSeconds * 1000).toLocaleString('en-US', {
            timeZone: config.timeZone,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: true,
        });

        // Fetch or cache group name (with safe 3s timeout)
        const groupName = await getGroupNameWithTimeout(jid);

        // Sender identification
        let senderDisplay: string;
        if (msg.key.fromMe) {
            senderDisplay = 'Me (Bot Account)';
        } else {
            const senderId = msg.key.participant || msg.key.remoteJid || 'Unknown';
            const pushName = msg.pushName || '';
            senderDisplay = pushName ? `${pushName} (${senderId.split('@')[0]})` : senderId.split('@')[0];
        }

        const messageId = msg.key.id || `msg_${tsSeconds}_${jid.split('@')[0]}`;

        // Extract text content
        const textContent =
            m.conversation ||
            m.extendedTextMessage?.text ||
            m.imageMessage?.caption ||
            m.videoMessage?.caption ||
            m.documentMessage?.caption ||
            m.buttonsResponseMessage?.selectedDisplayText ||
            m.listResponseMessage?.title ||
            '';

        // Check for media
        let mediaType = 'Text';
        let mediaFileName: string | undefined;
        let mediaLocalPath: string | undefined;
        let thumbnailPath: string | undefined;

        if (m.imageMessage) {
            mediaType = 'Image';
            if (downloadMedia && waSocket) {
                try {
                    const ext = 'jpg';
                    const fileName = `img_${Date.now()}_${messageId.slice(0, 8)}.${ext}`;
                    const fullPath = path.join(MEDIA_DIR, fileName);

                    const timeoutDownload = new Promise<null>((resolve) => setTimeout(() => resolve(null), 10000));
                    const downloadPromise = downloadMediaMessage(
                        msg,
                        'buffer',
                        {},
                        {
                            logger: pino({ level: 'silent' }),
                            reuploadRequest: waSocket.updateMediaMessage,
                        }
                    );

                    const buffer: any = await Promise.race([downloadPromise, timeoutDownload]);

                    if (buffer && Buffer.isBuffer(buffer)) {
                        await fs.promises.writeFile(fullPath, buffer);
                        mediaFileName = fileName;
                        mediaLocalPath = fullPath;

                        // Create compressed thumbnail fitting inside 2.30" x 1.70" (221x163px)
                        const thumbName = `thumb_${fileName}`;
                        const thumbPath = path.join(MEDIA_DIR, thumbName);
                        try {
                            await sharp(buffer)
                                .resize({
                                    width: 221,
                                    height: 163,
                                    fit: 'inside',
                                    withoutEnlargement: true,
                                })
                                .jpeg({ quality: 75 })
                                .toFile(thumbPath);
                            thumbnailPath = thumbPath;
                        } catch {
                            thumbnailPath = fullPath;
                        }

                        console.log(`🖼️ Saved group photo and generated compressed thumbnail: ${fileName} from [${groupName}]`);
                    }
                } catch (mediaErr: any) {
                    console.warn(`Could not download image: ${mediaErr.message}`);
                }
            }
        } else if (m.documentMessage) {
            mediaType = 'Document';
            mediaFileName = m.documentMessage.fileName || 'document';
        } else if (m.videoMessage) {
            mediaType = 'Video';
            // A frame of the video (or WhatsApp's own preview) so reports and Excel show what was filmed
            if (downloadMedia && waSocket) {
                try {
                    const sock = waSocket;
                    const thumbPath = path.join(MEDIA_DIR, `thumb_vid_${Date.now()}_${messageId.slice(0, 8)}.jpg`);
                    thumbnailPath = await makeVideoThumbnail(m.videoMessage, async () => {
                        const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), 30000));
                        const dl = downloadMediaMessage(msg, 'buffer', {}, {
                            logger: pino({ level: 'silent' }),
                            reuploadRequest: sock.updateMediaMessage,
                        });
                        const buf: any = await Promise.race([dl, timeout]);
                        return buf && Buffer.isBuffer(buf) ? buf : null;
                    }, thumbPath);
                    if (thumbnailPath) console.log(`🎬 Saved video thumbnail from [${groupName}]`);
                } catch (videoErr: any) {
                    console.warn(`Could not make video thumbnail: ${videoErr.message}`);
                }
            }
        } else if (m.audioMessage) {
            mediaType = 'Audio';
        } else if (m.stickerMessage) {
            mediaType = 'Sticker';
        } else if (m.locationMessage) {
            mediaType = 'Location';
        } else if (m.contactMessage) {
            mediaType = 'Contact';
        }

        const effectiveText = textContent || (mediaType !== 'Text' ? `(${mediaType})` : '');
        const quotedMessageId = config.enableQuotedMessageId ? extractQuotedMessageId(m) : undefined;

        if (effectiveText || mediaType !== 'Text') {
            console.log(`💬 Captured WhatsApp group message [${groupName}] from [${senderDisplay}]: ${effectiveText.slice(0, 50)}`);
            recordGroupActivity(groupName, tsSeconds * 1000);
            await appendWhatsAppMessage({
                messageId,
                timestamp: dateStr,
                timestampMs: tsSeconds * 1000,
                groupName,
                sender: senderDisplay,
                text: effectiveText,
                mediaType,
                mediaFileName,
                mediaLocalPath,
                thumbnailPath,
                quotedMessageId: quotedMessageId || undefined,
            });
            if (!isHrMessage(effectiveText)) mirrorWhatsAppMessage({
                messageId,
                groupJid: jid,
                groupName,
                sender: senderDisplay,
                senderId: msg.key.fromMe ? undefined : (msg.key.participant || undefined),
                text: effectiveText,
                mediaType,
                mediaFileName,
                quotedMessageId: quotedMessageId || undefined,
                rawTimestamp: dateStr,
                timestampMs: tsSeconds * 1000,
            });
        }
    } catch (err: any) {
        console.error('Error handling WhatsApp group message:', err.message);
    }
}

export async function startWhatsAppClient(): Promise<void> {
    // Ensure Excel file exists right away
    await ensureExcelInitialized();

    if (waSocket && currentStatus === 'connected') {
        console.log('WhatsApp client is already connected.');
        return;
    }

    setStatus('connecting');
    console.log('🟢 Initializing WhatsApp Read-Only Client (Baileys)...');

    // Clean up old socket if any
    try {
        if (waSocket) {
            waSocket.ev.removeAllListeners('connection.update');
            waSocket.ev.removeAllListeners('messages.upsert');
            waSocket.end(undefined);
            waSocket = null;
        }
    } catch {}

    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

    waSocket = makeWASocket({
        auth: state,
        printQRInTerminal: false,
        logger: pino({ level: 'silent' }), // Silent logs to prevent spam
        browser: ['amirClaw Reader', 'Desktop', '1.0.0'],
        syncFullHistory: false,
        markOnlineOnConnect: false,
        keepAliveIntervalMs: 25_000,
        defaultQueryTimeoutMs: 60_000,
        connectTimeoutMs: 60_000,
        getMessage: async () => undefined,
    });

    waSocket.ev.on('creds.update', saveCreds);

    // Connection state updates
    waSocket.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            currentQrText = qr;
            console.log('\n📱 WhatsApp QR Code generated! Scan it with your phone:\n');
            qrcodeTerminal.generate(qr, { small: true });

            // Generate image buffer for Telegram delivery
            try {
                currentQrBuffer = await QRCode.toBuffer(qr, { width: 350 });
            } catch (err) {
                console.error('Error rendering QR image buffer:', err);
            }
        }

        if (connection === 'close') {
            setStatus('disconnected');
            currentQrBuffer = null;
            const statusCode = (lastDisconnect?.error as any)?.output?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            loggedOut = !shouldReconnect;
            console.log(`❌ WhatsApp disconnected (code: ${statusCode}). Reconnecting: ${shouldReconnect}`);

            if (shouldReconnect) {
                setTimeout(() => startWhatsAppClient(), 4000);
            }
        } else if (connection === 'open') {
            setStatus('connected');
            loggedOut = false;
            currentQrBuffer = null;
            currentQrText = null;
            console.log('✅ WhatsApp connected successfully! Now listening to group messages in READ-ONLY mode.');
        }
    });

    // History sync listener
    waSocket.ev.on('messaging-history.set', ({ messages }) => {
        if (!messages || messages.length === 0) return;
        console.log(`📥 History sync: ${messages.length} messages (processing in background)`);
        // Process history non-blocking so real-time messages are not delayed
        setImmediate(async () => {
            for (const msg of messages) {
                await processGroupMessage(msg, false).catch(() => {});
            }
            await flushWhatsAppMessages().catch(() => {});
        });
    });

    // Message listener for real-time messages
    waSocket.ev.on('messages.upsert', async ({ messages, type }) => {
        console.log(`📨 Received ${messages.length} WhatsApp event(s) (type: ${type})`);
        for (const msg of messages) {
            await processGroupMessage(msg, true);
        }
        // Immediately flush any captured messages to Excel
        await flushWhatsAppMessages();
    });
}
