// Pictures for videos recorded before the bot saved video frames (owner, 15-10).
//
// The old video files were never kept, so the bot asks WhatsApp for the group history again
// ("on-demand history sync", answered by the linked phone). Each video that comes back gets a
// picture: a frame when it can still be downloaded, else the small preview inside the message.
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

export const PAGE_SIZE = 50;
export const MAX_PAGES_PER_GROUP = 12;
export const WAIT_PER_PAGE_MS = 25_000;
export const MAX_ATTEMPTS = 3;

function msgTsMs(msg: any): number {
    const t = msg?.messageTimestamp;
    const s = typeof t === 'number' ? t : t && typeof t.toNumber === 'function' ? t.toNumber() : Number(t || 0);
    return s * 1000;
}

export class VideoBackfill {
    private pending = new Map<string, MissingVideo>();
    // Oldest message WhatsApp sent back per chat: the next page starts there
    private oldest = new Map<string, { key: HistoryKey; tsMs: number }>();
    private received = new Map<string, number>();
    recovered = 0;

    constructor(private deps: BackfillDeps) {}

    /** Called for every history batch the bot receives (also ones it did not ask for). */
    async onHistory(messages: any[]): Promise<void> {
        for (const msg of messages || []) {
            const jid = msg?.key?.remoteJid;
            const id = msg?.key?.id;
            if (!jid || !id) continue;
            const ts = msgTsMs(msg);
            this.received.set(jid, (this.received.get(jid) || 0) + 1);
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

    async run(): Promise<{ total: number; recovered: number; left: number }> {
        for (const v of this.deps.missing()) this.pending.set(v.messageId, v);
        const total = this.pending.size;
        if (!total) return { total: 0, recovered: 0, left: 0 };

        const groups = [...new Set([...this.pending.values()].map((v) => v.groupName))];
        for (const group of groups) {
            const jid = this.deps.groupJid(group);
            const start = this.deps.anchor(group);
            if (!jid || !start) {
                this.deps.log?.(`video pictures: group "${group}" not available, skipped`);
                continue;
            }
            let key: HistoryKey = { remoteJid: jid, id: start.id, fromMe: start.fromMe };
            let ts = start.tsMs;
            for (let page = 0; page < MAX_PAGES_PER_GROUP && this.pendingIn(group) > 0; page++) {
                const before = this.received.get(jid) || 0;
                try {
                    await this.deps.fetchHistory(PAGE_SIZE, key, ts);
                } catch (e: any) {
                    this.deps.log?.(`history request failed for "${group}": ${e?.message || e}`);
                    break;
                }
                await this.deps.wait(WAIT_PER_PAGE_MS);
                const next = this.oldest.get(jid);
                // Nothing new came back, or WhatsApp has no older messages: stop this group
                if ((this.received.get(jid) || 0) === before || !next || next.tsMs >= ts) break;
                key = next.key;
                ts = next.tsMs;
                const oldestPending = Math.min(...[...this.pending.values()]
                    .filter((v) => v.groupName === group && v.tsMs).map((v) => v.tsMs as number));
                if (Number.isFinite(oldestPending) && ts < oldestPending) break; // went past every missing video
            }
        }
        return { total, recovered: this.recovered, left: this.pending.size };
    }

    private pendingIn(group: string): number {
        let n = 0;
        for (const v of this.pending.values()) if (v.groupName === group) n++;
        return n;
    }
}

/** Summary sent to the owner (English, like every bot message). */
export function backfillSummary(r: { total: number; recovered: number; left: number }, attempt: number): string {
    if (!r.total) return '🎬 Old videos: every video already has a picture.';
    const head = `🎬 Old video pictures recovered: ${r.recovered} of ${r.total}.`;
    if (!r.left) return head;
    return attempt < MAX_ATTEMPTS
        ? `${head} ${r.left} not returned by WhatsApp yet; the bot will try again on the next restart (keep the phone online).`
        : `${head} ${r.left} could not be recovered from WhatsApp (attempt ${attempt} of ${MAX_ATTEMPTS}). New videos always get a picture.`;
}
