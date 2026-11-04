import fs from 'fs';
import path from 'path';
import { getSchedulerDb } from '../src/reports/scheduler.js';
import { slotAction, draftKey, saveDraft, getDraft, draftMessage, handleParkingDraftCallback } from '../src/reports/parking_drafts.js';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

const SCRATCH_DIR = path.resolve('workspace', 'test_scratch');
fs.mkdirSync(SCRATCH_DIR, { recursive: true });
const DB_PATH = path.join(SCRATCH_DIR, 'test_parking_drafts.db');
if (fs.existsSync(DB_PATH)) fs.unlinkSync(DB_PATH);
const db = getSchedulerDb(DB_PATH);

function fakeCtx() {
    const ctx: any = { from: { username: 'owner' }, answers: [] as string[], markups: [] as any[] };
    ctx.answerCallbackQuery = async (o: any) => { ctx.answers.push(o.text); };
    ctx.editMessageReplyMarkup = async (o: any) => { ctx.markups.push(o.reply_markup); };
    return ctx;
}

async function run() {
    console.log('[TEST: owner parking report at 10 / 12 / 14]');
    const ten = 10 * 60;
    const now = 1_000_000_000;
    assert(slotAction(9 * 60, ten, 0, now) === 'wait', '09:00 is too early for the 10:00 report');
    assert(slotAction(9 * 60 + 30, ten, 0, now) === 'poll', 'from 09:30 the parts are checked');
    assert(slotAction(9 * 60 + 40, ten, now - 60_000, now) === 'wait', 'checked again only every 5 minutes');
    assert(slotAction(9 * 60 + 45, ten, now - 5 * 60_000, now) === 'poll', '5 minutes later it checks again');
    assert(slotAction(ten, ten, now, now) === 'deadline', 'at 10:00 it is sent with whatever arrived');
    assert(slotAction(ten + 10, ten, now, now) === 'deadline', 'a restart at 10:10 still sends it');
    assert(slotAction(ten + 20, ten, 0, now) === 'wait', 'not after 10:15');
    assert(slotAction(ten + 30, ten, 0, now, true) === 'poll', 'after an incomplete report it keeps looking for the late parts');
    assert(slotAction(ten + 30, ten, now - 60_000, now, true) === 'wait', 'every 5 minutes');
    assert(slotAction(ten + 61, ten, 0, now, true) === 'wait', 'for up to an hour');

    assert(draftKey('04-11-2026', '10:00') === '04-11-2026_1000', 'draft key');
    const note = draftMessage({ success: true, text: 'REPORT', missing: ['Rumble Area'] });
    assert(note === 'REPORT\n\n⚠️ Not received yet: Rumble Area', 'the owner sees what is missing');
    assert(draftMessage({ success: true, text: 'REPORT', missing: [] }) === 'REPORT', 'no note when complete');

    console.log('\n[TEST: Send to Control Room button]');
    const key = draftKey('04-11-2026', '12:00');
    saveDraft(key, '🅿️ *MEYDAN PARKING* 12:00', db);
    const sent: { group: string; text: string }[] = [];
    const sender = async (group: string, text: string) => { sent.push({ group, text }); return 'Control Room Specialization Group'; };

    const ctx1 = fakeCtx();
    await handleParkingDraftCallback(ctx1, `pk:s:${key}`, sender, db);
    assert(sent.length === 1 && sent[0].text === '🅿️ *MEYDAN PARKING* 12:00', 'pressing Send posts the report text');
    assert(sent[0].group === 'Control Room Specialization', 'to the Control Room group');
    assert(!sent[0].text.includes('Not received yet'), 'the missing-parts note is not posted');
    assert(!!getDraft(key, db)?.sent_at, 'the draft is marked sent');
    assert(ctx1.markups.length === 1, 'the button changes to "Sent"');

    const ctx2 = fakeCtx();
    await handleParkingDraftCallback(ctx2, `pk:s:${key}`, sender, db);
    assert(sent.length === 1, 'a second press does not post it twice');
    assert(ctx2.answers[0].startsWith('Already sent'), 'the owner is told it was already sent');

    saveDraft(key, 'CHANGED', db);
    assert(getDraft(key, db)?.text === '🅿️ *MEYDAN PARKING* 12:00', 'a sent draft is never changed');

    const failKey = draftKey('04-11-2026', '14:00');
    saveDraft(failKey, 'REPORT 14', db);
    const ctx3 = fakeCtx();
    await handleParkingDraftCallback(ctx3, `pk:s:${failKey}`, async () => { throw new Error('WhatsApp not connected'); }, db);
    assert(ctx3.answers[0].includes('WhatsApp not connected'), 'a failed send is reported');
    assert(!getDraft(failKey, db)?.sent_at, 'and can be pressed again');

    assert(!(await handleParkingDraftCallback(fakeCtx(), 'cor:m:d:123', sender, db)), 'other buttons are not handled here');

    console.log('ALL PARKING DRAFT TESTS PASSED');
    setTimeout(() => process.exit(0), 50);
}

run().catch((err) => {
    console.error(err);
    process.exit(1);
});
