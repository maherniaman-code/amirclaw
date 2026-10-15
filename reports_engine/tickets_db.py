"""Persistent ticket store for cross-shift SLA, trend detection, and resolved_by tracking."""
import re
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


_ORDINALS = {"ground": 0, "gf": 0, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
# What can break. Two tickets are the same issue only when they name the same object; places
# ("washroom", "lift lobby") and people ("@Prakash") never make two tickets a repeat.
# Extend with `repeat_objects` in config/meydan_rules.yaml.
_REPEAT_OBJECTS = {
    "light", "bulb", "lamp", "ac", "aircon", "chiller", "fan", "grill", "duct", "door", "glass", "window",
    "lift", "elevator", "escalator", "barrier", "gate", "card reader", "access control", "toilet", "flush",
    "tap", "shower", "sink", "basin", "pipe", "leak", "leakage", "drain", "heater", "freezer", "fridge",
    "refrigerator", "oven", "microwave", "coffee machine", "socket", "power", "electrical", "ups", "cctv",
    "camera", "alarm", "smoke", "sprinkler", "extinguisher", "hose", "signage", "ceiling", "panel", "tile",
    "carpet", "chair", "table", "desk", "curtain", "blind", "lock", "handle", "hinge", "holder", "screen",
    "tv", "printer", "network", "wifi", "intercom", "pump", "generator", "roof", "wall",
}
_PLACE_PHRASES = ("lift lobby", "lift loby", "lift area", "door to", "fire exit staircase", "exit staircase")


def floor_of(text: str) -> Optional[int]:
    """Floor number written in a ticket ("3rd Floor", "first floor", "GF", "level 4"), else None."""
    t = text.lower()
    m = re.search(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:floor|flr)\b|\blevel\s*(\d{1,2})\b|\blvl\s*(\d{1,2})\b", t)
    if m:
        return int(next(g for g in m.groups() if g))
    m = re.search(r"\b(ground|gf|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b(?:\s*floor)?", t)
    if m and (m.group(1) == "gf" or re.search(rf"\b{m.group(1)}\s*floor", t)):
        return _ORDINALS[m.group(1)]
    return None


def issue_words(text: str) -> set:
    """Objects a ticket says are broken ("Fire Exit Signage Light is not Working" -> {signage, light})."""
    t = re.sub(r"@\S+", " ", text.lower())
    for phrase in _PLACE_PHRASES:
        t = t.replace(phrase, " ")
    try:
        from .config_loader import get_config
        objects = _REPEAT_OBJECTS | {o.lower() for o in get_config().get("repeat_objects", [])}
    except Exception:  # pragma: no cover - config unreadable
        objects = _REPEAT_OBJECTS
    return {o for o in objects if re.search(rf"\b{re.escape(o)}(?:s|es)?\b", t)}


def same_issue(text_a: str, text_b: str) -> bool:
    """Same fault again: not on two different floors, and naming at least one common broken thing."""
    fa, fb = floor_of(text_a), floor_of(text_b)
    if fa is not None and fb is not None and fa != fb:
        return False
    return bool(issue_words(text_a) & issue_words(text_b))


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
        # Only the same fault coming back counts, not every ticket in the building
        text = ticket.get("text", "")
        prior = [h for h in history if h["message_id"] != msg_id and h.get("report_type") == report_type
                 and same_issue(text, h.get("text_snippet") or "")]
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
