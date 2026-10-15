import { buildOverdueMessage } from '../src/reports/telegram_handlers.js';
import type { OverdueTicket } from '../src/reports/bridge.js';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

console.log('[TEST: overdue reminder message]');
const id = 'ACB3BEFFAF8B347207F64AECCDB2C645';
const mk = (i: number, report: OverdueTicket['report']): OverdueTicket => ({
    id: `${id.slice(0, 30)}${String(i).padStart(2, '0')}`, report, building: 'M', hours_open: 75.3,
    reported: '12-10-2026 06:12', text: 'M Building back side right side barrier is not opening',
});

const { text, keyboard } = buildOverdueMessage([mk(1, 'maintenance'), mk(2, 'fire_safety')]);
const buttons = keyboard.inline_keyboard.flat() as any[];
assert(text.includes('#1 🔧 Building M — 3.1 days — 12-10-2026 06:12'), 'maintenance line with building, age in days, reported time');
assert(text.includes('#2 🧯'), 'fire-safety tickets use the fire icon');
assert(buttons.length === 4, 'two buttons per ticket');
assert(buttons[0].callback_data === `cor:m:d:${mk(1, 'maintenance').id}`, '✅ saves a Done correction for maintenance');
assert(buttons[3].callback_data === `cor:f:x:${mk(2, 'fire_safety').id}`, '❌ on fire safety marks it not a ticket');
assert(buttons.every((b) => Buffer.byteLength(b.callback_data) <= 64), 'callback data within 64 bytes');

const many = buildOverdueMessage(Array.from({ length: 30 }, (_, i) => mk(i, 'maintenance')));
assert((many.keyboard.inline_keyboard.flat() as any[]).length === 50, 'capped at 25 tickets');
assert(many.text.includes('+5 more in the next reminder.'), 'mentions the tickets left for the next run');

assert(!/[\u0600-\u06FF]/.test(text + many.text), 'reminder text is English only');

console.log('ALL OVERDUE MESSAGE TESTS PASSED');
