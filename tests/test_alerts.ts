import { shouldAlertSilence, alertOwner } from '../src/monitoring/alerts.js';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

async function run() {
    console.log('[TEST: owner alerts]');
    const H = 3_600_000;
    const now = Date.UTC(2026, 9, 15, 10, 0); // 14:00 Dubai
    const started = now - 10 * H;

    // Silence rule (defaults: 3h, watched 08:00-22:00 Dubai)
    assert(shouldAlertSilence(now - 4 * H, now, started, 14), '4h without messages at 14:00 -> alert');
    assert(!shouldAlertSilence(now - 2 * H, now, started, 14), '2h without messages -> no alert');
    assert(!shouldAlertSilence(now - 6 * H, now, started, 3), 'night hours are not watched');
    assert(!shouldAlertSilence(0, now, now - 1 * H, 14), 'fresh restart gets time to catch up');

    // Cooldown: same key is sent once per hour (no bot attached -> only logs, returns quietly)
    const errors: string[] = [];
    const orig = console.error;
    console.error = (...a: any[]) => { errors.push(a.join(' ')); };
    await alertOwner('test-key', 'first');
    await alertOwner('test-key', 'second (suppressed)');
    await alertOwner('test-key', 'third (forced)', { force: true });
    console.error = orig;
    assert(errors.filter((e) => e.includes('[alert:test-key]')).length === 2, 'cooldown suppresses repeats, force bypasses it');

    console.log('ALL ALERT TESTS PASSED');
}

run().catch((e) => { console.error(e); process.exit(1); });
