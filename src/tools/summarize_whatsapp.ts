import ExcelJS from 'exceljs';
import fs from 'fs';
import { EXCEL_PATH } from '../whatsapp/excel.js';

export interface WhatsAppMessageSummaryItem {
    timestamp: string;
    group: string;
    sender: string;
    text: string;
}

export async function readRecentWhatsAppMessages(maxRows: number = 80): Promise<WhatsAppMessageSummaryItem[]> {
    if (!fs.existsSync(EXCEL_PATH)) {
        return [];
    }

    try {
        const workbook = new ExcelJS.Workbook();
        await workbook.xlsx.readFile(EXCEL_PATH);
        const worksheet = workbook.getWorksheet('WhatsApp Messages') || workbook.worksheets[0];

        if (!worksheet) return [];

        const totalRows = worksheet.rowCount;
        const startRow = Math.max(2, totalRows - maxRows + 1);
        const messages: WhatsAppMessageSummaryItem[] = [];

        for (let rowNum = startRow; rowNum <= totalRows; rowNum++) {
            const row = worksheet.getRow(rowNum);
            // Columns: 1: Message ID, 2: Timestamp, 3: Group, 4: Sender, 5: Message Text, 6: Media Type, 7: Preview
            const timestamp = String(row.getCell(2).value || '');
            const group = String(row.getCell(3).value || '');
            const sender = String(row.getCell(4).value || '');
            const text = String(row.getCell(5).value || '').trim();

            if (text && text !== 'Message Text') {
                messages.push({
                    timestamp,
                    group,
                    sender,
                    text,
                });
            }
        }

        return messages;
    } catch (err: any) {
        console.error('Error reading WhatsApp excel for digest:', err.message);
        return [];
    }
}

export const summarizeWhatsAppTool = {
    name: 'summarize_whatsapp',
    description:
        'Read and summarize recent captured WhatsApp group messages from the Excel database. ' +
        'Produces an organized digest of discussions, announcements, zoom/meet links, deadlines, and questions per group.',
    parameters: {
        type: 'object',
        properties: {
            groupName: {
                type: 'string',
                description: 'Optional group name to filter messages by. Leave blank to summarize all active groups.',
            },
            limit: {
                type: 'number',
                description: 'Number of recent messages to analyze (default 50, max 100).',
            },
        },
        required: [],
    },
    execute: async (args: { groupName?: string; limit?: number }) => {
        const limit = Math.min(Math.max(args.limit || 50, 10), 100);
        const messages = await readRecentWhatsAppMessages(limit);

        if (messages.length === 0) {
            return {
                success: false,
                count: 0,
                message: '📭 لم يتم العثور على رسائل واتساب مسجلة في ملف الإكسل حتى الآن.',
            };
        }

        // Filter if specific group requested
        const filtered = args.groupName
            ? messages.filter((m) => m.group.toLowerCase().includes(args.groupName!.toLowerCase()))
            : messages;

        // Group messages by group name
        const groupsMap = new Map<string, WhatsAppMessageSummaryItem[]>();
        for (const m of filtered) {
            const g = m.group || 'مجموعة عامة';
            if (!groupsMap.has(g)) groupsMap.set(g, []);
            groupsMap.get(g)!.push(m);
        }

        const formattedDigest = Array.from(groupsMap.entries())
            .map(([group, msgs]) => {
                const lines = msgs
                    .slice(-30) // last 30 msgs per group
                    .map((m) => `• [${m.sender}]: ${m.text}`)
                    .join('\n');
                return `👥 **المجموعة: ${group}** (${msgs.length} رسالة):\n${lines}`;
            })
            .join('\n\n---\n\n');

        return {
            success: true,
            totalAnalyzed: filtered.length,
            groupsCount: groupsMap.size,
            data: formattedDigest,
            message: `📊 تم استخراج ${filtered.length} رسالة من ${groupsMap.size} مجموعة واتساب لتحليلها وتلخيصها:\n\n${formattedDigest}`,
        };
    },
};
