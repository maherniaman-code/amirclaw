import { getCurrentTimeTool } from './get_current_time.js';
import { generateImageTool } from './generate_image.js';
import { generateVideoTool } from './generate_video.js';
import { executePythonTool } from './execute_python.js';
import { readFileTool } from './read_file.js';
import { generateVoiceTool } from './generate_voice.js';
import { searchWebTool } from './search_web.js';
import { scrapeWebpageTool } from './scrape_webpage.js';
import { setReminderTool } from './set_reminder.js';
import { manageMemoryTool } from './manage_memory.js';
import { summarizeWhatsAppTool } from './summarize_whatsapp.js';

export const availableTools = [
    getCurrentTimeTool,
    generateImageTool,
    generateVideoTool,
    executePythonTool,
    readFileTool,
    generateVoiceTool,
    searchWebTool,
    scrapeWebpageTool,
    setReminderTool,
    manageMemoryTool,
    summarizeWhatsAppTool,
];

export async function executeTool(name: string, args: any, context?: any) {
    const tool = availableTools.find((t) => t.name === name);
    if (!tool) {
        throw new Error(`Tool ${name} not found`);
    }
    return await tool.execute(args, context);
}
