"""Classification snapshot, diff and accuracy (rules only, owner corrections as ground truth).

    python3 -m reports_engine.evaluate                       # accuracy vs owner corrections
    python3 -m reports_engine.evaluate --save before.json    # snapshot every message's decision
    python3 -m reports_engine.evaluate --diff before.json    # what changed since that snapshot

Workflow for a keyword change: --save, edit config/meydan_rules.yaml, --diff, review, deploy.
Snapshots and accuracy run with REPORTS_IGNORE_CORRECTIONS=1, i.e. the rules alone.
"""

import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .manager import ReportsManager

REPORTS = ("maintenance", "fire_safety", "operations", "violations", "permissions")


def _decision_for_item(report: str, item: Dict[str, Any]) -> str:
    status = (item.get("status") or "").lower()
    closed = any(w in status for w in ("done", "completed", "rectified", "parked", "closed"))
    return f"{report}:{'closed' if closed else 'open'}"


def snapshot(manager: Optional[ReportsManager] = None) -> Dict[str, List[str]]:
    """message id -> sorted decisions, e.g. ["maintenance:open"], ["maintenance:reply"], ["operations:excluded"]."""
    m = manager or ReportsManager()
    prev = os.environ.get("REPORTS_IGNORE_CORRECTIONS")
    os.environ["REPORTS_IGNORE_CORRECTIONS"] = "1"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            m.generate_maintenance(target_date="all")
            m.generate_fire_safety(target_date="all")
            m.generate_operations(target_date="all")
            m.generate_parking_violations(target_date="all")
            m.generate_vehicle_permissions(target_date="all")
    finally:
        if prev is None:
            os.environ.pop("REPORTS_IGNORE_CORRECTIONS", None)
        else:
            os.environ["REPORTS_IGNORE_CORRECTIONS"] = prev

    out: Dict[str, set] = defaultdict(set)
    for report in REPORTS:
        for it in m.last_items.get(report, []):
            out[it["message_id"]].add(_decision_for_item(report, it))
        for a in m.last_audit.get(report, []):
            if a.get("audit_status", "").startswith("Linked"):
                out[a["message_id"]].add(f"{report}:reply")
        for r in m.last_review.get(report, []):
            if "message_id" in r and "type" not in r:
                out[r["message_id"]].add(f"{report}:excluded")
    return {mid: sorted(d) for mid, d in out.items()}


def diff(old: Dict[str, List[str]], new: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    return [
        {"message_id": mid, "before": old.get(mid, []), "after": new.get(mid, [])}
        for mid in sorted(old.keys() | new.keys())
        if old.get(mid, []) != new.get(mid, [])
    ]


def _corrections(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    from .corrections_db import DEFAULT_DB_PATH

    path = db_path or os.environ.get("REPORTS_DB_PATH") or DEFAULT_DB_PATH
    if not Path(path).exists():
        return []
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM manual_corrections ORDER BY id").fetchall()
    latest: Dict[str, Dict[str, Any]] = {}
    for r in rows:  # the latest correction per message wins, as in the reports
        latest[r["message_id"]] = dict(r)
    return list(latest.values())


def accuracy(snap: Dict[str, List[str]], corrections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Share of owner corrections the rules already got right on their own."""
    per_report: Dict[str, Counter] = defaultdict(Counter)
    misses = []
    for corr in corrections:
        mid, target = corr["message_id"], corr["target_report"]
        decisions = snap.get(mid, [])
        if target == "ignore":
            report = next((d.split(":")[0] for d in decisions), "unknown")
            ok = not any(d.endswith((":open", ":closed")) for d in decisions)
            expected = "not a ticket"
        else:
            report = target
            status = (corr.get("target_status") or "").lower()
            want = "closed" if status in ("done", "completed", "rectified") else "open"
            ok = f"{target}:{want}" in decisions
            expected = f"{target}:{want}"
        per_report[report]["total"] += 1
        per_report[report]["correct"] += int(ok)
        if not ok:
            misses.append({"message_id": mid, "expected": expected, "rules_said": decisions,
                           "note": corr.get("notes") or ""})
    total = sum(c["total"] for c in per_report.values())
    correct = sum(c["correct"] for c in per_report.values())
    return {
        "labels": total,
        "accuracy": round(correct / total, 3) if total else None,
        "per_report": {r: {"labels": c["total"], "accuracy": round(c["correct"] / c["total"], 3)}
                       for r, c in per_report.items()},
        "misses": misses,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Classification snapshot / diff / accuracy")
    ap.add_argument("--excel", default=None, help="Workbook to evaluate (default: live workspace)")
    ap.add_argument("--save", help="Write a snapshot JSON")
    ap.add_argument("--diff", help="Compare against a saved snapshot JSON")
    args = ap.parse_args()

    snap = snapshot(ReportsManager(Path(args.excel)) if args.excel else None)
    print(f"📸 {len(snap)} classified messages "
          f"({sum(1 for d in snap.values() if any(x.endswith((':open', ':closed')) for x in d))} tickets/items)")
    if args.save:
        Path(args.save).write_text(json.dumps(snap, ensure_ascii=False, indent=1))
        print(f"💾 Saved snapshot to {args.save}")
    if args.diff:
        changes = diff(json.loads(Path(args.diff).read_text()), snap)
        print(f"🔀 {len(changes)} message(s) changed decision")
        for c in changes[:50]:
            print(f"   {c['message_id'][:12]}  {c['before']} -> {c['after']}")
    acc = accuracy(snap, _corrections())
    if acc["labels"]:
        print(f"🎯 Accuracy vs {acc['labels']} owner corrections: {acc['accuracy']:.0%}")
        for r, v in acc["per_report"].items():
            print(f"   {r}: {v['accuracy']:.0%} of {v['labels']}")
        for miss in acc["misses"][:20]:
            print(f"   ✗ {miss['message_id'][:12]} expected {miss['expected']}, rules said {miss['rules_said']} {miss['note']}")
    else:
        print("🎯 No owner corrections yet — accuracy will appear once the Telegram buttons are used.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
