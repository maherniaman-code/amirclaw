"""Duplicate detection and merging using Python difflib without AI."""

import difflib
import re
from datetime import datetime
from typing import List, Dict, Any, Tuple


def normalize_for_comparison(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def calculate_similarity(text1: str, text2: str) -> float:
    n1 = normalize_for_comparison(text1)
    n2 = normalize_for_comparison(text2)
    if not n1 or not n2:
        return 0.0
    return difflib.SequenceMatcher(None, n1, n2).ratio()


def deduplicate_events(
    events: List[Dict[str, Any]],
    similarity_threshold: float = 0.85,
    max_time_diff_minutes: int = 120,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Deduplicates events across groups or within groups.
    Returns (unique_events, flagged_uncertain_duplicates).
    Each unique event maintains 'source_message_ids' and 'source_groups'.
    """
    unique_events: List[Dict[str, Any]] = []
    flagged_uncertain: List[Dict[str, Any]] = []

    for event in events:
        merged = False
        text = event.get("text") or event.get("issue") or event.get("activity") or ""
        bld = event.get("building") or ""
        floor = event.get("floor") or ""
        dt = event.get("datetime")

        for u in unique_events:
            u_text = u.get("text") or u.get("issue") or u.get("activity") or ""
            u_bld = u.get("building") or ""
            u_floor = u.get("floor") or ""
            u_dt = u.get("datetime")

            ratio = calculate_similarity(text, u_text)
            
            # Location matching rule:
            # 1. Same building (if either has building, both must match)
            # 2. Same floor (if either has floor, both must have the EXACT SAME floor)
            same_bld = (bld == u_bld) if (bld or u_bld) else True
            same_floor = (floor == u_floor) if (floor or u_floor) else True
            same_loc = same_bld and same_floor

            # Check time proximity if datetimes available
            time_close = True
            same_group = event.get("group_name") == u.get("group_name")
            if dt and u_dt and isinstance(dt, datetime) and isinstance(u_dt, datetime):
                diff_mins = abs((dt - u_dt).total_seconds()) / 60.0
                if diff_mins > max_time_diff_minutes or dt.date() != u_dt.date():
                    time_close = False
                    # Cross-group same-day same-building: allow wider window for dedup
                    if not same_group and same_bld and dt.date() == u_dt.date():
                        time_close = True
                    # Check if it might be a repeat ticket on a later day or hours apart
                    if ratio >= 0.70 and same_loc and not time_close:
                        flagged_uncertain.append({
                            "type": "Possible repeat of an earlier ticket",
                            "current_event": event,
                            "earlier_event": u,
                            "similarity": round(ratio, 2),
                        })

            # Merge only if:
            # - Similarity ratio >= threshold (0.85)
            # - Exact same building AND exact same floor
            # - Time proximity is close (same day / within max_time_diff_minutes)
            if ratio >= similarity_threshold and same_loc and time_close:
                # Merge into existing unique event
                u.setdefault("source_message_ids", [])
                u.setdefault("source_groups", [])
                
                msg_id = event.get("message_id")
                grp = event.get("group_name")
                if msg_id and msg_id not in u["source_message_ids"]:
                    u["source_message_ids"].append(msg_id)
                if grp and grp not in u["source_groups"]:
                    u["source_groups"].append(grp)
                
                # Merge photo paths and binary data
                for p in event.get("photos", []):
                    if p not in u.setdefault("photos", []):
                        u["photos"].append(p)
                for pd in event.get("photos_data", []):
                    if pd not in u.setdefault("photos_data", []):
                        u["photos_data"].append(pd)

                merged = True
                break
            elif 0.65 <= ratio < similarity_threshold and same_loc and time_close:
                # Borderline match - flag for review, do not auto-merge
                flagged_uncertain.append({
                    "type": "Near-match conflict (uncertain duplicate)",
                    "event_a": u,
                    "event_b": event,
                    "similarity": round(ratio, 2),
                })

        if not merged:
            ev_copy = dict(event)
            msg_id = ev_copy.get("message_id")
            grp = ev_copy.get("group_name")
            ev_copy["source_message_ids"] = [msg_id] if msg_id else []
            ev_copy["source_groups"] = [grp] if grp else []
            unique_events.append(ev_copy)

    return unique_events, flagged_uncertain
