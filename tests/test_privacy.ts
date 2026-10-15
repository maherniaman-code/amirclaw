import { isHrMessage } from '../src/privacy.js';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

console.log('[TEST: HR / leave messages stay on the server]');
assert(isHrMessage('Leave Details : Full name : Manoj Staff Id : 242580 Start Date : 27 February 2027 Total Days :60'), 'leave request detected');
assert(isHrMessage('Name: X Emp ID:-201775 Start Date:-30/10/2027'), 'variant wording detected');
assert(!isHrMessage('Q building first floor Speed Gate is out of service'), 'fault report is not HR');
assert(!isHrMessage(''), 'empty text is not HR');
console.log('ALL PRIVACY TESTS PASSED');
