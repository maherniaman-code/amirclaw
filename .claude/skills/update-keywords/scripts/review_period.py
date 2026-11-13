"""Print messages with the bot's decision, for reviewing rules by eye.

    review_period.py --db workspace/whatsapp_messages.db --snapshot before.json --from 03-11-2026 --to 13-11-2026
        every message of the report groups in the period (both dates included) and its decision
    review_period.py --db ... --snapshot before.after.json --before before.json
        only the messages whose decision changed, with their full text (review every one)

Decisions look like "maintenance:open", "maintenance:closed", "operations:excluded"; "-" = not classified.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone, timedelta

DUBAI = timezone(timedelta(hours=4))
GROUPS = ("Meydan FM Team💪", "Fire & Safety Team Meydan", "Meydan - Security Team",
          "Parking Team at Meydan", "Cleaning Matters at Meydan", "Podium Parking Access")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--before", help="earlier snapshot: print only changed decisions")
    ap.add_argument("--from", dest="start", help="DD-MM-YYYY")
    ap.add_argument("--to", dest="end", help="DD-MM-YYYY")
    ap.add_argument("--groups", help="comma-separated group names (default: the report groups)")
    args = ap.parse_args()

    snap = json.load(open(args.snapshot))
    before = json.load(open(args.before)) if args.before else None
    groups = tuple(g.strip() for g in args.groups.split(",")) if args.groups else GROUPS

    def day(s: str, end: bool = False) -> int:
        d = datetime.strptime(s, "%d-%m-%Y").replace(tzinfo=DUBAI) + (timedelta(days=1) if end else timedelta())
        return int(d.timestamp() * 1000)

    q = "SELECT message_id, ts_utc_ms, group_name, sender, text, quoted_message_id FROM messages WHERE 1=1"
    params: list = []
    if args.start:
        q += " AND ts_utc_ms >= ?"
        params.append(day(args.start))
    if args.end:
        q += " AND ts_utc_ms < ?"
        params.append(day(args.end, end=True))
    con = sqlite3.connect(args.db)
    rows = con.execute(q + " ORDER BY ts_utc_ms", params).fetchall()
    con.close()

    shown = 0
    for mid, ts, group, sender, text, quoted in rows:
        if before is None and group not in groups:
            continue
        now_d = ",".join(snap.get(mid, [])) or "-"
        if before is not None:
            was = ",".join(before.get(mid, [])) or "-"
            if was == now_d:
                continue
            label = f"{was}  ->  {now_d}"
        else:
            label = now_d
        when = datetime.fromtimestamp((ts or 0) / 1000, DUBAI).strftime("%d-%m %H:%M")
        name = (sender or "").split(" (")[0]
        reply = f" [reply to {quoted[:8]}]" if quoted else ""
        print(f"{when} | {group[:22]} | {name[:18]} | {label}{reply}\n    {(text or '').strip()[:400]}\n")
        shown += 1
    print(f"--- {shown} message(s)")


if __name__ == "__main__":
    main()
