import fs from 'fs';
import path from 'path';

// Owner requirement: everything the bot sends for reports is English (Arabic only in conversation).
// Arabic is allowed only where the bot READS Arabic input (media-type names in excel.ts).
function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

console.log('[TEST: report-facing TypeScript strings are English]');
const ARABIC = /[؀-ۿ]/;
const files = ['src/reports/telegram_handlers.ts', 'src/reports/scheduler.ts', 'src/monitoring/alerts.ts', 'src/reports/bridge.ts'];
for (const f of files) {
    const lines = fs.readFileSync(path.resolve(f), 'utf8').split('\n');
    const bad = lines.map((l, i) => [i + 1, l] as const).filter(([, l]) => ARABIC.test(l) && !l.trim().startsWith('//'));
    assert(bad.length === 0, `${f} has no Arabic text${bad.length ? ': line ' + bad.map(([n]) => n).join(', ') : ''}`);
}
const excel = fs.readFileSync(path.resolve('src/whatsapp/excel.ts'), 'utf8').split('\n');
const excelBad = excel.filter((l) => ARABIC.test(l) && !/t\.includes\('[^']+'\) \|\| t\.includes\(/.test(l) && !l.trim().startsWith('//'));
assert(excelBad.length === 0, 'excel.ts only uses Arabic to recognise media types');
console.log('ALL ENGLISH-ONLY TESTS PASSED');
