// Recovers what the bot did not record from WhatsApp history (owner, 15-10):
//   - pictures for videos recorded before video frames were saved;
//   - older messages of the report groups (config/meydan_rules.yaml `groups`), back to a date,
//     so reports can be built for days before the bot was recording.
//
// The bot asks WhatsApp for older messages ("on-demand history sync", answered by the linked
// phone). Messages that come back are stored like live ones; each video that was missing a
// picture gets a frame when it can still be downloaded, else the preview inside the message.
// WhatsApp may answer only part of the requests (or none, if the phone is offline); the run is
// retried on the next starts, up to MAX_ATTEMPTS, and the owner gets a summary either way.

export interface MissingVideo { messageId: string; groupName: string; tsMs: number | null }
export interface HistoryKey { remoteJid: string; id: string; fromMe: boolean }

export interface BackfillDeps {
    missing(): MissingVideo[];
    groupJid(groupName: string): string | undefined;
    anchor(groupName: string): { id: string; tsMs: number; fromMe: boolean } | null;
    fetchHistory(count: number, key: HistoryKey, tsMs: number): Promise<unknown>;
    makeThumb(msg: any): Promise<Buffer | null>;
    saveThumb(messageId: string, jpeg: Buffer): boolean;
    notify(text: string): Promise<void>;
    wait(ms: number): Promise<void>;
    log?(line: string): void;
}

/** Also page these groups back to untilTsMs (history recovery), not only to their missing videos. */
export interface HistoryTarget { groups: string[]; untilTsMs: number }

export const PAGE_SIZE = 50;
export const MAX_PAGES_PER_GROUP = 120;
export const WAIT_PER_PAGE_MS = 15_000;
export const MAX_ATTEMPTS = 3;

function msgTsMs(msg: any): number {
    const t = msg?.messageTimestamp;
    const s = typeof t === 'number' ? t : t && typeof t.toNumber === 'function' ? t.toNumber() : Number(t || 0);
    return s * 1000;
}

export interface BackfillResult {
    total: number;          // videos that were missing a picture
    recovered: number;      // ... and got one
    left: number;
    received: number;       // history messages WhatsApp sent back
    reached: string[];      // history groups recovered back to the target date
    stopped: string[];      // history groups where WhatsApp had nothing older (or did not answer)
}

export class VideoBackfill {
    private pending = new Map<string, MissingVideo>();
    // Oldest message WhatsApp sent back per chat: the next page starts there
    private oldest = new Map<string, { key: HistoryKey; tsMs: number }>();
    private receivedPerChat = new Map<string, number>();
    recovered = 0;

    constructor(private deps: BackfillDeps, private target?: HistoryTarget) {}

    /** Called for every history batch the bot receives (also ones it did not ask for). */
    async onHistory(messages: any[]): Promise<void> {
        for (const msg of messages || []) {
            const jid = msg?.key?.remoteJid;
            const id = msg?.key?.id;
            if (!jid || !id) continue;
            const ts = msgTsMs(msg);
            this.receivedPerChat.set(jid, (this.receivedPerChat.get(jid) || 0) + 1);
            const cur = this.oldest.get(jid);
            if (ts && (!cur || ts < cur.tsMs)) this.oldest.set(jid, { key: { remoteJid: jid, id, fromMe: !!msg.key.fromMe }, tsMs: ts });

            if (!this.pending.has(id) || !msg.message?.videoMessage) continue;
            try {
                const jpeg = await this.deps.makeThumb(msg);
                if (jpeg && this.deps.saveThumb(id, jpeg)) {
                    this.pending.delete(id);
                    this.recovered++;
                }
            } catch (e: any) {
                this.deps.log?.(`video picture failed for ${id}: ${e?.message || e}`);
            }
        }
    }

    async run(): Promise<BackfillResult> {
        for (const v of this.deps.missing()) this.pending.set(v.messageId, v);
        const total = this.pending.size;
        const historyGroups = new Set(this.target?.groups || []);
        const groups = [...new Set([...[...this.pending.values()].map((v) => v.groupName), ...historyGroups])];
        const reached: string[] = [];
        const stopped: string[] = [];

        for (const group of groups) {
            const jid = this.deps.groupJid(group);
            const start = this.deps.anchor(group);
            if (!jid || !start) {
                this.deps.log?.(`history: group "${group}" not available, skipped`);
                if (historyGroups.has(group)) stopped.push(group);
                continue;
            }
            const until = historyGroups.has(group) ? this.target!.untilTsMs : Infinity;
            let key: HistoryKey = { remoteJid: jid, id: start.id, fromMe: start.fromMe };
            let ts = start.tsMs;
            let done = ts <= until && this.pendingIn(group) === 0;
            for (let page = 0; page < MAX_PAGES_PER_GROUP && !done; page++) {
                const before = this.receivedPerChat.get(jid) || 0;
                try {
                    await this.deps.fetchHistory(PAGE_SIZE, key, ts);
                } catch (e: any) {
                    this.deps.log?.(`history request failed for "${group}": ${e?.message || e}`);
                    break;
                }
                await this.deps.wait(WAIT_PER_PAGE_MS);
                const next = this.oldest.get(jid);
                // Nothing new came back, or WhatsApp has no older messages: stop this group
                if ((this.receivedPerChat.get(jid) || 0) === before || !next || next.tsMs >= ts) break;
                key = next.key;
                ts = next.tsMs;
                const pendingTs = [...this.pending.values()]
                    .filter((v) => v.groupName === group && v.tsMs).map((v) => v.tsMs as number);
                const videosDone = this.pendingIn(group) === 0 || (pendingTs.length > 0 && ts < Math.min(...pendingTs));
                done = videosDone && ts <= until;
            }
            if (historyGroups.has(group)) (ts <= until ? reached : stopped).push(group);
        }
        const received = [...this.receivedPerChat.values()].reduce((a, b) => a + b, 0);
        return { total, recovered: this.recovered, left: this.pending.size, received, reached, stopped };
    }

    private pendingIn(group: string): number {
        let n = 0;
        for (const v of this.pending.values()) if (v.groupName === group) n++;
        return n;
    }
}

/** Summary sent to the owner (English, like every bot message). */
export function backfillSummary(
    r: { total: number; recovered: number; left: number; added?: number; reached?: string[]; stopped?: string[] },
    attempt: number, sinceLabel?: string,
): string {
    const lines: string[] = [];
    if (r.added !== undefined) {
        lines.push(`📥 Old messages recovered: ${r.added}${sinceLabel ? ` (report groups, back to ${sinceLabel})` : ''}.`);
        if (r.stopped?.length) lines.push(`   WhatsApp had nothing older for: ${r.stopped.join(', ')}.`);
    }
    if (!r.total) {
        lines.push('🎬 Old videos: every video already has a picture.');
    } else {
        lines.push(`🎬 Old video pictures recovered: ${r.recovered} of ${r.total}.`);
        if (r.left) {
            lines.push(attempt < MAX_ATTEMPTS
                ? `   ${r.left} not returned by WhatsApp yet; the bot will try again on the next restart (keep the phone online).`
                : `   ${r.left} could not be recovered from WhatsApp (attempt ${attempt} of ${MAX_ATTEMPTS}). New videos always get a picture.`);
        }
    }
    return lines.join('\n');
}

/** Report group names from config/meydan_rules.yaml (the `groups:` block). */
export function reportGroupsFromRules(yamlText: string): string[] {
    const block = yamlText.split(/\r?\n/);
    const start = block.findIndex((l) => /^groups:\s*$/.test(l));
    if (start < 0) return [];
    const out: string[] = [];
    for (const line of block.slice(start + 1)) {
        if (!/^\s+\S/.test(line)) break;                     // end of the indented block
        const m = line.match(/^\s+[\w-]+:\s*["']?(.+?)["']?\s*(#.*)?$/);
        if (m) out.push(m[1]);
    }
    return out;
}

/**
 * Which history WhatsApp sends may reach the bot. Baileys drops every history sync unless this
 * says yes (its default follows syncFullHistory, which is off). Only the "on demand" answers to
 * the recovery requests above are let through; the bulk history sent on (re)link stays ignored.
 */
export const ON_DEMAND_SYNC_TYPE = 6; // proto.HistorySync.HistorySyncType.ON_DEMAND
export function acceptHistorySync(msg: { syncType?: number | null }): boolean {
    return msg?.syncType === ON_DEMAND_SYNC_TYPE;
}
