"""Persisted report decisions (silver) and ticket lifecycle (gold).

Bronze is the raw message store (messages_db). Every report run records here, without
changing what the report shows:
  - classification_events: how each message was classified for each report, how a reply was
    linked (quoted / similar / proximity / same closer) and under which rules version;
  - tickets: current state of every ticket / item (opened, status, closed, resolved by);
  - ticket_status_history: one row per observed status change.

    python3 -m reports_engine.warehouse --explain <message id>     # why a message ended up where it is
    python3 -m reports_engine.warehouse --summary                  # counts per report / decision
"""

import argparse
import hashlib
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config_loader import BASE_DIR
from .date_utils import now_dubai

RULES_FILE = BASE_DIR / "config" / "meydan_rules.yaml"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    report        TEXT NOT NULL,
    period        TEXT,
    run_at        TEXT NOT NULL,
    rules_version TEXT,
    items         INTEGER,
    excluded      INTEGER
);
CREATE TABLE IF NOT EXISTS classification_events (
    message_id        TEXT NOT NULL,
    report            TEXT NOT NULL,
    decision          TEXT NOT NULL,   -- ticket | reply | excluded
    link_method       TEXT,            -- quoted | similar | proximity | same closer (replies)
    linked_ticket_id  TEXT,
    reason            TEXT,
    rules_version     TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    PRIMARY KEY (message_id, report)
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     TEXT NOT NULL,
    report        TEXT NOT NULL,
    building      TEXT,
    text          TEXT,
    opened_at     TEXT,
    status        TEXT,
    resolved_by   TEXT,
    closed_at     TEXT,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (ticket_id, report)
);
CREATE TABLE IF NOT EXISTS ticket_status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   TEXT NOT NULL,
    report      TEXT NOT NULL,
    status      TEXT NOT NULL,
    changed_at  TEXT NOT NULL,         -- closing reply time when known, else the run that saw it
    run_id      INTEGER
);
CREATE INDEX IF NOT EXISTS ix_hist_ticket ON ticket_status_history(ticket_id, report);
"""


def db_path() -> Path:
    return Path(os.environ.get("WAREHOUSE_DB_PATH") or BASE_DIR / "workspace" / "reports_warehouse.db")


def rules_version() -> str:
    try:
        return hashlib.sha1(RULES_FILE.read_bytes()).hexdigest()[:10]
    except OSError:
        return "unknown"


def _iso(dt: Any) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else None


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.executescript(SCHEMA)
    return c


def record_run(report: str, period: str, items: List[Dict[str, Any]], review: List[Dict[str, Any]],
               audit: List[Dict[str, Any]]) -> None:
    """Persist one report run. Never raises: losing an audit row must not break a report."""
    try:
        _record(report, period, items, review, audit)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[warn] warehouse record failed for {report}: {exc}", file=sys.stderr)


def _record(report, period, items, review, audit) -> None:
    now = now_dubai().isoformat()
    version = rules_version()
    with _connect() as c:
        run_id = c.execute(
            "INSERT INTO runs (report, period, run_at, rules_version, items, excluded) VALUES (?,?,?,?,?,?)",
            (report, period, now, version, len(items), len(review)),
        ).lastrowid

        def event(mid, decision, method=None, linked=None, reason=None):
            c.execute(
                """INSERT INTO classification_events
                   (message_id, report, decision, link_method, linked_ticket_id, reason, rules_version, first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(message_id, report) DO UPDATE SET
                     decision=excluded.decision, link_method=excluded.link_method,
                     linked_ticket_id=excluded.linked_ticket_id, reason=excluded.reason,
                     rules_version=excluded.rules_version, last_seen_at=excluded.last_seen_at""",
                (mid, report, decision, method, linked, reason, version, now, now),
            )

        for it in items:
            mid = it.get("message_id")
            if not mid:
                continue
            event(mid, "ticket")
            status = it.get("status") or ""
            closed_at = _iso(it.get("closed_at"))
            prev = c.execute("SELECT status FROM tickets WHERE ticket_id=? AND report=?", (mid, report)).fetchone()
            c.execute(
                """INSERT INTO tickets (ticket_id, report, building, text, opened_at, status, resolved_by, closed_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(ticket_id, report) DO UPDATE SET
                     status=excluded.status, resolved_by=excluded.resolved_by,
                     closed_at=COALESCE(excluded.closed_at, tickets.closed_at), last_seen_at=excluded.last_seen_at""",
                (mid, report, it.get("building") or it.get("level"), (it.get("text") or "")[:300],
                 _iso(it.get("datetime")), status, it.get("resolved_by"), closed_at, now),
            )
            if prev is None or prev[0] != status:
                c.execute(
                    "INSERT INTO ticket_status_history (ticket_id, report, status, changed_at, run_id) VALUES (?,?,?,?,?)",
                    (mid, report, status, closed_at or _iso(it.get("datetime")) if prev is None else (closed_at or now), run_id),
                )

        for a in audit:
            st = a.get("audit_status", "")
            if st.startswith("Linked"):
                method = st[st.find("(") + 1:st.find(")")] if "(" in st else None
                linked = st.rsplit(" ", 1)[-1]
                event(a["message_id"], "reply", method, linked)

        for r in review:
            if "message_id" in r and "type" not in r:
                event(r["message_id"], "excluded", reason=r.get("reason"))
        c.commit()


def explain(message_id: str) -> Dict[str, Any]:
    with _connect() as c:
        c.row_factory = sqlite3.Row
        events = [dict(r) for r in c.execute(
            "SELECT * FROM classification_events WHERE message_id=? ORDER BY report", (message_id,))]
        tickets = [dict(r) for r in c.execute("SELECT * FROM tickets WHERE ticket_id=?", (message_id,))]
        history = [dict(r) for r in c.execute(
            "SELECT report, status, changed_at FROM ticket_status_history WHERE ticket_id=? ORDER BY id", (message_id,))]
        replies = [dict(r) for r in c.execute(
            "SELECT message_id, report, link_method FROM classification_events WHERE linked_ticket_id=? ORDER BY last_seen_at",
            (message_id,))]
    return {"events": events, "tickets": tickets, "history": history, "replies": replies}


def summary() -> List[tuple]:
    with _connect() as c:
        return c.execute(
            "SELECT report, decision, COUNT(*) FROM classification_events GROUP BY report, decision ORDER BY report, decision"
        ).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description="Report decisions and ticket history")
    ap.add_argument("--explain", help="Message id to explain")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    if args.explain:
        e = explain(args.explain)
        if not (e["events"] or e["tickets"]):
            print("No record of this message in any report run yet.")
            return 1
        for ev in e["events"]:
            extra = f" via {ev['link_method']} -> {ev['linked_ticket_id']}" if ev["decision"] == "reply" else ""
            extra += f" ({ev['reason']})" if ev.get("reason") else ""
            print(f"[{ev['report']}] {ev['decision']}{extra} | rules {ev['rules_version']} | last run {ev['last_seen_at']}")
        for t in e["tickets"]:
            print(f"ticket [{t['report']}] status={t['status']} opened={t['opened_at']} closed={t['closed_at']} by={t['resolved_by']}")
        for h in e["history"]:
            print(f"   {h['changed_at']}  {h['report']}: {h['status']}")
        for r in e["replies"]:
            print(f"   reply {r['message_id']} ({r['link_method']})")
        return 0
    for report, decision, n in summary():
        print(f"{report:12} {decision:9} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
