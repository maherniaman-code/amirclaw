"""Master data (config/master_data.yaml): building aliases and the staff directory.

    python3 -m reports_engine.master_data --suggest     # draft staff entries from real senders
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .config_loader import BASE_DIR
from .sender_resolver import clean_sender_name

MASTER_FILE = BASE_DIR / "config" / "master_data.yaml"
SUGGESTED_FILE = BASE_DIR / "config" / "master_data.suggested.yaml"


@lru_cache(maxsize=1)
def load() -> Dict[str, Any]:
    try:
        return yaml.safe_load(MASTER_FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def sender_id(raw_sender: str) -> Optional[str]:
    m = re.search(r"\((\d{6,})\)\s*$", raw_sender or "")
    return m.group(1) if m else None


def display_name(raw_sender: Optional[str]) -> str:
    """Name to print in reports: staff directory entry, else the cleaned WhatsApp name (never the number)."""
    if not raw_sender:
        return ""
    entry = (load().get("staff") or {}).get(sender_id(raw_sender) or "")
    if isinstance(entry, dict) and entry.get("name"):
        return str(entry["name"])
    return clean_sender_name(raw_sender)


def building_from_aliases(text: str) -> Optional[str]:
    t = (text or "").lower()
    for code, aliases in (load().get("buildings") or {}).items():
        for alias in aliases or []:
            if re.search(r"(?<!\w)" + re.escape(alias.lower()) + r"(?!\w)", t):
                return str(code).upper()
    return None


TEAM_BY_GROUP = {"fm": "FM", "fire_safety": "Fire & Safety", "operations": "Security",
                 "parking_multistory": "Parking", "parking_team": "Parking", "cleaning": "Cleaning"}


def suggest(excel_path: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Draft staff entries: id -> name, team guessed from the report group the person posts in most."""
    from .manager import ReportsManager

    m = ReportsManager(excel_path)
    group_to_key = {v: k for k, v in m.config.get("groups", {}).items()}
    seen: Dict[str, Counter] = defaultdict(Counter)
    names: Dict[str, str] = {}
    for msg in m.parser.load_messages():
        sid = sender_id(msg["sender"])
        key = group_to_key.get(msg["group_name"])
        if sid and key:
            seen[sid][key] += 1
            names[sid] = clean_sender_name(msg["sender"])
    return {sid: {"name": names[sid], "team": TEAM_BY_GROUP.get(c.most_common(1)[0][0], "")}
            for sid, c in sorted(seen.items(), key=lambda kv: -sum(kv[1].values()))}


def main() -> int:
    ap = argparse.ArgumentParser(description="Master data helpers")
    ap.add_argument("--suggest", action="store_true", help=f"Write a draft staff directory to {SUGGESTED_FILE.name}")
    args = ap.parse_args()
    if args.suggest:
        staff = suggest()
        SUGGESTED_FILE.write_text(
            "# Draft from real senders. Review names/teams, then copy entries into master_data.yaml -> staff\n"
            + yaml.safe_dump({"staff": staff}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"✅ {len(staff)} senders written to {SUGGESTED_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
