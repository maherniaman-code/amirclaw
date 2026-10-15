"""Golden Output Acceptance Test Suite for Meydan Reports Engine.

Verifies that all 6 reports generated from workspace/whatsapp_messages.xlsx
produce the exact expected text, structure, metrics, and Message IDs.
Any change that alters rendered outputs will immediately fail the build.
"""

import re
import unittest
from reports_engine.manager import ReportsManager
from live_data import requires_live_data


@requires_live_data
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
        # 12-10 Fire Exit Signage light is closed by "Light replaced" (10:59, shared word "light").
        # 13-10 07:52 AC ticket is closed by Shrikant's "Done" at 08:25 (33 min, reply_link_minutes=60).
        # "Speed Gate is out of service" (10:52) is a ticket now that "out of service" is a fault word.
        self.assertIn("Q building first floor Speed Gate is out of service", text)
        self.assertIn("✅ Done — Shrikant Yadav", text)
        self.assertNotIn("Fire Exit Signage Light", text)
        self.assertIn("P building ground floor inside service lift", text)  # "not working properly" stays a fault
        # The M barrier (12-10 06:12) was fixed on 12-10 09:44 ("AVS ... put it back to normal"),
        # so it is no longer carried over into 13-10
        self.assertIn("• Completed: 1 of 9", text)
        self.assertNotIn("barrier is not opening", text)
        self.assertIn("• Open: 8", text)
        self.assertIn("• Safety priority: 3 tickets", text)

    def test_report_3_fire_safety_day_shift_golden_output(self):
        """Case 3: Fire & Safety Summary (Day Shift 13-10-2026)."""
        res = self.manager.generate_fire_safety(target_date="13-10-2026", target_shift="Day")
        text = res["text"]

        self.assertIn("# 🧯 Fire & Safety Summary – Meydan", text)
        self.assertIn("📅 Shift: Day Shift (13-10-2026)", text)
        # 12-10 19:40 "N building 2nd floor ... emergency exit light" was posted 25s after the 3rd-floor
        # photo by the same guard and used to be swallowed by media grouping; it is its own ticket now.
        self.assertIn("N building 2nd floor near to reception 1 emergency exit light is not working", text)
        self.assertIn("• Completed: 0 of 4", text)
        self.assertIn("• Open: 4", text)
        # "not yet Rectified" follow-up must not close the 11:25 ticket
        # (scan only that ticket's block; it may also carry an "🕐 Open: …" line)
        ticket_block = re.split(r"\n(?=\d+\. |\n)", text.split("13-10-2026 11:25")[1])[0]
        self.assertIn("⚠️ Pending", ticket_block)
        self.assertIn("• Safety priority: 3 high-risk tickets", text)
        # SLA is tracked in the backend only: present in the JSON, never in the report text
        self.assertNotIn("SLA", text)
        self.assertGreater(res["sla_breaches"], 0)

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
        # Embedded WhatsApp photos are picked up for the 📷 line
        self.assertIn("📷 Photo: Attached in Excel (Sheet: Photos)", text)

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
        # Unquoted parking-team replies now update the latest open request:
        # "Parked in VIP slot" (08:39) -> EE 80743, "Parked in the assigned slot." (11:28) -> FF 71016
        self.assertRegex(text, r"EE 80743[\s\S]*?Status: Parked in VIP slot")
        self.assertRegex(text, r"FF 71016[\s\S]*?Status: Parked in assigned slot")
        self.assertIn("• Parked / Confirmed: 2 of 6", text)
        self.assertIn("• Pending: 4", text)


if __name__ == "__main__":
    unittest.main()
