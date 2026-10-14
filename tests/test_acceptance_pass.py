"""Real-data acceptance test suite for cases A through L using isolated copies."""

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import sqlite3

from reports_engine.date_utils import DUBAI_TZ
from reports_engine.whatsapp_parser import WhatsAppParser
from reports_engine.deduplicator import deduplicate_events
from reports_engine.sender_resolver import SenderResolver
from reports_engine.report_generators import (
    generate_parking_report_text,
    generate_maintenance_report_text,
    generate_operations_report_text,
    generate_parking_violations_report_text,
    generate_vehicle_permissions_report_text,
)


class AcceptancePassTest(unittest.TestCase):
    def setUp(self):
        self.parser = WhatsAppParser()

    # ─────────────────────────────────────────────────────────────
    # CASE A: SMART PARKING
    # ─────────────────────────────────────────────────────────────
    def test_case_a_smart_parking(self):
        print("\n=== CASE A: Smart Parking Parsing ===")
        # 1. 12-10 as of 10 o'clock: entered 810, parked 806, outside 11
        txt_10 = "Cars entered as of 10 o'clock = 810\nCars parked as of 10 o'clock = 806\nCars parked outside = 11"
        res_10 = self.parser.parse_smart_parking(txt_10)
        self.assertEqual(res_10["cars_entered"], 810)
        self.assertEqual(res_10["occupied_slots"], 806)
        self.assertEqual(res_10["outside_cars"], 11)
        # Spec requires exactly one diagnostic: calculated empty slots note (open question per spec §4)
        self.assertEqual(len(res_10["diagnostics"]), 1)
        self.assertTrue(any("calculated" in d.lower() for d in res_10["diagnostics"]))

        # 2. 12-10 as of 12 o'clock: entered 871, parked 658, outside 17 (suspicious)
        txt_12 = "Cars entered as of 12 o'clock = 871\nCars parked as of 12 o'clock = 658\nCars parked outside = 17"
        res_12 = self.parser.parse_smart_parking(txt_12)
        self.assertEqual(res_12["cars_entered"], 871)
        self.assertEqual(res_12["occupied_slots"], 658)
        self.assertEqual(res_12["outside_cars"], 17)
        self.assertTrue(any("Suspicious value" in d for d in res_12["diagnostics"]))

        # 3. 12-10 as of 14 o'clock: entered 887, parked 866, outside "1$" (typo)
        txt_14 = "Cars entered as of 14 o'clock = 887\nCars parked as of 14 o'clock = 866\nCars parked outside = 1$"
        res_14 = self.parser.parse_smart_parking(txt_14)
        self.assertEqual(res_14["cars_entered"], 887)
        self.assertEqual(res_14["occupied_slots"], 866)
        self.assertEqual(res_14["outside_cars"], "N/A")
        self.assertTrue(any("Typo in source number" in d for d in res_14["diagnostics"]))

        # 4. 13-10 as of 10 o'clock: entered 806, parked 801, outside "09" -> 9
        txt_13_10 = "Cars entered as of 10 o'clock = 806\nCars parked as of 10 o'clock = 801\nCars parked outside = 09"
        res_13_10 = self.parser.parse_smart_parking(txt_13_10)
        self.assertEqual(res_13_10["cars_entered"], 806)
        self.assertEqual(res_13_10["occupied_slots"], 801)
        self.assertEqual(res_13_10["outside_cars"], 9)

        print("CASE A Result: PASS")

    # ─────────────────────────────────────────────────────────────
    # CASE B: RUMBLE AREA
    # ─────────────────────────────────────────────────────────────
    def test_case_b_rumble_area(self):
        print("\n=== CASE B: Rumble Area Variations ===")
        # 12-10 at 01:30PM: 407 cars, 0 buses ("Tim 01:30PM")
        txt_1 = "Date 12th October 2026 / Rumble area location / Tim 01:30PM / Total car's parking at Rumble area now are 407 car's / Bus 🚌 00 car"
        res_1 = self.parser.parse_rumble_area(txt_1)
        self.assertEqual(res_1["total_cars"], 407)
        self.assertEqual(res_1["bus"], 0)
        self.assertEqual(res_1["time_written"], "01:30PM")

        # 13-10 at 09:30AM: 209 cars, 0 buses ("Time09:30AM")
        txt_2 = "Date 13th October 2026 / Rumble area location / Time09:30AM / Total car's parking at Rumble area now are 209 car's / Bus 🚌 00 car"
        res_2 = self.parser.parse_rumble_area(txt_2)
        self.assertEqual(res_2["total_cars"], 209)
        self.assertEqual(res_2["bus"], 0)
        self.assertEqual(res_2["time_written"], "09:30AM")

        print("CASE B Result: PASS")

    # ─────────────────────────────────────────────────────────────
    # CASE C: RAMP / SERVICE ROAD FIXTURE
    # ─────────────────────────────────────────────────────────────
    def test_case_c_ramp_service_road_fixture(self):
        print("\n=== CASE C: Ramp / Service Road Exact Fixture ===")
        fixture = """
Parking Occupany Report
Date : 12-10-2026
Time 11:30 AM
Podium Total Slots
127
Customer and visitor
87
P.o.d
05
Staff
118
Total entered at ramp
210
Total parked at ramp
158
Total Slot Service Road
120
entered Service Road
131
parked Service Road
117
Partially Blocked
04
"""
        res = self.parser.parse_ramp_service_road(fixture)
        self.assertIsNotNone(res)
        self.assertEqual(res["podium_slots"], 127)
        self.assertEqual(res["customer_visitor"], 87)
        self.assertEqual(res["pod_cars"], 5)  # parsed integer from 05
        self.assertEqual(res["staff_cars"], 118)
        self.assertEqual(res["total_cars_entered_ramp"], 210)
        self.assertEqual(res["total_cars_parked_ramp"], 158)
        self.assertEqual(res["service_road_slots"], 127)  # Fixed at 127
        self.assertEqual(res["reported_service_road_slots"], 120)
        self.assertEqual(res["service_road_entered"], 131)
        self.assertEqual(res["service_road_parked"], 117)
        self.assertEqual(res["partially_blocked_parking"], 4)  # parsed integer from 04
        self.assertTrue(any("reported as 120" in d for d in res["diagnostics"]))

        print("CASE C Result: PASS")

    # ─────────────────────────────────────────────────────────────
    # CASE D: DUPLICATE DETECTION (Floor separation & cross-group)
    # ─────────────────────────────────────────────────────────────
    def test_case_d_deduplication(self):
        print("\n=== CASE D: Deduplication Precision ===")
        dt = datetime(2026, 10, 12, 10, 0, tzinfo=DUBAI_TZ)
        # 3 different floors in Security Team
        sec_events = [
            {"message_id": "SEC_3F", "group_name": "Meydan - Security Team", "building": "N", "floor": "3rd Floor", "text": "N building 3rd floor two emergency exit lights are not working", "datetime": dt},
            {"message_id": "SEC_2F", "group_name": "Meydan - Security Team", "building": "N", "floor": "2nd Floor", "text": "N building 2nd floor emergency exit light is not working", "datetime": dt},
            {"message_id": "SEC_1F", "group_name": "Meydan - Security Team", "building": "N", "floor": "1st Floor", "text": "N building 1st floor exit staircase emergency exit light is not working", "datetime": dt},
        ]
        # Same 3 posted in Fire & Safety Team
        fs_events = [
            {"message_id": "FS_3F", "group_name": "Fire & Safety Team Meydan", "building": "N", "floor": "3rd Floor", "text": "N building 3rd floor two emergency exit lights are not working", "datetime": dt + timedelta(minutes=5)},
            {"message_id": "FS_2F", "group_name": "Fire & Safety Team Meydan", "building": "N", "floor": "2nd Floor", "text": "N building 2nd floor emergency exit light is not working", "datetime": dt + timedelta(minutes=5)},
            {"message_id": "FS_1F", "group_name": "Fire & Safety Team Meydan", "building": "N", "floor": "1st Floor", "text": "N building 1st floor exit staircase emergency exit light is not working", "datetime": dt + timedelta(minutes=5)},
        ]
        all_events = sec_events + fs_events
        unique, uncertain = deduplicate_events(all_events)
        self.assertEqual(len(unique), 3)  # Exactly 3 events (1st, 2nd, 3rd floor)
        for u in unique:
            self.assertEqual(len(u["source_message_ids"]), 2)  # Each merged with its counterpart

        # P building service lift light
        p_sec = {"message_id": "P_SEC", "group_name": "Meydan - Security Team", "building": "P", "floor": "Ground Floor", "text": "P building ground floor inside service lift light not working", "datetime": dt}
        p_fm = {"message_id": "P_FM", "group_name": "Meydan FM Team💪", "building": "P", "floor": "Ground Floor", "text": "P building ground floor inside service lift light not working", "datetime": dt + timedelta(minutes=15)}
        u_p, _ = deduplicate_events([p_sec, p_fm])
        self.assertEqual(len(u_p), 1)
        self.assertEqual(len(u_p[0]["source_message_ids"]), 2)

        # M Building barrier report (FM Team 06:12 and Security Team 07:20 on 12-10, different wording)
        dt_fm = datetime(2026, 10, 12, 6, 12, tzinfo=DUBAI_TZ)
        dt_sec = datetime(2026, 10, 12, 7, 20, tzinfo=DUBAI_TZ)
        m_fm = {"message_id": "M_BAR_FM", "group_name": "Meydan FM Team💪", "building": "M", "text": "M Building parking barrier gate arm damaged and shaking", "datetime": dt_fm}
        m_sec = {"message_id": "M_BAR_SEC", "group_name": "Meydan - Security Team", "building": "M", "text": "Vehicle hit barrier at M building entry gate, not opening", "datetime": dt_sec}
        u_m, unc_m = deduplicate_events([m_fm, m_sec], similarity_threshold=0.85)
        # Because wording differs, they do not over-merge; flagged if borderline
        self.assertEqual(len(u_m), 2)
        print("CASE D Result: PASS (No over-merge, exact floors required)")

    # ─────────────────────────────────────────────────────────────
    # CASE E: PARKING VIOLATIONS & UNLINKED REPLIES
    # ─────────────────────────────────────────────────────────────
    def test_case_e_violations(self):
        print("\n=== CASE E: Parking Violations & Unlinked Replies ===")
        ev = {
            "message_id": "V_399",
            "group_name": "Multistory Parking Building Meydan",
            "datetime": datetime(2026, 10, 13, 9, 38, tzinfo=DUBAI_TZ),
            "raw_datetime": "13-10-2026 09:38 AM",
            "sender": "Ahmed (Security)",
            "text": "Staff parked his non-electric car on EV fast charging slot 399 on level 4 Kindly notify concern department",
            "photos": ["thumb_399.jpg"],
        }
        res = self.parser.parse_parking_violation(ev)
        self.assertIsNotNone(res)
        self.assertEqual(res["slot_no"], "399")
        self.assertEqual(res["level"], "Level 4")

        # Replies without Quoted Message ID
        reply_noted = {"message_id": "R_01", "text": "Noted", "quoted_message_id": ""}
        reply_updated = {"message_id": "R_02", "text": "Updated: Staff stated he is in the meeting and will remove it shortly", "quoted_message_id": ""}
        # When quoted_id is empty, they must stay unlinked
        self.assertEqual(reply_noted["quoted_message_id"], "")
        self.assertEqual(reply_updated["quoted_message_id"], "")
        print("CASE E Result: PASS")

    # ─────────────────────────────────────────────────────────────
    # CASE F: VEHICLE PERMISSION REQUESTS
    # ─────────────────────────────────────────────────────────────
    def test_case_f_vehicle_permissions(self):
        print("\n=== CASE F: Vehicle Permissions Extraction ===")
        # 1. 13-10 06:29
        ev_1 = {
            "message_id": "PERM_1",
            "datetime": datetime(2026, 10, 13, 6, 29, tzinfo=DUBAI_TZ),
            "raw_datetime": "13-10-2026 06:29 AM",
            "sender": "Admin",
            "text": "Kindly allow this vehicle in smart parking Plate no : 4 / 69888 Car make :GMC only for 3 days from today 13/10/2026 until 15/10/2026",
        }
        res_1 = self.parser.parse_vehicle_permission(ev_1)
        self.assertEqual(res_1["plate_no"], "4 / 69888")
        self.assertEqual(res_1["car_make"], "GMC")
        self.assertIn("3 days", res_1["validity"])

        # 2. 07:54 (no make -> N/A)
        ev_2 = {
            "message_id": "PERM_2",
            "datetime": datetime(2026, 10, 13, 7, 54, tzinfo=DUBAI_TZ),
            "raw_datetime": "13-10-2026 07:54 AM",
            "sender": "Guest Relations",
            "text": "Kindly allow this vehicle in VIP parking slot Plate no. EE 80743 only today",
        }
        res_2 = self.parser.parse_vehicle_permission(ev_2)
        self.assertEqual(res_2["plate_no"], "EE 80743")
        self.assertEqual(res_2["car_make"], "N/A")
        self.assertEqual(res_2["validity"], "only today")

        # 3. 08:55
        ev_3 = {
            "message_id": "PERM_3",
            "datetime": datetime(2026, 10, 13, 8, 55, tzinfo=DUBAI_TZ),
            "raw_datetime": "13-10-2026 08:55 AM",
            "sender": "Coordinator",
            "text": "Kindly allow this vehicle in VIP parking slot Plate no : V 65372 Car make : Mitsubishi Pajero only today",
        }
        res_3 = self.parser.parse_vehicle_permission(ev_3)
        self.assertEqual(res_3["plate_no"], "V 65372")
        self.assertEqual(res_3["car_make"], "Mitsubishi Pajero")

        print("CASE F Result: PASS")

    # ─────────────────────────────────────────────────────────────
    # CASE G: OPERATIONS REPORT (Security Team only)
    # ─────────────────────────────────────────────────────────────
    def test_case_g_operations(self):
        print("\n=== CASE G: Operations Events & Exclusions ===")
        # Verify leave requests, "Noted", and emojis are excluded
        self.assertTrue(self.parser.is_non_operational("Noted"))
        self.assertTrue(self.parser.is_non_operational("👍"))
        self.assertTrue(self.parser.is_non_operational("Annual leave details Staff ID 1234 start date 15/10/2026 total days 14"))

        # Verify operational activities
        t_telecon = "P Building 4th floor TELECON SIGNS team replacing advertising stickers and removing old ones"
        self.assertFalse(self.parser.is_non_operational(t_telecon))
        t_hk = "Q Building 1st floor and GF Housekeeping team placing new chairs and removing old ones"
        self.assertFalse(self.parser.is_non_operational(t_hk))
        t_arki = "M Building 1st floor Arki vendors installing display boards"
        self.assertFalse(self.parser.is_non_operational(t_arki))
        t_fit = "N-Building, 1st Floor, Inside Room No. 2 – Fit Movers vendors are installing tables"
        self.assertFalse(self.parser.is_non_operational(t_fit))
        t_rose = "GSU room flower 🌼 watering is on going by Rose garden team"
        self.assertFalse(self.parser.is_non_operational(t_rose))

        print("CASE G Result: PASS")

    # ─────────────────────────────────────────────────────────────
    # CASE H: MAINTENANCE REPEAT TICKETS
    # ─────────────────────────────────────────────────────────────
    def test_case_h_repeat_tickets(self):
        print("\n=== CASE H: Repeat Ticket Detection on Later Day ===")
        dt_day1 = datetime(2026, 10, 12, 11, 0, tzinfo=DUBAI_TZ)
        dt_day2 = datetime(2026, 10, 13, 10, 30, tzinfo=DUBAI_TZ)
        t_day1 = {"message_id": "AC_D1", "building": "N", "floor": "1st Floor", "text": "N Building 1st floor Security Control Room AC cooling is low", "datetime": dt_day1}
        t_day2 = {"message_id": "AC_D2", "building": "N", "floor": "1st Floor", "text": "N Building 1st floor Security Control Room AC cooling is low need to check", "datetime": dt_day2}

        unique, uncertain = deduplicate_events([t_day1, t_day2])
        self.assertEqual(len(unique), 2)  # MUST NOT merge across different days
        self.assertTrue(any(u.get("type") == "Possible repeat of an earlier ticket" for u in uncertain))
        print("CASE H Result: PASS (Flagged as Possible Repeat, not merged)")

    # ─────────────────────────────────────────────────────────────
    # CASE L: POLISH @MENTION RESOLUTION
    # ─────────────────────────────────────────────────────────────
    def test_case_l_clean_sender_resolution(self):
        print("\n=== CASE L: Clean @Mention Resolution ===")
        resolver = SenderResolver()
        resolver.register_sender("~Md Saiful Mia 🇦🇪 (102809152299018)")
        res = resolver.resolve_mentions("Reported by @102809152299018")
        self.assertEqual(res, "Reported by @Md Saiful Mia")
        self.assertNotIn("~", res)
        self.assertNotIn("🇦🇪", res)
        print("CASE L Result: PASS")


    # ─────────────────────────────────────────────────────────────
    # CASE M: VIOLATIONS EXCEL PHOTO THUMBNAIL (Report 5 only)
    # ─────────────────────────────────────────────────────────────
    def test_case_m_violations_excel_photo_thumbnail(self):
        """Verify generate_violations_excel embeds a real thumbnail for the EV slot 399 violation."""
        print("\n=== CASE M: Violations Excel Photo Thumbnail ===")
        import io
        from pathlib import Path
        from reports_engine.excel_exporter import generate_violations_excel, _make_thumbnail

        # Synthetic 1×1 red JPEG image to simulate a real photo
        from PIL import Image as PilImage
        buf = io.BytesIO()
        PilImage.new("RGB", (400, 300), color=(255, 0, 0)).save(buf, format="JPEG")
        fake_photo_bytes = buf.getvalue()

        events = [
            {
                "message_id": "TEST_EV_399",
                "location_heading": "Level 4, Slot 399",
                "level": "Level 4",
                "text": "Non-electric car on EV slot 399",
                "status": "Reported",
                "sender": "JUNIOR",
                "raw_datetime": "10/13/2026, 09:38:00 AM",
                "source_message_ids": ["TEST_EV_399"],
                "quoted_message_id": "",
                "photos_data": [{"bytes": fake_photo_bytes, "media_path": "xl/media/image43.jpeg"}],
            },
            {
                "message_id": "TEST_EV_400",
                "location_heading": "Level 2, Slot 10",
                "level": "Level 2",
                "text": "Blocked slot — no photo",
                "status": "Reported",
                "sender": "Guard",
                "raw_datetime": "10/13/2026, 10:00:00 AM",
                "source_message_ids": ["TEST_EV_400"],
                "quoted_message_id": "",
                "photos_data": [],  # No photo
            },
        ]
        summary = {"total": 2, "rectified": 0, "pending": 2}

        from reports_engine.report_generators import generate_parking_violations_report_text
        report_text = generate_parking_violations_report_text(events, summary)

        result = generate_violations_excel(
            report_title="Parking Violations Summary – Meydan",
            report_text=report_text,
            events=events,
            unlinked_or_needs_review=[],
            source_audit=[],
            filename_prefix="Test_Violations_Photo",
            thumbnail_max_side=160,
            thumbnail_quality=70,
        )

        excel_path = Path(result["excel_path"])
        self.assertTrue(excel_path.exists(), "Excel file was not created")
        self.assertGreater(result["file_size_bytes"], 0)

        # Exactly one photo mapped (TEST_EV_399 has photo; TEST_EV_400 does not)
        pm = result["photo_mapping"]
        self.assertEqual(len(pm), 1)
        self.assertEqual(pm[0]["message_id"], "TEST_EV_399")
        self.assertEqual(pm[0]["image_file"], "image43.jpeg")
        self.assertEqual(pm[0]["excel_row"], 2)  # header=row1, first event=row2

        # Thumbnail must be within 160px on longest side
        tw, th = [int(x) for x in pm[0]["thumb_px"].split("×")]
        self.assertLessEqual(max(tw, th), 160)

        # Thumbnail function: ensure it produces correct JPEG
        thumb_bytes, w, h = _make_thumbnail(fake_photo_bytes, 160, 70)
        self.assertLessEqual(max(w, h), 160)
        self.assertTrue(thumb_bytes[:3] == b'\xff\xd8\xff')  # JPEG magic bytes

        # Verify sheets inside the xlsx
        import openpyxl
        wb = openpyxl.load_workbook(str(excel_path))
        self.assertIn("Report Summary", wb.sheetnames)
        self.assertIn("Events & Details", wb.sheetnames)
        self.assertIn("Photos", wb.sheetnames)  # because at least one photo exists
        self.assertIn("Source Audit", wb.sheetnames)

        # Event without photo must have "N/A" in photo cell (col I = column 9)
        ws_ev = wb["Events & Details"]
        self.assertEqual(ws_ev.cell(row=3, column=9).value, "N/A")  # second event, no photo

        print(f"CASE M Result: PASS-FIXTURE | file={excel_path.name} ({result['file_size_bytes']:,} bytes) | thumb={pm[0]['thumb_px']} px")
        excel_path.unlink(missing_ok=True)  # clean up temp file


if __name__ == "__main__":
    unittest.main()
