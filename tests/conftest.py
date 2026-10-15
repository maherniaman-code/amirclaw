"""Test-wide isolation, loaded by pytest before any test module imports reports_engine.

- REPORTS_NOW freezes the clock so ticket ages, "last 7 days" windows and carry-over are deterministic.
- Ticket and correction databases point to a throwaway folder, never the live workspace ones.
"""
import atexit
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="amirclaw-tests-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

os.environ.setdefault("REPORTS_NOW", "2026-10-15T12:00:00+04:00")
os.environ["TICKETS_DB_PATH"] = os.path.join(_TMP, "tickets.db")
os.environ.setdefault("REPORTS_DB_PATH", os.path.join(_TMP, "corrections.db"))
os.environ["WAREHOUSE_DB_PATH"] = os.path.join(_TMP, "warehouse.db")
