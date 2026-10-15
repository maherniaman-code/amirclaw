import { VideoBackfill, backfillSummary, reportGroupsFromRules, PAGE_SIZE, MAX_ATTEMPTS, type BackfillDeps, type HistoryKey } from '../src/whatsapp/video_backfill.js';
import { readFileSync } from 'fs';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

const FM = 'Meydan FM Team💪', FM_JID = 'fm@g.us';
const SEC = 'Meydan - Security Team', SEC_JID = 'sec@g.us';
const T0 = Date.UTC(2026, 9, 15, 16, 0) / 1000; // seconds

// A fake chat history: one message per minute going back; some are the videos with no picture
function chat(jid: string, n: number, videoAt: Set<number>) {
    return Array.from({ length: n }, (_, i) => ({
        key: { remoteJid: jid, id: `${jid}-${i}`, fromMe: false },
        messageTimestamp: T0 - i * 60,
        message: videoAt.has(i) ? { videoMessage: { jpegThumbnail: new Uint8Array([1, 2, 3]) } } : { conversation: 'text' },
    }));
}

async function main() {
    console.log('[TEST: old video pictures from WhatsApp history]');
    const fm = chat(FM_JID, 200, new Set([10, 120]));   // second video needs a 3rd page
    const sec = chat(SEC_JID, 30, new Set([5]));
    const missing = [
        { messageId: `${FM_JID}-10`, groupName: FM, tsMs: (T0 - 600) * 1000 },
        { messageId: `${FM_JID}-120`, groupName: FM, tsMs: (T0 - 7200) * 1000 },
        { messageId: `${SEC_JID}-5`, groupName: SEC, tsMs: (T0 - 300) * 1000 },
        { messageId: 'other-1', groupName: 'Group the bot left', tsMs: T0 * 1000 },
    ];
    const saved = new Map<string, Buffer>();
    const requests: HistoryKey[] = [];
    let backfill: VideoBackfill;
    const deps: BackfillDeps = {
        missing: () => missing.filter((m) => !saved.has(m.messageId)),
        groupJid: (name) => ({ [FM]: FM_JID, [SEC]: SEC_JID } as Record<string, string>)[name],
        anchor: (name) => (name === FM ? { id: `${FM_JID}-0`, tsMs: T0 * 1000, fromMe: false }
            : name === SEC ? { id: `${SEC_JID}-0`, tsMs: T0 * 1000, fromMe: false } : null),
        // WhatsApp answers with the PAGE_SIZE messages older than the key
        fetchHistory: async (count, key) => {
            requests.push(key);
            const all = key.remoteJid === FM_JID ? fm : sec;
            const from = all.findIndex((m) => m.key.id === key.id) + 1;
            await backfill.onHistory(all.slice(from, from + count));
        },
        makeThumb: async (msg) => Buffer.from(msg.message.videoMessage.jpegThumbnail),
        saveThumb: (id, jpeg) => { if (saved.has(id)) return false; saved.set(id, jpeg); return true; },
        notify: async () => {},
        wait: async () => {},
    };
    backfill = new VideoBackfill(deps);
    const r = await backfill.run();

    assert(r.total === 4 && r.recovered === 3 && r.left === 1, 'three videos recovered, the one in a group the bot left is reported');
    assert(saved.has(`${FM_JID}-120`), 'pages back until the oldest missing video (3rd page)');
    assert(requests.filter((k) => k.remoteJid === FM_JID).length === 3, 'stops paging once every video of the group is found');
    assert(requests.filter((k) => k.remoteJid === SEC_JID).length === 1, 'one request is enough for the short group');
    assert(requests.every((k) => typeof k.id === 'string' && k.fromMe === false), 'requests use the message keys WhatsApp needs');
    assert(PAGE_SIZE === 50, 'pages of 50 messages');

    // WhatsApp does not answer (phone offline): no endless loop, nothing saved
    const silent = new VideoBackfill({ ...deps, missing: () => [missing[0]], fetchHistory: async () => {} });
    const s = await silent.run();
    assert(s.recovered === 0 && s.left === 1, 'no answer from WhatsApp leaves the video missing without looping');

    // History recovery: report groups go back to the target date even with no missing video
    const requests2: HistoryKey[] = [];
    let hb: VideoBackfill;
    const untilTsMs = (T0 - 130 * 60) * 1000;  // 130 minutes back = 3 pages of 50 one-minute messages
    hb = new VideoBackfill({
        ...deps,
        missing: () => [],
        fetchHistory: async (count, key) => {
            requests2.push(key);
            const all = key.remoteJid === FM_JID ? fm : sec;
            const from = all.findIndex((m) => m.key.id === key.id) + 1;
            await hb.onHistory(all.slice(from, from + count));
        },
    }, { groups: [FM, SEC, 'Group the bot left'], untilTsMs });
    const h = await hb.run();
    assert(requests2.filter((k) => k.remoteJid === FM_JID).length === 3, 'pages a report group back to the target date');
    assert(h.reached.includes(FM), 'group with enough history reaches the date');
    assert(h.stopped.includes(SEC) && h.stopped.includes('Group the bot left'), 'short group and missing group are reported as stopped');
    assert(h.received === 150 + 29, 'counts every message WhatsApp sent back');

    const groups = reportGroupsFromRules(readFileSync('config/meydan_rules.yaml', 'utf8'));
    assert(groups.includes('Meydan FM Team💪') && groups.includes('Parking Team at Meydan') && groups.length === 6,
        'report groups read from config/meydan_rules.yaml');

    const summary = backfillSummary({ total: 2, recovered: 2, left: 0, added: 340, reached: [FM], stopped: [SEC] }, 1, '17/07/2026');
    assert(summary.includes('Old messages recovered: 340') && summary.includes('back to 17/07/2026') && summary.includes(SEC),
        'summary tells how many old messages came back and where WhatsApp stopped');
    assert(backfillSummary({ total: 4, recovered: 3, left: 1 }, 1).includes('try again on the next restart'), 'summary says it will retry');
    assert(backfillSummary({ total: 4, recovered: 3, left: 1 }, MAX_ATTEMPTS).includes('could not be recovered'), 'last attempt says what is lost');
    assert(!/[؀-ۿ]/.test(backfillSummary({ total: 2, recovered: 2, left: 0 }, 1)), 'summary is English only');

    // Store helpers on a real SQLite file
    const os = await import('os'); const fs = await import('fs'); const path = await import('path');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'backfill-store-'));
    process.env.MESSAGES_DB_PATH = path.join(dir, 'm.db');
    const store = await import('../src/whatsapp/store.js');
    const base = { timestamp: 't', groupName: FM, sender: 'Peace (1)', quotedMessageId: '' };
    await store.storeWhatsAppMessage({ ...base, messageId: 'V1', text: 'Q bldg water leakage', mediaBadge: '🎬 Video', timestampMs: 1000 });
    await store.storeWhatsAppMessage({ ...base, messageId: 'T1', text: 'Noted', mediaBadge: '💬 Text', timestampMs: 2000 });
    await store.storeWhatsAppMessage({ ...base, messageId: 'B1', text: 'x', mediaBadge: '💬 Text', timestampMs: 3000, sender: 'Me (Bot Account)' });
    assert(store.missingVideoThumbs().map((v) => v.messageId).join() === 'V1', 'finds the video without a picture');
    assert(JSON.stringify(store.newestMessageInGroup(FM)) === JSON.stringify({ id: 'B1', tsMs: 3000, fromMe: true }), 'newest message of the group is the starting point');
    assert(store.setThumbnail('V1', Buffer.from([9])) && !store.setThumbnail('V1', Buffer.from([8])), 'saves once, never overwrites');
    assert(store.missingVideoThumbs().length === 0, 'video no longer missing');
    store.setMeta('k', 'v');
    assert(store.getMeta('k') === 'v' && store.getMeta('none') === null, 'meta flags for retries');
    fs.rmSync(dir, { recursive: true, force: true });

    console.log('ALL VIDEO BACKFILL TESTS PASSED');
}

main().catch((e) => { console.error(e); process.exit(1); });
