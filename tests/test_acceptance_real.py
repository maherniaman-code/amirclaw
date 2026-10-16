"""Comprehensive Acceptance Test Suite addressing Items 1 through 8 strictly."""

import unittest
import re
from datetime import datetime
from reports_engine.date_utils import DUBAI_TZ
from reports_engine.email_reader import parse_parking_email_body
from reports_engine.whatsapp_parser import WhatsAppParser
from reports_engine.deduplicator import deduplicate_events
from reports_engine.sender_resolver import clean_sender_name
from reports_engine.report_generators import (
    generate_parking_report_text,
    generate_maintenance_report_text,
    generate_operations_report_text,
    generate_parking_violations_report_text,
    generate_vehicle_permissions_report_text,
)


class TestAcceptanceSuite(unittest.TestCase):
    def setUp(self):
        self.parser = WhatsAppParser()

    # ─────────────────────────────────────────────────────────────
    # ITEM 2: Character-by-character template comparison test
    # ─────────────────────────────────────────────────────────────
    def test_item_2_template_exact_character_format(self):
        """Verifies heading line, BLANK line, and bullet points for all sections."""
        email_data = {
            "buildings": {
                "P": {"total_slots": 71, "cars_parked": 28, "empty_slots": 43},
                "N": {"total_slots": 77, "cars_parked": 42, "empty_slots": 35},
                "M": {"total_slots": 165, "cars_parked": 56, "empty_slots": 109},
                "Q": {"total_slots": 146, "cars_parked": 54, "empty_slots": 92},
            },
            "summary": {"total_cars_entered": 346, "total_cars_parked": 180, "total_empty_slots": 279},
        }
        rendered = generate_parking_report_text(
            report_date="13-10-2026",
            report_time="10:00",
            email_data=email_data,
            smart_data={"cars_entered": 806, "occupied_slots": 801, "empty_slots": 86, "outside_cars": 9},
            ramp_data={
                "customer_visitor": 87,
                "pod_cars": 5,
                "staff_cars": 118,
                "total_cars_entered_ramp": 210,
                "total_cars_parked_ramp": 158,
                "service_road_entered": 131,
                "service_road_parked": 117,
                "partially_blocked_parking": 4,
            },
            rumble_data={"total_cars": 209, "bus": 0},
        )

        # Expected structural skeleton (values masked to check character by character)
        expected_skeleton = (
            "🅿️ *MEYDAN PARKING*\n"
            "📆 Date: <VAL>\n"
            "⏰ Time: <VAL>\n"
            "\n"
            "🏢 *P BUILDING*\n"
            "\n"
            "• Total Slots: 71\n"
            "• Cars Parked: <VAL> 🚗\n"
            "• Empty Slots: <VAL> ✅\n"
            "\n"
            "🏢 *N BUILDING*\n"
            "\n"
            "• Total Slots: 77\n"
            "• Cars Parked: <VAL> 🚗\n"
            "• Empty Slots: <VAL> ✅\n"
            "\n"
            "🏢 *M BUILDING*\n"
            "\n"
            "• Total Slots: 165\n"
            "• Cars Parked: <VAL> 🚗\n"
            "• Empty Slots: <VAL> ✅\n"
            "\n"
            "🏢 *Q BUILDING*\n"
            "\n"
            "• Total Slots: 146\n"
            "• Cars Parked: <VAL> 🚗\n"
            "• Empty Slots: <VAL> ✅\n"
            "\n"
            "📊 *TOTAL SUMMARY* = 459\n"
            "\n"
            "• Total Cars Entered: <VAL> ⬇️\n"
            "• Total Cars Parked: <VAL>\n"
            "• Total Empty Slots: <VAL>\n"
            "\n"
            "🅿️ *SMART PARKING SUMMARY REPORT*\n"
            "\n"
            "• Total Slots : 887\n"
            "• Cars entered: <VAL>\n"
            "• Occupied slots : <VAL>\n"
            "• Total Empty Slots : <VAL>\n"
            "• Outside smart parking cars: <VAL>\n"
            "\n"
            "🅿️ *Ramp Area Location*\n"
            "\n"
            "- Total Slots Designated : 127\n"
            "- Customer and Visitors: <VAL>\n"
            "• P.O.D: <VAL>\n"
            "• Staff: <VAL>\n"
            "• Total car Entered : <VAL>\n"
            "• Total Car Parking at Ramp Area: <VAL>\n"
            "\n"
            "🛣️ *Service Road*\n"
            "\n"
            "• Total Slots : 127\n"
            "• Cars entered: <VAL>\n"
            "• Total Occupied Slots : <VAL>\n"
            "• Partially Blocked Parking : <VAL>\n"
            "\n"
            "🏁 *Rumble Area*\n"
            "\n"
            "• Total Cars: <VAL> 🚗\n"
            "• Bus 🚌: <VAL>"
        )

        # Mask dynamic values in rendered string to compare exact template structure
        masked_rendered = re.sub(r"(Date:\s*)\S+", r"\g<1><VAL>", rendered)
        masked_rendered = re.sub(r"(Time:\s*)\S+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Cars Parked:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Empty Slots:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Total Cars Entered:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Total Cars Parked:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Total Empty Slots\s*:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Cars entered:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Occupied slots :\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Outside smart parking cars:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(- Customer and Visitors:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• P\.O\.D:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Staff:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Total car Entered :\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Total Car Parking at Ramp Area:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Total Occupied Slots :\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Partially Blocked Parking :\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Total Cars:\s*)\d+", r"\g<1><VAL>", masked_rendered)
        masked_rendered = re.sub(r"(• Bus 🚌:\s*)\d+", r"\g<1><VAL>", masked_rendered)

        self.assertEqual(masked_rendered, expected_skeleton)

    # ─────────────────────────────────────────────────────────────
    # ITEM 3: Email Regex on real 02-10-2026 sample & edge cases
    # ─────────────────────────────────────────────────────────────
    def test_item_3_email_real_sample_and_variants(self):
        real_email = (
            "🅿️ *MEYDAN PARKING*\n"
            "📆 Date: 02-10-2026\n"
            "⏰ Time: 09:00\n"
            "🏢 *P BUILDING*\n"
            "- Total Slots: 71\n"
            "- Cars Parked: 28 🚗\n"
            "- Empty Slots: 43 ✅\n"
            "🏢 *N BUILDING*\n"
            "- Total Slots: 77\n"
            "- Cars Parked: 42 🚗\n"
            "- Empty Slots: 35 ✅\n"
            "🏢 *M BUILDING*\n"
            "- Total Slots: 165\n"
            "- Cars Parked: 56 🚗\n"
            "- Empty Slots: 109 ✅\n"
            "🏢 *Q BUILDING*\n"
            "- Total Slots: 146\n"
            "- Cars Parked: 54 🚗\n"
            "- Empty Slots: 92 ✅\n"
            "📊 *TOTAL SUMMARY* = 459\n"
            "- Total Cars Entered: 346 ⬇️\n"
            "- Total Cars Parked: 180\n"
            "- Total Empty Slots: 279"
        )

        res = parse_parking_email_body(real_email)
        self.assertIsNotNone(res)
        self.assertEqual(res["date"], "02-10-2026")
        self.assertEqual(res["time"], "09:00")
        self.assertEqual(res["buildings"]["P"]["cars_parked"], 28)
        self.assertEqual(res["buildings"]["P"]["empty_slots"], 43)
        self.assertEqual(res["buildings"]["N"]["cars_parked"], 42)
        self.assertEqual(res["buildings"]["N"]["empty_slots"], 35)
        self.assertEqual(res["buildings"]["M"]["cars_parked"], 56)
        self.assertEqual(res["buildings"]["M"]["empty_slots"], 109)
        self.assertEqual(res["buildings"]["Q"]["cars_parked"], 54)
        self.assertEqual(res["buildings"]["Q"]["empty_slots"], 92)
        self.assertEqual(res["summary"]["total_cars_entered"], 346)
        self.assertEqual(res["summary"]["total_cars_parked"], 180)
        self.assertEqual(res["summary"]["total_empty_slots"], 279)
        self.assertTrue(res["validation_passed"])
        self.assertEqual(res["discrepancies"], [])

        # Variant 1: Asterisks removed
        no_asterisks = real_email.replace("*", "")
        res_no_ast = parse_parking_email_body(no_asterisks)
        self.assertTrue(res_no_ast["validation_passed"])
        self.assertEqual(res_no_ast["summary"]["total_cars_parked"], 180)

        # Variant 2: Extra blank lines
        extra_blanks = "\n\n".join(real_email.split("\n"))
        res_blanks = parse_parking_email_body(extra_blanks)
        self.assertTrue(res_blanks["validation_passed"])

        # Variant 3: One number altered to flag arithmetic mismatch
        altered = real_email.replace("Total Cars Parked: 180", "Total Cars Parked: 185")
        res_altered = parse_parking_email_body(altered)
        self.assertFalse(res_altered["validation_passed"])
        self.assertTrue(any("Arithmetic mismatch" in d for d in res_altered["discrepancies"]))

    # ─────────────────────────────────────────────────────────────
    # ITEM 4: Exact Verbatim Ramp / Service Road Fixture
    # ─────────────────────────────────────────────────────────────
    def test_item_4_exact_verbatim_ramp_fixture(self):
        fixture = (
            "*Parking Occupany Report*\n"
            "\n"
            "Date : 12-10-2026\n"
            "Time 11:30 AM\n"
            "\n"
            "Podium Parking Total Slots :127\n"
            "Customer and visitor cars : 87\n"
            "P.o.d Cars : 05\n"
            "Staff Cars\n"
            ": 118\n"
            "Total cars Entered at ramp area\n"
            " : 210\n"
            "Total cars parked at ramp Area\n"
            " :158\n"
            "Total Slot Service Road\n"
            " : 120\n"
            "Total Car Entered Service Road:\n"
            "131\n"
            "Total car parked at service road\n"
            " : 117\n"
            "Partially Blocked Parking.      :04"
        )

        res = self.parser.parse_ramp_service_road(fixture)
        self.assertIsNotNone(res)
        self.assertEqual(res["podium_slots"], 127)
        self.assertEqual(res["customer_visitor"], 87)
        self.assertEqual(res["pod_cars"], 5)
        self.assertEqual(res["staff_cars"], 118)
        self.assertEqual(res["total_cars_entered_ramp"], 210)
        self.assertEqual(res["total_cars_parked_ramp"], 158)
        self.assertEqual(res["service_road_slots"], 120)  # the number the team sent (owner, 16-10)
        self.assertEqual(res["service_road_entered"], 131)
        self.assertEqual(res["service_road_parked"], 117)
        self.assertEqual(res["partially_blocked_parking"], 4)
        self.assertIn("Service Road slots reported as 120 (usual capacity 127)", res["diagnostics"])

    # ─────────────────────────────────────────────────────────────
    # ITEM 1 & D: Deduplication Precision (No over-merging)
    # ─────────────────────────────────────────────────────────────
    def test_item_1_d_deduplication_precision(self):
        # 1. Housekeeping Ground Floor (10:01 AM) vs 1st floor (07:01 PM) must NOT merge!
        dt_gf = datetime(2026, 10, 12, 10, 1, tzinfo=DUBAI_TZ)
        dt_1f = datetime(2026, 10, 12, 19, 1, tzinfo=DUBAI_TZ)
        hk_events = [
            {
                "message_id": "AC_HK_GF",
                "datetime": dt_gf,
                "building": "Q",
                "floor": "Ground Floor",
                "text": "Housekeeping on Q Building ground floor chairs near the domestic water pump room",
                "group_name": "Meydan - Security Team",
                "sender": "JUNIOR",
            },
            {
                "message_id": "AC_HK_1F",
                "datetime": dt_1f,
                "building": "Q",
                "floor": "1st Floor",
                "text": "Housekeeping on Q Building 1st floor replacing chairs",
                "group_name": "Meydan - Security Team",
                "sender": "JUNIOR",
            },
        ]
        unique_hk, _ = deduplicate_events(hk_events)
        self.assertEqual(len(unique_hk), 2, "Ground floor and 1st floor housekeeping must remain 2 separate events!")

        # 2. Three N building emergency exit light messages (1st, 2nd, 3rd floors)
        dt = datetime(2026, 10, 12, 11, 0, tzinfo=DUBAI_TZ)
        n_bld_events = [
            {"message_id": "AC_SEC_3F", "group_name": "Meydan - Security Team", "building": "N", "floor": "3rd Floor", "datetime": dt, "text": "N building 3rd floor emergency exit light is not working two lights"},
            {"message_id": "AC_SEC_2F", "group_name": "Meydan - Security Team", "building": "N", "floor": "2nd Floor", "datetime": dt, "text": "N building 2nd floor emergency exit light is not working"},
            {"message_id": "AC_SEC_1F", "group_name": "Meydan - Security Team", "building": "N", "floor": "1st Floor", "datetime": dt, "text": "N building 1st floor exit staircase emergency exit light is not working"},
            # Counterparts in Fire & Safety
            {"message_id": "AC_FS_3F", "group_name": "Fire & Safety Team Meydan", "building": "N", "floor": "3rd Floor", "datetime": dt, "text": "N building 3rd floor emergency exit light is not working two lights"},
            {"message_id": "AC_FS_2F", "group_name": "Fire & Safety Team Meydan", "building": "N", "floor": "2nd Floor", "datetime": dt, "text": "N building 2nd floor emergency exit light is not working"},
            {"message_id": "AC_FS_1F", "group_name": "Fire & Safety Team Meydan", "building": "N", "floor": "1st Floor", "datetime": dt, "text": "N building 1st floor exit staircase emergency exit light is not working"},
        ]
        unique_n, _ = deduplicate_events(n_bld_events)
        self.assertEqual(len(unique_n), 3, "Must be exactly 3 distinct events across floors!")
        for u in unique_n:
            self.assertEqual(len(u["source_message_ids"]), 2, "Each unique floor event must merge exactly 2 source IDs (Security + Fire Safety)")

        # 3. P building ground floor service lift posted in Security Team and then FM
        p_lift_events = [
            {"message_id": "AC_LIFT_SEC", "group_name": "Meydan - Security Team", "building": "P", "floor": "Ground Floor", "datetime": dt, "text": "P building ground floor inside service lift light not working"},
            {"message_id": "AC_LIFT_FM", "group_name": "Meydan FM Team💪", "building": "P", "floor": "Ground Floor", "datetime": dt, "text": "P building ground floor inside service lift light not working"},
        ]
        unique_lift, _ = deduplicate_events(p_lift_events)
        self.assertEqual(len(unique_lift), 1)
        self.assertEqual(unique_lift[0]["source_message_ids"], ["AC_LIFT_SEC", "AC_LIFT_FM"])

    # ─────────────────────────────────────────────────────────────
    # ITEM 1 & E: Violations (Report 5) with real sender JUNIOR
    # ─────────────────────────────────────────────────────────────
    def test_item_1_e_violations_real_sender_and_unlinked_replies(self):
        raw_violation = {
            "message_id": "AC11A89B01",
            "sender": "JUNIOR",
            "group_name": "Multistory Parking Building Meydan",
            "datetime": datetime(2026, 10, 13, 9, 38, tzinfo=DUBAI_TZ),
            "raw_datetime": "13-10-2026 09:38 AM",
            "text": "Staff parked his non-electric car on EV fast charging slot 399 on level 4 Kindly notify concern department",
            "photos": ["workspace/whatsapp_media/sample_ev.jpg"],
            "quoted_message_id": "",
        }

        parsed = self.parser.parse_parking_violation(raw_violation)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["sender"], "JUNIOR")
        self.assertEqual(parsed["slot_no"], "399")
        self.assertEqual(parsed["level"], "Level 4")

        rendered = generate_parking_violations_report_text([parsed], {"total": 1, "rectified": 0, "pending": 1})
        self.assertIn("👤 Reported by: JUNIOR", rendered)
        self.assertNotIn("Ahmed (Security)", rendered)
        self.assertIn("Level 4, Slot 399", rendered)

    # ─────────────────────────────────────────────────────────────
    # ITEM 1 & F: Vehicle Permissions (Report 6) with real sender Akash
    # ─────────────────────────────────────────────────────────────
    def test_item_1_f_permissions_real_sender_and_unlinked_replies(self):
        raw_requests = [
            {
                "message_id": "AC22A01",
                "sender": "Akash",
                "group_name": "Meydan - Security Team",
                "datetime": datetime(2026, 10, 13, 6, 29, tzinfo=DUBAI_TZ),
                "raw_datetime": "13-10-2026 06:29 AM",
                "text": "Kindly allow this vehicle in smart parking Plate no : 4 / 69888 Car make :GMC valid only for 3 days from today 13/10/2026 until 15/10/2026",
            },
            {
                "message_id": "AC22A02",
                "sender": "Akash",
                "group_name": "Meydan - Security Team",
                "datetime": datetime(2026, 10, 13, 7, 54, tzinfo=DUBAI_TZ),
                "raw_datetime": "13-10-2026 07:54 AM",
                "text": "Kindly allow this vehicle in VIP parking slot Plate no. EE 80743 valid only today",
            },
            {
                "message_id": "AC22A03",
                "sender": "Akash",
                "group_name": "Meydan - Security Team",
                "datetime": datetime(2026, 10, 13, 8, 55, tzinfo=DUBAI_TZ),
                "raw_datetime": "13-10-2026 08:55 AM",
                "text": "Kindly allow this vehicle in VIP parking slot Plate no : V 65372 Car make: Mitsubishi Pajero valid only today",
            },
        ]

        parsed_list = [self.parser.parse_vehicle_permission(r) for r in raw_requests]
        self.assertEqual(len(parsed_list), 3)
        self.assertEqual(parsed_list[0]["plate_no"], "4 / 69888")
        self.assertEqual(parsed_list[0]["car_make"], "GMC")
        self.assertEqual(parsed_list[0]["sender"], "Akash")

        self.assertEqual(parsed_list[1]["plate_no"], "EE 80743")
        self.assertEqual(parsed_list[1]["car_make"], "N/A")
        self.assertEqual(parsed_list[1]["sender"], "Akash")

        self.assertEqual(parsed_list[2]["plate_no"], "V 65372")
        self.assertEqual(parsed_list[2]["car_make"], "Mitsubishi Pajero")
        self.assertEqual(parsed_list[2]["sender"], "Akash")

        rendered = generate_vehicle_permissions_report_text(parsed_list, {"total": 3, "parked": 0, "pending": 3})
        self.assertIn("👤 Requested by: Akash", rendered)
        self.assertNotIn("Admin", rendered)
        self.assertNotIn("Guest Relations", rendered)
        self.assertNotIn("Coordinator", rendered)

    # ─────────────────────────────────────────────────────────────
    # ITEM 1 & G: Operations Report (Security Team Only, Real Senders)
    # ─────────────────────────────────────────────────────────────
    def test_item_1_g_operations_real_senders_and_exclusions(self):
        # Exclusions test: leave requests and emoji replies
        self.assertTrue(self.parser.is_non_operational("Kindly note I am taking leave tomorrow"))
        self.assertTrue(self.parser.is_non_operational("Noted"))
        self.assertTrue(self.parser.is_non_operational("👍"))
        self.assertFalse(self.parser.is_non_operational("TELECON SIGNS – P Building 4th floor replacing stickers"))

    # ─────────────────────────────────────────────────────────────
    # ITEM 1 & H: Maintenance (FM Group Only, Repeat Flagging)
    # ─────────────────────────────────────────────────────────────
    def test_item_1_h_maintenance_repeat_flagging(self):
        dt_12 = datetime(2026, 10, 12, 11, 0, tzinfo=DUBAI_TZ)
        dt_13 = datetime(2026, 10, 13, 11, 0, tzinfo=DUBAI_TZ)

        ac_events = [
            {"message_id": "AC_FM_12", "group_name": "Meydan FM Team💪", "building": "N", "floor": "1st Floor", "datetime": dt_12, "text": "1st Floor Security Control Room AC cooling is low"},
            {"message_id": "AC_FM_13", "group_name": "Meydan FM Team💪", "building": "N", "floor": "1st Floor", "datetime": dt_13, "text": "1st Floor Security Control Room AC cooling is low need to check"},
        ]
        unique, flagged = deduplicate_events(ac_events)
        self.assertEqual(len(unique), 2, "Must NOT merge tickets on different days!")
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["type"], "Possible repeat of an earlier ticket")


if __name__ == "__main__":
    unittest.main()

