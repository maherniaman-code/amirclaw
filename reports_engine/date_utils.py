"""Date and time parsing utilities supporting Western and Arabic formats in Asia/Dubai timezone."""

import os
import re
from datetime import datetime, time
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Any

DUBAI_TZ = ZoneInfo("Asia/Dubai")

ARABIC_TO_WESTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def now_dubai() -> datetime:
    """Current Dubai time. REPORTS_NOW (ISO 8601) freezes it for deterministic tests and replays."""
    fixed = os.environ.get("REPORTS_NOW")
    if fixed:
        dt = datetime.fromisoformat(fixed)
        return dt if dt.tzinfo else dt.replace(tzinfo=DUBAI_TZ)
    return datetime.now(DUBAI_TZ)


def normalize_datetime_string(raw: str) -> str:
    """Normalize Arabic digits, Unicode formatting marks, and Arabic AM/PM markers."""
    if not raw:
        return ""
    text = str(raw).strip()
    # Remove Unicode bidirectional and formatting marks (covers Arabic WhatsApp date format)
    for mark in ("\u200f", "\u200e", "\u202b", "\u202a", "\u202c", "\u202d", "\u202e", "\ufeff"):
        text = text.replace(mark, "")
    # Translate Arabic numerals
    text = text.translate(ARABIC_TO_WESTERN_DIGITS)
    # Replace Arabic comma with standard comma
    text = text.replace("،", ",")
    # Normalize Arabic AM/PM markers
    # 'ص' -> 'AM', 'م' -> 'PM'
    text = re.sub(r"\bص\b|(?<=\d)\s*ص", "AM", text)
    text = re.sub(r"\bم\b|(?<=\d)\s*م", "PM", text)
    # Normalize spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_datetime(raw: str) -> Optional[datetime]:
    """Parse mixed date/time strings into timezone-aware Asia/Dubai datetime."""
    if not raw:
        return None
    normalized = normalize_datetime_string(raw)

    day_first = [
        "%d/%m/%Y, %I:%M:%S %p",
        "%d/%m/%Y, %I:%M %p",
        "%d/%m/%Y, %H:%M:%S",
        "%d/%m/%Y, %H:%M",
    ]
    month_first = [
        "%m/%d/%Y, %I:%M:%S %p",
        "%m/%d/%Y, %I:%M %p",
        "%m/%d/%Y, %H:%M:%S",
        "%m/%d/%Y, %H:%M",
    ]
    # The WhatsApp client writes Western-digit AM/PM timestamps with toLocaleString('en-US') (MM/DD);
    # Arabic-digit (ar-EG) and 24h timestamps are DD/MM.
    is_arabic = bool(re.search(r"[٠-٩]", str(raw)))
    is_us_12h = not is_arabic and bool(re.search(r"\b(?:AM|PM)\b", normalized))
    patterns = (month_first + day_first) if is_us_12h else (day_first + month_first)
    patterns += [
        # DD-MM-YYYY HH:MM:SS
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p",
        # YYYY-MM-DD HH:MM:SS
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",
    ]

    for pat in patterns:
        try:
            dt = datetime.strptime(normalized, pat)
            return dt.replace(tzinfo=DUBAI_TZ)
        except ValueError:
            continue

    # Attempt dateutil parser fallback
    try:
        from dateutil import parser as du_parser
        dt = du_parser.parse(normalized, dayfirst=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DUBAI_TZ)
        else:
            dt = dt.astimezone(DUBAI_TZ)
        return dt
    except Exception:
        return None


def get_shift_for_timestamp(dt: datetime) -> Tuple[str, str]:
    """
    Returns (shift_name, shift_date_str).
    Day shift: 05:00 to 16:59:59 (Asia/Dubai) on current date.
    Night shift: 17:00 to 04:59:59 (Asia/Dubai). If before 05:00, belongs to previous calendar day's night shift.
    """
    dt_dubai = dt.astimezone(DUBAI_TZ)
    t = dt_dubai.time()

    if time(5, 0) <= t < time(17, 0):
        return "Day", dt_dubai.strftime("%d-%m-%Y")
    else:
        # Night shift
        if t < time(5, 0):
            # Belongs to the night shift starting previous evening
            from datetime import timedelta
            prev_date = (dt_dubai - timedelta(days=1)).strftime("%d-%m-%Y")
            return "Night", prev_date
        else:
            return "Night", dt_dubai.strftime("%d-%m-%Y")


def is_within_freshness(msg_dt: datetime, slot_dt: datetime, window_minutes: int = 60) -> bool:
    """Checks if message is at or before slot time within freshness window."""
    diff_seconds = (slot_dt - msg_dt).total_seconds()
    return 0 <= diff_seconds <= (window_minutes * 60)


def format_report_timestamp(dt: Any) -> str:
    """Formats datetime (or string representation) as DD-MM-YYYY HH:MM in Asia/Dubai timezone."""
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        parsed = parse_datetime(dt)
        if not parsed:
            return dt
        dt = parsed
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=DUBAI_TZ)
    else:
        dt = dt.astimezone(DUBAI_TZ)
    return dt.strftime("%d-%m-%Y %H:%M")

