"""Comprehensive automated test suite for Meydan Reports Automation Engine."""

import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from reports_engine.date_utils import (
    DUBAI_TZ,
    normalize_datetime_string,
    parse_datetime,
    get_shift_for_timestamp,
    is_within_freshness,
)
from reports_engine import tickets_db
from reports_engine.config_loader import get_config
from reports_engine.manager import _keyword_at_word_start
from reports_engine.repair_tickets import repair_tickets
from reports_engine.email_reader import parse_parking_email_body
from reports_engine.sender_resolver import SenderResolver
from reports_engine.deduplicator import deduplicate_events, calculate_similarity
from reports_engine.whatsapp_parser import WhatsAppParser
from reports_engine.report_generators import (
    generate_parking_report_text,
    generate_maintenance_report_text,
    generate_operations_report_text,
    generate_parking_violations_report_text,
    generate_vehicle_permissions_report_text,
)


class TestDateUtils(unittest.TestCase):
    def test_arabic_numerals_normalization(self):
        raw = "١١‏/١٠‏/٢٠٢٦، ١٠:٢٩:٤٥ ص"
        norm = normalize_datetime_string(raw)
        self.assertIn("11/10/2026", norm)
        self.assertIn("AM", norm)

    def test_parse_datetime_arabic(self):
        raw = "١١‏/١٠‏/٢٠٢٦، ١٠:٢٩:٤٥ ص"
        dt = parse_datetime(raw)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)
        self.assertEqual(dt.day, 11)
        self.assertEqual(dt.hour, 10)
        self.assertEqual(dt.minute, 29)

    def test_parse_datetime_western_12h(self):
        raw = "10/11/2026, 02:59:22 PM"
        dt = parse_datetime(raw)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)
        self.assertEqual(dt.day, 11)
        self.assertEqual(dt.hour, 14)

    def test_parse_datetime_bot_us_format_day_le_12(self):
        dt = parse_datetime("10/12/2026, 06:12:22 AM")
        self.assertEqual((dt.month, dt.day, dt.hour), (10, 12, 6))

    def test_parse_datetime_24h_is_day_first(self):
        dt = parse_datetime("10/12/2026, 18:12")
        self.assertEqual((dt.month, dt.day), (12, 10))

    def test_shift_assignment(self):
        # Day shift: 05:00 to 16:59:59
        dt_day = datetime(2026, 10, 13, 10, 30, tzinfo=DUBAI_TZ)
        shift, s_date = get_shift_for_timestamp(dt_day)
        self.assertEqual(shift, "Day")
        self.assertEqual(s_date, "13-10-2026")

        # Night shift at 20:00
        dt_night = datetime(2026, 10, 13, 20, 0, tzinfo=DUBAI_TZ)
        shift, s_date = get_shift_for_timestamp(dt_night)
        self.assertEqual(shift, "Night")
        self.assertEqual(s_date, "13-10-2026")

        # Night shift after midnight (03:00) belongs to previous evening shift
        dt_early_morn = datetime(2026, 10, 14, 3, 0, tzinfo=DUBAI_TZ)
        shift, s_date = get_shift_for_timestamp(dt_early_morn)
        self.assertEqual(shift, "Night")
        self.assertEqual(s_date, "13-10-2026")

    def test_freshness_window(self):
        slot_dt = datetime(2026, 10, 13, 12, 0, tzinfo=DUBAI_TZ)
        # Message 30 mins before -> Fresh
        msg_30m = datetime(2026, 10, 13, 11, 30, tzinfo=DUBAI_TZ)
        self.assertTrue(is_within_freshness(msg_30m, slot_dt, window_minutes=60))

        # Message 75 mins before -> Stale
        msg_75m = datetime(2026, 10, 13, 10, 45, tzinfo=DUBAI_TZ)
        self.assertFalse(is_within_freshness(msg_75m, slot_dt, window_minutes=60))

        # Message after slot time -> Not before
        msg_after = datetime(2026, 10, 13, 12, 5, tzinfo=DUBAI_TZ)
        self.assertFalse(is_within_freshness(msg_after, slot_dt, window_minutes=60))


class TestEmailParsingAndArithmetic(unittest.TestCase):
    def setUp(self):
        self.sample_valid_email = """
MEYDAN PARKING
Date: 13-10-2026
Time: 12:00

🏢 *P BUILDING*
- Total Slots: 71
- Cars Parked: 50 🚗
- Empty Slots: 21 ✅

🏢 *N BUILDING*
- Total Slots: 77
- Cars Parked: 60 🚗
- Empty Slots: 17 ✅

🏢 *M BUILDING*
- Total Slots: 165
- Cars Parked: 100 🚗
- Empty Slots: 65 ✅

🏢 *Q BUILDING*
- Total Slots: 146
- Cars Parked: 90 🚗
- Empty Slots: 56 ✅

📊 *TOTAL SUMMARY* = 459
- Total Cars Entered: 350 ⬇️
- Total Cars Parked: 300
- Total Empty Slots: 159
"""

    def test_parse_valid_email(self):
        res = parse_parking_email_body(self.sample_valid_email)
        self.assertIsNotNone(res)
        self.assertEqual(res["date"], "13-10-2026")
        self.assertEqual(res["time"], "12:00")
        self.assertEqual(res["buildings"]["P"]["cars_parked"], 50)
        self.assertEqual(res["buildings"]["N"]["cars_parked"], 60)
        self.assertEqual(res["buildings"]["M"]["cars_parked"], 100)
        self.assertEqual(res["buildings"]["Q"]["cars_parked"], 90)
        self.assertEqual(res["summary"]["total_cars_entered"], 350)
        self.assertEqual(res["summary"]["total_cars_parked"], 300)
        self.assertEqual(res["summary"]["total_empty_slots"], 159)
        self.assertTrue(res["validation_passed"])
        self.assertEqual(len(res["discrepancies"]), 0)

    def test_email_arithmetic_discrepancy(self):
        # Tamper total parked to 320 instead of 300 (sum 50+60+100+90 = 300)
        bad_email = self.sample_valid_email.replace("Total Cars Parked: 300", "Total Cars Parked: 320")
        res = parse_parking_email_body(bad_email)
        self.assertIsNotNone(res)
        self.assertFalse(res["validation_passed"])
        self.assertTrue(any("Arithmetic mismatch" in d for d in res["discrepancies"]))


class TestSenderResolver(unittest.TestCase):
    def test_mention_resolution(self):
        resolver = SenderResolver()
        resolver.register_sender("~Md Saiful Mia 🇦🇪 (102809152299018)")
        resolver.register_sender("Masrawy (214203004629128)")

        text = "Staff car reported by @102809152299018 please contact @214203004629128"
        resolved = resolver.resolve_mentions(text)
        self.assertIn("@Md Saiful Mia", resolved)
        self.assertIn("@Masrawy", resolved)
        self.assertNotIn("@102809152299018", resolved)


class TestDeduplication(unittest.TestCase):
    def test_cross_group_duplicate_merge(self):
        dt = datetime(2026, 10, 13, 14, 0, tzinfo=DUBAI_TZ)
        events = [
            {
                "message_id": "MSG_001",
                "group_name": "Meydan FM Team💪",
                "building": "M",
                "floor": "1st Floor",
                "text": "Inside Room No. 2 AC cooling is low need to check",
                "datetime": dt,
            },
            {
                "message_id": "MSG_002",
                "group_name": "Meydan - Security Team",
                "building": "M",
                "floor": "1st Floor",
                "text": "Inside Room No 2 AC cooling is low need to check",
                "datetime": dt + timedelta(minutes=10),
            },
        ]
        unique, uncertain = deduplicate_events(events)
        self.assertEqual(len(unique), 1)
        self.assertEqual(len(uncertain), 0)
        self.assertIn("MSG_001", unique[0]["source_message_ids"])
        self.assertIn("MSG_002", unique[0]["source_message_ids"])

    def test_uncertain_near_match_flagged(self):
        dt = datetime(2026, 10, 13, 14, 0, tzinfo=DUBAI_TZ)
        events = [
            {
                "message_id": "MSG_A",
                "building": "M",
                "text": "Glass door handle loose near main entrance",
                "datetime": dt,
            },
            {
                "message_id": "MSG_B",
                "building": "M",
                "text": "Glass door not closing smoothly near entrance",
                "datetime": dt + timedelta(minutes=15),
            },
        ]
        unique, uncertain = deduplicate_events(events, similarity_threshold=0.85)
        self.assertEqual(len(unique), 2)
        self.assertTrue(len(uncertain) >= 1)


class TestReportParsersAndLinking(unittest.TestCase):
    def setUp(self):
        self.parser = WhatsAppParser()

    def test_smart_parking_parsing(self):
        sample = """
@Security
Cars entered as of 14 o'clock = 887
Cars parked as of 14 o'clock = 866

Cars parked outside = 11
"""
        res = self.parser.parse_smart_parking(sample)
        self.assertIsNotNone(res)
        self.assertEqual(res["hour_written"], 14)
        self.assertEqual(res["cars_entered"], 887)
        self.assertEqual(res["occupied_slots"], 866)
        self.assertEqual(res["empty_slots"], 887 - 866)
        self.assertEqual(res["outside_cars"], 11)

    def test_ramp_service_road_parsing(self):
        sample = """
Parking Occupany Report
Date : 13-10-2026
Time 12:00 PM
Podium Parking Total Slots : 127
Customer and visitor cars : 45
P.o.d Cars : 5
Staff Cars : 30
Total cars Entered at ramp area : 120
Total cars parked at ramp Area : 80
Total Slot Service Road : 127
Total Car Entered Service Road : 50
Total Car parked at service road : 35
Partially Blocked Parking : 2
"""
        res = self.parser.parse_ramp_service_road(sample)
        self.assertIsNotNone(res)
        self.assertEqual(res["customer_visitor"], 45)
        self.assertEqual(res["pod_cars"], 5)
        self.assertEqual(res["staff_cars"], 30)
        self.assertEqual(res["total_cars_parked_ramp"], 80)
        self.assertEqual(res["service_road_slots"], 127)
        self.assertEqual(res["service_road_parked"], 35)
        self.assertEqual(res["partially_blocked_parking"], 2)

    def test_rumble_area_parsing(self):
        sample = "Date 13th October 2026 / Rumble area location / Time09:30AM / Total car's parking at Rumble area now are 25 car's / Bus 🚌 02 car"
        res = self.parser.parse_rumble_area(sample)
        self.assertIsNotNone(res)
        self.assertEqual(res["total_cars"], 25)
        self.assertEqual(res["bus"], 2)

    def test_parking_violation_parsing(self):
        event = {
            "message_id": "V_001",
            "datetime": datetime.now(DUBAI_TZ),
            "raw_datetime": "13-10-2026 11:00 AM",
            "sender": "Ahmed (Security)",
            "group_name": "Multistory Parking Building Meydan",
            "text": "Staff parked his non-electric car on EV fast charging slot 399 on level 4 Kindly notify concern department",
            "photos": ["thumb_v1.jpg"],
        }
        res = self.parser.parse_parking_violation(event)
        self.assertIsNotNone(res)
        self.assertEqual(res["level"], "Level 4")
        self.assertEqual(res["slot_no"], "399")

    def test_vehicle_permission_parsing(self):
        event = {
            "message_id": "P_001",
            "datetime": datetime.now(DUBAI_TZ),
            "raw_datetime": "13-10-2026 09:30 AM",
            "sender": "Operations Admin (971501234567)",
            "group_name": "Parking Team at Meydan",
            "text": "Kindly allow this vehicle in smart parking / VIP parking slot. Plate no : 4 / 69888 Car make :GMC only for 3 days from today 13/10/2026 until 15/10/2026",
            "photos": [],
        }
        res = self.parser.parse_vehicle_permission(event)
        self.assertIsNotNone(res)
        self.assertEqual(res["plate_no"], "4 / 69888")
        self.assertEqual(res["car_make"], "GMC")
        self.assertIn("3 days", res["validity"])


class TestReportFormatting(unittest.TestCase):
    def test_parking_layout_reproduction(self):
        report = generate_parking_report_text(
            report_date="13-10-2026",
            report_time="14:00",
            email_data=None,
            smart_data={"total_slots": 887, "cars_entered": 500, "occupied_slots": 400, "empty_slots": 487, "outside_cars": 10},
            ramp_data={"customer_visitor": 30, "pod_cars": 2, "staff_cars": 20, "total_cars_entered_ramp": 70, "total_cars_parked_ramp": 52, "service_road_entered": 40, "service_road_parked": 25, "partially_blocked_parking": 1},
            rumble_data={"total_cars": 15, "bus": 1},
        )
        self.assertIn("🅿️ *MEYDAN PARKING*", report)
        self.assertIn("🏢 *P BUILDING*", report)
        self.assertIn("📊 *TOTAL SUMMARY* = 459", report)
        self.assertIn("🅿️ *SMART PARKING SUMMARY REPORT*", report)
        self.assertIn("🅿️ *Ramp Area Location*\n\n• Total Slots Designated : 127", report)
        self.assertIn("🛣️ *Service Road*\n\n• Total Slots : 127", report)
        self.assertIn("🏁 *Rumble Area*", report)


class TestOperationsKeywordMatching(unittest.TestCase):
    def setUp(self):
        ops = get_config()["operations"]
        self.vendors, self.activities = ops["vendors"], ops["activities"]

    def _included(self, text):
        t = text.lower()
        return bool(_keyword_at_word_start(t, self.vendors) or _keyword_at_word_start(t, self.activities))

    def test_word_forms_and_new_vendors_are_included(self):
        # Real Security Team messages from 14-10-2026 that the exact-word list missed
        self.assertTrue(self._included("These two sofa are shifted from p building to N building"))
        self.assertTrue(self._included("Al zawaya company staff remove 6 damaged chairs in m building 4th floor"))
        self.assertEqual(_keyword_at_word_start("al zawaya company staff remove", self.vendors), "al zawaya")
        self.assertEqual(_keyword_at_word_start("eurosec vendor is removing the 1 camera", self.vendors), "eurosec")
        for text in ("Table installation work done", "Chairs removed", "Boxes delivered to Q building"):
            self.assertTrue(self._included(text), text)

    def test_keywords_only_match_at_word_start(self):
        # "arki" used to match inside "parking"; bare "shift" is not a keyword (handover chatter)
        self.assertIsNone(_keyword_at_word_start("m building back side parking barrier", self.vendors))
        self.assertFalse(self._included("Night shift guard reporting, all clear"))
        self.assertFalse(self._included("Download the attendance sheet"))

    def test_guard_fault_reports_are_not_operations(self):
        # Real fault reports from 11-14 Oct 2026; they belong to maintenance, not operations
        ops = get_config()["operations"]
        faults = ops["fault_markers"]
        for text in (
            "The light near the Q Building Ground Floor near exit door is not working. Kindly inform Imdaad",
            "Two external lights installed above the elevators are not working and require maintenance.",
            "Q building first floor Speed Gate is out of service",
        ):
            t = text.lower()
            vendor = _keyword_at_word_start(t, self.vendors)
            act = _keyword_at_word_start(t, self.activities)
            self.assertIsNotNone(_keyword_at_word_start(t, faults), text)
            self.assertFalse(vendor and act, text)  # fault wins unless vendor AND activity both match
        # Vendor work on a faulty asset still counts
        t = "kone vendor installing new rope on the lift that is out of service"
        self.assertTrue(_keyword_at_word_start(t, self.vendors) and _keyword_at_word_start(t, self.activities))


class TestTicketsSla(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "tickets.db")
        tickets_db.init_tickets_table(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_hours_open_uses_parsed_datetime_not_raw_text(self):
        created = datetime(2026, 10, 12, 6, 12, 22, tzinfo=DUBAI_TZ)
        now_iso = (created + timedelta(hours=30)).isoformat()
        ticket = {
            "message_id": "msg-sla-1",
            "datetime": created,
            "raw_datetime": "10/12/2026, 06:12:22 AM",
            "status": "Pending",
            "text": "AC not working",
        }
        tickets_db.enrich_ticket(ticket, "maintenance", "FM", "Morning", now_iso, db_path=self.db_path)

        self.assertGreater(ticket["hours_open"], 0)
        self.assertAlmostEqual(ticket["hours_open"], 30.0, places=1)
        self.assertTrue(ticket["sla_breach"])
        stored = tickets_db.get_first_seen("msg-sla-1", self.db_path)
        self.assertEqual(datetime.fromisoformat(stored), created)

    def test_legacy_non_iso_first_seen_is_reparsed_and_repaired(self):
        legacy_raw = "١١‏/١٠‏/٢٠٢٦، ١٠:٢٩:٤٥ ص"  # 11 Oct 2026 10:29:45 (DD/MM)
        tickets_db.upsert_ticket(
            "msg-sla-2", "maintenance", "FM", None, None, "Leak", "Pending", None,
            first_seen=legacy_raw, last_seen=legacy_raw, shift_label="Morning", db_path=self.db_path,
        )
        now_iso = datetime(2026, 10, 12, 16, 29, 45, tzinfo=DUBAI_TZ).isoformat()
        ticket = {"message_id": "msg-sla-2", "status": "Pending", "text": "Leak"}
        tickets_db.enrich_ticket(ticket, "maintenance", "FM", "Morning", now_iso, db_path=self.db_path)

        self.assertAlmostEqual(ticket["hours_open"], 30.0, places=1)
        self.assertTrue(ticket["sla_breach"])
        stored = tickets_db.get_first_seen("msg-sla-2", self.db_path)
        self.assertEqual(datetime.fromisoformat(stored), datetime(2026, 10, 11, 10, 29, 45, tzinfo=DUBAI_TZ))

    def test_repair_script_rewrites_legacy_rows(self):
        iso = datetime(2026, 10, 12, 9, 0, tzinfo=DUBAI_TZ).isoformat()
        rows = {
            "msg-us": "10/12/2026, 06:12:22 AM",      # en-US MM/DD -> 12 Oct
            "msg-ar": "١١‏/١٠‏/٢٠٢٦، ١٠:٢٩:٤٥ ص",     # Arabic DD/MM -> 11 Oct
            "msg-iso": iso,
            "msg-bad": "not a date",
        }
        for mid, fs in rows.items():
            tickets_db.upsert_ticket(
                mid, "maintenance", "FM", None, None, "x", "Pending", None,
                first_seen=fs, last_seen=iso, shift_label="Morning", db_path=self.db_path,
            )

        dry = repair_tickets(self.db_path, apply=False)
        self.assertEqual({e["message_id"] for e in dry["fixed"]}, {"msg-us", "msg-ar"})
        self.assertEqual([e["message_id"] for e in dry["unparseable"]], ["msg-bad"])
        self.assertEqual(tickets_db.get_first_seen("msg-us", self.db_path), rows["msg-us"])  # dry run wrote nothing

        repair_tickets(self.db_path, apply=True)
        get = lambda mid: datetime.fromisoformat(tickets_db.get_first_seen(mid, self.db_path))
        self.assertEqual(get("msg-us"), datetime(2026, 10, 12, 6, 12, 22, tzinfo=DUBAI_TZ))
        self.assertEqual(get("msg-ar"), datetime(2026, 10, 11, 10, 29, 45, tzinfo=DUBAI_TZ))
        self.assertEqual(tickets_db.get_first_seen("msg-iso", self.db_path), iso)
        self.assertEqual(tickets_db.get_first_seen("msg-bad", self.db_path), "not a date")
        self.assertEqual(repair_tickets(self.db_path)["fixed"], [])  # idempotent


if __name__ == "__main__":
    unittest.main()


class TestDataQualityReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from reports_engine.manager import ReportsManager
        from reports_engine.data_quality import generate_data_quality
        cls.res = generate_data_quality(ReportsManager())

    def test_metrics_are_internally_consistent(self):
        m = self.res["metrics"]
        self.assertEqual(sum(m["per_group"].values()), m["messages"])
        self.assertLessEqual(m["reply_used"], m["reply_captured"])
        self.assertLessEqual(m["reply_captured"], m["messages"])
        self.assertLessEqual(m["tickets_without_building"], m["tickets"])
        self.assertLessEqual(m["permissions_without_plate"], m["permissions"])

    def test_reply_warning_needs_recorded_reply_info(self):
        m = self.res["metrics"]
        if m["messages"] and m["reply_captured"] / m["messages"] < 0.8:
            self.assertNotIn("أكتر من الربط بالـ Reply", self.res["text"])
            self.assertIn("مش دقيقة لسه", self.res["text"])
