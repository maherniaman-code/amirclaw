"""Central Manager coordinating report extraction, deduplication, generation, and Excel output."""

import json
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from .config_loader import get_config, BASE_DIR
from .date_utils import DUBAI_TZ, parse_datetime, is_within_freshness, get_shift_for_timestamp, format_report_timestamp
from .whatsapp_parser import WhatsAppParser
from .deduplicator import deduplicate_events
from .email_reader import fetch_parking_email_from_imap
from .corrections_db import get_correction_for_message
from .tickets_db import enrich_ticket, CHRONIC_HOURS
from .report_generators import (
    generate_parking_report_text,
    generate_maintenance_report_text,
    generate_fire_safety_report_text,
    generate_operations_report_text,
    generate_parking_violations_report_text,
    generate_vehicle_permissions_report_text,
    generate_cleaning_report_text,
)
from .excel_exporter import generate_parking_excel, generate_generic_report_excel, generate_violations_excel


def is_closure_text(text: str, closure_kws: List[str]) -> bool:
    """True if a closure keyword appears and is not negated (e.g. 'not yet rectified')."""
    t = text.lower()
    for kw in closure_kws:
        kw_re = re.escape(kw.lower())
        if not re.search(rf"\b{kw_re}\b", t):
            continue
        if re.search(rf"\b(?:not|never|yet to be|pending)\s+(?:\w+\s+){{0,2}}{kw_re}\b", t):
            continue
        return True
    return False


def find_ticket_by_id(tickets_dict: Dict[str, Dict[str, Any]], q_id: str) -> Optional[Dict[str, Any]]:
    if not q_id:
        return None
    if q_id in tickets_dict:
        return tickets_dict[q_id]
    for t in tickets_dict.values():
        if q_id in t.get("source_message_ids", []):
            return t
    return None


def find_nearest_open_ticket_by_proximity(
    tickets: Dict[str, Dict[str, Any]],
    event_dt: Optional[datetime],
    max_minutes: int = 30,
) -> Optional[Dict[str, Any]]:
    """Fallback when quoted_id is absent: links reply to the most-recent open ticket within max_minutes.

    Handles the real-world case where 97%+ of WhatsApp replies lack a quoted_message_id.
    """
    if not event_dt:
        return None
    best: Optional[Dict[str, Any]] = None
    best_diff = float("inf")
    for t in tickets.values():
        if "done" in t.get("status", "").lower():
            continue
        t_dt = t.get("datetime")
        if not isinstance(t_dt, datetime):
            continue
        diff_secs = (event_dt - t_dt).total_seconds()
        if 0 < diff_secs <= max_minutes * 60 and diff_secs < best_diff:
            best_diff = diff_secs
            best = t
    return best


def _keyword_at_word_start(text_lower: str, keywords: List[str]) -> Optional[str]:
    """
    First keyword that begins a word in text_lower, or None.
    Keywords may be word roots: "remov" matches remove/removed/removing,
    while "arki" no longer matches inside "parking".
    """
    for kw in keywords:
        if re.search(r"(?<!\w)" + re.escape(kw.lower()), text_lower):  # \w covers Arabic letters too
            return kw
    return None


def classify_operations_message(text: str, ops_cfg: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """
    Classify a Security Team message for the operations report.
    Returns (category, vendor): category is "activity", "fault" or "none"; vendor is the
    canonical vendor name (aliases resolved) or None.
    A fault marker wins unless the message names a vendor/party AND an activity
    ("Kone technician repairing the lift that is out of service" is still vendor work).
    """
    t_low = text.lower()
    aliases = {k.lower(): v.lower() for k, v in ops_cfg.get("vendor_aliases", {}).items()}
    vendor = _keyword_at_word_start(t_low, ops_cfg.get("vendors", []) + list(aliases))
    vendor = aliases.get(vendor, vendor)
    # Generic parties ("workers", "contractor", "technician") count as someone doing the work
    party = vendor or _keyword_at_word_start(t_low, ops_cfg.get("parties", []))
    has_act = _keyword_at_word_start(t_low, ops_cfg.get("activities", [])) is not None
    if _keyword_at_word_start(t_low, ops_cfg.get("fault_markers", [])) is not None and not (party and has_act):
        return "fault", vendor
    if party or has_act:
        return "activity", vendor
    return "none", vendor


# Words too generic to tie a completion message to an earlier activity
_LINK_STOPWORDS = {
    "building", "floor", "ground", "vendor", "vendors", "work", "team", "staff", "company",
    "with", "from", "that", "this", "there", "have", "will", "they", "their", "been", "were",
    "today", "near", "side", "inside", "outside", "done", "left", "completed", "complete", "finished",
    # reply chatter that says nothing about which ticket it is
    "please", "kindly", "follow", "update", "check", "asap", "thanks", "noted", "okay", "sir",
    "replaced", "fixed", "working", "rectified", "resolved", "restored", "cleared",
}


def _is_completion(text_lower: str, completion_kws: List[str]) -> bool:
    """A completion keyword at a word start that is not negated ("not yet completed")."""
    for kw in completion_kws:
        kw_re = re.escape(kw.lower())
        if not re.search(r"(?<!\w)" + kw_re, text_lower):
            continue
        if re.search(r"\b(?:not|never|yet to be|pending)\s+(?:\w+\s+){0,2}" + kw_re, text_lower):
            continue
        return True
    return False


def _content_words(text_lower: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", text_lower) if w not in _LINK_STOPWORDS}


def _find_activity_to_complete(
    events: List[Dict[str, Any]], ev: Dict[str, Any], max_hours: int = 12
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    The open activity a completion message refers to: the quoted one if WhatsApp recorded a
    Reply, else the most recent open activity within max_hours sharing a content word
    ("Table installation work done" -> "Fit Movers vendors are installing a new ... table").
    """
    open_events = [e for e in events if e["status"] != "Completed"]
    q_id = ev.get("quoted_message_id", "")
    if q_id:
        for e in open_events:
            if q_id == e["message_id"] or q_id in e.get("source_message_ids", []):
                return e, "quoted"
    ev_dt = ev.get("datetime")
    words = _content_words(ev["text"].lower())
    if not ev_dt or not words:
        return None, ""
    best = None
    for e in open_events:
        e_dt = e.get("datetime")
        if not isinstance(e_dt, datetime) or not (0 < (ev_dt - e_dt).total_seconds() <= max_hours * 3600):
            continue
        if words & _content_words(e["text"].lower()) and (best is None or e_dt > best["datetime"]):
            best = e
    return (best, "similar") if best else (None, "")


def _vendor_from_recent_activity(
    events: List[Dict[str, Any]], ev_dt: Optional[datetime], max_minutes: int
) -> Optional[Dict[str, Any]]:
    """
    Most recent earlier activity within max_minutes that names a vendor. Guards often post
    follow-up photos of the same job without repeating the company
    ("Al zawaya ... remove 6 chairs" 18:08 -> "These two sofa are shifted ..." 18:18).
    """
    if not isinstance(ev_dt, datetime) or max_minutes <= 0:
        return None
    best = None
    for e in events:
        e_dt = e.get("datetime")
        if e.get("vendor_source") != "named" or not isinstance(e_dt, datetime):
            continue
        if 0 <= (ev_dt - e_dt).total_seconds() <= max_minutes * 60 and (best is None or e_dt > best["datetime"]):
            best = e
    return best


def _is_exact_closure(text: str, exact_closures: List[str]) -> bool:
    """Whole-message closures like "Working" or "Ok now" (punctuation/emoji ignored)."""
    norm = " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())
    return norm in {e.lower() for e in exact_closures}


def find_open_ticket_by_shared_word(
    tickets: Dict[str, Dict[str, Any]], ev_dt: Optional[datetime], text: str, max_hours: int = 12
) -> Optional[Dict[str, Any]]:
    """Most recent open ticket within max_hours sharing a content word ("Light replaced" -> "...light not working")."""
    words = _content_words(text.lower())
    if not isinstance(ev_dt, datetime) or not words:
        return None
    best = None
    for t in tickets.values():
        t_dt = t.get("datetime")
        if "done" in t.get("status", "").lower() or not isinstance(t_dt, datetime):
            continue
        if 0 < (ev_dt - t_dt).total_seconds() <= max_hours * 3600 and words & _content_words(t["text"].lower()):
            if best is None or t_dt > best["datetime"]:
                best = t
    return best


class ReportsManager:
    def __init__(self, excel_path: Optional[Path] = None):
        self.excel_path = excel_path or (BASE_DIR / "workspace" / "whatsapp_messages.xlsx")
        self.config = get_config()
        self.parser = WhatsAppParser(self.excel_path)

    # ─────────────────────────────────────────────────────────────
    # REPORT 1: PARKING REPORT
    # ─────────────────────────────────────────────────────────────
    def generate_parking(self, slot_request: str = "latest", report_date: Optional[str] = None) -> Dict[str, Any]:
        messages = self.parser.load_messages()
        now_dubai = datetime.now(DUBAI_TZ)
        date_str = report_date or now_dubai.strftime("%d-%m-%Y")

        slots = self.config.get("timing", {}).get("parking_slots", ["10:00", "12:00", "14:00"])
        freshness_mins = self.config.get("timing", {}).get("freshness_window_minutes", 60)

        # Resolve target slot
        target_slot = "14:00"
        if slot_request in slots:
            target_slot = slot_request
        elif slot_request == "10:00":
            target_slot = "10:00"
        elif slot_request == "12:00":
            target_slot = "12:00"
        elif slot_request == "14:00":
            target_slot = "14:00"
        elif slot_request == "latest":
            # Find closest past slot or last slot
            target_slot = "14:00"
            for s in slots:
                sh, sm = map(int, s.split(":"))
                if now_dubai.time() >= time(sh, sm):
                    target_slot = s

        # Build slot datetime for freshness checking
        sh, sm = map(int, target_slot.split(":"))
        # Parse target date
        try:
            d_obj = datetime.strptime(date_str, "%d-%m-%Y").date()
        except Exception:
            d_obj = now_dubai.date()
        slot_dt = datetime.combine(d_obj, time(sh, sm), tzinfo=DUBAI_TZ)

        diagnostics: List[str] = []
        source_audit: List[Dict[str, Any]] = []

        # 1. Email building figures from existing program via IMAP
        email_data, email_status = fetch_parking_email_from_imap(date_str, target_slot)
        if not email_data:
            diagnostics.append(f"Building figures email missing or unavailable for slot {target_slot} ({email_status})")
            source_audit.append({
                "source_id": "IMAP_EMAIL",
                "group_name": "Mailbox",
                "sender": "parking_dashboard_gui",
                "timestamp": date_str + " " + target_slot,
                "section": "Building Figures (P, N, M, Q)",
                "status": f"Missing: {email_status}",
            })
        else:
            if not email_data.get("validation_passed", True):
                for disc in email_data.get("discrepancies", []):
                    diagnostics.append(f"Email Validation Discrepancy: {disc}")
            source_audit.append({
                "source_id": "EMAIL_" + date_str + "_" + target_slot,
                "group_name": "Mailbox",
                "sender": "parking_dashboard_gui",
                "timestamp": email_data["date"] + " " + email_data["time"],
                "section": "Building Figures (P, N, M, Q)",
                "status": "Verified" if email_data.get("validation_passed") else "Discrepancy",
            })

        # Filter WhatsApp messages
        ms_grp = self.config.get("groups", {}).get("parking_multistory", "Multistory Parking Building Meydan")
        pt_grp = self.config.get("groups", {}).get("parking_team", "Parking Team at Meydan")

        smart_data: Optional[Dict[str, Any]] = None
        ramp_data: Optional[Dict[str, Any]] = None
        rumble_data: Optional[Dict[str, Any]] = None

        # Process backwards (latest messages first)
        for m in reversed(messages):
            grp = m["group_name"]
            txt = m["text"]
            m_dt = m["datetime"]

            # Smart parking: by hour written in text ("as of 14 o'clock" -> 14:00 slot) on target date
            if not smart_data and grp == ms_grp and m_dt and m_dt.date() == d_obj:
                parsed_smart = self.parser.parse_smart_parking(txt)
                if parsed_smart:
                    hr = parsed_smart["hour_written"]
                    if hr == sh or (hr == 0 and is_within_freshness(m_dt, slot_dt, freshness_mins)):
                        smart_data = parsed_smart
                        source_audit.append({
                            "source_id": m["message_id"],
                            "group_name": grp,
                            "sender": m["sender"],
                            "timestamp": m["raw_datetime"],
                            "section": "Smart Parking",
                            "status": "Valid",
                        })

            # Ramp / Service Road on target date
            if not ramp_data and grp == ms_grp and m_dt and m_dt.date() == d_obj:
                parsed_ramp = self.parser.parse_ramp_service_road(txt)
                if parsed_ramp:
                    if is_within_freshness(m_dt, slot_dt, freshness_mins):
                        ramp_data = parsed_ramp
                        # Check service road reported slots vs 127
                        if parsed_ramp.get("reported_service_road_slots") not in [None, 127]:
                            diagnostics.append(
                                f"Service Road capacity reported as {parsed_ramp['reported_service_road_slots']} in WA message (fixed at 127 in report)"
                            )
                        source_audit.append({
                            "source_id": m["message_id"],
                            "group_name": grp,
                            "sender": m["sender"],
                            "timestamp": m["raw_datetime"],
                            "section": "Ramp Area & Service Road",
                            "status": "Valid",
                        })
                    elif (slot_dt - m_dt).total_seconds() > (freshness_mins * 60):
                        diagnostics.append(f"Ramp report found but older than {freshness_mins} minutes freshness window")

            # Rumble area on target date
            if not rumble_data and grp == pt_grp and m_dt and m_dt.date() == d_obj:
                parsed_rumble = self.parser.parse_rumble_area(txt)
                if parsed_rumble:
                    if is_within_freshness(m_dt, slot_dt, freshness_mins):
                        rumble_data = parsed_rumble
                        source_audit.append({
                            "source_id": m["message_id"],
                            "group_name": grp,
                            "sender": m["sender"],
                            "timestamp": m["raw_datetime"],
                            "section": "Rumble Area",
                            "status": "Valid",
                        })



        if smart_data and smart_data.get("diagnostics"):
            diagnostics.extend(smart_data["diagnostics"])
        if ramp_data and ramp_data.get("diagnostics"):
            diagnostics.extend(ramp_data["diagnostics"])

        if not smart_data:
            diagnostics.append(f"Smart Parking message not found for slot {target_slot}")
        if not ramp_data:
            diagnostics.append(f"Ramp & Service Road message not found or outside freshness window for slot {target_slot}")
        if not rumble_data:
            diagnostics.append(f"Rumble Area message not found or outside freshness window for slot {target_slot}")

        report_text = generate_parking_report_text(
            report_date=date_str,
            report_time=target_slot,
            email_data=email_data,
            smart_data=smart_data,
            ramp_data=ramp_data,
            rumble_data=rumble_data,
        )

        excel_path = generate_parking_excel(
            report_text=report_text,
            email_data=email_data,
            smart_data=smart_data,
            ramp_data=ramp_data,
            rumble_data=rumble_data,
            source_audit=source_audit,
            filename_prefix=f"Meydan_Parking_{target_slot.replace(':', '')}",
        )

        return {
            "slot": target_slot,
            "date": date_str,
            "text": report_text,
            "excel_path": str(excel_path),
            "diagnostics": diagnostics,
            "audit_count": len(source_audit),
        }

    def _get_shift_range(self, date_str: str, shift_name: str) -> Tuple[datetime, datetime]:
        d = datetime.strptime(date_str, "%d-%m-%Y").date()
        if shift_name.lower() == "full":
            # Whole operational day: Day shift + following Night shift (05:00 → 05:00 next day)
            start = datetime.combine(d, time(5, 0), tzinfo=DUBAI_TZ)
            end = datetime.combine(d + timedelta(days=1), time(5, 0), tzinfo=DUBAI_TZ)
            return start, end
        if shift_name.lower() == "day":
            start = datetime.combine(d, time(5, 0), tzinfo=DUBAI_TZ)
            end = datetime.combine(d, time(17, 0), tzinfo=DUBAI_TZ)
        else:
            start = datetime.combine(d, time(17, 0), tzinfo=DUBAI_TZ)
            end = datetime.combine(d + timedelta(days=1), time(5, 0), tzinfo=DUBAI_TZ)
        return start, end

    def _resolve_date_range(self, target_date: Optional[str], target_shift: Optional[str]) -> Tuple[datetime, datetime]:
        now = datetime.now(DUBAI_TZ)
        if target_date == "week":
            return now - timedelta(days=7), now
        if target_date == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start, now
        date = target_date or now.strftime("%d-%m-%Y")
        shift = target_shift or ("day" if 5 <= now.hour < 17 else "night")
        return self._get_shift_range(date, shift)

    @staticmethod
    def _link_stats(
        source_audit: List[Dict[str, Any]],
        unlinked: List[Dict[str, Any]],
        tickets: List[Dict[str, Any]],
        start: datetime,
        end: datetime,
    ) -> Dict[str, int]:
        """How replies inside [start, end) were linked to tickets, and ticket location coverage."""
        def in_range(raw: str) -> bool:
            dt = parse_datetime(raw)
            return bool(dt and start <= dt < end)

        period_tickets = [t for t in tickets if not t.get("is_carry_over")]
        return {
            "replies_quoted": sum(1 for a in source_audit if a["audit_status"].startswith("Linked (quoted)") and in_range(a["raw_datetime"])),
            "replies_proximity": sum(1 for a in source_audit if a["audit_status"].startswith("Linked (proximity)") and in_range(a["raw_datetime"])),
            "replies_unlinked": sum(1 for u in unlinked if u["reason"].startswith("Unlinked reply") and in_range(u["raw_datetime"])),
            "tickets": len(period_tickets),
            "tickets_without_building": sum(1 for t in period_tickets if not t.get("building")),
        }

    def _date_range_label(self, target_date: Optional[str], target_shift: Optional[str]) -> str:
        if target_date == "week":
            return "Last 7 Days"
        if target_date == "month":
            return datetime.now(DUBAI_TZ).strftime("%B %Y — Full Month")
        if target_date and target_shift and target_shift.lower() == "full":
            return f"Full Day ({target_date})"
        if target_date and target_shift:
            return f"{target_shift.title()} Shift ({target_date})"
        now = datetime.now(DUBAI_TZ)
        shift = target_shift or ("Day" if 5 <= now.hour < 17 else "Night")
        date = target_date or now.strftime("%d-%m-%Y")
        return f"{shift.title()} Shift ({date})"

    def _link_reply(
        self, tickets: Dict[str, Dict[str, Any]], ev: Dict[str, Any], quoted_id: str
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """Ticket a reply belongs to: quoted Reply, else shared word within hours, else latest open within minutes."""
        target = find_ticket_by_id(tickets, quoted_id)
        if target or quoted_id:
            return target, "quoted"
        target = find_open_ticket_by_shared_word(
            tickets, ev.get("datetime"), ev["text"], self.config.get("reply_link_hours", 12)
        )
        if target:
            return target, "similar"
        target = find_nearest_open_ticket_by_proximity(
            tickets, ev.get("datetime"), self.config.get("reply_link_minutes", 30)
        )
        return target, "proximity"

    def _filter_to_period(
        self, items: List[Dict[str, Any]], target_date: Optional[str]
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        For reports without shifts (violations, permissions, cleaning): no date keeps every item;
        a date / "week" / "month" keeps only items reported in that period (date = full operational day).
        """
        if not target_date:
            return items, None
        date, shift = self._default_period(target_date, None)
        start, end = self._resolve_date_range(date, shift)
        kept = [i for i in items if isinstance(i.get("datetime"), datetime) and start <= i["datetime"] < end]
        return kept, self._date_range_label(date, shift)

    @staticmethod
    def _with_period_line(report_text: str, label: Optional[str]) -> str:
        """Insert "📅 Period: …" under the report title."""
        if not label:
            return report_text
        title, _, rest = report_text.partition("\n")
        return f"{title}\n\n📅 Period: {label}\n{rest}"

    @staticmethod
    def _default_period(target_date: Optional[str], target_shift: Optional[str]) -> Tuple[str, Optional[str]]:
        """
        No date → the shift in progress. A specific date without a shift (Telegram date
        buttons) → that whole operational day, 05:00 → 05:00 next day.
        """
        now = datetime.now(DUBAI_TZ)
        if target_date in ("week", "month") or target_shift:
            return target_date, target_shift
        if not target_date:
            return now.strftime("%d-%m-%Y"), ("Day" if 5 <= now.hour < 17 else "Night")
        # Before 05:00 the operational day is still yesterday's (its night shift is running)
        if target_date == now.strftime("%d-%m-%Y") and now.hour < 5:
            target_date = (now - timedelta(days=1)).strftime("%d-%m-%Y")
        return target_date, "Full"

    # ─────────────────────────────────────────────────────────────
    # REPORT 2: MAINTENANCE REPORT (Meydan FM Team💪)
    # ─────────────────────────────────────────────────────────────
    def generate_maintenance(
        self,
        target_date: Optional[str] = None,
        target_shift: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_date, target_shift = self._default_period(target_date, target_shift)

        all_msgs = self.parser.load_messages(load_images=True)  # thumbnails for the Excel
        fm_grp = self.config.get("groups", {}).get("fm", "Meydan FM Team💪")
        fm_msgs = [m for m in all_msgs if m["group_name"] == fm_grp]
        grouped_events = self.parser.group_media_events(fm_msgs)

        tickets: Dict[str, Dict[str, Any]] = {}
        unlinked: List[Dict[str, Any]] = []
        source_audit: List[Dict[str, Any]] = []

        closure_kws = self.config.get("closure", ["done", "resolved", "replaced", "rectified"])
        closure_exact = self.config.get("closure_exact", [])
        progress_kws = self.config.get("in_progress", ["checking", "notified to", "will replace"])
        followup_kws = self.config.get("followup_not_ticket", ["follow up", "is it restored", "urgent action required", "daily issues"])
        fault_kws = self.config.get("fault", [
            "not working", "not opening", "not closing", "not cold", "not stable",
            "blinking", "shaking", "hanging", "loose", "need to check", "need to fix",
            "need to rectify", "need to increase", "require maintenance",
            "electrical power issue", "cooling is very low", "out of service", "free"
        ])

        for ev in grouped_events:
            msg_id = ev["message_id"]
            text = ev["text"]
            t_low = text.lower()
            quoted_id = ev.get("quoted_message_id", "")

            # Check manual correction first
            corr = get_correction_for_message(msg_id, fm_grp, text)
            if corr and corr.get("target_report") == "ignore":
                continue

            # Check if this is a short reply / closure / follow-up / ack
            is_closure = is_closure_text(t_low, closure_kws) or _is_exact_closure(text, closure_exact)
            is_progress = any(p in t_low for p in progress_kws)
            is_ack = self.parser.is_non_operational(text)
            is_followup = any(f in t_low for f in followup_kws) or (not is_closure and any(re.search(rf"\b{re.escape(k.lower())}\b", t_low) for k in closure_kws))
            is_empty_or_image = text.strip() in ["(Image)", ""] or (len(text.strip()) <= 4 and not re.search(r"\w{3,}", text))

            if is_closure or is_progress or is_ack or is_followup or is_empty_or_image:
                target_ticket, link_method = self._link_reply(tickets, ev, quoted_id)
                if target_ticket:
                    if is_closure:
                        target_ticket["status"] = "Done"
                        target_ticket["resolved_by"] = ev["sender"]
                    elif is_progress:
                        target_ticket["status"] = "In Progress"
                        target_ticket["progress_note"] = text[:30]
                    target_ticket.setdefault("replies", []).append(text)
                    source_audit.append({
                        "message_id": msg_id,
                        "group_name": fm_grp,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                        "quoted_message_id": quoted_id,
                        "audit_status": f"Linked ({link_method}) -> Ticket {target_ticket['message_id']}",
                    })
                else:
                    unlinked.append({
                        "message_id": msg_id,
                        "reason": "Unlinked reply without matching quoted ticket ID",
                        "text": text,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                    })
                continue

            # A non-reply message is a ticket ONLY if it matches fault keywords (or manual correction)
            has_fault = any(k in t_low for k in fault_kws) or (corr and corr.get("target_status"))
            if not has_fault:
                unlinked.append({
                    "message_id": msg_id,
                    "reason": "Non-ticket message (status/question/explanation/bare mention)",
                    "text": text,
                    "sender": ev["sender"],
                    "raw_datetime": ev["raw_datetime"],
                })
                continue

            bld, floor, heading = self.parser.extract_building_and_floor(text)
            if not heading:
                # Named place ("GSU Room", "Lobby"…) or the report's generic heading, never message text
                heading = self._extract_named_location(text) or ""

            status = "Pending"
            if corr and corr.get("target_status"):
                status = corr["target_status"]

            # Possible repeat detection against earlier reported tickets
            repeat_flag = None
            for prev_id, prev_t in tickets.items():
                if prev_t.get("building") and prev_t["building"] == bld:
                    if ("ac" in t_low and "ac" in prev_t["text"].lower()) or ("control room" in t_low and "control room" in prev_t["text"].lower()):
                        prev_time = format_report_timestamp(prev_t["datetime"])
                        repeat_flag = f"Possible repeat of ticket from {prev_time}"
                        break

            ticket = {
                "message_id": msg_id,
                "datetime": ev["datetime"],
                "raw_datetime": ev["raw_datetime"],
                "building": bld,
                "floor": floor,
                "location_heading": heading,
                "issue": text,
                "text": text,
                "status": status,
                "repeat_flag": repeat_flag,
                "sender": ev["sender"],
                "group_name": fm_grp,
                "photos": ev.get("photos", []),
                "photos_data": ev.get("photos_data", []),
                "quoted_message_id": quoted_id,
                "source_message_ids": ev.get("source_message_ids", [msg_id]),
            }
            tickets[msg_id] = ticket
            source_audit.append({
                "message_id": msg_id,
                "group_name": fm_grp,
                "sender": ev["sender"],
                "raw_datetime": ev["raw_datetime"],
                "quoted_message_id": quoted_id,
                "audit_status": "Valid Ticket",
            })

        ticket_list = list(tickets.values())
        unique_tickets, flagged_dups = deduplicate_events(ticket_list)

        # Date-range filtering & carry-over (chronic split at CHRONIC_HOURS)
        shift_start, shift_end = self._resolve_date_range(target_date, target_shift)
        filtered = []
        chronic_tickets = []
        is_range_query = target_date in ("week", "month")
        for t in unique_tickets:
            t_dt = t.get("datetime")
            if not t_dt:
                continue
            if shift_start <= t_dt < shift_end:
                t["is_carry_over"] = False
                filtered.append(t)
            elif not is_range_query and t_dt < shift_start and "done" not in t["status"].lower():
                age_hours = (shift_start - t_dt).total_seconds() / 3600
                t_carried = dict(t)
                t_carried["is_carry_over"] = True
                if age_hours >= CHRONIC_HOURS:
                    t_carried["is_chronic"] = True
                    chronic_tickets.append(t_carried)
                else:
                    filtered.append(t_carried)
        unique_tickets = filtered + chronic_tickets

        # Enrich tickets: hours_open, sla_breach, chronic_building_flag, persist to DB
        now_iso = datetime.now(DUBAI_TZ).isoformat()
        shift_label = self._date_range_label(target_date, target_shift)
        for t in unique_tickets:
            enrich_ticket(t, "maintenance", fm_grp, shift_label, now_iso)

        # Summary metrics
        completed = sum(1 for t in unique_tickets if "done" in t["status"].lower())
        total = len(unique_tickets)
        open_count = total - completed
        sla_breaches = sum(1 for t in unique_tickets if t.get("sla_breach"))

        # Teams derived strictly from real tickets
        all_text = " ".join(t["text"] for t in unique_tickets)
        matched_teams = []
        for team in self.config.get("teams", ["imdaad", "avs", "kone"]):
            if re.search(rf"\b{team}\b", all_text, re.IGNORECASE):
                matched_teams.append(team.upper() if team.lower() == "avs" else team.title())
        teams_str = ", ".join(matched_teams) if matched_teams else "N/A"

        # Safety priority: real count of safety tickets
        safety_kws = ["fire", "hazard", "emergency", "hanging", "loose", "barrier", "gate"]
        safety_count = sum(1 for t in unique_tickets if any(sk in t["text"].lower() for sk in safety_kws))
        prio_str = f"{safety_count} tickets" if safety_count > 0 else "N/A"

        summary_stats = {
            "completed": completed,
            "total": total,
            "open": open_count,
            "sla_breaches": sla_breaches,
            "safety_priority": prio_str,
            "teams": teams_str,
            "shift_info": shift_label,
        }

        report_text = generate_maintenance_report_text(unique_tickets, summary_stats)
        excel_path = generate_generic_report_excel(
            report_title="Maintenance Summary – Meydan FM",
            report_text=report_text,
            events=unique_tickets,
            unlinked_or_needs_review=unlinked + flagged_dups,
            source_audit=source_audit,
            filename_prefix="Meydan_FM_Maintenance",
        )

        return {
            "text": report_text,
            "excel_path": str(excel_path),
            "total": total,
            "open": open_count,
            "completed": completed,
            "sla_breaches": sla_breaches,
            "unlinked_count": len(unlinked),
            "link_stats": self._link_stats(source_audit, unlinked, unique_tickets, shift_start, shift_end),
        }

    # ─────────────────────────────────────────────────────────────
    # REPORT 3: FIRE & SAFETY (Fire & Safety Team Meydan)
    # ─────────────────────────────────────────────────────────────
    def generate_fire_safety(
        self,
        target_date: Optional[str] = None,
        target_shift: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_date, target_shift = self._default_period(target_date, target_shift)

        all_msgs = self.parser.load_messages(load_images=True)  # thumbnails for the Excel
        fs_grp = self.config.get("groups", {}).get("fire_safety", "Fire & Safety Team Meydan")
        fs_msgs = [m for m in all_msgs if m["group_name"] == fs_grp]
        grouped_events = self.parser.group_media_events(fs_msgs)

        tickets: Dict[str, Dict[str, Any]] = {}
        unlinked: List[Dict[str, Any]] = []
        source_audit: List[Dict[str, Any]] = []

        closure_kws = self.config.get("closure", ["done", "resolved", "replaced", "rectified"])
        closure_exact = self.config.get("closure_exact", [])
        progress_kws = self.config.get("in_progress", ["checking", "notified to", "will replace"])
        followup_kws = self.config.get("followup_not_ticket", ["follow up", "is it restored", "urgent action required", "daily issues"])

        for ev in grouped_events:
            msg_id = ev["message_id"]
            text = ev["text"]
            t_low = text.lower()
            quoted_id = ev.get("quoted_message_id", "")

            corr = get_correction_for_message(msg_id, fs_grp, text)
            if corr and corr.get("target_report") == "ignore":
                continue

            is_closure = is_closure_text(t_low, closure_kws) or _is_exact_closure(text, closure_exact)
            is_progress = any(p in t_low for p in progress_kws)
            is_ack = self.parser.is_non_operational(text)
            is_followup = any(f in t_low for f in followup_kws) or (not is_closure and any(re.search(rf"\b{re.escape(k.lower())}\b", t_low) for k in closure_kws))
            is_fyi = bool(re.search(r"\bfyi\b", t_low))
            is_empty_or_image = text.strip() in ["(Image)", ""] or (len(text.strip()) <= 4 and not re.search(r"\w{3,}", text))

            if is_closure or is_progress or is_ack or is_followup or is_fyi or is_empty_or_image:
                target_ticket, link_method = self._link_reply(tickets, ev, quoted_id)
                if target_ticket:
                    if is_closure:
                        target_ticket["status"] = "Done"
                        target_ticket["resolved_by"] = ev["sender"]
                    elif is_progress:
                        target_ticket["status"] = "In Progress"
                        target_ticket["progress_note"] = text[:30]
                    target_ticket.setdefault("replies", []).append(text)
                    source_audit.append({
                        "message_id": msg_id,
                        "group_name": fs_grp,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                        "quoted_message_id": quoted_id,
                        "audit_status": f"Linked ({link_method}) -> Ticket {target_ticket['message_id']}",
                    })
                else:
                    unlinked.append({
                        "message_id": msg_id,
                        "reason": "Unlinked reply without matching quoted ticket ID",
                        "text": text,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                    })
                continue

            bld, floor, heading = self.parser.extract_building_and_floor(text)
            if not heading:
                # Named place ("GSU Room", "Lobby"…) or the report's generic heading, never message text
                heading = self._extract_named_location(text) or ""

            status = "Pending"
            if corr and corr.get("target_status"):
                status = corr["target_status"]

            # Detect possible repeat of an earlier ticket in same building
            repeat_flag = None
            fire_repeat_kws = ("fire", "smoke", "alarm", "sprinkler", "exit", "panel")
            for prev_t in tickets.values():
                if prev_t.get("building") and prev_t["building"] == bld:
                    if any(kw in t_low and kw in prev_t["text"].lower() for kw in fire_repeat_kws):
                        prev_time = format_report_timestamp(prev_t["datetime"])
                        repeat_flag = f"Possible repeat of ticket from {prev_time}"
                        break

            ticket = {
                "message_id": msg_id,
                "datetime": ev["datetime"],
                "raw_datetime": ev["raw_datetime"],
                "building": bld,
                "floor": floor,
                "location_heading": heading,
                "issue": text,
                "text": text,
                "status": status,
                "repeat_flag": repeat_flag,
                "sender": ev["sender"],
                "group_name": fs_grp,
                "photos": ev.get("photos", []),
                "photos_data": ev.get("photos_data", []),
                "quoted_message_id": quoted_id,
                "source_message_ids": ev.get("source_message_ids", [msg_id]),
            }
            tickets[msg_id] = ticket
            source_audit.append({
                "message_id": msg_id,
                "group_name": fs_grp,
                "sender": ev["sender"],
                "raw_datetime": ev["raw_datetime"],
                "quoted_message_id": quoted_id,
                "audit_status": "Valid Ticket",
            })

        ticket_list = list(tickets.values())
        unique_tickets, flagged_dups = deduplicate_events(ticket_list)

        # Date-range filtering & carry-over (chronic split at CHRONIC_HOURS)
        shift_start, shift_end = self._resolve_date_range(target_date, target_shift)
        is_range_query = target_date in ("week", "month")
        filtered = []
        chronic_tickets = []
        for t in unique_tickets:
            t_dt = t.get("datetime")
            if not t_dt:
                continue
            if shift_start <= t_dt < shift_end:
                t["is_carry_over"] = False
                filtered.append(t)
            elif not is_range_query and t_dt < shift_start and "done" not in t["status"].lower():
                age_hours = (shift_start - t_dt).total_seconds() / 3600
                t_carried = dict(t)
                t_carried["is_carry_over"] = True
                if age_hours >= CHRONIC_HOURS:
                    t_carried["is_chronic"] = True
                    chronic_tickets.append(t_carried)
                else:
                    filtered.append(t_carried)
        unique_tickets = filtered + chronic_tickets

        # Enrich: hours_open, sla_breach, chronic_building_flag, persist to DB
        now_iso = datetime.now(DUBAI_TZ).isoformat()
        shift_label = self._date_range_label(target_date, target_shift)
        for t in unique_tickets:
            enrich_ticket(t, "fire_safety", fs_grp, shift_label, now_iso)

        completed = sum(1 for t in unique_tickets if "done" in t["status"].lower())
        total = len(unique_tickets)
        open_count = total - completed
        sla_breaches = sum(1 for t in unique_tickets if t.get("sla_breach"))

        # Safety priority: count only tickets with genuine high-risk keywords
        high_risk_kws = self.config.get("safety_priority", [
            "emergency exit", "exit light", "fire exit", "fire fighting", "fire pump",
            "barrier", "gate", "hanging", "loose", "hazard",
        ])
        safety_count = sum(
            1 for t in unique_tickets
            if any(kw in t["text"].lower() for kw in high_risk_kws)
        )
        prio_str = f"{safety_count} high-risk tickets" if safety_count > 0 else "N/A"

        summary_stats = {
            "completed": completed,
            "total": total,
            "open": open_count,
            "sla_breaches": sla_breaches,
            "safety_priority": prio_str,
            "teams": "N/A",
            "shift_info": shift_label,
        }

        report_text = generate_fire_safety_report_text(unique_tickets, summary_stats)
        excel_path = generate_generic_report_excel(
            report_title="Fire & Safety Summary – Meydan",
            report_text=report_text,
            events=unique_tickets,
            unlinked_or_needs_review=unlinked + flagged_dups,
            source_audit=source_audit,
            filename_prefix="Meydan_Fire_Safety",
        )

        return {
            "text": report_text,
            "excel_path": str(excel_path),
            "total": total,
            "open": open_count,
            "completed": completed,
            "sla_breaches": sla_breaches,
            "unlinked_count": len(unlinked),
            "link_stats": self._link_stats(source_audit, unlinked, unique_tickets, shift_start, shift_end),
        }

    # ─────────────────────────────────────────────────────────────
    # REPORT 4: OPERATIONS REPORT (Meydan - Security Team)
    # ─────────────────────────────────────────────────────────────
    def generate_operations(
        self,
        target_date: Optional[str] = None,
        target_shift: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_date, target_shift = self._default_period(target_date, target_shift)

        all_msgs = self.parser.load_messages(load_images=True)  # photos for the 📷 line and Photos sheet
        sec_grp = self.config.get("groups", {}).get("operations", "Meydan - Security Team")
        sec_msgs = [m for m in all_msgs if m["group_name"] == sec_grp]
        grouped_events = self.parser.group_media_events(sec_msgs)

        events: List[Dict[str, Any]] = []
        source_audit: List[Dict[str, Any]] = []
        needs_review: List[Dict[str, Any]] = []

        ops_cfg = self.config.get("operations", {})
        completion_kws = ops_cfg.get(
            "completion", ["complet", "done", "finish", "vendors left", "cleared"]
        )

        active_vendors_set = set()

        for ev in grouped_events:
            text = ev["text"]
            t_low = text.lower()
            msg_id = ev["message_id"]

            # Apply date-range filtering
            ev_dt = ev.get("datetime")
            if ev_dt:
                _s, _e = self._resolve_date_range(target_date, target_shift)
                if not (_s <= ev_dt < _e):
                    continue

            # A completion update closes the activity it refers to instead of becoming a new item
            is_completion = _is_completion(t_low, completion_kws)
            if is_completion:
                target, link_method = _find_activity_to_complete(events, ev)
                if target:
                    target["status"] = "Completed"
                    target["completed_by"] = ev["sender"]
                    target["source_message_ids"].append(msg_id)
                    source_audit.append({
                        "message_id": msg_id,
                        "group_name": sec_grp,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                        "quoted_message_id": ev.get("quoted_message_id", ""),
                        "audit_status": f"Linked ({link_method}) completion -> Event {target['message_id']}",
                    })
                    continue

            if self.parser.is_non_operational(text):
                continue

            # Identify if it is an operational activity (faults belong to maintenance)
            category, vendor_hit = classify_operations_message(text, ops_cfg)

            if category != "activity":
                needs_review.append({
                    "message_id": msg_id,
                    "reason": "Fault report, not an operational activity" if category == "fault"
                    else "Security message without matching operational keywords",
                    "text": text,
                    "sender": ev["sender"],
                    "raw_datetime": ev["raw_datetime"],
                })
                continue

            # Find matching vendor
            vendor_name = "Operations Team"
            vendor_source = None
            inferred_from = None
            if vendor_hit:
                vendor_name, vendor_source = vendor_hit.title(), "named"
            else:
                inferred_from = _vendor_from_recent_activity(events, ev.get("datetime"), ops_cfg.get("vendor_inherit_minutes", 60))
                if inferred_from:
                    vendor_name, vendor_source = inferred_from["vendor"], "inferred"
            if vendor_source:
                active_vendors_set.add(vendor_name)

            bld, floor, heading = self.parser.extract_building_and_floor(text)
            if not heading:
                # Named place ("GSU Room", "Lobby"…) or the report's generic heading, never message text
                heading = self._extract_named_location(text) or ""

            # "Reported" until its own text or a later linked update says it is complete
            status = "Completed" if is_completion else "Reported"

            time_str = ev["datetime"].strftime("%I:%M %p") if ev["datetime"] else "N/A"
            photos_data = ev.get("photos_data", [])
            photo_ref = ev.get("photo_ref") or ("Attached in Excel (Sheet: Photos)" if photos_data else "N/A")

            item = {
                "message_id": msg_id,
                "datetime": ev["datetime"],
                "raw_datetime": ev["raw_datetime"],
                "time_str": time_str,
                "building": bld,
                "floor": floor,
                "location_heading": heading,
                "vendor": vendor_name,
                "activity": text,
                "text": text,
                "status": status,
                "sender": ev["sender"],
                "group_name": sec_grp,
                "photos": ev.get("photos", []),
                "photos_data": photos_data,
                "photo_ref": photo_ref,
                "quoted_message_id": ev.get("quoted_message_id", ""),
                "source_message_ids": [msg_id],
                "vendor_source": vendor_source,
            }
            events.append(item)
            source_audit.append({
                "message_id": msg_id,
                "group_name": sec_grp,
                "sender": ev["sender"],
                "raw_datetime": ev["raw_datetime"],
                "quoted_message_id": ev.get("quoted_message_id", ""),
                "audit_status": f"Operational Event (vendor inferred from {inferred_from['message_id']})"
                if inferred_from else "Operational Event",
            })

        unique_events, flagged_dups = deduplicate_events(events)
        total = len(unique_events)
        completed = sum(1 for e in unique_events if e["status"] == "Completed")
        reported = total - completed

        shift_info = self._date_range_label(target_date, target_shift)
        summary_stats = {
            "total": total,
            "completed": completed,
            "reported": reported,
            "vendors": ", ".join(sorted(active_vendors_set)) if active_vendors_set else "Operations Team",
            "shift_info": shift_info,
        }

        report_text = generate_operations_report_text(unique_events, summary_stats)
        excel_path = generate_generic_report_excel(
            report_title="Operations Summary – Meydan Security",
            report_text=report_text,
            events=unique_events,
            unlinked_or_needs_review=needs_review + flagged_dups,
            source_audit=source_audit,
            filename_prefix="Meydan_Operations",
        )

        return {
            "text": report_text,
            "excel_path": str(excel_path),
            "total": total,
            "completed": completed,
            "reported": reported,
        }

    # ─────────────────────────────────────────────────────────────
    # REPORT 5: PARKING VIOLATIONS (Multistory Parking Building Meydan)
    # ─────────────────────────────────────────────────────────────
    def generate_parking_violations(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        all_msgs = self.parser.load_messages(load_images=True)
        ms_grp = self.config.get("groups", {}).get("parking_multistory", "Multistory Parking Building Meydan")
        ms_msgs = [m for m in all_msgs if m["group_name"] == ms_grp]
        grouped_events = self.parser.group_media_events(ms_msgs)

        violations: Dict[str, Dict[str, Any]] = {}
        unlinked: List[Dict[str, Any]] = []
        source_audit: List[Dict[str, Any]] = []

        for ev in grouped_events:
            msg_id = ev["message_id"]
            text = ev["text"]
            quoted_id = ev.get("quoted_message_id", "")

            # Check if this is a follow-up reply using config closure/progress keywords + ack filter
            closure_kws = self.config.get("closure", ["done", "resolved", "replaced", "rectified", "cleared", "removed"])
            progress_kws = self.config.get("in_progress", ["checking", "notified to", "will replace"])
            t_low = text.lower()
            is_closure_reply = is_closure_text(t_low, closure_kws)
            is_progress_reply = any(p in t_low for p in progress_kws)
            is_reply = is_closure_reply or is_progress_reply or self.parser.is_non_operational(text) or "updated:" in t_low
            if is_reply:
                if quoted_id and quoted_id in violations:
                    if is_closure_reply or "removed" in t_low:
                        violations[quoted_id]["status"] = "Rectified"
                    elif is_progress_reply:
                        violations[quoted_id]["status"] = f"In Progress ({text[:30]})"
                    source_audit.append({
                        "message_id": msg_id,
                        "group_name": ms_grp,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                        "quoted_message_id": quoted_id,
                        "audit_status": f"Linked reply -> Violation {quoted_id}",
                    })
                else:
                    unlinked.append({
                        "message_id": msg_id,
                        "reason": "Unlinked violation follow-up reply",
                        "text": text,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                    })
                continue

            parsed_viol = self.parser.parse_parking_violation(ev)
            if parsed_viol:
                violations[msg_id] = parsed_viol
                source_audit.append({
                    "message_id": msg_id,
                    "group_name": ms_grp,
                    "sender": ev["sender"],
                    "raw_datetime": ev["raw_datetime"],
                    "quoted_message_id": quoted_id,
                    "audit_status": "Parking Violation",
                })

        viol_list = list(violations.values())
        unique_violations, flagged_dups = deduplicate_events(viol_list)
        unique_violations, period_label = self._filter_to_period(unique_violations, target_date)

        total = len(unique_violations)
        rectified = sum(1 for v in unique_violations if "rectified" in v["status"].lower())
        pending = total - rectified

        summary_stats = {
            "total": total,
            "rectified": rectified,
            "pending": pending,
        }

        report_text = self._with_period_line(
            generate_parking_violations_report_text(unique_violations, summary_stats), period_label
        )

        thumb_cfg = self.config.get("violations_excel", {})
        excel_result = generate_violations_excel(
            report_title="Parking Violations Summary – Meydan",
            report_text=report_text,
            events=unique_violations,
            unlinked_or_needs_review=unlinked + flagged_dups,
            source_audit=source_audit,
            filename_prefix="Meydan_Parking_Violations",
            thumbnail_max_side=thumb_cfg.get("thumbnail_max_side", 160),
            thumbnail_quality=thumb_cfg.get("thumbnail_quality", 70),
        )

        excel_path = excel_result["excel_path"]
        file_size = excel_result["file_size_bytes"]
        photo_mapping = excel_result["photo_mapping"]

        print(f"📊 Violations Excel: {excel_path.name} ({file_size:,} bytes)")
        if photo_mapping:
            print("📷 Photo mapping (message row → image):")
            for pm in photo_mapping:
                print(f"   Row {pm['excel_row']} | MsgID {pm['message_id']} | {pm['image_file']} | {pm['thumb_px']} px")
        else:
            print("📷 No photos mapped to violations.")

        return {
            "text": report_text,
            "excel_path": str(excel_path),
            "file_size_bytes": file_size,
            "photo_mapping": photo_mapping,
            "total": total,
            "rectified": rectified,
            "pending": pending,
        }

    # ─────────────────────────────────────────────────────────────
    # REPORT 6: VEHICLE PERMISSIONS (Parking Team at Meydan)
    # ─────────────────────────────────────────────────────────────
    def generate_vehicle_permissions(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        all_msgs = self.parser.load_messages()
        pt_grp = self.config.get("groups", {}).get("parking_team", "Parking Team at Meydan")
        pt_msgs = [m for m in all_msgs if m["group_name"] == pt_grp]
        grouped_events = self.parser.group_media_events(pt_msgs)

        permissions: Dict[str, Dict[str, Any]] = {}
        unlinked: List[Dict[str, Any]] = []
        source_audit: List[Dict[str, Any]] = []

        for ev in grouped_events:
            msg_id = ev["message_id"]
            text = ev["text"]
            quoted_id = ev.get("quoted_message_id", "")

            # Check if this is a permission reply — read from config
            perm_replies = [r.lower() for r in self.config.get("vehicle_permission", {}).get("replies", ["parked in vip slot", "noted"])]
            is_reply = self.parser.is_non_operational(text) or any(r in text.lower() for r in perm_replies)
            if is_reply:
                if quoted_id and quoted_id in permissions:
                    permissions[quoted_id]["status"] = "Parked in VIP slot" if "parked" in text.lower() else "Noted"
                    source_audit.append({
                        "message_id": msg_id,
                        "group_name": pt_grp,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                        "quoted_message_id": quoted_id,
                        "audit_status": f"Linked reply -> Permission {quoted_id}",
                    })
                else:
                    unlinked.append({
                        "message_id": msg_id,
                        "reason": "Unlinked permission reply",
                        "text": text,
                        "sender": ev["sender"],
                        "raw_datetime": ev["raw_datetime"],
                    })
                continue

            parsed_perm = self.parser.parse_vehicle_permission(ev)
            if parsed_perm:
                permissions[msg_id] = parsed_perm
                source_audit.append({
                    "message_id": msg_id,
                    "group_name": pt_grp,
                    "sender": ev["sender"],
                    "raw_datetime": ev["raw_datetime"],
                    "quoted_message_id": quoted_id,
                    "audit_status": "Vehicle Permission Request",
                })

        perm_list = list(permissions.values())
        unique_perms, flagged_dups = deduplicate_events(perm_list)
        unique_perms, period_label = self._filter_to_period(unique_perms, target_date)

        total = len(unique_perms)
        parked = sum(1 for p in unique_perms if "parked" in p["status"].lower())
        pending = total - parked

        summary_stats = {
            "total": total,
            "parked": parked,
            "pending": pending,
        }

        report_text = self._with_period_line(
            generate_vehicle_permissions_report_text(unique_perms, summary_stats), period_label
        )
        excel_path = generate_generic_report_excel(
            report_title="Vehicle Permission Requests – Meydan",
            report_text=report_text,
            events=unique_perms,
            unlinked_or_needs_review=unlinked + flagged_dups,
            source_audit=source_audit,
            filename_prefix="Meydan_Vehicle_Permissions",
        )

        return {
            "text": report_text,
            "excel_path": str(excel_path),
            "total": total,
            "parked": parked,
            "pending": pending,
        }

    # ─────────────────────────────────────────────────────────────
    # HELPERS (used by multiple reports)
    # ─────────────────────────────────────────────────────────────
    def _extract_building(self, text: str) -> Optional[str]:
        pat = self.config.get(
            "building_regex",
            r"\b(?:(?:building|bldg|block|gate|tower|zone)\s*[:-]?\s*([PNMQ])|([PNMQ])\s*[:-]?\s*(?:building|bldg|block|gate|tower|zone|level))\b",
        )
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            return None
        return next((g for g in m.groups() if g), None) or None

    def _extract_floor(self, text: str) -> Optional[str]:
        m = re.search(self.config.get("floor_regex", r"(ground floor|\bgf\b|\d(?:st|nd|rd|th) floor|level \d)"), text, re.IGNORECASE)
        return m.group(0).strip().title() if m else None

    def _get_shift_label(self, dt: datetime) -> str:
        shifts = self.config.get("timing", {}).get("shifts", {})
        day_start = time(*map(int, shifts.get("day", {}).get("start", "05:00").split(":")))
        day_end = time(*map(int, shifts.get("day", {}).get("end", "17:00").split(":")))
        t = dt.time()
        shift = "Day" if day_start <= t < day_end else "Night"
        return f"{shift} Shift – {dt.strftime('%d %b %Y')}"

    def _extract_named_location(self, text: str) -> Optional[str]:
        t_low = text.lower()
        for loc in self.config.get("cleaning_locations", []):
            if loc in t_low:
                return " ".join(w.upper() if w in self.config.get("location_acronyms", []) else w.title() for w in loc.split())
        return None

    # ─────────────────────────────────────────────────────────────
    # REPORT 7: CLEANING SUMMARY (cleaning matters at meydan)
    # ─────────────────────────────────────────────────────────────
    def generate_cleaning(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Report 7 — Cleaning Summary – Meydan (group: cleaning matters at meydan)."""
        all_msgs = self.parser.load_messages(load_images=False)
        grp_name = self.config.get("groups", {}).get("cleaning", "cleaning matters at meydan")
        grp_msgs = [m for m in all_msgs if m["group_name"] == grp_name]
        grouped_events = self.parser.group_media_events(grp_msgs)

        tickets: Dict[str, Dict[str, Any]] = {}
        unlinked: List[Dict[str, Any]] = []
        source_audit: List[Dict[str, Any]] = []

        fault_kws = self.config.get("fault", [])
        closure_kws = self.config.get("closure", [])
        in_progress_kws = self.config.get("in_progress", [])
        exclude_exact = [e.lower() for e in self.config.get("exclude", {}).get("exact_replies", [])]
        cleaning_kws = self.config.get("cleaning_keywords", [
            "clean", "sweep", "mop", "spill", "dirty", "waste", "trash", "garbage",
            "housekeeping", "house keeping", "stain", "leak", "leaking", "wet floor",
            "sanitize", "sanitise", "disinfect",
            "نظاف", "تنظيف", "كنس", "سكب", "قمامة", "نفايات", "مسح", "تعقيم",
        ])
        priority_high_kws = self.config.get("cleaning_priority_high", [
            "blood", "vomit", "feces", "biohazard", "urgent clean",
        ])
        # Build exclusion rules: skip message if vendor AND keyword both match
        exclusion_rules = self.config.get("cleaning_exclude_if_also", [])

        def _is_excluded_by_rule(t_low: str) -> bool:
            for rule in exclusion_rules:
                vendor = rule.get("vendor", "").lower()
                keyword = rule.get("keyword", "").lower()
                if vendor and keyword and vendor in t_low and keyword in t_low:
                    return True
            return False

        for ev in grouped_events:
            msg_id = ev["message_id"]
            text = ev["text"]
            quoted_id = ev.get("quoted_message_id", "")
            text_lower = text.lower().strip()

            # Skip trivial messages
            if text_lower in exclude_exact or len(text_lower) <= 2:
                continue

            # Skip scheduled vendor visits (e.g. Rose Garden watering)
            if _is_excluded_by_rule(text_lower):
                source_audit.append({
                    "message_id": msg_id, "group_name": grp_name,
                    "sender": ev["sender"], "raw_datetime": ev["raw_datetime"],
                    "quoted_message_id": quoted_id, "audit_status": "Excluded (vendor routine)",
                })
                continue

            # Skip spam: less than 5 words and no location pattern
            if len(text_lower.split()) < 5 and not self._extract_building(text) and not self._extract_named_location(text):
                unlinked.append({
                    "message_id": msg_id, "reason": "Too short, no location — likely spam or noise",
                    "text": text, "sender": ev["sender"], "raw_datetime": ev["raw_datetime"],
                })
                continue

            is_reply = bool(quoted_id)
            is_closure = is_closure_text(text_lower, closure_kws)
            is_progress = any(k in text_lower for k in in_progress_kws)

            if is_reply and (is_closure or is_progress):
                if quoted_id in tickets:
                    if is_closure:
                        tickets[quoted_id]["status"] = "Done"
                    else:
                        tickets[quoted_id]["status"] = f"In Progress ({text[:40]})"
                    source_audit.append({
                        "message_id": msg_id, "group_name": grp_name,
                        "sender": ev["sender"], "raw_datetime": ev["raw_datetime"],
                        "quoted_message_id": quoted_id,
                        "audit_status": f"Linked reply → {quoted_id}",
                    })
                else:
                    unlinked.append({
                        "message_id": msg_id, "reason": "Unlinked cleaning reply",
                        "text": text, "sender": ev["sender"], "raw_datetime": ev["raw_datetime"],
                    })
                continue

            is_fault = any(re.search(r'\b' + re.escape(k) + r'\b', text_lower) for k in fault_kws)
            is_cleaning_task = any(k in text_lower for k in cleaning_kws)

            if is_fault or is_cleaning_task:
                building = self._extract_building(text)
                floor = self._extract_floor(text)
                named_loc = self._extract_named_location(text)
                priority = "high" if any(k in text_lower for k in priority_high_kws) else "normal"

                tickets[msg_id] = {
                    "message_id": msg_id,
                    "building": building,
                    "floor": floor,
                    "named_location": named_loc,
                    "issue": text,
                    "status": "Pending",
                    "priority": priority,
                    "sender": ev["sender"],
                    "raw_datetime": ev["raw_datetime"],
                    "datetime": ev.get("datetime"),
                    "source_message_ids": [msg_id],
                    "quoted_message_id": quoted_id,
                }
                source_audit.append({
                    "message_id": msg_id, "group_name": grp_name,
                    "sender": ev["sender"], "raw_datetime": ev["raw_datetime"],
                    "quoted_message_id": quoted_id, "audit_status": "Cleaning Task",
                })

        ticket_list = list(tickets.values())
        # Sort high-priority tickets first
        ticket_list.sort(key=lambda t: (0 if t.get("priority") == "high" else 1))
        unique_tickets, flagged_dups = deduplicate_events(ticket_list)

        unique_tickets, period_label = self._filter_to_period(unique_tickets, target_date)
        shift_label = period_label or self._get_shift_label(datetime.now(tz=DUBAI_TZ))

        total = len(unique_tickets)
        completed = sum(1 for t in unique_tickets if "done" in t["status"].lower())
        open_count = total - completed
        high_priority = sum(1 for t in unique_tickets if t.get("priority") == "high")

        summary_stats = {
            "total": total, "completed": completed,
            "open": open_count, "high_priority": high_priority,
        }
        report_text = generate_cleaning_report_text(shift_label, unique_tickets, summary_stats)

        excel_path = generate_generic_report_excel(
            report_title="Cleaning Summary – Meydan",
            report_text=report_text,
            events=unique_tickets,
            unlinked_or_needs_review=unlinked + flagged_dups,
            source_audit=source_audit,
            filename_prefix="Meydan_Cleaning",
        )

        return {
            "text": report_text,
            "excel_path": str(excel_path),
            "total": total,
            "completed": completed,
            "open": open_count,
            "high_priority": high_priority,
        }
