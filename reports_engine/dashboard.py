"""Owner dashboard: one self-contained HTML file (inline SVG, no internet needed), sent on Telegram.

    python3 -m reports_engine.cli --report dashboard
"""

import contextlib
import html
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from .date_utils import DUBAI_TZ, now_dubai
from .excel_exporter import OUTPUT_DIR
from .manager import ReportsManager

DAYS = 30


def _is_done(t: Dict[str, Any]) -> bool:
    return any(w in (t.get("status") or "").lower() for w in ("done", "completed"))


LABEL_W = 150  # label column; charts are drawn left-to-right so mixed Arabic/English labels never clip
BAR_W = 200


def _label(text: str) -> str:
    text = str(text)
    return html.escape(text if len(text) <= 22 else text[:21] + "…")


def _bars(rows: List[Tuple[str, float]], unit: str = "", color: str = "var(--accent)") -> str:
    """Horizontal bar chart as inline SVG: label | bar | value."""
    if not rows:
        return '<p class="empty">لا توجد بيانات</p>'
    peak = max(v for _, v in rows) or 1
    h = 26
    out = [f'<svg viewBox="0 0 400 {len(rows) * h}" class="chart" direction="ltr" role="img">']
    for i, (label, value) in enumerate(rows):
        w = max(2, BAR_W * value / peak)
        y = i * h
        val = f"{value:.1f}" if isinstance(value, float) and not value.is_integer() else f"{int(value)}"
        out.append(f'<text x="4" y="{y + 17}" class="lbl">{_label(label)}</text>')
        out.append(f'<rect x="{LABEL_W}" y="{y + 5}" width="{w:.1f}" height="16" rx="3" fill="{color}"/>')
        out.append(f'<text x="{LABEL_W + w + 6:.1f}" y="{y + 17}" class="val">{val}{html.escape(unit)}</text>')
    out.append("</svg>")
    return "".join(out)


def _pairs(rows: List[Tuple[str, int, int]]) -> str:
    """Opened vs closed per week: two bars per row."""
    if not rows:
        return '<p class="empty">لا توجد بيانات</p>'
    peak = max(max(a, b) for _, a, b in rows) or 1
    h = 34
    out = [f'<svg viewBox="0 0 400 {len(rows) * h}" class="chart" direction="ltr" role="img">']
    for i, (label, opened, closed) in enumerate(rows):
        y = i * h
        out.append(f'<text x="4" y="{y + 20}" class="lbl">{_label(label)}</text>')
        for j, (v, c) in enumerate(((opened, "var(--warn)"), (closed, "var(--ok)"))):
            w = max(2, BAR_W * v / peak)
            out.append(f'<rect x="{LABEL_W}" y="{y + 4 + j * 13}" width="{w:.1f}" height="11" rx="2" fill="{c}"/>')
            out.append(f'<text x="{LABEL_W + w + 6:.1f}" y="{y + 13 + j * 13}" class="val">{v}</text>')
    out.append("</svg>")
    return "".join(out)


def generate_dashboard(manager: ReportsManager) -> Dict[str, Any]:
    now = now_dubai()
    since = now - timedelta(days=DAYS)
    with contextlib.redirect_stdout(io.StringIO()):
        manager.generate_maintenance(target_date="all")
        maint = list(manager.last_items.get("maintenance", []))
        manager.generate_fire_safety(target_date="all")
        fire = list(manager.last_items.get("fire_safety", []))
        manager.generate_operations(target_date="all")
        ops = manager.last_items.get("operations", [])
        manager.generate_parking_violations(target_date="all")
        viols = manager.last_items.get("violations", [])
        manager.generate_vehicle_permissions(target_date="all")
        perms = manager.last_items.get("permissions", [])

    def recent(items):
        return [t for t in items if isinstance(t.get("datetime"), datetime) and t["datetime"] >= since]

    tickets = maint + fire
    recent_tickets = recent(tickets)
    open_now = [t for t in tickets if not _is_done(t)]
    closed_recent = [t for t in recent_tickets if _is_done(t)]
    close_hours = [(t["closed_at"] - t["datetime"]).total_seconds() / 3600 for t in closed_recent
                   if isinstance(t.get("closed_at"), datetime) and t["closed_at"] >= t["datetime"]]
    avg_close = sum(close_hours) / len(close_hours) if close_hours else None
    overdue = [t for t in open_now if isinstance(t.get("datetime"), datetime)
               and (now - t["datetime"]).total_seconds() > 48 * 3600]

    per_building = Counter(t.get("building") or "بدون مبنى" for t in recent_tickets)
    close_by_bld: Dict[str, List[float]] = defaultdict(list)
    for t in closed_recent:
        if isinstance(t.get("closed_at"), datetime):
            close_by_bld[t.get("building") or "بدون مبنى"].append((t["closed_at"] - t["datetime"]).total_seconds() / 3600)
    weeks: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    for t in recent_tickets:
        wk = (t["datetime"] - timedelta(days=t["datetime"].weekday())).strftime("%d-%m")
        weeks[wk][0] += 1
        if _is_done(t):
            weeks[wk][1] += 1
    vendors = Counter(e.get("vendor") for e in recent(ops) if e.get("vendor") and e.get("vendor") != "Operations Team")
    repeats = Counter(t.get("building") or "بدون مبنى" for t in recent_tickets if t.get("repeat_flag"))

    groups = manager.config.get("groups", {})
    day_counts: Dict[str, Counter] = defaultdict(Counter)
    for msg in manager.parser.load_messages():
        if msg["datetime"] and msg["datetime"] >= now - timedelta(days=14) and msg["group_name"] in groups.values():
            day_counts[msg["group_name"]][msg["datetime"].astimezone(DUBAI_TZ).strftime("%d-%m")] += 1
    activity = sorted(((g, sum(c.values())) for g, c in day_counts.items()), key=lambda r: -r[1])

    tiles = [
        ("تذاكر مفتوحة الآن", len(open_now)),
        (f"اتقفلت آخر {DAYS} يوم", len(closed_recent)),
        ("متوسط وقت الإغلاق", f"{avg_close:.1f} س" if avg_close is not None else "—"),
        ("مفتوحة > 48 ساعة", len(overdue)),
        (f"مخالفات آخر {DAYS} يوم", len(recent(viols))),
        (f"تصاريح آخر {DAYS} يوم", len(recent(perms))),
    ]
    sections = [
        ("التذاكر حسب المبنى", _bars(per_building.most_common(8))),
        ("مفتوحة مقابل مقفولة لكل أسبوع", _pairs([(f"أسبوع {k}", v[0], v[1]) for k, v in sorted(weeks.items())])
         + '<p class="legend"><span class="sw warn"></span>مفتوحة <span class="sw ok"></span>مقفولة</p>'),
        ("متوسط وقت الإغلاق حسب المبنى (ساعات)",
         _bars(sorted(((b, sum(v) / len(v)) for b, v in close_by_bld.items()), key=lambda r: -r[1]), " س", "var(--ok)")),
        ("أكثر الشركات نشاطاً", _bars(vendors.most_common(8), color="var(--accent2)")),
        ("الرسائل حسب الجروب (آخر 14 يوم)", _bars(activity, color="var(--accent2)")),
        ("الأعطال المتكررة حسب المبنى", _bars(repeats.most_common(8), color="var(--warn)")),
    ]

    title = f"لوحة ميدان — {now.strftime('%d-%m-%Y')}"
    doc = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root {{ --bg:#f8fafc; --card:#fff; --ink:#0f172a; --muted:#64748b; --line:#e2e8f0;
  --accent:#2563eb; --accent2:#7c3aed; --ok:#16a34a; --warn:#ea580c; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0b1120; --card:#111827; --ink:#e5e7eb; --muted:#94a3b8;
  --line:#1f2937; --accent:#60a5fa; --accent2:#a78bfa; --ok:#4ade80; --warn:#fb923c; }} }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif; }}
h1 {{ font-size:20px; margin:0 0 4px; }} .sub {{ color:var(--muted); margin:0 0 16px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:16px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }}
.tile b {{ display:block; font-size:24px; }} .tile span {{ color:var(--muted); font-size:13px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:12px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
.card h2 {{ font-size:15px; margin:0 0 8px; }}
.chart {{ width:100%; height:auto; }} .lbl {{ font-size:12px; fill:var(--ink); }} .val {{ font-size:11px; fill:var(--muted); }}
.empty {{ color:var(--muted); }} .legend {{ font-size:12px; color:var(--muted); margin:4px 0 0; }}
.sw {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 6px 0 2px; }}
.sw.ok {{ background:var(--ok); }} .sw.warn {{ background:var(--warn); }}
</style></head><body>
<h1>{html.escape(title)}</h1><p class="sub">آخر {DAYS} يوماً — الصيانة والحريق والعمليات والمواقف</p>
<div class="tiles">{''.join(f'<div class="tile"><b>{v}</b><span>{html.escape(k)}</span></div>' for k, v in tiles)}</div>
<div class="grid">{''.join(f'<div class="card"><h2>{html.escape(h)}</h2>{body}</div>' for h, body in sections)}</div>
</body></html>"""

    out = OUTPUT_DIR / f"Meydan_Dashboard_{now.strftime('%Y%m%d_%H%M')}.html"
    out.write_text(doc, encoding="utf-8")
    text = (f"📊 لوحة ميدان — آخر {DAYS} يوم\n"
            f"مفتوحة الآن: {len(open_now)} | اتقفلت: {len(closed_recent)} | أكتر من 48 ساعة: {len(overdue)}\n"
            "افتح الملف المرفق في المتصفح.")
    return {"text": text, "html_path": str(out), "total": len(recent_tickets)}
