"""Daily data-quality metrics, stored as a time series, with anomaly detection.

Each day (operational day, 05:00 -> 05:00) gets one row per metric in the warehouse table
dq_daily. Anomalies compare the day with the median of the previous 7 stored days.

    python3 -m reports_engine.cli --report dq_daily --date 14-10-2026
"""

import contextlib
import io
import sqlite3
import statistics
from datetime import timedelta
from typing import Any, Dict, List, Optional

from . import warehouse
from .date_utils import now_dubai
from .manager import ReportsManager

SCHEMA = """
CREATE TABLE IF NOT EXISTS dq_daily (
    day     TEXT NOT NULL,   -- YYYY-MM-DD (operational day)
    metric  TEXT NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (day, metric)
);
"""


def compute_day(manager: ReportsManager, date: str) -> Dict[str, float]:
    """date is DD-MM-YYYY. Returns metric -> value."""
    start, end = manager._resolve_date_range(date, "Full")
    msgs = [m for m in manager.parser.load_messages() if m["datetime"] and start <= m["datetime"] < end]
    metrics: Dict[str, float] = {"messages_total": len(msgs)}
    for key, group in manager.config.get("groups", {}).items():
        metrics[f"messages:{key}"] = sum(1 for m in msgs if m["group_name"] == group)
    metrics["unparsed_dates"] = sum(1 for m in manager.parser.load_messages() if m["datetime"] is None)

    with contextlib.redirect_stdout(io.StringIO()):
        maint = manager.generate_maintenance(target_date=date, target_shift="Full")
        fire = manager.generate_fire_safety(target_date=date, target_shift="Full")
    for k in ("replies_quoted", "replies_proximity", "replies_unlinked", "tickets", "tickets_without_building"):
        metrics[k] = maint["link_stats"].get(k, 0) + fire["link_stats"].get(k, 0)
    linked = metrics["replies_quoted"] + metrics["replies_proximity"]
    metrics["unlinked_share"] = round(metrics["replies_unlinked"] / (linked + metrics["replies_unlinked"]), 3) \
        if (linked + metrics["replies_unlinked"]) else 0.0
    metrics["no_building_share"] = round(metrics["tickets_without_building"] / metrics["tickets"], 3) \
        if metrics["tickets"] else 0.0
    return metrics


def _store(day_iso: str, metrics: Dict[str, float]) -> None:
    with sqlite3.connect(str(warehouse.db_path())) as c:
        c.executescript(SCHEMA)
        c.executemany("INSERT OR REPLACE INTO dq_daily (day, metric, value) VALUES (?,?,?)",
                      [(day_iso, k, float(v)) for k, v in metrics.items()])
        c.commit()


def _history(day_iso: str, days: int = 7) -> Dict[str, List[float]]:
    with sqlite3.connect(str(warehouse.db_path())) as c:
        c.executescript(SCHEMA)
        rows = c.execute(
            "SELECT metric, value FROM dq_daily WHERE day < ? AND day >= date(?, ?)",
            (day_iso, day_iso, f"-{days} days"),
        ).fetchall()
    hist: Dict[str, List[float]] = {}
    for metric, value in rows:
        hist.setdefault(metric, []).append(value)
    return hist


def anomalies(metrics: Dict[str, float], history: Dict[str, List[float]], group_names: Dict[str, str]) -> List[str]:
    """Plain-language problems for the owner. Needs at least 3 earlier days for baselines."""
    out = []
    if metrics.get("unparsed_dates", 0) > 0:
        out.append(f"{int(metrics['unparsed_dates'])} رسالة بتاريخ لم يُقرأ")
    for key, name in group_names.items():
        past = history.get(f"messages:{key}", [])
        if len(past) >= 3 and statistics.median(past) >= 5 and metrics.get(f"messages:{key}", 0) == 0:
            out.append(f"جروب «{name}» صامت تماماً (المعتاد حوالي {statistics.median(past):.0f} رسالة يومياً)")
    replies = metrics.get("replies_quoted", 0) + metrics.get("replies_proximity", 0) + metrics.get("replies_unlinked", 0)
    past_share = history.get("unlinked_share", [])
    if replies >= 5 and metrics.get("unlinked_share", 0) > max(0.3, 2 * (statistics.median(past_share) if len(past_share) >= 3 else 0.15)):
        out.append(f"{metrics['unlinked_share']:.0%} من ردود الصيانة/الحريق لم تُربط بتذكرة — ربما تغيّرت طريقة الكتابة")
    if metrics.get("tickets", 0) >= 5 and metrics.get("no_building_share", 0) > 0.3:
        out.append(f"{metrics['no_building_share']:.0%} من التذاكر بدون رقم مبنى")
    return out


def generate_dq_daily(manager: ReportsManager, date: Optional[str] = None) -> Dict[str, Any]:
    """Compute, store and check one operational day (default: yesterday)."""
    date = date or (now_dubai() - timedelta(days=1)).strftime("%d-%m-%Y")
    d, mth, y = date.split("-")
    day_iso = f"{y}-{mth}-{d}"
    metrics = compute_day(manager, date)
    history = _history(day_iso)
    _store(day_iso, metrics)
    problems = anomalies(metrics, history, manager.config.get("groups", {}))
    # Daily housekeeping: HR / leave retention (config privacy.hr_retention_days, null = keep)
    from . import messages_db
    messages_db.purge_hr(
        messages_db.db_path_for(manager.excel_path),
        manager.config.get("privacy", {}).get("hr_retention_days"),
        manager.config.get("exclude", {}).get("leave_markers", []),
    )
    text = (f"🩺 مراقبة جودة البيانات — {date}\n" + "\n".join(f"⚠️ {p}" for p in problems)) if problems else \
        f"✅ جودة البيانات ليوم {date} طبيعية ({int(metrics['messages_total'])} رسالة)"
    return {"text": text, "total": int(metrics["messages_total"]), "anomalies": problems, "metrics": metrics}
