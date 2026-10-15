import { execFile } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import fs from 'fs';
import { config } from '../config.js';

const execFileAsync = promisify(execFile);
const ROOT_DIR = path.resolve(process.cwd());

export interface ReportResult {
    success: boolean;
    report?: string;
    text?: string;
    excelPath?: string;
    diagnostics?: string[];
    error?: string;
    total?: number;
    completed?: number;
    open?: number;
    reported?: number;
    rectified?: number;
    parked?: number;
    pending?: number;
    // Report number -> WhatsApp message id, for the Telegram correction buttons
    items?: { no: number; id: string; status: string }[];
    // Open tickets older than the overdue threshold (owner reminders)
    overdue?: OverdueTicket[];
}

export interface OverdueTicket {
    id: string;
    report: 'maintenance' | 'fire_safety';
    building: string;
    hours_open: number;
    reported: string;
    text: string;
}

export async function runReportEngine(
    reportType: 'parking' | 'maintenance' | 'fire_safety' | 'operations' | 'violations' | 'permissions' | 'cleaning' | 'data_quality' | 'owner_kpis' | 'morning_digest' | 'overdue' | 'list_corrections',
    options?: { slot?: string; date?: string; shift?: 'Day' | 'Night'; excelPath?: string }
): Promise<ReportResult> {
    const pythonBin = config.pythonPath || 'python3';
    const args = ['-m', 'reports_engine.cli', '--report', reportType];

    if (options?.slot) {
        args.push('--slot', options.slot);
    }
    if (options?.date) {
        args.push('--date', options.date);
    }
    if (options?.shift) {
        args.push('--shift', options.shift);
    }
    if (options?.excelPath) {
        args.push('--excel', options.excelPath);
    }

    try {
        const { stdout, stderr } = await execFileAsync(pythonBin, args, {
            cwd: ROOT_DIR,
            timeout: 45000,
            maxBuffer: 10 * 1024 * 1024,
            env: { ...process.env },
        });

        // Try to find JSON in stdout first, then stderr (cli.py writes crash errors to stderr)
        const searchLines = (text: string): string => {
            const lines = text.trim().split('\n');
            for (let i = lines.length - 1; i >= 0; i--) {
                if (lines[i].trim().startsWith('{')) return lines[i].trim();
            }
            return '';
        };

        const jsonLine = searchLines(stdout) || searchLines(stderr);

        if (!jsonLine) {
            const hint = stderr.trim().slice(0, 300) || stdout.trim().slice(0, 300);
            throw new Error(`No JSON output from Python reporting engine: ${hint}`);
        }

        const parsed = JSON.parse(jsonLine);
        if (parsed.error) {
            return {
                success: false,
                error: parsed.message || 'Unknown Python engine error',
            };
        }

        return {
            success: true,
            report: reportType,
            text: parsed.text,
            excelPath: parsed.excel_path,
            diagnostics: parsed.diagnostics || [],
            total: parsed.total,
            completed: parsed.completed,
            open: parsed.open,
            reported: parsed.reported,
            rectified: parsed.rectified,
            parked: parsed.parked,
            pending: parsed.pending,
            items: Array.isArray(parsed.items) ? parsed.items : [],
            overdue: Array.isArray(parsed.overdue) ? parsed.overdue : [],
        };
    } catch (err: any) {
        console.error(`Error executing Python report engine (${reportType}):`, err.message);
        return {
            success: false,
            error: err.message || 'Failed to execute Python reporting engine',
        };
    }
}

/**
 * Saves a manual classification correction (applied on top of automatic matching in every
 * later report). targetReport "ignore" drops the message from reports.
 */
export async function saveCorrection(
    messageId: string,
    targetReport: string,
    targetStatus?: string,
    correctedBy?: string,
    notes?: string,
): Promise<boolean> {
    const pythonBin = config.pythonPath || 'python3';
    const args = ['-m', 'reports_engine.cli', '--report', 'list_corrections',
        '--msg-id', messageId, '--target-report', targetReport];
    if (targetStatus) args.push('--target-status', targetStatus);
    if (correctedBy) args.push('--corrected-by', correctedBy);
    if (notes) args.push('--notes', notes);
    try {
        const { stdout } = await execFileAsync(pythonBin, args, { cwd: ROOT_DIR, timeout: 20000, env: { ...process.env } });
        return stdout.includes('"correction_saved": true');
    } catch (err: any) {
        console.error('Error saving correction:', err.message);
        return false;
    }
}
