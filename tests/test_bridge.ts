import { runReportEngine } from '../src/reports/bridge.js';

async function testBridge() {
    console.log('Testing TypeScript -> Python Bridge...');
    const parkingRes = await runReportEngine('parking', { slot: '14:00' });
    console.log('Parking report success:', parkingRes.success);
    console.log('Has text:', !!parkingRes.text);
    console.log('Excel path:', parkingRes.excelPath);
    console.log('Diagnostics:', parkingRes.diagnostics);

    const opsRes = await runReportEngine('operations');
    console.log('Operations report success:', opsRes.success);
    console.log('Operations total:', opsRes.total);
    console.log('Operations completed:', opsRes.completed);

    if (parkingRes.success && opsRes.success) {
        console.log('✅ BRIDGE TEST PASSED PERFECTLY!');
    } else {
        console.error('❌ BRIDGE TEST FAILED');
        process.exit(1);
    }
}

testBridge().catch(console.error);
