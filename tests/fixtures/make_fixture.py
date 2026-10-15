"""Build the anonymized test fixture from a real WhatsApp workbook.

    python3 tests/fixtures/make_fixture.py "path/to/whatsapp_messages.xlsx"

Writes tests/fixtures/whatsapp_messages.anon.json. Anonymization:
- every sender becomes "Person N (10000N)" (stable by first appearance), and their names
  are replaced wherever they appear in message text (@mentions included);
- every standalone number of 4+ digits (plates, staff IDs, phone numbers) is replaced
  digit by digit, keeping its length and shape; dates like 13/10/2026 and times are kept;
- leave-request messages (names, staff IDs) are replaced by one generic text;
- photos are dropped (they can show plates and people).
Message ids, groups, timestamps and the wording that drives classification are kept.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import openpyxl  # noqa: E402

from reports_engine.date_utils import parse_datetime  # noqa: E402

OUT = Path(__file__).with_name("whatsapp_messages.anon.json")
LEAVE_MARKERS = ("leave details", "staff id", "start date", "total days", "emp id", "annual leave")
DIGIT_MAP = str.maketrans("0123456789", "3816092745")


def _mask_numbers(text: str) -> str:
    return re.sub(r"(?<![\d/:.])\d{4,}(?![\d/:])", lambda m: m.group(0).translate(DIGIT_MAP), text)


def main(src: str) -> None:
    wb = openpyxl.load_workbook(src, data_only=True, read_only=True)
    rows = [r for r in wb.active.iter_rows(min_row=2, values_only=True) if r and len(r) >= 5 and r[0] and r[2]]
    wb.close()

    people: dict = {}
    for r in rows:
        sender = str(r[3] or "")
        if sender and sender not in people:
            people[sender] = f"Person {len(people) + 1} ({100000 + len(people) + 1})"
    # Names to scrub from text: the part of each sender before " (id)", longest first
    names = sorted({re.sub(r"\s*\(.*$", "", s).strip() for s in people if s}, key=len, reverse=True)
    name_map = {n: people[s].split(" (")[0] for s in people for n in [re.sub(r"\s*\(.*$", "", s).strip()] if n}

    out = []
    for r in rows:
        text = str(r[4] or "")
        if any(m in text.lower() for m in LEAVE_MARKERS):
            text = "Leave Details : Full name : Person X Staff Id : 000000 Start Date : 01/01/2027 Total Days : 30"
        for n in names:
            if len(n) >= 3:
                # whole words only: a sender called "Ric" must not turn "electrical" into "electPerson 7al"
                text = re.sub(r"(?<!\w)" + re.escape(n) + r"(?!\w)", name_map[n], text, flags=re.IGNORECASE)
        # People mentioned by title who never posted ("maam najma", "Sir Rashed")
        text = re.sub(r"(?i)\b(maam|ma'am|madam|mr|mrs|ms|sir)(\.?\s+)(?!we\b|team\b|please\b)[A-Za-z]+",
                      lambda m: f"{m.group(1)}{m.group(2)}Person Y", text)
        text = _mask_numbers(text)
        ts_text = str(r[1] or "")
        dt = parse_datetime(ts_text)
        quoted = (str(r[7]) if r[7] is not None else None) if len(r) > 7 else None
        out.append({
            "message_id": str(r[0]).strip(),
            "timestamp_text": ts_text,
            "ts_utc_ms": int(dt.timestamp() * 1000) if dt else None,
            "group_name": str(r[2]).strip(),
            "sender": people.get(str(r[3] or ""), ""),
            "text": text,
            "media_type": str(r[5] or "") if len(r) > 5 else "Text",
            "quoted_message_id": quoted,
        })
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"Wrote {len(out)} anonymized messages, {len(people)} senders -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
