"""Persistent ticket store for cross-shift SLA, trend detection, and resolved_by tracking."""
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from .config_loader import BASE_DIR
from .date_utils import DUBAI_TZ, now_dubai, parse_datetime

DEFAULT_DB_PATH = os.environ.get("TICKETS_DB_PATH") or str(BASE_DIR / "workspace" / "tickets.db")

SLA_HOURS = 24  # tickets open longer than this are flagged as SLA breach
CHRONIC_HOURS = 48  # carry-over older than this moves to "Chronic Issues" section
TREND_DAYS = 7  # window for repeat-building trend detection
TREND_MIN_COUNT = 2  # minimum prior occurrences to flag as chronic trend


def _conn(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def init_tickets_table(db_path: str = DEFAULT_DB_PATH) -> None:
    with _conn(db_path) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                message_id   TEXT PRIMARY KEY,
                report_type  TEXT NOT NULL,
                group_name   TEXT,
                building     TEXT,
                floor        TEXT,
                text_snippet TEXT,
                status       TEXT DEFAULT 'Pending',
                resolved_by  TEXT,
                first_seen   TEXT NOT NULL,
                last_seen    TEXT NOT NULL,
                shift_label  TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_t_building  ON tickets(building)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_t_last_seen ON tickets(last_seen)")
        c.commit()


try:
    init_tickets_table()
except Exception:
    pass


def upsert_ticket(
    message_id: str,
    report_type: str,
    group_name: str,
    building: Optional[str],
    floor: Optional[str],
    text_snippet: str,
    status: str,
    resolved_by: Optional[str],
    first_seen: str,
    last_seen: str,
    shift_label: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    with _conn(db_path) as c:
        exists = c.execute(
            "SELECT first_seen FROM tickets WHERE message_id=?", (message_id,)
        ).fetchone()
        if exists:
            c.execute(
                "UPDATE tickets SET last_seen=?, status=?, resolved_by=?, shift_label=? WHERE message_id=?",
                (last_seen, status, resolved_by, shift_label, message_id),
            )
        else:
            c.execute(
                """INSERT INTO tickets
                   (message_id, report_type, group_name, building, floor,
                    text_snippet, status, resolved_by, first_seen, last_seen, shift_label)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    message_id, report_type, group_name, building, floor,
                    text_snippet[:150], status, resolved_by, first_seen, last_seen, shift_label,
                ),
            )
        c.commit()


def get_first_seen(message_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[str]:
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT first_seen FROM tickets WHERE message_id=?", (message_id,)
        ).fetchone()
        return row["first_seen"] if row else None


def set_first_seen(message_id: str, first_seen: str, db_path: str = DEFAULT_DB_PATH) -> None:
    with _conn(db_path) as c:
        c.execute("UPDATE tickets SET first_seen=? WHERE message_id=?", (first_seen, message_id))
        c.commit()


def _to_datetime(value: Any) -> Optional[datetime]:
    """Coerce a datetime, ISO string, or WhatsApp-style timestamp text to a tz-aware datetime."""
    if isinstance(value, datetime):
        dt = value
    elif value:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            dt = parse_datetime(str(value))
    else:
        dt = None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=DUBAI_TZ)
    return dt


def get_building_occurrences(
    building: str, days: int = TREND_DAYS, db_path: str = DEFAULT_DB_PATH
) -> List[Dict[str, Any]]:
    """All tickets for a building in the last N days (excluding today's message_id handled separately)."""
    cutoff = (now_dubai() - timedelta(days=days)).isoformat()
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT * FROM tickets WHERE building=? AND last_seen >= ? ORDER BY last_seen DESC",
            (building, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]


def enrich_ticket(
    ticket: Dict[str, Any],
    report_type: str,
    group_name: str,
    shift_label: str,
    now_iso: str,
    db_path: str = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Adds hours_open, sla_breach, chronic_flag to a ticket dict,
    then upserts it into the DB.
    """
    msg_id = ticket.get("message_id", "")
    building = ticket.get("building") or ""
    status = ticket.get("status", "Pending")
    resolved_by = ticket.get("resolved_by")

    # Determine first_seen: prefer DB record (persists across reports).
    # Prefer the parsed tz-aware ticket["datetime"]; raw_datetime is WhatsApp display text.
    ticket_dt = _to_datetime(ticket.get("datetime")) or _to_datetime(ticket.get("raw_datetime"))
    ticket_dt_iso = ticket_dt.isoformat() if ticket_dt else now_iso

    db_first = get_first_seen(msg_id, db_path)
    first_seen = ticket_dt_iso
    if db_first:
        try:
            datetime.fromisoformat(db_first)
            first_seen = db_first
        except ValueError:
            # Legacy rows stored raw WhatsApp text; re-parse and repair in place.
            repaired = parse_datetime(db_first)
            if repaired:
                first_seen = repaired.isoformat()
            set_first_seen(msg_id, first_seen, db_path)

    # hours_open
    try:
        fs = _to_datetime(first_seen)
        now_dt = _to_datetime(now_iso)
        hours_open = max((now_dt - fs).total_seconds() / 3600, 0.0)
    except Exception:
        hours_open = 0.0

    ticket["hours_open"] = round(hours_open, 1)
    ticket["sla_breach"] = (
        "done" not in status.lower() and "resolved" not in status.lower()
        and hours_open > SLA_HOURS
    )

    # Trend / chronic building detection
    if building:
        history = get_building_occurrences(building, days=TREND_DAYS, db_path=db_path)
        prior = [h for h in history if h["message_id"] != msg_id]
        if len(prior) >= TREND_MIN_COUNT:
            ticket["chronic_building_flag"] = f"🔴 {len(prior) + 1}x in {TREND_DAYS} days"

    # Persist to DB
    upsert_ticket(
        message_id=msg_id,
        report_type=report_type,
        group_name=group_name,
        building=building or None,
        floor=ticket.get("floor"),
        text_snippet=ticket.get("text", "")[:150],
        status=status,
        resolved_by=resolved_by,
        first_seen=first_seen,
        last_seen=now_iso,
        shift_label=shift_label,
        db_path=db_path,
    )

    return ticket
