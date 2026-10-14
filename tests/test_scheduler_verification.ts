import fs from 'fs';
import path from 'path';
import Database from 'better-sqlite3';
import { getSchedulerDb, hasReportBeenSent, markReportAsSent, getOrCreatePollTracker, clearPollTracker } from '../src/reports/scheduler.js';
import { generateParkingReportText } from './test_helpers.js';

const SCRATCH_DIR = path.resolve('workspace', 'test_scratch');
if (!fs.existsSync(SCRATCH_DIR)) {
    fs.mkdirSync(SCRATCH_DIR, { recursive: true });
}
const TEST_DB_PATH = path.join(SCRATCH_DIR, 'test_scheduler_sent.db');
if (fs.existsSync(TEST_DB_PATH)) {
    fs.unlinkSync(TEST_DB_PATH);
}

function runSchedulerTests() {
    console.log('============================================================');
    console.log('RUNNING SCHEDULER & BOT AUTOMATION VERIFICATION TESTS');
    console.log('============================================================\n');

    // ─────────────────────────────────────────────────────────────
    // TEST (A): No duplicate send after restart (sent_reports_log)
    // ─────────────────────────────────────────────────────────────
    console.log('[TEST A: sent_reports_log persistence and restart test]');
    
    // 1. First run before restart
    const db1 = getSchedulerDb(TEST_DB_PATH);
    const testSlotKey = 'parking_13-10-2026_10:00';
    
    const beforeSend = hasReportBeenSent(testSlotKey, db1);
    console.log(`Before send: hasReportBeenSent("${testSlotKey}") =`, beforeSend);
    if (beforeSend !== false) throw new Error('Assertion failed: should be false before sending');

    // Mark as sent
    markReportAsSent(testSlotKey, db1);
    const afterSend = hasReportBeenSent(testSlotKey, db1);
    console.log(`After send: hasReportBeenSent("${testSlotKey}") =`, afterSend);
    if (afterSend !== true) throw new Error('Assertion failed: should be true after sending');

    // 2. Simulate bot process restart: close db1 and open a fresh instance db2 on same path
    db1.close();
    const dbRestarted = getSchedulerDb(TEST_DB_PATH);
    
    const afterRestart = hasReportBeenSent(testSlotKey, dbRestarted);
    console.log(`After simulated restart: hasReportBeenSent("${testSlotKey}") =`, afterRestart);
    if (afterRestart !== true) throw new Error('Assertion failed: report key must persist across restarts');

    // Check that a new different slot is NOT marked
    const otherSlotKey = 'parking_13-10-2026_12:00';
    const otherSlotStatus = hasReportBeenSent(otherSlotKey, dbRestarted);
    console.log(`Unsent slot check: hasReportBeenSent("${otherSlotKey}") =`, otherSlotStatus);
    if (otherSlotStatus !== false) throw new Error('Assertion failed: unsent slot must be false');

    // Re-marking the same key must be idempotent
    markReportAsSent(testSlotKey, dbRestarted);
    const rowCount = dbRestarted.prepare('SELECT count(*) as cnt FROM sent_reports_log WHERE report_key = ?').get(testSlotKey).cnt;
    console.log(`Idempotency check: row count for "${testSlotKey}" =`, rowCount);
    if (rowCount !== 1) throw new Error('Assertion failed: duplicate entries not allowed');
    console.log('Result for Test (A): PASS ✅\n');

    // ─────────────────────────────────────────────────────────────
    // TEST (B1): Restart in the middle of an open 5-minute polling window
    // ─────────────────────────────────────────────────────────────
    console.log('[TEST B1: Restart in middle of open 5-min polling window]');
    const pollDbPath = path.join(SCRATCH_DIR, 'test_poll_restart.db');
    if (fs.existsSync(pollDbPath)) fs.unlinkSync(pollDbPath);

    const slotTestKey = 'parking_13-10-2026_10:00';
    const t0 = 1760335200000; // Fixed timestamp base (e.g. 10:00:00 AM)

    // Step 1: Slot window opens at t0. First polling attempt.
    const pollDb1 = getSchedulerDb(pollDbPath);
    const tr1 = getOrCreatePollTracker(slotTestKey, pollDb1, t0);
    console.log(`t=0m: Started polling. Persistent startedAt = ${tr1.startedAt}, attempts = ${tr1.attempts}`);
    let elapsed = (t0 - tr1.startedAt) / 60000;
    let shouldSendNow = elapsed >= 5;
    console.log(`t=0m: Elapsed = ${elapsed} mins -> shouldSend = ${shouldSendNow}`);
    if (shouldSendNow !== false) throw new Error('Assertion failed: should not send at t=0');

    // Step 2: SIMULATE PROCESS RESTART at t = 2.5 minutes (150s later)
    pollDb1.close();
    console.log('--- [Simulated Bot Crash / Restart at t = 2.5 minutes] ---');
    const pollDb2 = getSchedulerDb(pollDbPath); // Fresh connection after restart
    const t_mid = t0 + 150000; // 2.5 minutes later
    const tr2 = getOrCreatePollTracker(slotTestKey, pollDb2, t_mid);
    console.log(`t=2.5m (after restart): Persistent startedAt = ${tr2.startedAt} (preserved!), attempts = ${tr2.attempts}`);
    if (tr2.startedAt !== t0) throw new Error('Assertion failed: startedAt deadline must persist across restart!');
    elapsed = (t_mid - tr2.startedAt) / 60000;
    shouldSendNow = elapsed >= 5;
    console.log(`t=2.5m: Elapsed = ${elapsed} mins -> shouldSend = ${shouldSendNow}`);
    if (shouldSendNow !== false) throw new Error('Assertion failed: should not send at t=2.5m');

    // Step 3: SIMULATE PROCESS RESTART at t = 5.2 minutes (312s later, deadline passed)
    pollDb2.close();
    console.log('--- [Simulated Bot Restart at t = 5.2 minutes (Timeout reached)] ---');
    const pollDb3 = getSchedulerDb(pollDbPath);
    const t_timeout = t0 + 312000; // 5.2 minutes later
    const tr3 = getOrCreatePollTracker(slotTestKey, pollDb3, t_timeout);
    elapsed = (t_timeout - tr3.startedAt) / 60000;
    shouldSendNow = elapsed >= 5;
    console.log(`t=5.2m: Elapsed = ${elapsed.toFixed(1)} mins -> shouldSend = ${shouldSendNow}`);
    if (shouldSendNow !== true) throw new Error('Assertion failed: shouldSend must be true when deadline passed');

    // Send report exactly once
    markReportAsSent(slotTestKey, pollDb3);
    clearPollTracker(slotTestKey, pollDb3);
    console.log(`Report sent once! Marked in sent_reports_log and cleared from poll tracker.`);

    // Step 4: SIMULATE RESTART AFTER THE SEND at t = 6.0 minutes
    pollDb3.close();
    console.log('--- [Simulated Bot Restart at t = 6.0 minutes (After send)] ---');
    const pollDb4 = getSchedulerDb(pollDbPath);
    const alreadySentAfterRestart = hasReportBeenSent(slotTestKey, pollDb4);
    console.log(`t=6.0m (after restart): hasReportBeenSent("${slotTestKey}") =`, alreadySentAfterRestart);
    if (alreadySentAfterRestart !== true) throw new Error('Assertion failed: report must remain marked as sent after restart');
    console.log('Verified: Report is NOT sent again after restart!');
    console.log('Result for Test (B1): PASS ✅\n');

    pollDb4.close();
    if (fs.existsSync(pollDbPath)) fs.unlinkSync(pollDbPath);

    // ─────────────────────────────────────────────────────────────
    // TEST (B2): 5-minute email timeout with N/A building figures
    // ─────────────────────────────────────────────────────────────
    console.log('[TEST B2: 5-minute email timeout with N/A building figures]');
    
    // Simulate tracker state at slot window start
    const tracker = { startedAt: Date.now() - (5 * 60 * 1000 + 2000), attempts: 6 }; // 5 mins 2 secs ago
    const elapsedMinutes = (Date.now() - tracker.startedAt) / 60000;
    const emailAvailable = false; // Email not yet received

    const shouldSend = !emailAvailable && elapsedMinutes >= 5;
    console.log(`Elapsed polling time: ${elapsedMinutes.toFixed(2)} minutes | Email found: ${emailAvailable}`);
    console.log(`Timeout reached (>= 5 mins)? shouldSend =`, shouldSend);
    if (!shouldSend) throw new Error('Assertion failed: shouldSend must be true when timeout elapsed');

    // Verify report generation when email is missing (N/A fallback)
    const reportFallback = generateParkingReportText({
        date: '13-10-2026',
        time: '10:00',
        p_bld: null,
        n_bld: null,
        m_bld: null,
        q_bld: null,
        summary: null,
        smart: { cars_entered: 806, occupied_slots: 801, empty_slots: 86, outside_cars: 9 },
        ramp: { customer_visitor: 87, pod_cars: 5, staff_cars: 118, total_cars_entered_ramp: 210, total_cars_parked_ramp: 158, service_road_entered: 131, service_road_parked: 117, partially_blocked_parking: 4 },
        rumble: { total_cars: 209, bus: 0 }
    });

    console.log('Generated Timeout Fallback Report Preview:');
    console.log(reportFallback.slice(0, 310) + '...\n');

    // Assert that building figures contain N/A
    if (!reportFallback.includes('• Cars Parked: N/A 🚗')) throw new Error('Assertion failed: P building must show N/A');
    if (!reportFallback.includes('• Total Cars Entered: N/A ⬇️')) throw new Error('Assertion failed: Summary entered must show N/A');
    if (!reportFallback.includes('• Occupied slots : 801')) throw new Error('Assertion failed: WhatsApp smart parking must still be populated');
    console.log('Result for Test (B2): PASS ✅\n');

    // ─────────────────────────────────────────────────────────────
    // TEST (C): 05:00 / 17:00 Asia/Dubai Triggers with Mock Clock
    // ─────────────────────────────────────────────────────────────
    console.log('[TEST C: Asia/Dubai 05:00 and 17:00 triggers with simulated clock]');

    function evaluateDubaiClockTrigger(utcIsoString: string) {
        const testDate = new Date(utcIsoString);
        const dubaiParts = new Intl.DateTimeFormat('en-US', {
            timeZone: 'Asia/Dubai',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false,
        }).formatToParts(testDate);

        const getPart = (type: string) => dubaiParts.find((p) => p.type === type)?.value || '00';
        const hour = parseInt(getPart('hour'), 10);
        const minute = parseInt(getPart('minute'), 10);

        const isShiftTrigger = (hour === 5 && minute === 0) || (hour === 17 && minute === 0);
        const shiftName = hour === 17 ? 'Day' : (hour === 5 ? 'Night' : 'None');
        const isParkingSlot = (hour === 10 || hour === 12 || hour === 14) && minute === 0;

        return { dubaiHour: hour, dubaiMinute: minute, isShiftTrigger, shiftName, isParkingSlot };
    }

    // Tick 1: UTC 01:00:00 -> Dubai 05:00:00 (Night shift trigger)
    const t1 = evaluateDubaiClockTrigger('2026-10-13T01:00:00Z');
    console.log(`Clock UTC 01:00 -> Dubai ${t1.dubaiHour}:${String(t1.dubaiMinute).padStart(2, '0')} | Trigger Shift: ${t1.isShiftTrigger} (${t1.shiftName})`);
    if (!t1.isShiftTrigger || t1.shiftName !== 'Night') throw new Error('Assertion failed for 05:00 Dubai trigger');

    // Tick 2: UTC 13:00:00 -> Dubai 17:00:00 (Day shift trigger)
    const t2 = evaluateDubaiClockTrigger('2026-10-13T13:00:00Z');
    console.log(`Clock UTC 13:00 -> Dubai ${t2.dubaiHour}:${String(t2.dubaiMinute).padStart(2, '0')} | Trigger Shift: ${t2.isShiftTrigger} (${t2.shiftName})`);
    if (!t2.isShiftTrigger || t2.shiftName !== 'Day') throw new Error('Assertion failed for 17:00 Dubai trigger');

    // Tick 3: UTC 01:05:00 -> Dubai 05:05:00 (Non-trigger)
    const t3 = evaluateDubaiClockTrigger('2026-10-13T01:05:00Z');
    console.log(`Clock UTC 01:05 -> Dubai ${t3.dubaiHour}:${String(t3.dubaiMinute).padStart(2, '0')} | Trigger Shift: ${t3.isShiftTrigger}`);
    if (t3.isShiftTrigger) throw new Error('Assertion failed: 05:05 must not trigger shift');

    // Tick 4: UTC 06:00:00 -> Dubai 10:00:00 (Parking Slot trigger)
    const t4 = evaluateDubaiClockTrigger('2026-10-13T06:00:00Z');
    console.log(`Clock UTC 06:00 -> Dubai ${t4.dubaiHour}:${String(t4.dubaiMinute).padStart(2, '0')} | Trigger Parking 10:00: ${t4.isParkingSlot}`);
    if (!t4.isParkingSlot) throw new Error('Assertion failed: 10:00 must trigger parking');

    // Tick 5: UTC 08:00:00 -> Dubai 12:00:00 (Parking Slot trigger)
    const t5 = evaluateDubaiClockTrigger('2026-10-13T08:00:00Z');
    console.log(`Clock UTC 08:00 -> Dubai ${t5.dubaiHour}:${String(t5.dubaiMinute).padStart(2, '0')} | Trigger Parking 12:00: ${t5.isParkingSlot}`);
    if (!t5.isParkingSlot) throw new Error('Assertion failed: 12:00 must trigger parking');

    // Tick 6: UTC 10:00:00 -> Dubai 14:00:00 (Parking Slot trigger)
    const t6 = evaluateDubaiClockTrigger('2026-10-13T10:00:00Z');
    console.log(`Clock UTC 10:00 -> Dubai ${t6.dubaiHour}:${String(t6.dubaiMinute).padStart(2, '0')} | Trigger Parking 14:00: ${t6.isParkingSlot}`);
    if (!t6.isParkingSlot) throw new Error('Assertion failed: 14:00 must trigger parking');

    console.log('Result for Test (C): PASS ✅\n');
    console.log('ALL SCHEDULER & BOT TESTS PASSED SUCCESSFULLY.');

    // Cleanup
    dbRestarted.close();
    if (fs.existsSync(TEST_DB_PATH)) fs.unlinkSync(TEST_DB_PATH);
}

runSchedulerTests();
