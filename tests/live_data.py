"""Tests that read the live WhatsApp workbook (workspace/, not in git) skip where it is absent, e.g. CI.
The anonymized fixture tests (test_fixture_reports.py) cover the same reports everywhere."""
import unittest
from pathlib import Path

LIVE_WORKBOOK = Path(__file__).resolve().parent.parent / "workspace" / "whatsapp_messages.xlsx"
requires_live_data = unittest.skipUnless(LIVE_WORKBOOK.exists(), "live workspace/whatsapp_messages.xlsx not present")
