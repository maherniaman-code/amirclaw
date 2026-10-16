import { execFile } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import sharp from 'sharp';

// Kept sharper than the 221x163 Excel box: phone videos are upright, and a frame squeezed
// into that box was a blur 91 px wide. The Excel files scale it down to fit the cell.
const THUMB_MAX = 480;
// Larger videos keep WhatsApp's own preview: downloading them is slow on a small server
export const VIDEO_FRAME_MAX_BYTES = 25 * 1024 * 1024;

let ffmpegChecked: Promise<boolean> | null = null;

/** ffmpeg on PATH? Checked once; without it videos still get WhatsApp's built-in preview. */
export function hasFfmpeg(): Promise<boolean> {
    if (!ffmpegChecked) {
        ffmpegChecked = new Promise((resolve) => {
            execFile('ffmpeg', ['-version'], { timeout: 5000 }, (err) => resolve(!err));
        });
    }
    return ffmpegChecked;
}

/** One frame (about 1 s in, or the first frame of a shorter clip) of a video file as JPEG. */
export function grabFrame(videoPath: string, framePath: string): Promise<boolean> {
    const run = (seek: string) => new Promise<boolean>((resolve) => {
        execFile('ffmpeg', ['-y', '-loglevel', 'error', '-ss', seek, '-i', videoPath, '-frames:v', '1', '-q:v', '3', framePath],
            { timeout: 20000 }, (err) => resolve(!err && fs.existsSync(framePath) && fs.statSync(framePath).size > 0));
    });
    return run('1').then((ok) => ok || run('0'));
}

async function writeThumb(image: Buffer | string, thumbPath: string): Promise<boolean> {
    try {
        await sharp(image).resize({ width: THUMB_MAX, height: THUMB_MAX, fit: 'inside', withoutEnlargement: true })
            .jpeg({ quality: 82 }).toFile(thumbPath);
        return true;
    } catch {
        return false;
    }
}

export interface VideoInfo {
    jpegThumbnail?: Uint8Array | null;
    fileLength?: number | { toNumber(): number } | null;
}

/**
 * Thumbnail for a WhatsApp video: a real frame when ffmpeg is installed and the video can be
 * downloaded, else the small preview WhatsApp sends inside the message. The video itself is not kept.
 * Returns the thumbnail path, or undefined when neither is available.
 */
export async function makeVideoThumbnail(
    video: VideoInfo,
    download: () => Promise<Buffer | null>,
    thumbPath: string,
): Promise<string | undefined> {
    const size = typeof video.fileLength === 'number' ? video.fileLength
        : video.fileLength && typeof video.fileLength.toNumber === 'function' ? video.fileLength.toNumber() : 0;
    let reason = '';
    if (!(await hasFfmpeg())) reason = 'ffmpeg not found';
    else if (size > VIDEO_FRAME_MAX_BYTES) reason = `video larger than ${VIDEO_FRAME_MAX_BYTES / 1024 / 1024} MB`;
    else {
        const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-video-'));
        try {
            const buffer = await download();
            if (buffer && buffer.length) {
                const videoPath = path.join(tmp, 'video.mp4');
                const framePath = path.join(tmp, 'frame.jpg');
                fs.writeFileSync(videoPath, buffer);
                if (await grabFrame(videoPath, framePath) && await writeThumb(framePath, thumbPath)) return thumbPath;
                reason = 'ffmpeg could not read a frame';
            } else {
                reason = 'download failed or timed out';
            }
        } catch (err: any) {
            reason = `error: ${err?.message || err}`;
        } finally {
            fs.rmSync(tmp, { recursive: true, force: true });
        }
    }
    // WhatsApp's own preview is small and blurry: say why the real frame was not used
    console.warn(`🎬 No video frame (${reason}): using WhatsApp's small preview`);
    if (video.jpegThumbnail && video.jpegThumbnail.length && await writeThumb(Buffer.from(video.jpegThumbnail), thumbPath)) {
        return thumbPath;
    }
    return undefined;
}
