import { buildCorrectionKeyboard } from '../src/reports/telegram_handlers.js';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

console.log('[TEST: Telegram correction buttons]');
const id = 'ACB3BEFFAF8B347207F64AECCDB2C645'; // real 32-char WhatsApp message id
const items = Array.from({ length: 30 }, (_, i) => ({ no: i + 1, id }));

const kb = buildCorrectionKeyboard('fire_safety', items)!;
const rows = kb.inline_keyboard.filter((r) => r.length > 0);
const buttons = rows.flat() as any[];

assert(rows.length === 25, 'capped at 25 items (4 buttons each, Telegram max 100)');
assert(buttons.length === 100, '100 buttons');
assert(buttons.every((b) => Buffer.byteLength(b.callback_data) <= 64), 'every callback_data fits Telegram 64-byte limit');
assert(buttons[0].callback_data === `cor:f:d:${id}`, 'callback format cor:<report>:<action>:<message id>');
assert(rows[0].map((b: any) => b.text).join(' ') === '#1 ✅ #1 ↩️ #1 ❌ #1 🔁', 'button labels use report numbers');
assert(buildCorrectionKeyboard('violations', items) === null, 'no buttons for unsupported reports');
assert(buildCorrectionKeyboard('maintenance', []) === null, 'no buttons when the report has no items');

console.log('ALL CORRECTION BUTTON TESTS PASSED');
