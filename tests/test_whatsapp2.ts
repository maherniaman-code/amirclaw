import { groupAllowed, normalizeGroupName } from '../src/whatsapp/client.js';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

console.log('[TEST: second WhatsApp number records only its groups]');
// Owner, 02-11: "control Room Specialization" and "podium Parking Access"
const allowed = ['control Room Specialization', 'podium Parking Access'].map(normalizeGroupName);

assert(groupAllowed('Control Room Specialization', allowed), 'exact group, other capitals');
assert(groupAllowed('Podium Parking Access 🚗', allowed), 'group name with an emoji');
assert(groupAllowed('Podium  Parking-Access', allowed), 'extra spaces and a dash');
assert(!groupAllowed('Meydan FM Team💪', allowed), 'other groups are ignored');
assert(!groupAllowed('Parking Team at Meydan', allowed), 'a similar parking group is ignored');
assert(groupAllowed('Meydan FM Team💪', null), 'the main number (no list) records every group');

console.log('ALL SECOND WHATSAPP TESTS PASSED');
setTimeout(() => process.exit(0), 50);
