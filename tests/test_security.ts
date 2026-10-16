import path from 'path';
import {
    allowedWorkspacePath, assertPublicUrl, isPrivateAddress, requestPythonApproval, safeChildEnv, safeUploadName,
    takePythonApproval,
} from '../src/security.js';
import { executePythonTool } from '../src/tools/execute_python.js';
import { readFileTool } from '../src/tools/read_file.js';

function assert(cond: boolean, msg: string) {
    if (!cond) throw new Error(`Assertion failed: ${msg}`);
    console.log(`✓ ${msg}`);
}

async function rejects(p: Promise<unknown>): Promise<boolean> {
    try { await p; return false; } catch { return true; }
}

async function main() {
    console.log('[TEST: security guards]');

    // Python never runs straight from the assistant: it waits for the owner's tap
    const asked: any = await executePythonTool.execute({ code: 'print(1)' }, { userId: 42 });
    assert(asked.success === false && typeof asked.pendingApproval === 'string', 'assistant Python waits for approval');
    const noChat: any = await executePythonTool.execute({ code: 'print(1)' });
    assert(noChat.success === false && !noChat.pendingApproval, 'no Python without a Telegram user');
    assert(takePythonApproval(asked.pendingApproval, 7) === null, 'another user cannot run it');
    assert(takePythonApproval(asked.pendingApproval, 42) === 'print(1)', 'the same user can run it');
    assert(takePythonApproval(asked.pendingApproval, 42) === null, 'and only once');
    const old = requestPythonApproval('print(2)', 42, 0);
    assert(takePythonApproval(old, 42, 11 * 60 * 1000) === null, 'approval expires after 10 minutes');

    const env = safeChildEnv({ PATH: '/usr/bin', HOME: '/home/u', TELEGRAM_BOT_TOKEN: 't', GROQ_API_KEY: 'k', IMAP_PASS: 'p' });
    assert(env.PATH === '/usr/bin' && !('TELEGRAM_BOT_TOKEN' in env) && !('GROQ_API_KEY' in env) && !('IMAP_PASS' in env),
        'Python gets no secrets in its environment');

    // Files: workspace only, never the WhatsApp login, databases or credentials
    const root = path.resolve('/srv/app/workspace');
    assert(allowedWorkspacePath(root, 'downloads/data.csv') === path.join(root, 'downloads/data.csv'), 'normal upload readable');
    for (const bad of ['../.env', '../workspace_evil/x.txt', '/etc/passwd', 'whatsapp_session/creds.json',
        'whatsapp_messages.db', 'tickets.db', 'downloads/../../service-account.json', 'backups/x.json', '.env', '']) {
        assert(allowedWorkspacePath(root, bad) === null, `blocked: "${bad}"`);
    }
    const viaTool: any = await readFileTool.execute({ filePath: 'whatsapp_session/creds.json' });
    assert(viaTool.success === false && /denied/.test(viaTool.error), 'read_file tool refuses the WhatsApp login');

    assert(safeUploadName('../../.env', 'f') === 'env', 'upload name cannot leave the folder or be hidden');
    assert(safeUploadName('report.xlsx', 'f') === 'report.xlsx', 'normal upload name kept');
    assert(safeUploadName('', 'file_1') === 'file_1', 'empty upload name gets a fallback');

    // Web pages: no internal addresses (cloud metadata, the server itself, private networks)
    for (const ip of ['127.0.0.1', '10.1.2.3', '172.20.0.1', '192.168.1.1', '169.254.169.254', '::1', 'fd00::1', '::ffff:127.0.0.1']) {
        assert(isPrivateAddress(ip), `private: ${ip}`);
    }
    assert(!isPrivateAddress('8.8.8.8') && !isPrivateAddress('172.32.0.1'), 'public addresses allowed');
    assert(await rejects(assertPublicUrl('http://169.254.169.254/latest/meta-data/')), 'cloud metadata link blocked');
    assert(await rejects(assertPublicUrl('http://localhost:3000/')), 'localhost link blocked');
    assert(await rejects(assertPublicUrl('file:///etc/passwd')), 'file link blocked');

    console.log('ALL SECURITY TESTS PASSED');
}

main().catch((e) => { console.error(e); process.exit(1); });
