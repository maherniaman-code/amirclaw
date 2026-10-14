"""Resolve @mention numeric IDs to sender names using historical WhatsApp sender table without AI."""

import re
from typing import Dict, Optional

SENDER_REGEX = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")
MENTION_REGEX = re.compile(r"@(\d{7,20})")


def clean_sender_name(name: str) -> str:
    if not name:
        return ""
    # Strip leading ~ and spaces
    cleaned = re.sub(r"^[~\s]+", "", name)
    # Strip trailing phone/account ID in parentheses like (126569045606449)
    cleaned = re.sub(r"\s*\(\d+\)\s*$", "", cleaned)
    # Strip country flag emojis (Unicode regional indicators)
    cleaned = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}", "", cleaned)
    # Strip stray emojis or special characters if any, preserving alphabets and spaces
    return cleaned.strip()


class SenderResolver:
    def __init__(self):
        self.id_to_name: Dict[str, str] = {}

    def register_sender(self, sender_raw: str) -> None:
        if not sender_raw:
            return
        m = SENDER_REGEX.match(sender_raw.strip())
        if m:
            name, num_id = m.group(1).strip(), m.group(2).strip()
            clean_name = clean_sender_name(name)
            if clean_name and num_id:
                self.id_to_name[num_id] = clean_name

    def resolve_mentions(self, text: str) -> str:
        if not text:
            return ""

        def repl(match: re.Match) -> str:
            num_id = match.group(1)
            resolved = self.id_to_name.get(num_id)
            if resolved:
                return f"@{resolved}"
            return match.group(0)

        return MENTION_REGEX.sub(repl, text)

    def get_name_by_id(self, num_id: str) -> Optional[str]:
        return self.id_to_name.get(num_id)
