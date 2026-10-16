import { execFileSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import sharp from 'sharp';
import { makeVideoThumbnail, hasFfmpeg, VIDEO_FRAME_MAX_BYTES } from '../src/whatsapp/video_thumb.js';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

async function main() {
    console.log('[TEST: video thumbnails]');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'video-thumb-test-'));
    try {
        // WhatsApp's embedded preview: a small JPEG inside the message
        const preview = await sharp({ create: { width: 96, height: 54, channels: 3, background: { r: 200, g: 30, b: 30 } } })
            .jpeg().toBuffer();
        const tooBig = { jpegThumbnail: preview, fileLength: VIDEO_FRAME_MAX_BYTES + 1 };
        let downloads = 0;
        const t1 = await makeVideoThumbnail(tooBig, async () => { downloads++; return null; }, path.join(dir, 't1.jpg'));
        assert(t1 !== undefined && fs.existsSync(t1), 'large video uses the preview inside the message');
        assert(downloads === 0, 'large video is not downloaded');

        const nothing = await makeVideoThumbnail({ fileLength: VIDEO_FRAME_MAX_BYTES + 1 }, async () => null, path.join(dir, 't2.jpg'));
        assert(nothing === undefined, 'no preview and no download gives no thumbnail (message still saved)');

        const failed = await makeVideoThumbnail({ jpegThumbnail: preview, fileLength: 1000 }, async () => null, path.join(dir, 't3.jpg'));
        assert(failed !== undefined && fs.existsSync(failed), 'failed download falls back to the preview');

        if (await hasFfmpeg()) {
            const video = path.join(dir, 'clip.mp4');
            execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=duration=2:size=640x360:rate=10',
                '-pix_fmt', 'yuv420p', video]);
            const buf = fs.readFileSync(video);
            const t4 = await makeVideoThumbnail({ jpegThumbnail: preview, fileLength: buf.length }, async () => buf, path.join(dir, 't4.jpg'));
            const meta = await sharp(t4!).metadata();
            assert(meta.width === 480 && meta.height === 270, 'real frame from the video, kept sharp (480 px)');
            const leftovers = fs.readdirSync(os.tmpdir()).filter((f) => f.startsWith('wa-video-'));
            assert(leftovers.length === 0, 'downloaded video is deleted after the frame is taken');
        } else {
            console.log('- ffmpeg not installed: frame extraction not tested here');
        }
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
    console.log('ALL VIDEO THUMBNAIL TESTS PASSED');
}

main().catch((e) => { console.error(e); process.exit(1); });
