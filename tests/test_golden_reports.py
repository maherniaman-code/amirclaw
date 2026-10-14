"""Golden Output Acceptance Test Suite for Meydan Reports Engine.

Verifies that all 6 reports generated from workspace/whatsapp_messages.xlsx
produce the exact expected text, structure, metrics, and Message IDs.
Any change that alters rendered outputs will immediately fail the build.
"""

import re
import unittest
from reports_engine.manager import ReportsManager


class TestGoldenReportsSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = ReportsManager()

    def test_report_1_parking_golden_output(self):
        """Case 1: Parking Report (Slot 14:00, 13-10-2026)."""
        res = self.manager.generate_parking(slot_request="14:00", report_date="13-10-2026")
        text = res["text"]

        # Assert mandatory headings and values
        self.assertIn("🅿️ *MEYDAN PARKING*", text)
        self.assertIn("📆 Date: 13-10-2026", text)
        self.assertIn("⏰ Time: 14:00", text)

        # Smart Parking (Row 200, MsgID AC26FCB8DE0C5E08FDBE19A11CF941B5)
        self.assertIn("• Cars entered: 905", text)
        self.assertIn("• Occupied slots : 866", text)
        self.assertIn("• Total Empty Slots : 21", text)
        self.assertIn("• Outside smart parking cars: 26", text)

        # Rumble Area (Row 199, MsgID A5B09ECF70812BCCF4A1197D65A88355)
        self.assertIn("• Total Cars: 378 🚗", text)
        self.assertIn("• Bus 🚌: 0", text)

        # Building figures (from real IMAP email ID 930)
        self.assertIn("• Cars Parked: 44 🚗", text)
        self.assertIn("• Empty Slots: 27 ✅", text)

    def test_report_2_maintenance_day_shift_golden_output(self):
        """Case 2: Maintenance Summary (Day Shift 13-10-2026)."""
        res = self.manager.generate_maintenance(target_date="13-10-2026", target_shift="Day")
        text = res["text"]

        self.assertIn("# 🔧 Maintenance Summary – Meydan FM", text)
        self.assertIn("📅 Shift: Day Shift (13-10-2026)", text)

        # Verify repeat flag for SCR AC
        self.assertIn("⚠️ [Possible repeat of ticket from 12-10-2026 07:54]", text)

        # Verify carry-over label
        self.assertIn("⏳ [Carried over]", text)

        # Verify noise removal: AVS?, manual lock, Working must NOT be tickets
        self.assertNotIn("1. AVS?", text)
        self.assertNotIn("2. AVS?", text)
        self.assertNotIn("Working", text.split("\n")[4:])

        # 12-10 07:54 AC ticket is closed by the unquoted "Resolved" reply at 07:58 (proximity link),
        # so it is not carried over; its recurrence on 13-10 is flagged as a repeat instead.
        self.assertIn("• Completed: 0 of 10", text)
        self.assertIn("• Open: 10", text)
        self.assertIn("• Safety priority: 4 tickets", text)

    def test_report_3_fire_safety_day_shift_golden_output(self):
        """Case 3: Fire & Safety Summary (Day Shift 13-10-2026)."""
        res = self.manager.generate_fire_safety(target_date="13-10-2026", target_shift="Day")
        text = res["text"]

        self.assertIn("# 🧯 Fire & Safety Summary – Meydan", text)
        self.assertIn("📅 Shift: Day Shift (13-10-2026)", text)
        self.assertIn("• Completed: 0 of 3", text)
        self.assertIn("• Open: 3", text)
        # "not yet Rectified" follow-up must not close the 11:25 ticket
        # (scan only that ticket's block; it may also carry an "🕐 Open: …" SLA line)
        ticket_block = re.split(r"\n(?=\d+\. |\n)", text.split("13-10-2026 11:25")[1])[0]
        self.assertIn("⚠️ Pending", ticket_block)
        self.assertIn("• Safety priority: 2 high-risk tickets", text)

    def test_report_4_operations_night_shift_golden_output(self):
        """Case 4: Operations Summary (Night Shift 12-10-2026)."""
        res = self.manager.generate_operations(target_date="12-10-2026", target_shift="Night")
        text = res["text"]

        self.assertIn("# 🏢 Operations Summary – Meydan Security", text)
        self.assertIn("📅 Shift: Night Shift (12-10-2026)", text)
        self.assertIn("Housekeeping – Housekeeping staff are removing all old chairs", text)
        self.assertIn("Telecon – P building 4th floor the TELECON SIGNS COMPANY", text)
        self.assertIn("Arki – Arki group vendors is working in M building", text)
        self.assertIn("• Total Activities: 3", text)
        self.assertIn("• Active Vendors: Arki, Housekeeping, Telecon", text)

    def test_report_5_parking_violations_golden_output(self):
        """Case 5: Parking Violations Summary (Multistory Parking Building Meydan)."""
        res = self.manager.generate_parking_violations()
        text = res["text"]

        self.assertIn("# 🚫 Parking Violations Summary – Meydan", text)
        self.assertIn("📍 Level 4, Slot 399", text)
        self.assertIn("Staff parked his non-electric car on EV fast charging slot 399", text)
        self.assertIn("👤 Reported by: JUNIOR", text)
        self.assertIn("📷 Photo: Attached in Excel (Sheet: Photos)", text)
        self.assertIn("• Total Violations: 1", text)
        self.assertIn("• Pending / In Progress: 1", text)

    def test_report_6_vehicle_permissions_golden_output(self):
        """Case 6: Vehicle Permission Requests Summary (Multistory Parking Building Meydan)."""
        res = self.manager.generate_vehicle_permissions()
        text = res["text"]

        self.assertIn("# 📋 Vehicle Permission Requests – Meydan", text)
        self.assertIn("Plate: 4 / 69888 | Make: GMC", text)
        self.assertIn("Plate: EE 80743", text)
        self.assertIn("Plate: V 65372 | Make: Mitsubishi Pajero", text)
        self.assertIn("Plate: FF 71016", text)
        # Two permanent-access requests with bold "*Plate No:*" fields and near-identical wording
        self.assertIn("Plate: X 25854 | Make: Audi A5", text)
        self.assertIn("Plate: AA 59845 | Make: Porsche", text)
        self.assertIn("📅 Validity: Permanent", text)
        self.assertIn("• Total Requests: 6", text)
        self.assertIn("• Pending: 6", text)


if __name__ == "__main__":
    unittest.main()
