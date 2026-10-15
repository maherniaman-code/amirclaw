"""End-to-end tests of the CLI exactly as the bot calls it (src/reports/bridge.ts), on the
anonymized fixture: every report type, the Telegram correction flow, error output, and the
maintenance command-line tools.
"""

import contextlib
import functools
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reports_engine import cli, tickets_db
from reports_engine import manager as manager_mod

from test_fixture_reports import build_fixture_db

ALL_REPORTS = ["parking", "maintenance", "fire_safety", "operations", "violations", "permissions", "cleaning",
               "data_quality", "owner_kpis", "morning_digest", "overdue", "dq_daily", "dashboard", "list_corrections"]


class CliTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.workbook = build_fixture_db(tmp)
        cls._patches = [
            mock.patch.dict("os.environ", {
                "REPORTS_SOURCE": "auto",
                "REPORTS_NOW": "2026-10-15T12:00:00+04:00",
                "REPORTS_DB_PATH": str(tmp / "corrections.db"),
                "WAREHOUSE_DB_PATH": str(tmp / "warehouse.db"),
            }),
            mock.patch.object(manager_mod, "enrich_ticket",
                              functools.partial(tickets_db.enrich_ticket, db_path=str(tmp / "tickets.db"))),
            mock.patch("reports_engine.excel_exporter.OUTPUT_DIR", tmp),
            mock.patch("reports_engine.dashboard.OUTPUT_DIR", tmp),
            # Parking reads building figures from an IMAP mailbox: never touch the network in tests
            mock.patch.object(manager_mod, "fetch_parking_email_from_imap",
                              return_value=(None, "email missing or unavailable")),
        ]
        for p in cls._patches:
            p.start()
        tickets_db.init_tickets_table(str(tmp / "tickets.db"))

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls._patches):
            p.stop()
        cls._tmp.cleanup()

    def run_cli(self, *args):
        """Run cli.main() like `python -m reports_engine.cli ...`; returns (exit code, parsed JSON, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        argv = ["cli", "--excel", str(self.workbook), *args]
        code = 0
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as e:
                code = e.code or 0
        # The bridge takes the last JSON line of stdout, else of stderr
        lines = [l for l in (out.getvalue() + "\n" + err.getvalue()).splitlines() if l.strip().startswith("{")]
        return code, (json.loads(lines[-1]) if lines else None), err.getvalue()


class TestCliReports(CliTestCase):
    def test_every_report_returns_bridge_readable_json(self):
        for report in ALL_REPORTS:
            with self.subTest(report=report):
                args = ["--report", report]
                if report in ("maintenance", "fire_safety", "operations"):
                    args += ["--date", "13-10-2026"]
                if report == "dq_daily":
                    args += ["--date", "13-10-2026"]
                code, res, err = self.run_cli(*args)
                self.assertEqual(code, 0, err[-500:])
                self.assertIsNotNone(res, "no JSON on stdout")
                self.assertNotIn("error", res)
                if report == "list_corrections":
                    self.assertIn("corrections", res)
                else:
                    self.assertIsInstance(res.get("text"), str)
                    self.assertTrue(res["text"].strip())

    def test_shift_and_period_arguments(self):
        _, day, _ = self.run_cli("--report", "maintenance", "--date", "13-10-2026", "--shift", "Day")
        _, full, _ = self.run_cli("--report", "maintenance", "--date", "13-10-2026")
        self.assertIn("Day Shift (13-10-2026)", day["text"])
        self.assertIn("Full Day (13-10-2026)", full["text"])
        _, week, _ = self.run_cli("--report", "violations", "--date", "week")
        self.assertIn("Last 7 Days", week["text"])

    def test_items_numbering_matches_report_text(self):
        _, res, _ = self.run_cli("--report", "maintenance", "--date", "13-10-2026", "--shift", "Day")
        numbers = [i["no"] for i in res["items"]]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        for n in numbers:
            self.assertIn(f"\n{n}. ", res["text"])

    def test_bad_input_returns_error_json_not_a_crash(self):
        code, res, _ = self.run_cli("--report", "maintenance", "--date", "31-02-2026")
        self.assertEqual(code, 1)
        self.assertTrue(res["error"])
        self.assertIn("message", res)


class TestCliCorrectionFlow(CliTestCase):
    """What the Telegram buttons do: save a correction with the CLI, then request the report again."""

    def save(self, msg_id, target_report, status=None):
        args = ["--report", "list_corrections", "--msg-id", msg_id, "--target-report", target_report,
                "--corrected-by", "owner", "--notes", "telegram"]
        if status:
            args += ["--target-status", status]
        code, res, err = self.run_cli(*args)
        self.assertEqual(code, 0, err)
        self.assertTrue(res["correction_saved"])
        return res

    def item(self, res, msg_id):
        return next((i for i in res["items"] if i["id"] == msg_id), None)

    def test_done_correction_shows_in_next_report(self):
        _, before, _ = self.run_cli("--report", "maintenance", "--date", "13-10-2026", "--shift", "Day")
        target = next(i for i in before["items"] if i["status"] == "Pending")
        # carried-over tickets leave the report once fixed; use one reported in this shift
        manager = manager_mod.ReportsManager(self.workbook)
        with contextlib.redirect_stdout(io.StringIO()):
            manager.generate_maintenance(target_date="13-10-2026", target_shift="Day")
        in_shift = next(t for t in manager.last_items["maintenance"]
                        if not t.get("is_carry_over") and t["status"] == "Pending")
        self.save(in_shift["message_id"], "maintenance", "Done")
        _, after, _ = self.run_cli("--report", "maintenance", "--date", "13-10-2026", "--shift", "Day")
        self.assertEqual(self.item(after, in_shift["message_id"])["status"], "Done")
        self.assertIn("(manual)", after["text"])
        self.assertEqual(after["completed"], before["completed"] + 1)
        self.assertIsNotNone(target)

    def test_not_a_ticket_removes_it(self):
        _, before, _ = self.run_cli("--report", "fire_safety", "--date", "13-10-2026", "--shift", "Day")
        victim = before["items"][-1]["id"]
        self.save(victim, "ignore")
        _, after, _ = self.run_cli("--report", "fire_safety", "--date", "13-10-2026", "--shift", "Day")
        self.assertIsNone(self.item(after, victim))
        self.assertEqual(after["total"], before["total"] - 1)

    def test_operations_completed_and_reopen(self):
        _, before, _ = self.run_cli("--report", "operations", "--date", "12-10-2026", "--shift", "Night")
        act = before["items"][0]["id"]
        self.save(act, "operations", "Completed")
        _, done, _ = self.run_cli("--report", "operations", "--date", "12-10-2026", "--shift", "Night")
        self.assertEqual(self.item(done, act)["status"], "Completed")
        self.assertIn("Completed (manual)", done["text"])
        self.save(act, "operations", "Reported")  # ↩️ still open: the latest correction wins
        _, reopened, _ = self.run_cli("--report", "operations", "--date", "12-10-2026", "--shift", "Night")
        self.assertEqual(self.item(reopened, act)["status"], "Reported")

    def test_list_corrections(self):
        self.save("SOME_MESSAGE_ID", "maintenance", "Done")
        _, res, _ = self.run_cli("--report", "list_corrections")
        self.assertIn("SOME_MESSAGE_ID", [c["message_id"] for c in res["corrections"]])


class TestCommandLineTools(CliTestCase):
    def call(self, module, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", [module.__name__, *args]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = module.main()
        return code, out.getvalue()

    def test_messages_db_tools(self):
        from reports_engine import messages_db
        db = messages_db.db_path_for(self.workbook)
        code, out = self.call(messages_db, "--excel", str(self.workbook), "--fill-ts")
        self.assertEqual(code, 0)
        self.assertIn("Filled exact time for 0", out)
        target = Path(self._tmp.name) / "export.xlsx"
        code, out = self.call(messages_db, "--excel", str(self.workbook), "--export-excel", str(target))
        self.assertEqual((code, json.loads(out)["exported"] > 0, target.exists()), (0, True, True))
        code, _ = self.call(messages_db, "--excel", str(Path(self._tmp.name) / "missing.xlsx"))
        self.assertEqual(code, 1)  # no workbook to import
        self.assertTrue(messages_db.is_ready(db))

    def test_warehouse_tools(self):
        from reports_engine import warehouse
        with contextlib.redirect_stdout(io.StringIO()):
            manager_mod.ReportsManager(self.workbook).generate_maintenance(target_date="13-10-2026", target_shift="Day")
        code, out = self.call(warehouse, "--summary")
        self.assertEqual(code, 0)
        self.assertIn("maintenance", out)
        msg = out and warehouse.summary()
        self.assertTrue(msg)
        code, _ = self.call(warehouse, "--explain", "NO_SUCH_MESSAGE")
        self.assertEqual(code, 1)

    def test_evaluate_tool(self):
        from reports_engine import evaluate
        snap = Path(self._tmp.name) / "snap.json"
        code, out = self.call(evaluate, "--excel", str(self.workbook), "--save", str(snap))
        self.assertEqual(code, 0)
        self.assertTrue(snap.exists())
        code, out = self.call(evaluate, "--excel", str(self.workbook), "--diff", str(snap))
        self.assertEqual(code, 0)
        self.assertIn("0 message(s) changed decision", out)

    def test_master_data_suggest(self):
        from reports_engine import master_data
        staff = master_data.suggest(self.workbook)
        self.assertTrue(staff)
        self.assertTrue(all(set(v) == {"name", "team"} for v in staff.values()))
        out_file = Path(self._tmp.name) / "suggested.yaml"
        with mock.patch.object(master_data, "SUGGESTED_FILE", out_file), \
                mock.patch.object(master_data, "suggest", return_value=staff):
            code, _ = self.call(master_data, "--suggest")
        self.assertEqual(code, 0)
        self.assertIn("staff:", out_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
