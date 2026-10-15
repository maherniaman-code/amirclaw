"""Reprocess every recorded message with the current rules.

Reports are always built from the raw messages, but two stores keep results of earlier runs:
tickets.db (status, first seen, repeat counts: used by the digest and overdue reminders) and
the warehouse (decisions per message, ticket history, daily data-quality metrics). After a rule
or keyword change, this rebuilds both from scratch, day by day, so old data follows the new rules.

    python3 -m reports_engine.reprocess            # dry run: build into a temp folder, show what changes
    python3 -m reports_engine.reprocess --apply    # back up the current stores, then replace them

Never touched: the raw messages and the manual corrections (reports_corrections.db), which are
re-applied as part of every report.
"""

import argparse
import contextlib
import functools
import io
import os
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import manager as manager_mod
from . import excel_exporter, tickets_db, warehouse
from .config_loader import BASE_DIR
from .date_utils import now_dubai
from .dq_metrics import _store as store_dq_day, compute_day


def _ticket_rows(path: Path) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """(message id, report) -> (status, text) of a tickets.db; empty when it does not exist."""
    if not path.exists():
        return {}
    with sqlite3.connect(str(path)) as c:
        try:
            rows = c.execute("SELECT message_id, report_type, status, text_snippet FROM tickets").fetchall()
        except sqlite3.OperationalError:
            return {}
    return {(r[0], r[1]): (r[2] or "", r[3] or "") for r in rows}


def _days(manager: manager_mod.ReportsManager) -> List[str]:
    """Every operational day from the first recorded message to today, as DD-MM-YYYY."""
    dts = [m["datetime"] for m in manager.parser.load_messages(load_images=False) if m.get("datetime")]
    if not dts:
        return []
    day = (min(dts) - timedelta(hours=5)).date()   # before 05:00 belongs to the previous day
    last = now_dubai().date()
    out = []
    while day <= last:
        out.append(day.strftime("%d-%m-%Y"))
        day += timedelta(days=1)
    return out


def _build(manager: manager_mod.ReportsManager, tickets_path: Path, warehouse_path: Path) -> int:
    """Run every report for every day into fresh stores. Returns the number of days processed."""
    tickets_db.init_tickets_table(str(tickets_path))
    saved_enrich, saved_env = manager_mod.enrich_ticket, os.environ.get("WAREHOUSE_DB_PATH")
    saved_out = excel_exporter.OUTPUT_DIR
    manager_mod.enrich_ticket = functools.partial(tickets_db.enrich_ticket, db_path=str(tickets_path))
    os.environ["WAREHOUSE_DB_PATH"] = str(warehouse_path)
    # Each report also writes an Excel file: keep those out of the real reports folder
    excel_exporter.OUTPUT_DIR = tickets_path.parent / "xlsx"
    excel_exporter.OUTPUT_DIR.mkdir(exist_ok=True)
    try:
        days = _days(manager)
        with contextlib.redirect_stdout(io.StringIO()):  # some reports print Excel diagnostics
            for day in days:
                d, m, y = day.split("-")
                store_dq_day(f"{y}-{m}-{d}", compute_day(manager, day))  # runs maintenance + fire safety
                manager.generate_operations(target_date=day, target_shift="Full")
                manager.generate_parking_violations(target_date=day)
                manager.generate_vehicle_permissions(target_date=day)
                manager.generate_cleaning(target_date=day)
        return len(days)
    finally:
        manager_mod.enrich_ticket = saved_enrich
        excel_exporter.OUTPUT_DIR = saved_out
        if saved_env is None:
            os.environ.pop("WAREHOUSE_DB_PATH", None)
        else:
            os.environ["WAREHOUSE_DB_PATH"] = saved_env


def _compare(old: Dict, new: Dict) -> Dict[str, Any]:
    def by_status(rows):
        return Counter(f"{report}: {'Done' if 'done' in st.lower() else 'open'}" for (_, report), (st, _) in rows.items())
    changed = [(k, old[k][0], new[k][0], new[k][1]) for k in old.keys() & new.keys() if old[k][0] != new[k][0]]
    return {
        "before": dict(by_status(old)), "after": dict(by_status(new)),
        "changed": sorted(changed, key=lambda x: x[0]),
        "removed": sorted((k, old[k][1]) for k in old.keys() - new.keys()),
        "added": sorted((k, new[k][1]) for k in new.keys() - old.keys()),
    }


def reprocess(excel_path: Optional[Path] = None, tickets_path: Optional[Path] = None,
              warehouse_path: Optional[Path] = None, apply: bool = False,
              backup_root: Optional[Path] = None) -> Dict[str, Any]:
    excel_path = Path(excel_path or BASE_DIR / "workspace" / "whatsapp_messages.xlsx")
    tickets_path = Path(tickets_path or tickets_db.DEFAULT_DB_PATH)
    warehouse_path = Path(warehouse_path or warehouse.db_path())
    work = tickets_path.parent / ".reprocess_tmp"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    new_tickets, new_warehouse = work / tickets_path.name, work / warehouse_path.name
    try:
        days = _build(manager_mod.ReportsManager(excel_path), new_tickets, new_warehouse)
        summary = _compare(_ticket_rows(tickets_path), _ticket_rows(new_tickets))
        summary["days"] = days
        if apply:
            backup = (backup_root or tickets_path.parent / "backups") / f"reprocess-{now_dubai():%Y%m%d-%H%M%S}"
            backup.mkdir(parents=True, exist_ok=True)
            for current, rebuilt in ((tickets_path, new_tickets), (warehouse_path, new_warehouse)):
                if current.exists():
                    shutil.copy2(current, backup / current.name)
                os.replace(rebuilt, current)
            summary["backup"] = str(backup)
        return summary
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reprocess all recorded messages with the current rules")
    ap.add_argument("--apply", action="store_true", help="back up and replace tickets.db and the warehouse")
    ap.add_argument("--excel", help="WhatsApp workbook (default workspace/whatsapp_messages.xlsx)")
    args = ap.parse_args()
    s = reprocess(Path(args.excel) if args.excel else None, apply=args.apply)
    print(f"Reprocessed {s['days']} day(s) with the current rules.")
    print("Tickets before: " + (", ".join(f"{k} {v}" for k, v in sorted(s["before"].items())) or "none"))
    print("Tickets after:  " + (", ".join(f"{k} {v}" for k, v in sorted(s["after"].items())) or "none"))
    for (mid, report), old, new, text in s["changed"][:15]:
        print(f"  changed [{report}] {old} -> {new}: {' '.join(text.split())[:70]}")
    for (mid, report), text in s["removed"][:15]:
        print(f"  no longer a ticket [{report}]: {' '.join(text.split())[:70]}")
    for (mid, report), text in s["added"][:15]:
        print(f"  new ticket [{report}]: {' '.join(text.split())[:70]}")
    hidden = sum(max(len(s[k]) - 15, 0) for k in ("changed", "removed", "added"))
    if hidden:
        print(f"  ... and {hidden} more")
    if args.apply:
        print(f"Applied. Previous stores backed up in {s['backup']}")
    else:
        print("Dry run: nothing changed. Run again with --apply to replace the stores.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
