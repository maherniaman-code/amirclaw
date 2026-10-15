"""Owner-only daily messages: the 08:00 morning digest and the overdue-ticket reminder list.

Both are sent to the owners only (ALLOWED_USER_IDS), never to report recipients, so they may
mention SLA / ticket age that the team-facing reports keep in the backend.
"""

import contextlib
import io
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional

from .date_utils import DUBAI_TZ, format_report_timestamp, now_dubai
from .manager import ReportsManager

OVERDUE_HOURS = 48


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):  # violations report prints Excel diagnostics
        return fn(*args, **kwargs)


def _is_done(t: Dict[str, Any]) -> bool:
    return "done" in (t.get("status") or "").lower()


def _snippet(text: str, n: int = 60) -> str:
    return " ".join((text or "").split())[:n]


def _open_tickets(manager: ReportsManager, report: str, now: datetime) -> List[Dict[str, Any]]:
    """Every ticket still open right now: the current day's tickets plus carried-over / chronic ones."""
    gen = manager.generate_maintenance if report == "maintenance" else manager.generate_fire_safety
    _quiet(gen, target_date=now.strftime("%d-%m-%Y"))
    return [t for t in manager.last_items.get(report, []) if not _is_done(t)]


def generate_overdue(manager: ReportsManager, min_hours: float = OVERDUE_HOURS,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """Open maintenance / fire-safety tickets older than min_hours, oldest first."""
    now = now or now_dubai()
    items = []
    for report in ("maintenance", "fire_safety"):
        for t in _open_tickets(manager, report, now):
            hours = t.get("hours_open") or 0
            if hours > min_hours:
                items.append({
                    "id": t["message_id"],
                    "report": report,
                    "building": t.get("building") or "",
                    "hours_open": round(hours, 1),
                    "reported": format_report_timestamp(t.get("datetime")),
                    "text": _snippet(t.get("text"), 80),
                })
    items.sort(key=lambda i: i["hours_open"], reverse=True)
    return {"text": f"{len(items)} overdue ticket(s) (> {min_hours:g}h)", "total": len(items), "overdue": items}


def _ticket_lines(title: str, manager: ReportsManager, report: str, now: datetime, since: datetime) -> List[str]:
    open_now = _open_tickets(manager, report, now)
    gen = manager.generate_maintenance if report == "maintenance" else manager.generate_fire_safety
    yesterday = (now - timedelta(days=1)).strftime("%d-%m-%Y")
    _quiet(gen, target_date=yesterday, target_shift="Night")
    night = [t for t in manager.last_items.get(report, []) if not t.get("is_carry_over")]
    closed_overnight = sum(1 for t in night if _is_done(t))
    new_overnight = sum(1 for t in open_now if isinstance(t.get("datetime"), datetime) and t["datetime"] >= since)
    overdue = [t for t in open_now if (t.get("hours_open") or 0) > OVERDUE_HOURS]
    return [
        title,
        f"   • Open now: {len(open_now)} — new overnight: {new_overnight}, closed overnight: {closed_overnight}",
        f"   {'⚠️' if overdue else '✅'} Open more than {OVERDUE_HOURS} h: {len(overdue)}",
    ]


def generate_morning_digest(manager: ReportsManager, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or now_dubai()
    since = datetime.combine((now - timedelta(days=1)).date(), time(17, 0), tzinfo=DUBAI_TZ)  # night shift start

    lines = [
        "🌅 *Morning Digest*",
        f"📅 {now.strftime('%d-%m-%Y')} — since yesterday 17:00",
        "",
    ]
    lines += _ticket_lines("🔧 *Maintenance*", manager, "maintenance", now, since)
    lines.append("")
    lines += _ticket_lines("🧯 *Fire & Safety*", manager, "fire_safety", now, since)

    _quiet(manager.generate_parking_violations)
    viols = [v for v in manager.last_items.get("violations", [])
             if isinstance(v.get("datetime"), datetime) and v["datetime"] >= since]
    v_closed = sum(1 for v in viols if "rectified" in v["status"].lower() or v["status"].lower().startswith("closed"))

    _quiet(manager.generate_vehicle_permissions, target_date=now.strftime("%d-%m-%Y"))
    perms = manager.last_items.get("permissions", [])
    p_parked = sum(1 for p in perms if "parked" in p["status"].lower())
    p_noted = sum(1 for p in perms if p["status"] == "Noted")

    lines += [
        "",
        f"🚫 *Parking violations:* {len(viols)} — closed {v_closed}, pending {len(viols) - v_closed}",
        f"📋 *Permissions today:* {len(perms)} — parked {p_parked}, noted {p_noted}, no reply {len(perms) - p_parked - p_noted}",
    ]

    oldest = generate_overdue(manager, now=now)["overdue"][:3]
    if oldest:
        lines += ["", "🔴 *Oldest open tickets:*"]
        for i in oldest:
            age = f"{i['hours_open'] / 24:.1f} days"
            lines.append(f"   - {age} | {i['reported']} | {_snippet(i['text'], 55)}")

    return {"text": "\n".join(lines), "total": len(viols) + len(perms)}
