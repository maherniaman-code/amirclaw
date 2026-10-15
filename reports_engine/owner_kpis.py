"""Weekly owner KPI summary (owner only, never sent to report recipients).

Uses the same 7-day window as the "week" reports. SLA figures live here because the owner
asked for SLA to stay out of the team-facing reports but remain tracked in the backend.
"""

import contextlib
import io
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from .date_utils import DUBAI_TZ, format_report_timestamp
from .manager import ReportsManager
from .tickets_db import SLA_HOURS, CHRONIC_HOURS


def _hours_to_close(t: Dict[str, Any]) -> Optional[float]:
    opened, closed = t.get("datetime"), t.get("closed_at")
    if isinstance(opened, datetime) and isinstance(closed, datetime) and closed >= opened:
        return (closed - opened).total_seconds() / 3600
    return None


def _fmt_hours(h: Optional[float]) -> str:
    if h is None:
        return "—"
    return f"{h:.1f} ساعة" if h < 48 else f"{h / 24:.1f} يوم"


def _ticket_section(title: str, tickets: List[Dict[str, Any]]) -> List[str]:
    done = [t for t in tickets if "done" in t.get("status", "").lower()]
    open_ = [t for t in tickets if t not in done]
    close_times = [h for h in (_hours_to_close(t) for t in done) if h is not None]
    avg_close = sum(close_times) / len(close_times) if close_times else None
    sla = [t for t in open_ if (t.get("hours_open") or 0) > SLA_HOURS]
    chronic = sorted((t for t in open_ if (t.get("hours_open") or 0) > CHRONIC_HOURS),
                     key=lambda t: t.get("hours_open") or 0, reverse=True)
    buildings = Counter(t.get("building") or "بدون مبنى" for t in tickets)
    repeats = sum(1 for t in tickets if t.get("repeat_flag"))

    lines = [
        f"{title}",
        f"   • تذاكر الأسبوع: {len(tickets)} — اتقفل {len(done)}، مفتوح {len(open_)}",
        f"   • متوسط وقت الإغلاق: {_fmt_hours(avg_close)}"
        + (f" (من {len(close_times)} تذكرة)" if close_times else ""),
        f"   {'⚠️' if sla else '✅'} مفتوحة أكتر من {SLA_HOURS} ساعة (SLA): {len(sla)}",
    ]
    if tickets:
        top = ", ".join(f"{b}: {n}" for b, n in buildings.most_common(3))
        lines.append(f"   • أكتر المباني أعطال: {top}")
    if repeats:
        lines.append(f"   • أعطال متكررة: {repeats}")
    if chronic:
        lines.append(f"   🔴 أقدم التذاكر المفتوحة (أكتر من {CHRONIC_HOURS} ساعة):")
        for t in chronic[:5]:
            text = " ".join((t.get("text") or "").split())[:60]
            lines.append(f"      - {_fmt_hours(t.get('hours_open'))} | {format_report_timestamp(t.get('datetime'))} | {text}")
    return lines


def generate_owner_kpis(manager: ReportsManager) -> Dict[str, Any]:
    with contextlib.redirect_stdout(io.StringIO()):  # violations report prints Excel diagnostics
        manager.generate_maintenance(target_date="week")
        manager.generate_fire_safety(target_date="week")
        manager.generate_operations(target_date="week")
        manager.generate_parking_violations(target_date="week")
        manager.generate_vehicle_permissions(target_date="week")
    items = manager.last_items
    start, end = manager._resolve_date_range("week", None)

    ops = items.get("operations", [])
    vendors = Counter(e.get("vendor") for e in ops if e.get("vendor") and e.get("vendor") != "Operations Team")
    ops_done = sum(1 for e in ops if e.get("status") == "Completed")

    viols = items.get("violations", [])
    v_rect = sum(1 for v in viols if "rectified" in v.get("status", "").lower())
    v_closed = sum(1 for v in viols if v.get("status", "").lower().startswith("closed"))

    perms = items.get("permissions", [])
    p_parked = sum(1 for p in perms if "parked" in p.get("status", "").lower())
    p_noted = sum(1 for p in perms if p.get("status") == "Noted")

    lines = [
        "📈 *ملخص الأسبوع للمالك — آخر 7 أيام*",
        f"📅 {start.astimezone(DUBAI_TZ).strftime('%d-%m-%Y')} → {end.astimezone(DUBAI_TZ).strftime('%d-%m-%Y')}",
        "",
    ]
    lines += _ticket_section("🔧 *الصيانة*", items.get("maintenance", []))
    lines.append("")
    lines += _ticket_section("🧯 *الحريق والسلامة*", items.get("fire_safety", []))
    lines += [
        "",
        "🏢 *العمليات*",
        f"   • أنشطة: {len(ops)} — مكتملة {ops_done}",
        f"   • أكتر الشركات نشاطاً: {', '.join(f'{v}: {n}' for v, n in vendors.most_common(5)) or '—'}",
        "",
        "🚫 *مخالفات المواقف*",
        f"   • مخالفات: {len(viols)} — اتحلت {v_rect}، اتبلغت للقسم {v_closed}، معلقة {len(viols) - v_rect - v_closed}",
        "",
        "📋 *تصاريح المركبات*",
        f"   • طلبات: {len(perms)} — اتركنت {p_parked}، Noted {p_noted}، بدون رد {len(perms) - p_parked - p_noted}",
    ]
    return {
        "text": "\n".join(lines),
        "total": sum(len(items.get(k, [])) for k in ("maintenance", "fire_safety", "operations", "violations", "permissions")),
    }
