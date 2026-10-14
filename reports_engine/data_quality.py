"""Weekly data-quality report: how complete and well-linked the WhatsApp data feeding the reports is."""

from datetime import timedelta
from typing import Any, Dict, List

from .date_utils import DUBAI_TZ
from .manager import ReportsManager


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "—"


def generate_data_quality(manager: ReportsManager) -> Dict[str, Any]:
    groups_cfg = manager.config.get("groups", {})
    all_msgs = manager.parser.load_messages()
    dated = [m for m in all_msgs if m["datetime"]]

    # Same 7-day window the "week" reports use, so ticket stats and message stats line up.
    maint = manager.generate_maintenance(target_date="week")
    fire = manager.generate_fire_safety(target_date="week")
    start, end = manager._resolve_date_range("week", None)
    period = [m for m in dated if start <= m["datetime"] < end]

    # 1. Volume per group and silent days (possible bot downtime)
    per_group: Dict[str, int] = {name: 0 for name in groups_cfg.values()}
    for m in period:
        key = m["group_name"] if m["group_name"] in per_group else "Other groups"
        per_group[key] = per_group.get(key, 0) + 1

    first_ever = min((m["datetime"] for m in dated), default=None)
    days_with_msgs = {m["datetime"].astimezone(DUBAI_TZ).date() for m in period}
    silent_days: List[str] = []
    if first_ever:
        day = max(start.astimezone(DUBAI_TZ).date() + timedelta(days=1), first_ever.astimezone(DUBAI_TZ).date())
        last_full_day = end.astimezone(DUBAI_TZ).date() - timedelta(days=1)
        while day <= last_full_day:
            if day not in days_with_msgs:
                silent_days.append(day.strftime("%d-%m-%Y"))
            day += timedelta(days=1)

    # 2. Dates the engine could not read (these messages are invisible to every report)
    unparsed_dates = len(all_msgs) - len(dated)

    # 3. WhatsApp "Reply" usage, only over messages where the bot recorded reply info
    captured = [m for m in period if m.get("quoted_captured")]
    with_reply = sum(1 for m in captured if m["quoted_message_id"])
    # Reply-based judgements are only meaningful once reply info is recorded for most messages.
    reply_info_reliable = bool(period) and len(captured) / len(period) >= 0.8

    # 4. How maintenance + fire replies were linked to tickets
    ls = {k: maint["link_stats"][k] + fire["link_stats"][k] for k in maint["link_stats"]}
    linked_total = ls["replies_quoted"] + ls["replies_proximity"] + ls["replies_unlinked"]

    # 5. Vehicle permission requests with missing fields
    pt_grp = groups_cfg.get("parking_team", "Parking Team at Meydan")
    pt_events = manager.parser.group_media_events([m for m in period if m["group_name"] == pt_grp])
    perms = [p for p in (manager.parser.parse_vehicle_permission(e) for e in pt_events) if p]
    no_plate = sum(1 for p in perms if p["plate_no"] == "N/A")
    no_make = sum(1 for p in perms if p["car_make"] == "N/A")

    warnings: List[str] = []
    if silent_days:
        warnings.append(f"أيام بدون أي رسالة ({len(silent_days)}) — غالباً البوت كان مفصول عن الواتساب")
    if unparsed_dates:
        warnings.append(f"{unparsed_dates} رسالة تاريخها ما انقرى — مش ظاهرة في أي تقرير")
    if reply_info_reliable and ls["replies_proximity"] > ls["replies_quoted"]:
        warnings.append("ربط الردود بالتخمين (قرب الوقت) أكتر من الربط بالـ Reply — احتمال إغلاق غلط")
    if ls["tickets"] and ls["tickets_without_building"] / ls["tickets"] > 0.2:
        warnings.append("أكتر من 20% من التذاكر بدون مبنى")
    if no_plate:
        warnings.append(f"{no_plate} طلب تصريح بدون رقم لوحة")

    ok, warn = "✅", "⚠️"
    lines = [
        "🩺 *تقرير جودة الداتا — آخر 7 أيام*",
        f"📅 {start.astimezone(DUBAI_TZ).strftime('%d-%m-%Y')} → {end.astimezone(DUBAI_TZ).strftime('%d-%m-%Y')}",
        "",
        f"📥 *الرسائل:* {len(period)}",
    ]
    lines += [f"   • {name}: {count}" for name, count in per_group.items()]
    lines += [
        f"{warn if silent_days else ok} أيام بدون رسائل: {', '.join(silent_days) if silent_days else 'لا يوجد'}",
        f"{warn if unparsed_dates else ok} تواريخ ما انقرتش: {unparsed_dates}",
        "",
        "↩️ *استخدام الـ Reply في الواتساب*",
        (f"   • {with_reply} من {len(captured)} رسالة ({_pct(with_reply, len(captured))})"
         + (f" — البوت سجّل معلومة الرد لـ {len(captured)} من {len(period)} رسالة بس" if len(captured) < len(period) else "")
         if captured else "   • البوت لسه ما سجّلش معلومة الرد لأي رسالة في الفترة دي"),
        "",
        "🔗 *ربط ردود الإغلاق بالتذاكر (صيانة + حريق)*",
        f"   • بالـ Reply: {ls['replies_quoted']} ({_pct(ls['replies_quoted'], linked_total)})",
        f"   • بالتخمين (قرب الوقت): {ls['replies_proximity']} ({_pct(ls['replies_proximity'], linked_total)})",
        f"   • ما اتربطتش: {ls['replies_unlinked']} ({_pct(ls['replies_unlinked'], linked_total)})",
    ]
    if not reply_info_reliable:
        lines.append("   ℹ️ معلومة الـ Reply ما اتسجلتش لأغلب رسائل الفترة، فمقارنة Reply مقابل التخمين مش دقيقة لسه")
    lines += [
        "",
        "🏢 *التذاكر*",
        f"{warn if ls['tickets'] and ls['tickets_without_building'] / ls['tickets'] > 0.2 else ok} بدون مبنى: "
        f"{ls['tickets_without_building']} من {ls['tickets']} ({_pct(ls['tickets_without_building'], ls['tickets'])})",
        "",
        "🚗 *طلبات التصاريح*",
        f"{warn if no_plate else ok} بدون رقم لوحة: {no_plate} من {len(perms)}",
        f"   • بدون نوع العربية: {no_make} من {len(perms)}",
        "",
        "━━━━━━━━━━",
    ]
    lines += [f"⚠️ {w}" for w in warnings] if warnings else ["✅ مفيش مشاكل في جودة الداتا الأسبوع ده"]

    return {
        "text": "\n".join(lines),
        "total": len(period),
        "warnings": len(warnings),
        "metrics": {
            "messages": len(period),
            "per_group": per_group,
            "silent_days": silent_days,
            "unparsed_dates": unparsed_dates,
            "reply_captured": len(captured),
            "reply_used": with_reply,
            **ls,
            "permissions": len(perms),
            "permissions_without_plate": no_plate,
            "permissions_without_make": no_make,
        },
    }
