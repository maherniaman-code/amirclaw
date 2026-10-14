"""SQLite persistence for manual classification corrections without AI."""

import sqlite3
from pathlib import Path
from typing import Optional, Dict, List, Any
from .config_loader import BASE_DIR, load_env_file

load_env_file()
import os
DEFAULT_DB_PATH = os.environ.get("REPORTS_DB_PATH") or str(BASE_DIR / "workspace" / "reports_corrections.db")


from contextlib import contextmanager

@contextmanager
def get_db_connection(custom_path: Optional[str] = None):
    target_path = custom_path or os.environ.get("REPORTS_DB_PATH") or DEFAULT_DB_PATH
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_corrections_table() -> None:
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                group_name TEXT,
                keyword_trigger TEXT,
                target_report TEXT NOT NULL,
                target_building TEXT,
                target_status TEXT,
                corrected_by TEXT,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_corr_msg_id ON manual_corrections(message_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_corr_group ON manual_corrections(group_name);")
        conn.commit()


# Initialize table once at module import
try:
    init_corrections_table()
except Exception:
    pass


def save_correction(
    message_id: str,
    target_report: str,
    group_name: Optional[str] = None,
    keyword_trigger: Optional[str] = None,
    target_building: Optional[str] = None,
    target_status: Optional[str] = None,
    corrected_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    init_corrections_table()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO manual_corrections 
            (message_id, group_name, keyword_trigger, target_report, target_building, target_status, corrected_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                group_name,
                keyword_trigger,
                target_report,
                target_building,
                target_status,
                corrected_by,
                notes,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def list_corrections(limit: int = 50) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM manual_corrections ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_correction_for_message(message_id: str, group_name: str = "", text: str = "") -> Optional[Dict[str, Any]]:
    """Checks if there is an exact message correction or a group/keyword specific rule."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 1. Exact message ID match
        cursor.execute("SELECT * FROM manual_corrections WHERE message_id = ? ORDER BY id DESC LIMIT 1", (message_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)

        # 2. Group + keyword match if specified
        if group_name and text:
            cursor.execute(
                "SELECT * FROM manual_corrections WHERE group_name = ? AND keyword_trigger IS NOT NULL AND keyword_trigger != ''",
                (group_name,),
            )
            for r in cursor.fetchall():
                kw = r["keyword_trigger"].lower()
                if kw in text.lower():
                    return dict(r)

    return None


def undo_correction(correction_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM manual_corrections WHERE id = ?", (correction_id,))
        conn.commit()
        return cursor.rowcount > 0
