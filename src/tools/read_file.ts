import fs from 'fs';
import path from 'path';
import { allowedWorkspacePath } from '../security.js';

const WORKSPACE_DIR = path.resolve(process.cwd(), 'workspace');

export const readFileTool = {
    name: 'read_file',
    description: 'Read the contents of a file in the workspace or downloads folder (e.g., downloads/data.csv, downloads/script.py, or any text/data file).',
    parameters: {
        type: 'object',
        properties: {
            filePath: {
                type: 'string',
                description: 'Relative path to the file inside workspace (e.g., "downloads/data.csv" or "script.py")',
            },
        },
        required: ['filePath'],
    },
    execute: async (args: { filePath: string }) => {
        try {
            // Inside the workspace only, and never the WhatsApp login, databases or credentials
            const resolvedPath = allowedWorkspacePath(WORKSPACE_DIR, String(args.filePath || ''));
            if (!resolvedPath) {
                return { success: false, error: 'Access denied: this file cannot be read by the assistant.' };
            }

            if (!fs.existsSync(resolvedPath)) {
                return { success: false, error: `File not found: ${args.filePath}` };
            }

            const stat = await fs.promises.stat(resolvedPath);
            if (stat.size > 1024 * 1024 * 2) {
                // Read first 2MB
                const handle = await fs.promises.open(resolvedPath, 'r');
                const buffer = Buffer.alloc(1024 * 100);
                await handle.read(buffer, 0, buffer.length, 0);
                await handle.close();
                return {
                    success: true,
                    content: buffer.toString('utf-8') + '\n\n[الملف كبير، تم عرض أول 100 كيلوبايت]',
                    size: stat.size,
                };
            }

            const content = await fs.promises.readFile(resolvedPath, 'utf-8');
            return {
                success: true,
                content,
                size: stat.size,
            };
        } catch (err: any) {
            return {
                success: false,
                error: err.message,
            };
        }
    },
};
