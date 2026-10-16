"""Parking report built from WhatsApp alone (no building-figures email): owner, 16-10."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from reports_engine import excel_exporter, messages_db
from reports_engine.date_utils import DUBAI_TZ

BLD = """🅿️ *MEYDAN PARKING*
📆 Date: 16-10-2026
⏰ Time: 10:00
🏢 *P BUILDING*
• Total Slots: 71
• Cars Parked: 29 🚗
• Empty Slots: 42 ✅
🏢 *N BUILDING*
• Total Slots: 77
• Cars Parked: 35 🚗
• Empty Slots: 42 ✅
🏢 *M BUILDING*
• Total Slots: 165
• Cars Parked: 56 🚗
• Empty Slots: 109 ✅
🏢 *Q BUILDING*
• Total Slots: 146
• Cars Parked: 46 🚗
• Empty Slots: 100 ✅
📊 *TOTAL SUMMARY* = 459
• Total Cars Entered: 326 ⬇️
• Total Cars Parked: 166
• Total Empty Slots: 293"""
RUM = "Date 16-10-2026\nRumble area location\nTime 9:35am\nTotal cars parking at Rumble\nnow are 168cars\nBus 🚌 00car"
RAMP = """*Parking Occupany Report*

Date:    16-10-2026
Time:  9:30.  AM

Podium Parking Total Slots
:127
Customer and visitor cars
: 35
P.o.d Cars
: 02
Staff Cars
: 92
Total cars Entered at ramp area
: 129
Total cars parked at ramp Area
:110
Total Slot Service Road
:120
Total cars entered service road
:129
Total cars parked at  service road
: 115
Partially Blocked Parking.      :04"""
SMART = "Smart parking\n\n@⁨Sv Mohan⁩\n\nCars entered as of 10 o'clock = 556\n\nCars parked as of 10 o'clock = 551\n\nCars parked outside = 07"


class TestParkingFromWhatsApp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        for p in (mock.patch.dict(os.environ, {"REPORTS_SOURCE": "sqlite", "REPORTS_NOW": "2026-10-16T10:20:00+04:00",
                                               "IMAP_HOST": "", "REPORTS_DB_PATH": str(self.tmp / "c.db"),
                                               "WAREHOUSE_DB_PATH": str(self.tmp / "w.db")}),
                  mock.patch.object(excel_exporter, "OUTPUT_DIR", self.tmp)):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def report(self, msgs):
        with sqlite3.connect(self.tmp / "whatsapp_messages.db") as c:
            messages_db.ensure_schema(c)
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('excel_backfill_done', '1')")
            for mid, g, t, (h, m) in msgs:
                dt = datetime(2026, 10, 16, h, m, tzinfo=DUBAI_TZ)
                c.execute(f"INSERT INTO messages ({messages_db.COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (mid, dt.strftime("%m/%d/%Y, %I:%M:%S %p"), g, "Tester (1)", t, "💬 Text", "", None, None,
                           int(dt.timestamp() * 1000)))
        from reports_engine.manager import ReportsManager
        return ReportsManager(self.tmp / "whatsapp_messages.xlsx").generate_parking("10:00", "16-10-2026")

    def test_every_section_filled_from_whatsapp(self):
        r = self.report([("B1", "Parking Team at Meydan", BLD, (10, 2)), ("R1", "Parking Team at Meydan", RUM, (9, 36)),
                         ("R2", "Parking Team at Meydan", RAMP, (9, 31)),
                         ("S1", "Multistory Parking Building Meydan", SMART, (10, 1))])
        text = r["text"]
        # Owner's template (16-10): numbers as typed (02, 07, 04), dashes on the first two Ramp lines,
        # the Service Road slots the team sent (120), and the ramp total worked out (35 + 02 + 92)
        for line in ("• Cars Parked: 29 🚗", "• Empty Slots: 100 ✅", "• Total Cars Entered: 326 ⬇️", "• Total Cars Parked: 166",
                     "• Cars entered: 556", "• Outside smart parking cars: 07", "- Total Slots Designated : 127",
                     "- Customer and Visitors: 35", "• P.O.D: 02", "• Staff: 92", "• Total car Entered : 129",
                     "• Total Slots : 120", "• Total Occupied Slots : 115", "• Partially Blocked Parking : 04",
                     "• Total Cars: 168 🚗", "• Bus 🚌: 0"):
            self.assertIn(line, text)
        self.assertNotIn("N/A", text)
        self.assertFalse(any("email missing" in d for d in r["diagnostics"]))
        self.assertEqual(len(r["diagnostics"]), len(set(r["diagnostics"])))   # no repeated warnings

    def test_ramp_post_sent_late_counts_by_its_written_time(self):
        # Written "Time: 9:30 AM" but sent at 10:17 (owner, 16-10): it belongs to the 10:00 report
        r = self.report([("R2", "Oyp", RAMP, (10, 17))])
        self.assertIn("- Customer and Visitors: 35", r["text"])
        self.assertIn("• Total Occupied Slots : 115", r["text"])

    def test_ramp_post_written_for_a_later_slot_is_not_used(self):
        r = self.report([("R3", "Oyp", RAMP.replace("9:30.  AM", "11:30 AM"), (11, 31))])
        self.assertIn("- Customer and Visitors: N/A", r["text"])

    def test_other_slot_figures_are_not_used(self):
        r = self.report([("B2", "Parking Team at Meydan", BLD.replace("Time: 10:00", "Time: 14:00"), (14, 1))])
        self.assertIn("• Cars Parked: N/A 🚗", r["text"])


if __name__ == "__main__":
    unittest.main()
