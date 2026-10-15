"""Deterministic golden tests on the anonymized fixture (runs in CI, no live data needed).

Every report is generated from tests/fixtures/whatsapp_messages.anon.json with the clock frozen
(REPORTS_NOW, see conftest.py) and compared byte-for-byte with tests/fixtures/expected/<case>.txt.

After an intended change in report output, regenerate and review the diff:
    UPDATE_FIXTURES=1 python3 -m pytest tests/test_fixture_reports.py
"""

import contextlib
import functools
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reports_engine import messages_db, tickets_db
from reports_engine import manager as manager_mod
from reports_engine.manager import ReportsManager
from reports_engine.owner_digest import generate_morning_digest, generate_overdue
from reports_engine.owner_kpis import generate_owner_kpis

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = FIXTURES / "expected"
UPDATE = os.environ.get("UPDATE_FIXTURES") == "1"


def build_fixture_db(folder: Path) -> Path:
    """SQLite message store (marked ready) from the anonymized JSON; returns the workbook path to use."""
    rows = json.loads((FIXTURES / "whatsapp_messages.anon.json").read_text())
    db = folder / "whatsapp_messages.db"
    with sqlite3.connect(db) as c:
        messages_db.ensure_schema(c)
        c.executemany(
            "INSERT INTO messages (message_id, timestamp_text, group_name, sender, text, media_type, quoted_message_id, ts_utc_ms) "
            "VALUES (:message_id, :timestamp_text, :group_name, :sender, :text, :media_type, :quoted_message_id, :ts_utc_ms)",
            rows,
        )
        c.execute("INSERT INTO meta (key, value) VALUES (?, 'fixture')", (messages_db.BACKFILL_KEY,))
    return folder / "whatsapp_messages.xlsx"  # never created: the parser reads the ready SQLite next to it


CASES = {
    "maintenance_day_13-10": lambda m: m.generate_maintenance(target_date="13-10-2026", target_shift="Day")["text"],
    "fire_safety_day_13-10": lambda m: m.generate_fire_safety(target_date="13-10-2026", target_shift="Day")["text"],
    "operations_night_12-10": lambda m: m.generate_operations(target_date="12-10-2026", target_shift="Night")["text"],
    "operations_fullday_14-10": lambda m: m.generate_operations(target_date="14-10-2026")["text"],
    "violations_all": lambda m: m.generate_parking_violations()["text"],
    "violations_15-10": lambda m: m.generate_parking_violations(target_date="15-10-2026")["text"],
    "permissions_all": lambda m: m.generate_vehicle_permissions()["text"],
    "owner_kpis_week": lambda m: generate_owner_kpis(m)["text"],
    "morning_digest": lambda m: generate_morning_digest(m)["text"],
    "overdue": lambda m: json.dumps(generate_overdue(m)["overdue"], ensure_ascii=False, indent=1),
}


class TestFixtureReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        workbook = build_fixture_db(tmp)
        cls._patches = [
            mock.patch.dict("os.environ", {
                "REPORTS_SOURCE": "auto",
                "REPORTS_NOW": "2026-10-15T12:00:00+04:00",
                "REPORTS_DB_PATH": str(tmp / "corrections.db"),
                "WAREHOUSE_DB_PATH": str(tmp / "warehouse.db"),
            }),
            # Ticket history (carry-over, "9x in 7 days") in a database owned by this test class
            mock.patch.object(manager_mod, "enrich_ticket",
                              functools.partial(tickets_db.enrich_ticket, db_path=str(tmp / "tickets.db"))),
            mock.patch("reports_engine.excel_exporter.OUTPUT_DIR", tmp),
        ]
        for p in cls._patches:
            p.start()
        tickets_db.init_tickets_table(str(tmp / "tickets.db"))
        cls.manager = ReportsManager(workbook)
        with contextlib.redirect_stdout(io.StringIO()):  # warm-up: every ticket known before any assertion
            cls.manager.generate_maintenance(target_date="all")
            cls.manager.generate_fire_safety(target_date="all")

    @classmethod
    def tearDownClass(cls):
        for p in reversed(cls._patches):
            p.stop()
        cls._tmp.cleanup()

    def test_fixture_is_anonymized(self):
        blob = (FIXTURES / "whatsapp_messages.anon.json").read_text().lower()
        self.assertNotIn("akash", blob)
        self.assertNotIn("219378423463939", blob)

    def test_evaluate_snapshot_diff_and_accuracy(self):
        from reports_engine import evaluate
        from reports_engine.corrections_db import save_correction
        with contextlib.redirect_stdout(io.StringIO()):
            snap = evaluate.snapshot(self.manager)
        tickets = [mid for mid, d in snap.items() if "maintenance:open" in d]
        self.assertGreater(len(tickets), 3)
        self.assertEqual(evaluate.diff(snap, snap), [])
        changed = dict(snap, **{tickets[0]: ["maintenance:excluded"]})
        self.assertEqual([c["message_id"] for c in evaluate.diff(snap, changed)], [tickets[0]])

        # Owner labels in a private corrections DB (the other tests must not see them):
        # one the rules already get right (still open), one they missed (should be closed)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict("os.environ", {"REPORTS_DB_PATH": str(Path(tmp) / "labels.db")}):
            save_correction(tickets[1], "maintenance", target_status="Pending", corrected_by="owner")
            save_correction(tickets[2], "maintenance", target_status="Done", corrected_by="owner")
            acc = evaluate.accuracy(snap, evaluate._corrections())
            self.assertEqual((acc["labels"], acc["accuracy"]), (2, 0.5))
            self.assertEqual([m["message_id"] for m in acc["misses"]], [tickets[2]])
            # Snapshots are rules-only: the saved corrections must not leak into them
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(evaluate.snapshot(self.manager)[tickets[2]], snap[tickets[2]])

    def test_warehouse_records_decisions_and_history(self):
        from reports_engine import warehouse
        with contextlib.redirect_stdout(io.StringIO()):
            self.manager.generate_maintenance(target_date="13-10-2026", target_shift="Day")
        done = next(t for t in self.manager.last_items["maintenance"] if t["status"] == "Done" and t.get("closed_at"))
        e = warehouse.explain(done["message_id"])
        self.assertEqual(e["events"][0]["decision"], "ticket")
        self.assertEqual(e["tickets"][0]["status"], "Done")
        self.assertEqual(e["history"][-1]["status"], "Done")
        self.assertEqual(e["history"][-1]["changed_at"], done["closed_at"].isoformat())
        self.assertTrue(e["replies"])  # the closing reply is recorded with its link method
        self.assertTrue(all(r["link_method"] for r in e["replies"]))
        counts = {(r, d): n for r, d, n in warehouse.summary()}
        self.assertGreater(counts[("maintenance", "excluded")], 0)

    def test_daily_dq_metrics_and_anomalies(self):
        from reports_engine import dq_metrics
        with contextlib.redirect_stdout(io.StringIO()):
            days = [dq_metrics.generate_dq_daily(self.manager, d) for d in ("11-10-2026", "12-10-2026", "13-10-2026", "14-10-2026")]
        self.assertEqual([d["metrics"]["messages_total"] > 0 for d in days], [True] * 4)
        # 13-10: 30% of replies unlinked ("Follow up please", "Working") - at, not above, the 30% alert line
        self.assertEqual(days[2]["metrics"]["unlinked_share"], 0.3)
        self.assertEqual(days[2]["anomalies"], [])
        spike = {"replies_quoted": 1, "replies_proximity": 2, "replies_unlinked": 6, "unlinked_share": 0.667}
        self.assertTrue(any("not linked" in a for a in dq_metrics.anomalies(spike, {"unlinked_share": [0.1, 0.2, 0.1]}, {})))
        groups = {"fm": "Meydan FM Team"}
        quiet = dq_metrics.anomalies({"messages:fm": 0}, {"messages:fm": [20, 25, 30]}, groups)
        self.assertTrue(any("silent" in a for a in quiet))
        self.assertEqual(dq_metrics.anomalies({"messages:fm": 0}, {"messages:fm": [20]}, groups), [])  # no baseline yet

    def test_excel_export_round_trip(self):
        from reports_engine.whatsapp_parser import WhatsAppParser
        out = Path(self._tmp.name) / "export" / "whatsapp_messages.xlsx"
        n = messages_db.export_excel(out, messages_db.db_path_for(self.manager.excel_path))
        with mock.patch.dict("os.environ", {"REPORTS_SOURCE": "excel"}):
            exported = {m["message_id"]: m for m in WhatsAppParser(out).load_messages()}
        with mock.patch.dict("os.environ", {"REPORTS_SOURCE": "sqlite"}):
            source = {m["message_id"]: m for m in WhatsAppParser(self.manager.excel_path).load_messages()}
        self.assertEqual(n, len(source))
        fields = ("raw_datetime", "datetime", "group_name", "sender", "text", "media_type", "quoted_message_id")
        self.assertEqual([k for k in source if any(exported[k][f] != source[k][f] for f in fields)], [])

    def test_dashboard_html(self):
        from reports_engine.dashboard import generate_dashboard
        with contextlib.redirect_stdout(io.StringIO()):
            res = generate_dashboard(self.manager)
        doc = Path(res["html_path"]).read_text(encoding="utf-8")
        for part in ("Meydan Dashboard", "Tickets by building", "Average time to close", "Most active vendors", "<svg"):
            self.assertIn(part, doc)
        self.assertNotIn("http", doc.split("<body>")[1])  # self-contained: no external resources
        self.assertNotIn("(1000", doc)  # anonymized fixture ids never printed

    def test_no_arabic_in_any_report(self):
        """Owner requirement: reports are English only (Arabic is only for conversation)."""
        import re as _re
        from reports_engine.dashboard import generate_dashboard
        from reports_engine.data_quality import generate_data_quality
        from reports_engine import dq_metrics
        arabic = _re.compile(r"[\u0600-\u06FF]")
        with contextlib.redirect_stdout(io.StringIO()):
            outputs = {name: build(self.manager) for name, build in CASES.items()}
            outputs["data_quality"] = generate_data_quality(self.manager)["text"]
            outputs["dq_daily"] = dq_metrics.generate_dq_daily(self.manager, "13-10-2026")["text"]
            outputs["dashboard"] = Path(generate_dashboard(self.manager)["html_path"]).read_text(encoding="utf-8")
            outputs["cleaning"] = self.manager.generate_cleaning()["text"]
        for name, text in outputs.items():
            with self.subTest(report=name):
                self.assertIsNone(arabic.search(text), f"Arabic text in {name}: {arabic.findall(text)[:10]}")

    def test_reports_match_expected_output(self):
        EXPECTED.mkdir(exist_ok=True)
        for name, build in CASES.items():
            with self.subTest(case=name):
                with contextlib.redirect_stdout(io.StringIO()):
                    actual = build(self.manager).rstrip() + "\n"
                path = EXPECTED / f"{name}.txt"
                if UPDATE or not path.exists():
                    path.write_text(actual)
                self.assertEqual(path.read_text(), actual, f"{name} changed; if intended, run with UPDATE_FIXTURES=1")


if __name__ == "__main__":
    unittest.main()
