"""Report formatters reproducing required templates character-for-character."""

from typing import Dict, Any, List, Optional

from .master_data import display_name
from .date_utils import format_report_timestamp
from .config_loader import get_config


def generate_parking_report_text(
    report_date: str,
    report_time: str,
    email_data: Optional[Dict[str, Any]],
    smart_data: Optional[Dict[str, Any]],
    ramp_data: Optional[Dict[str, Any]],
    rumble_data: Optional[Dict[str, Any]],
) -> str:
    """
    Reproduces the exact Parking Report layout character-for-character.
    """
    bld = email_data.get("buildings", {}) if email_data else {}
    p_bld = bld.get("P", {})
    n_bld = bld.get("N", {})
    m_bld = bld.get("M", {})
    q_bld = bld.get("Q", {})
    summary = email_data.get("summary", {}) if email_data else {}

    _cap = get_config().get("capacities", {})
    _bld_cap = _cap.get("buildings", {})
    cap_p = _bld_cap.get("P", 71)
    cap_n = _bld_cap.get("N", 77)
    cap_m = _bld_cap.get("M", 165)
    cap_q = _bld_cap.get("Q", 146)
    cap_total = _bld_cap.get("total", 459)
    cap_smart = _cap.get("smart_parking", 887)
    cap_ramp = _cap.get("ramp", 127)
    cap_service = _cap.get("service_road", 127)

    # Smart Parking
    smart = smart_data or {}
    # Ramp & Service Road
    ramp = ramp_data or {}
    # Rumble Area
    rumble = rumble_data or {}

    def val(d: Dict[str, Any], k: str, default: str = "N/A") -> str:
        v = d.get(k)
        return str(v) if v is not None else default

    report = (
        f"🅿️ *MEYDAN PARKING*\n"
        f"📆 Date: {report_date}\n"
        f"⏰ Time: {report_time}\n"
        f"\n"
        f"🏢 *P BUILDING*\n"
        f"\n"
        f"• Total Slots: {cap_p}\n"
        f"• Cars Parked: {val(p_bld, 'cars_parked')} 🚗\n"
        f"• Empty Slots: {val(p_bld, 'empty_slots')} ✅\n"
        f"\n"
        f"🏢 *N BUILDING*\n"
        f"\n"
        f"• Total Slots: {cap_n}\n"
        f"• Cars Parked: {val(n_bld, 'cars_parked')} 🚗\n"
        f"• Empty Slots: {val(n_bld, 'empty_slots')} ✅\n"
        f"\n"
        f"🏢 *M BUILDING*\n"
        f"\n"
        f"• Total Slots: {cap_m}\n"
        f"• Cars Parked: {val(m_bld, 'cars_parked')} 🚗\n"
        f"• Empty Slots: {val(m_bld, 'empty_slots')} ✅\n"
        f"\n"
        f"🏢 *Q BUILDING*\n"
        f"\n"
        f"• Total Slots: {cap_q}\n"
        f"• Cars Parked: {val(q_bld, 'cars_parked')} 🚗\n"
        f"• Empty Slots: {val(q_bld, 'empty_slots')} ✅\n"
        f"\n"
        f"📊 *TOTAL SUMMARY* = {cap_total}\n"
        f"\n"
        f"• Total Cars Entered: {val(summary, 'total_cars_entered')} ⬇️\n"
        f"• Total Cars Parked: {val(summary, 'total_cars_parked')}\n"
        f"• Total Empty Slots: {val(summary, 'total_empty_slots')}\n"
        f"\n"
        f"🅿️ *SMART PARKING SUMMARY REPORT*\n"
        f"\n"
        f"• Total Slots : {cap_smart}\n"
        f"• Cars entered: {val(smart, 'cars_entered')}\n"
        f"• Occupied slots : {val(smart, 'occupied_slots')}\n"
        f"• Total Empty Slots : {val(smart, 'empty_slots')}\n"
        f"• Outside smart parking cars: {val(smart, 'outside_cars')}\n"
        f"\n"
        f"🅿️ *Ramp Area Location*\n"
        f"\n"
        f"• Total Slots Designated : {cap_ramp}\n"
        f"• Customer and Visitors: {val(ramp, 'customer_visitor')}\n"
        f"• P.O.D: {val(ramp, 'pod_cars')}\n"
        f"• Staff: {val(ramp, 'staff_cars')}\n"
        f"• Total car Entered : {val(ramp, 'total_cars_entered_ramp')}\n"
        f"• Total Car Parking at Ramp Area: {val(ramp, 'total_cars_parked_ramp')}\n"
        f"\n"
        f"🛣️ *Service Road*\n"
        f"\n"
        f"• Total Slots : {cap_service}\n"
        f"• Cars entered: {val(ramp, 'service_road_entered')}\n"
        f"• Total Occupied Slots : {val(ramp, 'service_road_parked')}\n"
        f"• Partially Blocked Parking : {val(ramp, 'partially_blocked_parking')}\n"
        f"\n"
        f"🏁 *Rumble Area*\n"
        f"\n"
        f"• Total Cars: {val(rumble, 'total_cars')} 🚗\n"
        f"• Bus 🚌: {val(rumble, 'bus')}"
    )
    return report


import re


def generate_maintenance_report_text(events: List[Dict[str, Any]], summary_stats: Dict[str, Any]) -> str:
    """Generates Report 2 — Maintenance Summary (Meydan FM)."""
    lines = ["# 🔧 Maintenance Summary – Meydan FM", ""]
    if summary_stats.get("shift_info"):
        lines.append(f"📅 Shift: {summary_stats['shift_info']}")
        lines.append("")

    # Group by building / location
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        loc = ev.get("location_heading") or "Unspecified Location"
        grouped.setdefault(loc, []).append(ev)

    # Separate chronic (>48h carry-over) for their own section
    normal_events = [e for e in events if not e.get("is_chronic")]
    chronic_events = [e for e in events if e.get("is_chronic")]

    def _render_ticket_block(items: List[Dict[str, Any]], start_idx: int) -> int:
        idx = start_idx
        for item in items:
            raw_title = item.get("issue") or item.get("text") or "Maintenance issue"
            issue_title = clean_title(raw_title)
            if item.get("is_carry_over") and not item.get("is_chronic"):
                issue_title = f"{issue_title} ⏳ [Carried over]"
            if item.get("is_chronic"):
                issue_title = f"{issue_title} 🔴 [Chronic >48h]"
            if item.get("repeat_flag"):
                issue_title = f"{issue_title} ⚠️ [{item['repeat_flag']}]"
            if item.get("chronic_building_flag"):
                issue_title = f"{issue_title} {item['chronic_building_flag']}"

            t_str = format_report_timestamp(item.get("datetime") or item.get("raw_datetime"))
            item["report_no"] = idx  # number shown in the report, used by Telegram correction buttons
            lines.append(f"{idx}. {issue_title}")
            lines.append(f"   ⏰ Reported: {t_str}")

            # hours_open
            hours = item.get("hours_open")
            if hours is not None and hours > 0:
                h_label = f"{int(hours)}h" if hours < 48 else f"{hours/24:.1f}d"
                # sla_breach is tracked in the backend (tickets.db / JSON) but not shown in the report
                lines.append(f"   🕐 Open: {h_label}")

            st = item.get("status", "Pending")
            if "done" in st.lower() or "resolved" in st.lower():
                resolved_by = item.get("resolved_by")
                done_line = "   ✅ Done"
                if resolved_by:
                    done_line += f" — {display_name(resolved_by)}"
                if item.get("manually_corrected"):
                    done_line += " (manual)"
                lines.append(done_line)
            elif "progress" in st.lower():
                note = item.get("progress_note") or "Checking"
                lines.append(f"   🔍 In Progress ({note})")
            else:
                lines.append("   ⚠️ Pending")
            idx += 1
        return idx

    # Group normal tickets by location
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ev in normal_events:
        loc = ev.get("location_heading") or "Unspecified Location"
        grouped.setdefault(loc, []).append(ev)

    item_idx = 1
    for loc, items in grouped.items():
        lines.append(f"📍 {loc}")
        item_idx = _render_ticket_block(items, item_idx)
        lines.append("")

    # Chronic issues section
    if chronic_events:
        lines.append("━━━━━━━━━━")
        lines.append("🔴 Chronic Issues (Open >48h)")
        lines.append("")
        grouped_chronic: Dict[str, List[Dict[str, Any]]] = {}
        for ev in chronic_events:
            loc = ev.get("location_heading") or "Unspecified Location"
            grouped_chronic.setdefault(loc, []).append(ev)
        for loc, items in grouped_chronic.items():
            lines.append(f"📍 {loc}")
            item_idx = _render_ticket_block(items, item_idx)
            lines.append("")

    lines.append("━━━━━━━━━━")
    lines.append("🎯 Summary")
    lines.append(f"• Completed: {summary_stats.get('completed', 0)} of {summary_stats.get('total', 0)}")
    lines.append(f"• Open: {summary_stats.get('open', 0)}")
    if chronic_events:
        lines.append(f"• 🔴 Chronic Issues (>48h): {len(chronic_events)}")
    lines.append(f"• Safety priority: {summary_stats.get('safety_priority', 'N/A')}")
    lines.append(f"• Teams: {summary_stats.get('teams', 'N/A')}")

    return "\n".join(lines)


def generate_fire_safety_report_text(events: List[Dict[str, Any]], summary_stats: Dict[str, Any]) -> str:
    """Generates Report 3 — Fire & Safety Summary (Meydan)."""
    lines = ["# 🧯 Fire & Safety Summary – Meydan", ""]
    if summary_stats.get("shift_info"):
        lines.append(f"📅 Shift: {summary_stats['shift_info']}")
        lines.append("")

    normal_events = [e for e in events if not e.get("is_chronic")]
    chronic_events = [e for e in events if e.get("is_chronic")]

    def _render_fs_block(items: List[Dict[str, Any]], start_idx: int) -> int:
        idx = start_idx
        for item in items:
            raw_title = item.get("issue") or item.get("text") or "Fire & Safety issue"
            issue_title = clean_title(raw_title)
            if item.get("is_carry_over") and not item.get("is_chronic"):
                issue_title = f"{issue_title} ⏳ [Carried over]"
            if item.get("is_chronic"):
                issue_title = f"{issue_title} 🔴 [Chronic >48h]"
            if item.get("repeat_flag"):
                issue_title = f"{issue_title} ⚠️ [{item['repeat_flag']}]"
            if item.get("chronic_building_flag"):
                issue_title = f"{issue_title} {item['chronic_building_flag']}"

            t_str = format_report_timestamp(item.get("datetime") or item.get("raw_datetime"))
            item["report_no"] = idx  # number shown in the report, used by Telegram correction buttons
            lines.append(f"{idx}. {issue_title}")
            lines.append(f"   ⏰ Reported: {t_str}")

            hours = item.get("hours_open")
            if hours is not None and hours > 0:
                h_label = f"{int(hours)}h" if hours < 48 else f"{hours/24:.1f}d"
                # sla_breach is tracked in the backend (tickets.db / JSON) but not shown in the report
                lines.append(f"   🕐 Open: {h_label}")

            st = item.get("status", "Pending")
            if "done" in st.lower() or "resolved" in st.lower():
                resolved_by = item.get("resolved_by")
                done_line = "   ✅ Done"
                if resolved_by:
                    done_line += f" — {display_name(resolved_by)}"
                if item.get("manually_corrected"):
                    done_line += " (manual)"
                lines.append(done_line)
            elif "progress" in st.lower():
                note = item.get("progress_note") or "Inspecting"
                lines.append(f"   🔍 In Progress ({note})")
            else:
                lines.append("   ⚠️ Pending")
            idx += 1
        return idx

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ev in normal_events:
        loc = ev.get("location_heading") or "Unspecified Location"
        grouped.setdefault(loc, []).append(ev)

    item_idx = 1
    for loc, items in grouped.items():
        lines.append(f"📍 {loc}")
        item_idx = _render_fs_block(items, item_idx)
        lines.append("")

    if chronic_events:
        lines.append("━━━━━━━━━━")
        lines.append("🔴 Chronic Issues (Open >48h)")
        lines.append("")
        grouped_chronic: Dict[str, List[Dict[str, Any]]] = {}
        for ev in chronic_events:
            loc = ev.get("location_heading") or "Unspecified Location"
            grouped_chronic.setdefault(loc, []).append(ev)
        for loc, items in grouped_chronic.items():
            lines.append(f"📍 {loc}")
            item_idx = _render_fs_block(items, item_idx)
            lines.append("")

    lines.append("━━━━━━━━━━")
    lines.append("🎯 Summary")
    lines.append(f"• Completed: {summary_stats.get('completed', 0)} of {summary_stats.get('total', 0)}")
    lines.append(f"• Open: {summary_stats.get('open', 0)}")
    if chronic_events:
        lines.append(f"• 🔴 Chronic Issues (>48h): {len(chronic_events)}")
    lines.append(f"• Safety priority: {summary_stats.get('safety_priority', 'N/A')}")

    return "\n".join(lines)


def generate_operations_report_text(events: List[Dict[str, Any]], summary_stats: Dict[str, Any]) -> str:
    """Generates Report 4 — Operations Summary (Meydan Security)."""
    lines = ["# 🏢 Operations Summary – Meydan Security", ""]
    if summary_stats.get("shift_info"):
        lines.append(f"📅 Shift: {summary_stats['shift_info']}")
        lines.append("")

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        loc = ev.get("location_heading") or "General Security Area"
        grouped.setdefault(loc, []).append(ev)

    item_idx = 1
    for loc, items in grouped.items():
        lines.append(f"📍 {loc}")
        for item in items:
            raw_act = item.get("activity") or item.get("text") or "Operational Activity"
            activity = re.sub(r"\s+", " ", raw_act).strip()
            vendor = item.get("vendor") or "Operations Team"
            t_str = format_report_timestamp(item.get("datetime") or item.get("raw_datetime"))
            photo_ref = item.get("photo_ref") or "N/A"
            status = item.get("status") or "Reported"

            item["report_no"] = item_idx
            lines.append(f"{item_idx}. {vendor} – {activity}")
            lines.append(f"   ⏰ Date & Time: {t_str} | 📷 Photo: {photo_ref}")
            if status == "Completed":
                lines.append("   ✅ Status: Completed" + (" (manual)" if item.get("manually_corrected") else ""))
            else:
                lines.append("   ℹ️ Status: Reported")
            item_idx += 1
        lines.append("")

    lines.append("━━━━━━━━━━")
    lines.append("🎯 Summary")
    lines.append(f"• Total Activities: {summary_stats.get('total', 0)}")
    lines.append(f"• Active Vendors: {summary_stats.get('vendors', 'N/A')}")
    lines.append(f"• Completed: {summary_stats.get('completed', 0)} of {summary_stats.get('total', 0)}")
    lines.append(f"• Reported: {summary_stats.get('reported', 0)}")

    return "\n".join(lines)


def clean_title(text: str) -> str:
    """Ticket text as a title: no leading "FYI..." / @mentions, no unresolved numeric @ids, one line."""
    t = re.sub(r"@\d{6,}", "", text or "")
    t = re.sub(r"^\s*(?:(?:fyi|dear\s+team)\b[\s.,:;!-]*|@\S+[\s,:;-]*)+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s+", " ", t)).strip()
    return t or re.sub(r"\s+", " ", text or "").strip()


def generate_parking_violations_report_text(events: List[Dict[str, Any]], summary_stats: Dict[str, Any]) -> str:
    """Generates Report 5 — Parking Violations Summary."""
    lines = ["# 🚫 Parking Violations Summary – Meydan", ""]

    item_idx = 1
    for item in events:
        lvl = item.get("level", "Unspecified Level")
        slot = item.get("slot_no", "N/A")
        lines.append(f"📍 {lvl}, Slot {slot}")

        issue_desc = clean_title(item.get("text") or "") or "Parking violation"
        reported_dt = format_report_timestamp(item.get("datetime") or item.get("raw_datetime"))
        sender = display_name(item.get("sender")) or "Security"
        photo_ref = item.get("photo_ref") or "N/A"
        st = item.get("status", "Reported")

        lines.append(f"{item_idx}. {issue_desc}")
        lines.append(f"   ⏰ Reported: {reported_dt} | 👤 Reported by: {sender}")
        lines.append(f"   📷 Photo: {photo_ref}")
        closed_by = display_name(item["resolved_by"]) if item.get("resolved_by") else ""
        if "rectified" in st.lower() or "done" in st.lower():
            lines.append("   ✅ Status: Rectified" + (f" — {closed_by}" if closed_by else ""))
        elif st.lower().startswith("closed"):
            lines.append(f"   ✅ Status: {st}" + (f" — {closed_by}" if closed_by else ""))
        else:
            lines.append(f"   ⚠️ Status: {st}")
        lines.append("")
        item_idx += 1

    if not events:
        lines.append("No active parking violations recorded for this period.")
        lines.append("")

    lines.append("━━━━━━━━━━")
    lines.append("🎯 Summary")
    lines.append(f"• Total Violations: {summary_stats.get('total', 0)}")
    lines.append(f"• Rectified: {summary_stats.get('rectified', 0)} of {summary_stats.get('total', 0)}")
    lines.append(f"• Closed (reported to department): {summary_stats.get('closed', 0)}")
    lines.append(f"• Pending / In Progress: {summary_stats.get('pending', 0)}")

    return "\n".join(lines)


def generate_vehicle_permissions_report_text(events: List[Dict[str, Any]], summary_stats: Dict[str, Any]) -> str:
    """Generates Report 6 — Vehicle Permission Requests."""
    lines = ["# 📋 Vehicle Permission Requests – Meydan", "", "📍 VIP / Smart Parking"]

    item_idx = 1
    for item in events:
        plate = item.get("plate_no", "N/A")
        make = item.get("car_make", "N/A")
        validity = item.get("validity") or "N/A"
        requester = display_name(item.get("sender")) or "Requester"
        time_str = format_report_timestamp(item.get("datetime") or item.get("raw_datetime"))
        st = item.get("status", "Pending")

        lines.append(f"{item_idx}. Plate: {plate} | Make: {make}")
        if item.get("area"):
            lines.append(f"   📍 Area: {item['area']}")
        lines.append(f"   📅 Validity: {validity}")
        lines.append(f"   👤 Requested by: {requester} | ⏰ Time: {time_str}")
        lines.append(f"   ℹ️ Status: {st}")
        item_idx += 1

    if not events:
        lines.append("No vehicle permission requests recorded for this period.")

    lines.append("")
    lines.append("━━━━━━━━━━")
    lines.append("🎯 Summary")
    lines.append(f"• Total Requests: {summary_stats.get('total', 0)}")
    lines.append(f"• Parked / Confirmed: {summary_stats.get('parked', 0)} of {summary_stats.get('total', 0)}")
    lines.append(f"• Pending: {summary_stats.get('pending', 0)}")

    return "\n".join(lines)


def generate_cleaning_report_text(
    shift_label: str,
    events: List[Dict[str, Any]],
    summary_stats: Dict[str, Any],
) -> str:
    """Generates Report 7 — Cleaning Summary – Meydan."""
    lines = ["# 🧹 Cleaning Summary – Meydan", "", f"📅 Shift: {shift_label}", ""]

    buildings: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        bld = ev.get("building") or "General"
        buildings.setdefault(bld, []).append(ev)

    item_idx = 1
    for bld, evs in sorted(buildings.items()):
        lines.append(f"📍 Building {bld}" if bld != "General" else "📍 General Area")
        for ev in evs:
            issue = ev.get("issue") or ev.get("text") or "Cleaning task"
            reported_dt = format_report_timestamp(ev.get("datetime") or ev.get("raw_datetime"))
            status = ev.get("status", "Pending")

            lines.append(f"{item_idx}. {issue}")
            lines.append(f"   ⏰ Reported: {reported_dt}")
            if ev.get("priority") == "high":
                lines.append("   🔴 High priority")
            if "done" in status.lower() or "completed" in status.lower():
                lines.append("   ✅ Done")
            elif "progress" in status.lower():
                note = status.split("(")[-1].rstrip(")") if "(" in status else ""
                lines.append(f"   🔍 In Progress ({note})" if note else "   🔍 In Progress")
            else:
                lines.append("   ⚠️ Pending")
            if ev.get("repeat_flag"):
                lines.append("   🔁 Possible repeat of an earlier ticket")
            lines.append("")
            item_idx += 1

    if not events:
        lines.append("No cleaning tasks recorded for this period.")
        lines.append("")

    lines.append("━━━━━━━━━━")
    lines.append("🎯 Summary")
    lines.append(f"• Completed: {summary_stats.get('completed', 0)} of {summary_stats.get('total', 0)}")
    lines.append(f"• Open: {summary_stats.get('open', 0)}")
    if summary_stats.get("high_priority"):
        lines.append(f"• High priority: {summary_stats['high_priority']}")

    return "\n".join(lines)

