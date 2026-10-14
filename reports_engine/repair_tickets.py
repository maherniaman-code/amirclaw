"""One-off repair: convert non-ISO first_seen/last_seen values in tickets.db to ISO.

Older versions stored the raw WhatsApp timestamp text (e.g. "10/12/2026, 06:12:22 AM" or
"١١‏/١٠‏/٢٠٢٦، ١٠:٢٩:٤٥ ص") in first_seen, which made hours_open 0 and hid SLA breaches.

Usage:
    python3 -m reports_engine.repair_tickets                 # dry run on the default DB
    python3 -m reports_engine.repair_tickets --apply         # back up, then write fixes
    python3 -m reports_engine.repair_tickets --db PATH --apply
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from .date_utils import parse_datetime
from .tickets_db import DEFAULT_DB_PATH

COLUMNS = ("first_seen", "last_seen")


def _repaired_iso(value: Optional[str]) -> Optional[str]:
    """Return an ISO replacement for a non-ISO value, or None if it is already ISO or empty."""
    if not value:
        return None
    try:
        datetime.fromisoformat(value)
        return None
    except ValueError:
        pass
    dt = parse_datetime(value)
    return dt.isoformat() if dt else ""


def repair_tickets(db_path: str = DEFAULT_DB_PATH, apply: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """
    Find non-ISO timestamps and (with apply=True) rewrite them as ISO.
    Returns {"fixed": [...], "unparseable": [...]}; unparseable values are left untouched.
    """
    fixed: List[Dict[str, Any]] = []
    unparseable: List[Dict[str, Any]] = []

    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(f"SELECT message_id, {', '.join(COLUMNS)} FROM tickets").fetchall()
        for row in rows:
            for col in COLUMNS:
                new_val = _repaired_iso(row[col])
                if new_val is None:
                    continue
                entry = {"message_id": row["message_id"], "column": col, "old": row[col], "new": new_val}
                if new_val:
                    fixed.append(entry)
                else:
                    unparseable.append(entry)

        if apply and fixed:
            for e in fixed:
                c.execute(f"UPDATE tickets SET {e['column']}=? WHERE message_id=?", (e["new"], e["message_id"]))
            c.commit()

    return {"fixed": fixed, "unparseable": unparseable}


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair non-ISO timestamps in tickets.db")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"Path to tickets.db (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--apply", action="store_true", help="Write fixes (default is a dry run)")
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print(f"tickets.db not found: {args.db}", file=sys.stderr)
        return 1

    if args.apply:
        backup = f"{args.db}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        with sqlite3.connect(args.db) as src, sqlite3.connect(backup) as dst:
            src.backup(dst)  # consistent snapshot even if the bot writes meanwhile
        print(f"Backup written: {backup}")

    result = repair_tickets(args.db, apply=args.apply)
    for e in result["fixed"]:
        print(f"{'FIXED' if args.apply else 'WOULD FIX'}  {e['message_id']}  {e['column']}: {e['old']!r} -> {e['new']}")
    for e in result["unparseable"]:
        print(f"UNPARSEABLE  {e['message_id']}  {e['column']}: {e['old']!r} (left unchanged)")

    mode = "applied" if args.apply else "dry run, nothing written; re-run with --apply"
    print(f"\n{len(result['fixed'])} value(s) to fix, {len(result['unparseable'])} unparseable ({mode}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
