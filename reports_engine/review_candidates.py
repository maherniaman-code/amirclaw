"""Weekly keyword-review list: the few messages of the last 7 days the rules most likely got wrong.

    python3 -m reports_engine.cli --report review_candidates

Instead of reading every message, review these, then update config/meydan_rules.yaml
(evaluate --save / --diff around the change).
"""

import contextlib
import io
import re
from typing import Any, Dict, List, Set

from .date_utils import parse_datetime
from .manager import ReportsManager
from .tickets_db import issue_words

MAX_EXAMPLES = 6
# A capitalised name right before vendor / team / company: "AV CONCEPT ... vandors", "Sinani vendors"
_NAME_BEFORE_PARTY = re.compile(
    r"\b([A-Z][A-Za-z&'’.-]+(?:\s+[A-Z][A-Za-z&'’.-]+){0,2})\s+(?:company'?s?\s+)?(?:vendors?|vandors?|team|company|co\b|group|staff)")
_GENERIC_NAMES = {
    "the", "our", "security", "maintenance", "cleaning", "housekeeping", "hk", "pest", "control", "fm", "mep",
    "it", "parking", "building", "smart", "staff", "all", "imdad", "civil", "electrical", "plumbing", "ac",
    "dear", "kindly", "please", "and", "from", "by", "with",
}


def _in_period(entry: Dict[str, Any], start, end) -> bool:
    dt = entry.get("datetime") or parse_datetime(entry.get("raw_datetime", ""))
    return bool(dt and start <= dt < end)


def _line(entry: Dict[str, Any]) -> str:
    dt = entry.get("datetime") or parse_datetime(entry.get("raw_datetime", ""))
    when = dt.strftime("%d-%m %H:%M") if dt else "?"
    text = " ".join(str(entry.get("text", "")).split())
    return f"  • {when} — {text[:110]}{'…' if len(text) > 110 else ''}"


def _known_vendor_words(manager: ReportsManager) -> Set[str]:
    ops = manager.config.get("operations", {})
    names = list(ops.get("vendors", [])) + list((ops.get("vendor_aliases") or {}).keys())
    return {w for n in names for w in n.lower().split()}


def _new_vendor_names(texts: List[str], known: Set[str]) -> List[str]:
    found: Dict[str, int] = {}
    for t in texts:
        for m in _NAME_BEFORE_PARTY.finditer(t):
            words = [w for w in m.group(1).split() if w.lower().strip(".'’") not in _GENERIC_NAMES]
            if not words or all(w.lower() in known for w in words) or re.fullmatch(r"[PNMQ]", words[-1]):
                continue
            name = " ".join(words)
            found[name.title()] = found.get(name.title(), 0) + 1
    return sorted(found, key=lambda n: -found[n])


def generate_review_candidates(manager: ReportsManager) -> Dict[str, Any]:
    with contextlib.redirect_stdout(io.StringIO()):
        manager.generate_maintenance(target_date="week")
        manager.generate_fire_safety(target_date="week")
        manager.generate_operations(target_date="week")
        manager.generate_vehicle_permissions(target_date="week")
    start, end = manager._resolve_date_range("week", None)
    review = manager.last_review

    # 1. Excluded maintenance / fire messages that still name a place or a broken object
    missed_faults = []
    for report in ("maintenance", "fire_safety"):
        for r in review.get(report, []):
            if not str(r.get("reason", "")).startswith(("Non-ticket", "Reminder or reply without")):
                continue
            text = str(r.get("text", ""))
            bld, floor, _ = manager.parser.extract_building_and_floor(text)
            if _in_period(r, start, end) and (bld or floor or issue_words(text)):
                missed_faults.append(r)

    # 2. Security messages with no activity word that talk about a vendor / team, and new company names
    ops_unmatched = [r for r in review.get("operations", [])
                     if str(r.get("reason", "")).startswith("Security message without matching")
                     and _in_period(r, start, end)
                     and re.search(r"\b(vendors?|vandors?|team|company|contractor|workers)\b",
                                   re.sub(r"\b(?:dear|hi|hello|all)\s+team\b", " ", str(r.get("text", "")), flags=re.I), re.I)]
    ops_texts = [str(r.get("text", "")) for r in ops_unmatched]
    ops_texts += [str(i.get("text", "")) for i in manager.last_items.get("operations", [])
                  if i.get("vendor") in (None, "", "Operations Team") and _in_period(i, start, end)]
    new_vendors = _new_vendor_names(ops_texts, _known_vendor_words(manager))

    # 3. Replies the engine could not link to any ticket
    unlinked = [r for report in ("maintenance", "fire_safety") for r in review.get(report, [])
                if str(r.get("reason", "")).startswith("Unlinked reply") and _in_period(r, start, end)
                and len(str(r.get("text", "")).split()) > 1]

    # 4. Parking-team messages that look like a permission request but were not read as one
    pt_grp = manager.config.get("groups", {}).get("parking_team", "Parking Team at Meydan")
    used = {i["message_id"] for i in manager.last_items.get("permissions", [])}
    used |= {a["message_id"] for a in manager.last_audit.get("permissions", [])}
    used |= {r.get("message_id") for r in review.get("permissions", [])}
    missed_perms = [m for m in manager.parser.load_messages()
                    if m["group_name"] == pt_grp and m["message_id"] not in used and _in_period(m, start, end)
                    and re.search(r"\b(plate|allow|allocate)\b", m["text"], re.I)]

    sections = [
        ("🔧 Possible missed faults (excluded, but name a place or a broken item)", missed_faults),
        ("🏢 Security messages about a vendor with no activity word", ops_unmatched),
        ("↩️ Replies not linked to any ticket", unlinked),
        ("📋 Parking-team messages that look like a permission request", missed_perms),
    ]
    lines = ["🔎 *Keyword review — last 7 days*",
             f"📅 {start.strftime('%d-%m-%Y')} → {end.strftime('%d-%m-%Y')}", ""]
    for title, entries in sections:
        lines.append(f"{title}: {len(entries)}")
        lines += [_line(e) for e in entries[:MAX_EXAMPLES]]
        if len(entries) > MAX_EXAMPLES:
            lines.append(f"  … +{len(entries) - MAX_EXAMPLES} more")
        lines.append("")
    lines.append(f"🏷️ Company names not in the vendor list: {', '.join(new_vendors[:12]) if new_vendors else 'none'}")
    total = sum(len(e) for _, e in sections)
    lines += ["", "━━━━━━━━━━",
              "✅ Nothing to review this week" if not total and not new_vendors
              else "Send this list to Claude to update the keywords."]
    return {
        "text": "\n".join(lines),
        "total": total,
        "metrics": {
            "missed_faults": len(missed_faults), "ops_unmatched": len(ops_unmatched), "unlinked_replies": len(unlinked),
            "missed_permissions": len(missed_perms), "new_vendors": new_vendors,
        },
    }
