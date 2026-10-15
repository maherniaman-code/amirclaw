"""Reprocessing all recorded messages with the current rules (reports_engine/reprocess.py)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reports_engine import excel_exporter, tickets_db
from reports_engine.reprocess import reprocess

from test_fixture_reports import build_fixture_db


def _ids(db: Path):
    with sqlite3.connect(str(db)) as c:
        return {r[0] for r in c.execute("SELECT message_id FROM tickets")}


class TestReprocess(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.workbook = build_fixture_db(self.tmp)
        self.tickets, self.warehouse = self.tmp / "tickets.db", self.tmp / "reports_warehouse.db"
        self.reports_dir = self.tmp / "reports"
        self.reports_dir.mkdir()
        for p in (mock.patch.dict("os.environ", {"REPORTS_SOURCE": "auto", "REPORTS_NOW": "2026-10-15T12:00:00+04:00",
                                                 "REPORTS_DB_PATH": str(self.tmp / "corrections.db")}),
                  mock.patch.object(excel_exporter, "OUTPUT_DIR", self.reports_dir)):
            p.start()
            self.addCleanup(p.stop)
        # A store written by older rules: one row that is not a ticket any more
        tickets_db.init_tickets_table(str(self.tickets))
        tickets_db.upsert_ticket("OLD_RULES_ONLY", "maintenance", "Meydan FM Team💪", "N", None, "Is it restored?",
                                 "Pending", None, "2026-10-12T07:33:00+04:00", "2026-10-12T07:33:00+04:00", "Day",
                                 db_path=str(self.tickets))

    def tearDown(self):
        self._tmp.cleanup()

    def run_it(self, apply):
        return reprocess(self.workbook, self.tickets, self.warehouse, apply=apply, backup_root=self.tmp / "backups")

    def test_dry_run_reports_changes_and_touches_nothing(self):
        s = self.run_it(apply=False)
        self.assertGreaterEqual(s["days"], 4)
        self.assertIn("OLD_RULES_ONLY", {mid for (mid, _), _ in s["removed"]})
        self.assertTrue(s["added"])
        self.assertEqual(_ids(self.tickets), {"OLD_RULES_ONLY"})
        self.assertFalse(self.warehouse.exists())
        self.assertFalse((self.tmp / ".reprocess_tmp").exists())
        self.assertEqual(list(self.reports_dir.iterdir()), [], "no Excel files in the real reports folder")

    def test_apply_backs_up_and_rebuilds_both_stores(self):
        s = self.run_it(apply=True)
        ids = _ids(self.tickets)
        self.assertNotIn("OLD_RULES_ONLY", ids)
        self.assertEqual(len(ids), len(s["added"]))
        self.assertIn("OLD_RULES_ONLY", _ids(Path(s["backup"]) / "tickets.db"))
        with sqlite3.connect(str(self.warehouse)) as c:
            days = c.execute("SELECT COUNT(DISTINCT day) FROM dq_daily").fetchone()[0]
            events = c.execute("SELECT COUNT(*) FROM classification_events").fetchone()[0]
        self.assertEqual(days, s["days"])
        self.assertGreater(events, 0)
        # Running again with the same rules changes nothing
        again = self.run_it(apply=False)
        self.assertEqual((again["changed"], again["removed"], again["added"]), ([], [], []))


if __name__ == "__main__":
    unittest.main()
