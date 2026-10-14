"""IMAP Email Reader for parking_dashboard_gui building occupancy reports."""

import imaplib
import email
from email.header import decode_header
import os
import re
from typing import Optional, Dict, Any, Tuple
from .config_loader import load_env_file

load_env_file()


def parse_parking_email_body(body_text: str) -> Optional[Dict[str, Any]]:
    """
    Parses building occupancy figures from email body text using deterministic regex.
    Ignores emojis, asterisks, and whitespace variations.
    """
    if not body_text:
        return None

    # Strip asterisks (markdown bold) and clean text
    cleaned = body_text.replace("*", "")

    # 1. Date and Time inside email body
    date_match = re.search(r"Date\s*:\s*(\d{2}-\d{2}-\d{4})", cleaned, re.IGNORECASE)
    time_match = re.search(r"Time\s*:\s*(\d{1,2}:\d{2})", cleaned, re.IGNORECASE)

    if not date_match or not time_match:
        return None

    email_date = date_match.group(1).strip()
    email_time = time_match.group(1).strip()
    if len(email_time.split(":")[0]) == 1:
        email_time = f"0{email_time}"

    result: Dict[str, Any] = {
        "date": email_date,
        "time": email_time,
        "buildings": {},
        "summary": {},
        "validation_passed": True,
        "discrepancies": [],
    }

    # 2. Buildings P, N, M, Q
    for b in ["P", "N", "M", "Q"]:
        pattern = (
            rf"\*?{b}\s+BUILDING\*?[\s\S]*?"
            r"Total Slots\s*:\s*(\d+)[\s\S]*?"
            r"Cars Parked\s*:\s*(\d+)[\s\S]*?"
            r"Empty Slots\s*:\s*(\d+)"
        )
        m = re.search(pattern, cleaned, re.IGNORECASE)
        if m:
            total_slots = int(m.group(1))
            parked = int(m.group(2))
            empty = int(m.group(3))
            result["buildings"][b] = {
                "total_slots": total_slots,
                "cars_parked": parked,
                "empty_slots": empty,
            }
        else:
            result["buildings"][b] = {
                "total_slots": "N/A",
                "cars_parked": "N/A",
                "empty_slots": "N/A",
            }
            result["discrepancies"].append(f"Building {b} figures missing or unparseable")

    # 3. Total Summary
    from .config_loader import get_config
    _cap = get_config().get("capacities", {})
    total_bld_cap = _cap.get("buildings", {}).get("total", 459)

    summary_pattern = (
        rf"\*?TOTAL\s+SUMMARY\*?\s*=\s*{total_bld_cap}[\s\S]*?"
        r"Total Cars Entered\s*:\s*(\d+)[\s\S]*?"
        r"Total Cars Parked\s*:\s*(\d+)[\s\S]*?"
        r"Total Empty Slots\s*:\s*(\d+)"
    )
    s_match = re.search(summary_pattern, cleaned, re.IGNORECASE)
    if s_match:
        entered = int(s_match.group(1))
        tot_parked = int(s_match.group(2))
        tot_empty = int(s_match.group(3))
        result["summary"] = {
            "total_cars_entered": entered,
            "total_cars_parked": tot_parked,
            "total_empty_slots": tot_empty,
        }

        # 4. Arithmetic Validation
        calc_parked = 0
        all_numeric = True
        for b in ["P", "N", "M", "Q"]:
            val = result["buildings"][b]["cars_parked"]
            if isinstance(val, int):
                calc_parked += val
            else:
                all_numeric = False

        if all_numeric:
            if calc_parked != tot_parked:
                result["validation_passed"] = False
                result["discrepancies"].append(
                    f"Arithmetic mismatch: Sum of buildings ({calc_parked}) != Total Cars Parked ({tot_parked})"
                )
            calc_empty = total_bld_cap - tot_parked
            if tot_empty != calc_empty:
                result["validation_passed"] = False
                result["discrepancies"].append(
                    f"Arithmetic mismatch: Total Empty Slots ({tot_empty}) != {total_bld_cap} - Total Parked ({calc_empty})"
                )
    else:
        result["summary"] = {
            "total_cars_entered": "N/A",
            "total_cars_parked": "N/A",
            "total_empty_slots": "N/A",
        }
        result["validation_passed"] = False
        result["discrepancies"].append("TOTAL SUMMARY block missing or unparseable in email")

    return result


def fetch_parking_email_from_imap(
    target_date: str,
    target_time_slot: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Fetches the parking email matching target_date and target_time_slot via IMAP.
    Returns (parsed_data, status_message).
    Credentials are read from .env only.
    """
    host = os.environ.get("IMAP_HOST", "")
    port = int(os.environ.get("IMAP_PORT", "993"))
    user = os.environ.get("IMAP_USER", "")
    password = os.environ.get("IMAP_PASS", "").replace(" ", "")
    use_tls = os.environ.get("IMAP_TLS", "true").lower() != "false"

    if not host or not user or not password or "example.com" in user or "your-app-password" in password:
        return None, "IMAP credentials not configured or placeholder values in .env"

    try:
        if use_tls:
            mail = imaplib.IMAP4_SSL(host, port)
        else:
            mail = imaplib.IMAP4(host, port)
        mail.login(user, password)
        mail.select("INBOX", readonly=True)

        # Search emails with subject Meydan Parking Report
        typ, data = mail.search(None, 'SUBJECT "Meydan Parking Report"')
        if typ != "OK" or not data or not data[0]:
            mail.logout()
            return None, "No emails found with subject 'Meydan Parking Report'"

        msg_ids = data[0].split()
        # Scan most recent emails first (up to last 20)
        for mid in reversed(msg_ids[-20:]):
            res, mdata = mail.fetch(mid, "(RFC822)")
            if res != "OK":
                continue
            raw_email = mdata[0][1]
            msg = email.message_from_bytes(raw_email)

            # Extract body
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disp = str(part.get("Content-Disposition"))
                    if content_type == "text/plain" and "attachment" not in content_disp:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

            parsed = parse_parking_email_body(body)
            if parsed:
                from .config_loader import get_config
                cfg = get_config()
                tolerance_mins = cfg.get("timing", {}).get("email_slot_tolerance_minutes", 5)

                def is_slot_match(et: str, st: str, tol: int) -> bool:
                    try:
                        eh, em = map(int, et.split(":"))
                        sh, sm = map(int, st.split(":"))
                        return abs((eh * 60 + em) - (sh * 60 + sm)) <= tol
                    except Exception:
                        return et == st

                if is_slot_match(parsed["time"], target_time_slot, tolerance_mins):
                    if not target_date or parsed["date"] == target_date:
                        mail.logout()
                        return parsed, "OK"

        mail.logout()
        return None, f"No email found matching slot {target_time_slot} on date {target_date}"
    except Exception as e:
        return None, f"IMAP connection or read error: {type(e).__name__} - {str(e)}"
