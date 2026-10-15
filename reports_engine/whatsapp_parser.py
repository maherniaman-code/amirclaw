"""WhatsApp messages extraction, event building, and classification engine without AI."""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import openpyxl

from . import master_data, messages_db
from .config_loader import get_config, BASE_DIR
from .date_utils import parse_datetime, DUBAI_TZ, ARABIC_TO_WESTERN_DIGITS
from .sender_resolver import SenderResolver

import zipfile
import xml.etree.ElementTree as ET

EXCEL_FILE = BASE_DIR / "workspace" / "whatsapp_messages.xlsx"

# Module-level resolver shared across all WhatsAppParser instances in the same process
_shared_resolver = SenderResolver()


def extract_xlsx_images(xlsx_path: Path) -> Dict[int, List[Dict[str, Any]]]:
    """Extracts DrawingML images from xlsx and maps them to 1-indexed row numbers."""
    row_images: Dict[int, List[Dict[str, Any]]] = {}
    if not xlsx_path.exists():
        return row_images
    try:
        with zipfile.ZipFile(str(xlsx_path), "r") as z:
            namelist = z.namelist()
            if "xl/drawings/_rels/drawing1.xml.rels" not in namelist or "xl/drawings/drawing1.xml" not in namelist:
                return {}
            rels_xml = z.read("xl/drawings/_rels/drawing1.xml.rels")
            rels_root = ET.fromstring(rels_xml)
            # ExcelJS writes "../media/x.jpeg", openpyxl writes "/xl/media/x.jpeg"
            rel_map = {
                r.attrib["Id"]: (r.attrib["Target"].lstrip("/") if r.attrib["Target"].startswith("/")
                                 else r.attrib["Target"].replace("../media/", "xl/media/"))
                for r in rels_root
            }
            draw_xml = z.read("xl/drawings/drawing1.xml")
            draw_root = ET.fromstring(draw_xml)
            ns = {
                "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
                "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            }
            anchors = draw_root.findall(".//xdr:oneCellAnchor", ns) + draw_root.findall(".//xdr:twoCellAnchor", ns)
            for anc in anchors:
                from_el = anc.find("xdr:from", ns)
                if from_el is not None:
                    row_el = from_el.find("xdr:row", ns)
                    if row_el is not None:
                        r = int(row_el.text) + 1
                        blip = anc.find(".//a:blip", ns)
                        if blip is not None:
                            eid = blip.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                            if eid in rel_map:
                                media_path = rel_map[eid]
                                data = z.read(media_path)
                                row_images.setdefault(r, []).append({"media_path": media_path, "bytes": data})
    except Exception as e:
        import sys
        print(f"[WARN] extract_xlsx_images failed: {e}", file=sys.stderr)
    return row_images


class WhatsAppParser:
    def __init__(self, excel_path: Optional[Path] = None):
        self.excel_path = excel_path or EXCEL_FILE
        self.config = get_config()
        self.resolver = _shared_resolver  # shared across all instances in this process
        self.raw_messages: List[Dict[str, Any]] = []
        self._cache_mtime: Optional[float] = None
        self._cache_has_images: bool = False

    def _message_source(self) -> Tuple[str, Path]:
        """
        "sqlite" when the bot's message database next to the workbook has finished its Excel
        import, else "excel". REPORTS_SOURCE=excel|sqlite forces one (tests, troubleshooting).
        """
        db = messages_db.db_path_for(self.excel_path)
        mode = os.environ.get("REPORTS_SOURCE", "auto").lower()
        if mode == "sqlite" and db.exists():
            return "sqlite", db
        if mode == "auto" and messages_db.is_ready(db):
            return "sqlite", db
        return "excel", self.excel_path

    def load_messages(self, load_images: bool = False) -> List[Dict[str, Any]]:
        """Loads and parses raw messages (SQLite store or Excel, same row layout) with optional photos."""
        source, path = self._message_source()
        if not path.exists():
            return []

        # Return cached result if the source is unchanged and image requirement is met
        try:
            current_mtime = (source, messages_db.source_mtime(path) if source == "sqlite" else path.stat().st_mtime)
            if (
                self.raw_messages
                and self._cache_mtime == current_mtime
                and (not load_images or self._cache_has_images)
            ):
                return self.raw_messages
        except Exception:
            current_mtime = None

        self.raw_messages = []
        ts_by_row: Dict[int, int] = {}
        if source == "sqlite":
            raw_rows, images_by_row, ts_by_row = messages_db.read_rows(path, load_images)
        else:
            wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
            ws = wb.active
            images_by_row = extract_xlsx_images(path) if load_images else {}
            raw_rows = []
            for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
                if row and len(row) >= 5:
                    raw_rows.append((row_idx, row))
            wb.close()

        # First pass: build sender ID map
        for _, row in raw_rows:
            sender = str(row[3] or "").strip()
            self.resolver.register_sender(sender)

        # Second pass: parse message rows
        for row_idx, row in raw_rows:
            msg_id = str(row[0] or "").strip()
            raw_dt = str(row[1] or "").strip()
            group_name = str(row[2] or "").strip()
            sender = str(row[3] or "").strip()
            raw_text = str(row[4] or "").strip()
            media_type = str(row[5] or "").strip() if len(row) > 5 else "Text"
            photo_preview = str(row[6] or "").strip() if len(row) > 6 else ""
            quoted_id = str(row[7] or "").strip() if len(row) > 7 else ""
            # None = the bot did not record reply info yet; "" = recorded, message was not a Reply
            quoted_captured = len(row) > 7 and row[7] is not None

            if not msg_id or not group_name:
                continue

            # Video messages: use caption if present, otherwise use placeholder so they
            # are distinguishable from genuinely empty messages.
            if "video" in media_type.lower() and not raw_text:
                raw_text = "(Video)"

            # Exact capture time when the bot recorded it; the text is display-only and locale-dependent
            ts_ms = ts_by_row.get(row_idx)
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=DUBAI_TZ) if ts_ms else parse_datetime(raw_dt)
            resolved_text = self.resolver.resolve_mentions(raw_text)
            row_photos = images_by_row.get(row_idx, [])

            self.raw_messages.append({
                "message_id": msg_id,
                "raw_datetime": raw_dt,
                "datetime": dt,
                "group_name": group_name,
                "sender": sender,
                "text": resolved_text,
                "media_type": media_type,
                "photo_preview": photo_preview,
                "photos_data": row_photos,
                "photo_ref": "Attached in Excel (Sheet: Photos)" if row_photos else "N/A",
                "quoted_message_id": quoted_id,
                "quoted_captured": quoted_captured,
            })

        # Sort chronologically by parsed datetime
        self.raw_messages.sort(key=lambda m: m["datetime"] or datetime.min.replace(tzinfo=DUBAI_TZ))

        # Update cache metadata
        self._cache_mtime = current_mtime
        self._cache_has_images = load_images

        return self.raw_messages

    def is_bot_sender(self, sender: str) -> bool:
        bot_names = self.config.get("exclude", {}).get("senders", ["Me (Bot Account)", "أنا (حساب البوت)"])
        for b in bot_names:
            if b.lower() in sender.lower():
                return True
        return False

    def is_non_operational(self, text: str) -> bool:
        t = text.lower().strip()
        # Remove leading emoji/symbols and re-check
        t_clean = re.sub(r"^[\U00010000-\U0010ffff -⿿　-〿\s✅👍⚠️]+", "", t).strip()

        exact = [e.lower() for e in self.config.get("exclude", {}).get("exact_replies", [])]

        # Exact match on raw or emoji-stripped text
        if t in exact or t_clean in exact:
            return True

        # Short ACK prefix patterns: "ok sir", "noted.", "noted thanks", "received sir"
        ack_prefixes = ("ok", "noted", "received", "thanks", "roger", "understood", "copy that")
        if any(t_clean == p or t_clean.startswith(p + " ") or t_clean.startswith(p + ".") or t_clean.startswith(p + ",")
               for p in ack_prefixes):
            # Only suppress if the message is short (no real content after the ack word)
            if len(t_clean.split()) <= 4:
                return True

        # Pure emoji / symbol message
        if re.match(r"^[\U00010000-\U0010ffff -⿿　-〿✅👍⚠️🆗🔴🟢🟡\s]+$", text.strip()):
            return True

        leaves = self.config.get("exclude", {}).get("leave_markers", [])
        for lm in leaves:
            if lm.lower() in t:
                return True

        # Short FYI messages (e.g. "@Prakash FYI" used in Fire & Safety as a follow-up signal)
        if re.search(r"\bfyi\b", t, re.IGNORECASE) and len(text.split()) <= 8:
            return True

        return False

    @staticmethod
    def _is_photo_only(msg: Dict[str, Any]) -> bool:
        """An image message with no caption of its own."""
        text = (msg.get("text") or "").strip()
        is_image = msg.get("media_type") == "📷 Image" or "(Image)" in text
        return is_image and text in ("", "(Image)")

    def group_media_events(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Attaches image rows arriving within seconds from the same sender to the corresponding text event.
        """
        events: List[Dict[str, Any]] = []
        i = 0
        n = len(messages)
        used_indices = set()

        while i < n:
            if i in used_indices:
                i += 1
                continue

            curr = messages[i]
            used_indices.add(i)

            # Skip bot account
            if self.is_bot_sender(curr["sender"]):
                i += 1
                continue

            ev = dict(curr)
            ev["photos"] = []
            ev["photos_data"] = list(curr.get("photos_data", []))
            ev["source_message_ids"] = [curr["message_id"]]
            if curr["media_type"] == "📷 Image" or "(Image)" in curr["text"]:
                if curr["photo_preview"]:
                    ev["photos"].append(curr["photo_preview"])

            # Look forward AND backward for adjacent photo messages within 35 seconds from same sender
            curr_dt = curr["datetime"]
            if curr_dt:
                # Check previous messages (photo sent just before the text)
                j = i - 1
                while j >= 0 and j not in used_indices:
                    prv = messages[j]
                    if prv["sender"] == curr["sender"] and prv["datetime"]:
                        diff = abs((curr_dt - prv["datetime"]).total_seconds())
                        if diff <= 35 and self._is_photo_only(prv):
                            used_indices.add(j)
                            ev["source_message_ids"].insert(0, prv["message_id"])
                            if prv.get("photos_data"):
                                ev["photos_data"] = list(prv["photos_data"]) + ev["photos_data"]
                            if prv.get("photo_preview") and prv["photo_preview"] not in ev["photos"]:
                                ev["photos"].insert(0, prv["photo_preview"])
                        else:
                            break
                    else:
                        break
                    j -= 1

                # Check next adjacent messages
                j = i + 1
                while j < n:
                    nxt = messages[j]
                    if nxt["sender"] == curr["sender"] and nxt["datetime"]:
                        diff = abs((nxt["datetime"] - curr_dt).total_seconds())
                        # Attach bare photos, or the caption text that follows a bare photo.
                        # Two messages that both carry text are separate reports (e.g. 4 permission
                        # requests sent within seconds) and must never be merged.
                        if diff <= 35 and (self._is_photo_only(nxt) or self._is_photo_only(ev)):
                            used_indices.add(j)
                            ev["source_message_ids"].append(nxt["message_id"])
                            if nxt.get("photos_data"):
                                ev["photos_data"].extend(nxt["photos_data"])
                            if nxt.get("photo_preview") and nxt["photo_preview"] not in ev["photos"]:
                                ev["photos"].append(nxt["photo_preview"])
                            if not ev.get("quoted_message_id") and nxt.get("quoted_message_id"):
                                ev["quoted_message_id"] = nxt["quoted_message_id"]

                            nxt_t = nxt["text"].strip()
                            ev_t = ev["text"].strip()
                            if ev_t in ["(Image)", ""] and nxt_t not in ["(Image)", "", "👍", "noted", "ok"]:
                                ev["text"] = nxt["text"]
                            elif "(Image)" in ev_t and len(nxt_t) > len(ev_t):
                                ev["text"] = nxt["text"]
                        else:
                            break
                    j += 1

            ev["photo_ref"] = "Attached in Excel (Sheet: Photos)" if ev["photos_data"] else "N/A"
            events.append(ev)
            i += 1

        return events

    # ─────────────────────────────────────────────────────────────
    # REPORT 1: PARKING WA PARSERS
    # ─────────────────────────────────────────────────────────────

    def parse_ramp_service_road(self, text: str) -> Optional[Dict[str, Any]]:
        """Parses the Ramp / Service Road occupancy report ("*Parking Occupany Report* ... Podium Parking
        Total Slots :127 Customer and visitor cars : 87 ... Partially Blocked Parking. :04")."""
        has_heading = re.search(r"Parking\s+Oc+up[a-z]*", text, re.IGNORECASE)
        has_body = re.search(r"ramp\s+area", text, re.IGNORECASE) and re.search(r"service\s+road", text, re.IGNORECASE)
        if not (has_heading or has_body) or not re.search(r"\d", text):
            return None

        # Clean digits and spacing
        t = text.translate(ARABIC_TO_WESTERN_DIGITS)

        def extract_val(label_pattern: str) -> Optional[int]:
            # Matches label followed by optional dots, spaces, colons, or dashes, and the integer
            pat = rf"{label_pattern}[\s\.\:\-]*(\d+)"
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                return int(m.group(1))
            return None

        cust_visitor = extract_val(r"Customer\s+and\s+visitor(?:\s+cars)?")
        pod_cars = extract_val(r"P\.?o\.?d(?:\s+Cars)?")
        staff_cars = extract_val(r"Staff(?:\s+Cars)?")
        total_entered_ramp = extract_val(r"Total(?:\s+cars)?\s+Entered\s+at\s+ramp(?:\s+area)?")
        if total_entered_ramp is None and None not in (cust_visitor, pod_cars, staff_cars):
            total_entered_ramp = cust_visitor + pod_cars + staff_cars  # "total of the above"
        podium_slots = extract_val(r"Podium\s+Parking\s+Total\s+Slots?") or 127
        total_parked_ramp = extract_val(r"Total(?:\s+cars)?\s+parked\s+at\s+ramp(?:\s+Area)?")

        # Service Road
        reported_sr_slots = extract_val(r"Total\s+Slot\s+Service\s+Road")
        sr_entered = extract_val(r"(?:Total\s+Car\s+)?Entered\s+Service\s+Road")
        sr_parked = extract_val(r"(?:Total\s+Car\s+)?parked\s+(?:at\s+)?service\s+road")
        blocked_parking = extract_val(r"Partially\s+Blocked(?:\s+Parking)?")

        diagnostics: List[str] = []
        if reported_sr_slots is not None and reported_sr_slots != 127:
            diagnostics.append(f"Service Road capacity reported as {reported_sr_slots} in WA message (fixed at 127 in report)")

        return {
            "type": "ramp_service_road",
            "podium_slots": podium_slots,
            "customer_visitor": cust_visitor if cust_visitor is not None else "N/A",
            "pod_cars": pod_cars if pod_cars is not None else "N/A",
            "staff_cars": staff_cars if staff_cars is not None else "N/A",
            "total_cars_entered_ramp": total_entered_ramp if total_entered_ramp is not None else "N/A",
            "total_cars_parked_ramp": total_parked_ramp if total_parked_ramp is not None else "N/A",
            "service_road_slots": 127,  # Fixed at 127
            "reported_service_road_slots": reported_sr_slots,
            "service_road_entered": sr_entered if sr_entered is not None else "N/A",
            "service_road_parked": sr_parked if sr_parked is not None else "N/A",
            "partially_blocked_parking": blocked_parking if blocked_parking is not None else "N/A",
            "diagnostics": diagnostics,
        }

    def parse_smart_parking(self, text: str) -> Optional[Dict[str, Any]]:
        """Parses Smart Parking occupancy numbers with typo checks and suspicious value validation."""
        t = text.translate(ARABIC_TO_WESTERN_DIGITS)
        ent_m = re.search(r"cars\s+entered\s+as\s+of\s+(\d+)\s*o.?clock\s*=\s*(\S+)", t, re.IGNORECASE)
        park_m = re.search(r"cars\s+parked\s+as\s+of\s+(\d+)\s*o.?clock\s*=\s*(\S+)", t, re.IGNORECASE)
        out_m = re.search(r"cars\s+parked\s+outside\s*=\s*(\S+)", t, re.IGNORECASE)

        if not ent_m and not park_m:
            return None

        diagnostics: List[str] = []
        hour_written = int(ent_m.group(1)) if ent_m else (int(park_m.group(1)) if park_m else 0)

        # Parse entered
        cars_entered: Any = "N/A"
        if ent_m:
            val_ent = ent_m.group(2).strip()
            if re.match(r"^\d+$", val_ent):
                cars_entered = int(val_ent)
            else:
                diagnostics.append(f"Typo detected in Cars entered: '{val_ent}' (showing N/A)")

        # Parse parked
        cars_parked: Any = "N/A"
        if park_m:
            val_park = park_m.group(2).strip()
            if re.match(r"^\d+$", val_park):
                cars_parked = int(val_park)
            else:
                diagnostics.append(f"Typo detected in Cars parked: '{val_park}' (showing N/A)")

        # Parse outside (handles typos like 1$, and leading zeros like 09 -> 9)
        cars_outside: Any = "N/A"
        if out_m:
            val_out = out_m.group(1).strip()
            if re.match(r"^\d+$", val_out):
                cars_outside = int(val_out)
            else:
                diagnostics.append(f"Typo in source number: Cars parked outside = '{val_out}' (flagged for review, showing N/A)")

        # Check suspicious values: parked far below entered
        if isinstance(cars_entered, int) and isinstance(cars_parked, int):
            if cars_entered - cars_parked > 150:
                diagnostics.append(
                    f"Suspicious value at {hour_written} o'clock: Cars parked ({cars_parked}) is far below Cars entered ({cars_entered}) by {cars_entered - cars_parked} (flagged for review)"
                )

        # Calculate empty slots from capacity 887
        empty_slots: Any = "N/A"
        if isinstance(cars_parked, int):
            if cars_parked > 887:
                diagnostics.append(f"Suspicious: cars_parked ({cars_parked}) exceeds capacity (887) — clamped to 0")
            empty_slots = max(0, 887 - cars_parked)
            diagnostics.append(f"Empty slots ({empty_slots}) is calculated from capacity (887 - {cars_parked} parked), not written in WhatsApp source message")

        return {
            "type": "smart_parking",
            "hour_written": hour_written,
            "total_slots": 887,
            "cars_entered": cars_entered,
            "occupied_slots": cars_parked,
            "empty_slots": empty_slots,
            "outside_cars": cars_outside,
            "diagnostics": diagnostics,
        }

    def parse_rumble_area(self, text: str) -> Optional[Dict[str, Any]]:
        """Parses Rumble Area count from Parking Team at Meydan."""
        if "rumble area" not in text.lower():
            return None

        t = text.translate(ARABIC_TO_WESTERN_DIGITS)
        cars_m = re.search(r"(?:are\s+)?(\d+)\s*cars?", t, re.IGNORECASE)
        bus_m = re.search(r"bus.*?(\d+)\s*car", t, re.IGNORECASE)
        time_m = re.search(r"tim[e]?\s*(\d{1,2}:\d{2}\s*[ap]m)", t, re.IGNORECASE)
        date_m = re.search(r"date\s*(\d+(?:st|nd|rd|th)?\s*\w+\s*\d{4})", t, re.IGNORECASE)

        tot_cars = int(cars_m.group(1)) if cars_m else "N/A"
        tot_bus = int(bus_m.group(1)) if bus_m else "N/A"

        return {
            "type": "rumble_area",
            "total_cars": tot_cars,
            "bus": tot_bus,
            "time_written": time_m.group(1) if time_m else "N/A",
            "date_written": date_m.group(1) if date_m else "N/A",
        }

    # ─────────────────────────────────────────────────────────────
    # REPORT 2 & 3: MAINTENANCE & FIRE/SAFETY PARSERS
    # ─────────────────────────────────────────────────────────────

    def extract_building_and_floor(self, text: str) -> Tuple[Optional[str], Optional[str], str]:
        """Extracts building (P, N, M, Q) and floor, returning (building, floor, clean_location)."""
        bld_pat = self.config.get(
            "building_regex",
            r"\b(?:(?:building|bldg|block|gate|tower|zone)\s*[:-]?\s*([PNMQ])|([PNMQ])\s*[:-]?\s*(?:building|bldg|block|gate|tower|zone|level))\b",
        )
        bld_match = re.search(bld_pat, text, re.IGNORECASE)
        bld = None
        if bld_match:
            bld = next((g for g in bld_match.groups() if g), None)
            if bld:
                bld = bld.upper()
        if not bld:
            bld = master_data.building_from_aliases(text)  # spellings the regex misses (config/master_data.yaml)

        floor_pat = self.config.get("floor_regex", r"(ground floor|\bgf\b|\d+(?:st|nd|rd|th)?\s*floor|level\s*\d+)")
        floor_match = re.search(floor_pat, text, re.IGNORECASE)
        floor = floor_match.group(1).title() if floor_match else None

        # If no building, keep exact location text
        location_header = f"Building {bld}" if bld else ""
        return bld, floor, location_header

    def extract_teams_and_priority(self, text: str) -> Tuple[str, str]:
        t = text.lower()
        matched_teams = []
        for tm in self.config.get("teams", []):
            if tm.lower() in t:
                matched_teams.append(tm.upper())

        matched_prio = []
        for sp in self.config.get("safety_priority", []):
            if sp.lower() in t:
                matched_prio.append(sp.title())

        teams_str = ", ".join(matched_teams) if matched_teams else "N/A"
        prio_str = ", ".join(matched_prio) if matched_prio else "N/A"
        return teams_str, prio_str

    # ─────────────────────────────────────────────────────────────
    # REPORT 5: PARKING VIOLATIONS PARSER
    # ─────────────────────────────────────────────────────────────

    def parse_parking_violation(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        text = event["text"]
        t_low = text.lower()

        keywords = self.config.get("parking_violation", {}).get("keywords", [])
        is_violation = any(k in t_low for k in keywords)
        if not is_violation:
            return None

        # Level / Floor
        lvl_m = re.search(r"\b(?:level|lvl|lv|floor)\.?\s*(\d+)", text, re.IGNORECASE)
        lvl = f"Level {lvl_m.group(1)}" if lvl_m else "Unspecified Level"

        # Slot number
        slot_m = re.search(r"slot\s*(?:no\.?|number)?\s*(\d+)", text, re.IGNORECASE)
        slot_no = slot_m.group(1) if slot_m else "N/A"

        photos_data = event.get("photos_data", [])
        photo_ref = "Attached in Excel (Sheet: Photos)" if photos_data else "N/A"
        return {
            "message_id": event["message_id"],
            "datetime": event["datetime"],
            "raw_datetime": event["raw_datetime"],
            "sender": event["sender"],
            "group_name": event["group_name"],
            "text": text,
            "level": lvl,
            "slot_no": slot_no,
            "status": "Reported",
            "photos": event.get("photos", []),
            "photos_data": photos_data,
            "photo_ref": photo_ref,
            "quoted_message_id": event.get("quoted_message_id", ""),
            "source_message_ids": event.get("source_message_ids", [event["message_id"]]),
        }

    # ─────────────────────────────────────────────────────────────
    # REPORT 6: VEHICLE PERMISSION REQUESTS PARSER
    # ─────────────────────────────────────────────────────────────

    def parse_vehicle_permission(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        text = event["text"]
        t_low = text.lower()

        perm_kws = self.config.get("vehicle_permission", {}).get("keywords", [
            "kindly allow this vehicle", "vip parking slot",
        ])
        if not any(k in t_low for k in perm_kws):
            return None

        # WhatsApp bold/italic markers (e.g. "*Plate No:* X 25854") break the field regexes
        plain = re.sub(r"[*_~]", "", text)

        # Plate No: e.g. "Plate no : 4 / 69888" or "Plate no. EE 80743"
        plate_m = re.search(r"plate\s*(?:no\.?|number)?\s*[:\-]?\s*([A-Za-z0-9\s/]+?)(?=\s*car\s*make|\s*valid|\s*only|\s*$|\n)", plain, re.IGNORECASE)
        plate_no = plate_m.group(1).strip() if plate_m else "N/A"

        # Car Make: e.g. "Car make :GMC" or "Car make: Mitsubishi Pajero"
        make_m = re.search(r"car\s*make\s*[:\-]?\s*([A-Za-z0-9\s]+?)(?=\s*valid|\s*only|\s*$|\n)", plain, re.IGNORECASE)
        car_make = make_m.group(1).strip() if make_m else "N/A"

        # Validity: e.g. "only for 3 days from today 13/10/2026 until 15/10/2026", "only for today", "only today"
        valid_m = re.search(r"(only\s+for\s+\d+\s+days[^\n\.]*|only\s+for\s+today[^\n\.]*|only\s+today[^\n\.]*|today\s+only[^\n\.]*|from\s+\d+/\d+/\d+\s+until\s+\d+/\d+/\d+)", plain, re.IGNORECASE)
        if valid_m:
            validity = valid_m.group(1).strip().rstrip(".").strip()
        elif "permanent" in t_low:
            validity = "Permanent"
        else:
            validity = "N/A"

        # Requester
        requester = event.get("sender", "Requester")

        return {
            "message_id": event.get("message_id", ""),
            "datetime": event.get("datetime"),
            "raw_datetime": event.get("raw_datetime", ""),
            "sender": requester,
            "group_name": event.get("group_name", ""),
            "text": text,
            "plate_no": plate_no,
            "car_make": car_make,
            "validity": validity,
            "status": "Pending",
            "photos": event.get("photos", []),
            "quoted_message_id": event.get("quoted_message_id", ""),
        }
