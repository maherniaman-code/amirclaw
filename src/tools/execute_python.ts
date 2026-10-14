import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs';
import path from 'path';

const execAsync = promisify(exec);

const WORKSPACE_DIR = path.resolve(process.cwd(), 'workspace');

// Ensure workspace directory exists
if (!fs.existsSync(WORKSPACE_DIR)) {
    fs.mkdirSync(WORKSPACE_DIR, { recursive: true });
}

// Find best python executable
const PYTHON_BIN = '/Library/Frameworks/Python.framework/Versions/3.13/bin/python3';

export const executePythonTool = {
    name: 'execute_python',
    description:
        'Execute Python 3 code with full access to libraries: pandas, openpyxl, xlsxwriter (for Excel files), ' +
        'matplotlib (for charts/plots), numpy, pillow (images), python-docx (Word docs), pypdf (PDFs), requests, and beautifulsoup4. ' +
        'Use this tool whenever you need to process Excel spreadsheets, perform data analysis, manipulate files, generate reports, or calculate complex formulas.',
    parameters: {
        type: 'object',
        properties: {
            code: {
                type: 'string',
                description: 'The complete Python 3 code to execute. Can import pandas, openpyxl, matplotlib, numpy, etc.',
            },
        },
        required: ['code'],
    },
    execute: async (args: { code: string }) => {
        const { code } = args;
        const scriptId = `script_${Date.now()}_${Math.random().toString(36).substring(7)}`;
        const scriptPath = path.join(WORKSPACE_DIR, `${scriptId}.py`);

        console.log(`🐍 Executing Python code with Excel & Data libraries (${scriptId})...`);

        try {
            // Snapshot files in workspace before run
            const beforeFiles = new Set(await fs.promises.readdir(WORKSPACE_DIR));

            // Write script to workspace
            await fs.promises.writeFile(scriptPath, code, 'utf-8');

            // Execute script
            const pythonCmd = fs.existsSync(PYTHON_BIN) ? PYTHON_BIN : 'python3';
            const { stdout, stderr } = await execAsync(`"${pythonCmd}" "${scriptPath}"`, {
                cwd: WORKSPACE_DIR,
                timeout: 30000,
                maxBuffer: 1024 * 1024 * 10, // 10MB buffer
            });

            // Clean up temporary script
            fs.unlink(scriptPath, () => {});

            let output = stdout ? stdout.trim() : '';
            if (stderr) {
                output += (output ? '\n' : '') + `[Errors/Warnings]:\n${stderr.trim()}`;
            }

            if (!output) {
                output = '(Code executed successfully with no printed output)';
            }

            // Detect newly created files in workspace (Excel, PDF, Images, Word, etc.)
            const afterFiles = await fs.promises.readdir(WORKSPACE_DIR);
            const newlyCreated = afterFiles.filter(
                (f) => !beforeFiles.has(f) && !f.startsWith('script_') && f !== 'downloads'
            );

            return {
                success: true,
                output,
                generatedFiles: newlyCreated,
                message: `تم تنفيذ كود بايثون بنجاح.\n\nالمخرجات:\n\`\`\`\n${output}\n\`\`\`${
                    newlyCreated.length > 0 ? `\n\n📁 الملفات التي تم إنشاؤها:\n${newlyCreated.map((f) => `• ${f}`).join('\n')}` : ''
                }`,
            };
        } catch (err: any) {
            // Clean up temporary script
            fs.unlink(scriptPath, () => {});

            console.error('Python execution error:', err);
            return {
                success: false,
                error: err.message,
                stderr: err.stderr || '',
                message: `❌ حدث خطأ أثناء تنفيذ كود بايثون:\n\`\`\`\n${err.stderr || err.message}\n\`\`\``,
            };
        }
    },
};
