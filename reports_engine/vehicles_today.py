"""Daily 07:00 owner message: every vehicle whose entry permission covers today.

    python3 -m reports_engine.cli --report vehicles_today [--date DD-MM-YYYY]

The date that counts is the one written in the request ("from 02/11/2026 until 11/11/2026"),
not the day it was sent. Requests with no end ("until further notice", permanent slots) are
listed in their own section.
"""

import contextlib
import io
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from .date_utils import now_dubai
from .manager import ReportsManager

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
# "02/11/2026", "2/11/26", "20 /10/2026", "( 11/11/2026 )"
_NUM_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4}|\d{2})(?!\d)")
# "21st oct 2026", "27th & 28th October 2026", "20th and 21st October"
_WORD_DAYS = re.compile(
    r"((?:\d{1,2}(?:st|nd|rd|th)?\s*(?:,|&|and)\s*)*\d{1,2}(?:st|nd|rd|th)?)\s+"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+(\d{4}))?", re.I)
_LONG_TERM = re.compile(
    r"until further notice|till further notice|permanent|allocate this parking slot|allocate the parking slot"
    r"|add the below vehicle|update the vehicle details", re.I)
_SLOT = re.compile(r"\(\s*(?:slot\s*)?([PNMQ])\s*-?\s*(\d{1,3})\s*\)", re.I)


def _year(y: Optional[str], sent: date) -> int:
    if not y:
        return sent.year
    return int(y) + 2000 if len(y) == 2 else int(y)


def _dates_in(text: str, sent: date) -> List[date]:
    """Dates written in the request, in order of appearance (day first, UAE style)."""
    found = []
    for m in _NUM_DATE.finditer(text):
        try:
            found.append((m.start(), date(_year(m.group(3), sent), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            continue
    for m in _WORD_DAYS.finditer(text):
        month = _MONTHS[m.group(2).lower()[:3]]
        for d in re.findall(r"\d{1,2}", m.group(1)):
            try:
                dt = date(_year(m.group(3), sent), month, int(d))
            except ValueError:
                continue
            if not m.group(3) and dt < sent - timedelta(days=180):   # "5th Jan" sent in December
                dt = dt.replace(year=dt.year + 1)
            found.append((m.start(), dt))
    return [d for _, d in sorted(found)]


def permission_period(text: str, sent: date) -> Dict[str, Any]:
    """
    {"kind": "dated", "start", "end", "days"} or {"kind": "long_term", "start"}.
    "days" is a set for separate days ("20th and 21st October"), else None (every day start..end).
    """
    t = re.sub(r"[*_~]", "", text)
    dates = _dates_in(t, sent)
    if _LONG_TERM.search(t):
        return {"kind": "long_term", "start": dates[0] if dates else sent}
    if len(dates) >= 2:
        if re.search(r"\b(?:from|until|till|to)\b", t, re.I):     # "from 20/10/2026 to 28/10/2026"
            return {"kind": "dated", "start": min(dates), "end": max(dates), "days": None}
        return {"kind": "dated", "start": min(dates), "end": max(dates), "days": set(dates)}   # "27th & 28th"
    if len(dates) == 1:
        d = dates[0]
        if re.search(r"\b(?:until|till)\b", t, re.I) and not re.search(r"\bfrom\b", t, re.I) and d >= sent:
            return {"kind": "dated", "start": sent, "end": d, "days": None}
        n = re.search(r"\bfor\s+(\d{1,2})\s+days?\b", t, re.I)
        end = d + timedelta(days=int(n.group(1)) - 1) if n else d
        return {"kind": "dated", "start": d, "end": end, "days": None}
    if re.search(r"\b(?:this|for the)\s+week\b", t, re.I):
        return {"kind": "dated", "start": sent, "end": sent + timedelta(days=6 - sent.weekday()), "days": None}
    if re.search(r"\btomorrow\b", t, re.I):
        d = sent + timedelta(days=1)
        return {"kind": "dated", "start": d, "end": d, "days": None}
    n = re.search(r"\bfor\s+(\d{1,2})\s+days?\b", t, re.I)
    if n:
        return {"kind": "dated", "start": sent, "end": sent + timedelta(days=int(n.group(1)) - 1), "days": None}
    # "only for today", "today only", or no date at all: the day it was sent
    return {"kind": "dated", "start": sent, "end": sent, "days": None}


def _covers(period: Dict[str, Any], day: date) -> bool:
    if period["kind"] == "long_term":
        return period["start"] <= day
    if period["days"]:
        return day in period["days"]
    return period["start"] <= day <= period["end"]


def _place(perm: Dict[str, Any]) -> str:
    text = perm.get("text", "")
    if re.search(r"expectant|mother", text, re.I):
        m = _SLOT.search(text) or re.search(r"\b([PNMQ])\s*-\s*(\d{1,3})\b", text)
        return f"Expectant mother {m.group(1).upper()}{m.group(2)}" if m else "Expectant mother"
    if perm.get("area"):
        return str(perm["area"])
    m = _SLOT.search(text)
    if m:
        return f"Slot {m.group(1).upper()}{m.group(2)}"
    if re.search(r"\bvip\b", text, re.I):
        return "VIP slot"
    return "Other"


def _group_title(place: str) -> str:
    if place.startswith("Slot"):
        return "Reserved slots"
    if place.startswith("Expectant mother"):
        return "Expectant mother"
    return place


def generate_vehicles_today(manager: ReportsManager, target_date: Optional[str] = None) -> Dict[str, Any]:
    cfg = manager.config.get("vehicle_permission", {})
    look_back = int(cfg.get("daily_look_back_days", 120))
    long_term_days = int(cfg.get("daily_long_term_days", 60))
    now = now_dubai()
    day = datetime.strptime(target_date, "%d-%m-%Y").date() if target_date else now.date()
    cutoff = datetime.combine(day, datetime.max.time()).replace(tzinfo=now.tzinfo)
    if not target_date or day == now.date():
        cutoff = now   # the 07:00 message lists what was requested up to now

    pt_grp = manager.config.get("groups", {}).get("parking_team", "Parking Team at Meydan")
    with contextlib.redirect_stdout(io.StringIO()):
        msgs = [m for m in manager.parser.load_messages()
                if m["group_name"] == pt_grp and m["datetime"]
                and day - timedelta(days=look_back) <= m["datetime"].date() and m["datetime"] <= cutoff]
        events = manager.parser.group_media_events(msgs)

    latest: Dict[tuple, Dict[str, Any]] = {}
    for ev in events:
        perm = manager.parser.parse_vehicle_permission(ev)
        if not perm:
            continue
        period = permission_period(ev["text"], ev["datetime"].date())
        if not _covers(period, day):
            continue
        if period["kind"] == "long_term" and (day - ev["datetime"].date()).days > long_term_days:
            continue
        place = _place(perm)
        key = (re.sub(r"\s+", "", str(perm.get("plate_no", ""))).upper(), place)
        if key[0] in ("", "N/A"):
            key = (ev["message_id"], place)
        latest[key] = {**perm, "period": period, "place": place, "sent": ev["datetime"]}   # later request wins

    today, long_term = [], []
    for p in sorted(latest.values(), key=lambda p: (_group_title(p["place"]), p["sent"])):
        (long_term if p["period"]["kind"] == "long_term" else today).append(p)

    def row(n: int, p: Dict[str, Any]) -> List[str]:
        per = p["period"]
        if per["kind"] == "long_term":
            until = "until further notice"
        elif per["end"] > per["start"] and not per["days"]:
            until = f"until {per['end'].strftime('%d-%m-%Y')}"
        else:
            until = "today only"
        make = p.get("car_make") or "N/A"
        by = re.sub(r"\s*\(.*?\)\s*$", "", str(p.get("sender", ""))).strip()
        return [f"{n}. Plate: {p.get('plate_no') or 'N/A'} | Make: {make}",
                f"   📍 {p['place']} | 📅 {until}",
                f"   👤 {by} | 🕒 Sent {p['sent'].strftime('%d-%m %H:%M')}"]

    lines = [f"🚗 *Vehicles allowed today — {day.strftime('%d-%m-%Y')}*", ""]
    n = 0
    if today:
        current = None
        for p in today:
            title = _group_title(p["place"])
            if title != current:
                lines += ["", f"📍 *{title}*"] if current else [f"📍 *{title}*"]
                current = title
            n += 1
            lines += row(n, p)
    else:
        lines.append("No dated requests cover today.")
    if long_term:
        lines += ["", "━━━━━━━━━━", f"📌 *Long-term / permanent* (requested in the last {long_term_days} days)"]
        for p in long_term:
            n += 1
            lines += row(n, p)
    lines += ["", "━━━━━━━━━━", "🎯 Summary",
              f"• Today: {len(today)} vehicle request(s)", f"• Long-term: {len(long_term)}"]
    return {"text": "\n".join(lines), "total": len(today) + len(long_term),
            "today": len(today), "long_term": len(long_term)}
