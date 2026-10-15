"""Import WhatsApp chat exports ("Export chat" on the phone) into the message store.

Adds the messages the bot never recorded: for each report group, only messages older than the
first message the bot recorded in that group, so nothing is counted twice. Photos and videos
inside the export become the message picture. Safe to re-run (same message, same id).

    python3 -m reports_engine.import_chat_export EXPORT.zip [MORE.zip ...]              # dry run
    python3 -m reports_engine.import_chat_export EXPORT.zip --since 01-09-2026 --apply   # import
    (then python3 -m reports_engine.reprocess --apply, or scripts/update.sh --reprocess)

The group is taken from the export name ("WhatsApp Chat with Meydan FM Team💪") and must be
one of the report groups in config/meydan_rules.yaml (capitalisation ignored); --group overrides.
"""

import argparse
import hashlib
import io
import re
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config_loader import get_config
from .date_utils import ARABIC_TO_WESTERN_DIGITS, DUBAI_TZ
from . import messages_db

THUMB_BOX = (221, 163)   # same box as the bot's thumbnails

# "15/10/2026, 9:37 pm - Name: text"  (Android; 12 or 24 hour)
ANDROID = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{2,4}),?\s+(\d{1,2}):(\d{2})(?::\d{2})?[\s ]*([ap]\.?m\.?)?\s+-\s+(.*)$", re.I)
# "[15/10/2026, 9:37:12 PM] Name: text"  (iPhone)
IPHONE = re.compile(r"^\[(\d{1,2})[/.](\d{1,2})[/.](\d{2,4}),?\s+(\d{1,2}):(\d{2})(?::\d{2})?[\s ]*([ap]\.?m\.?)?\]\s+(.*)$", re.I)
ATTACHED = re.compile(r"<attached:\s*([^>]+)>|([\w\-]+\.(?:jpe?g|png|webp|mp4|mov|3gp|opus|pdf|vcf))\s*\(file attached\)", re.I)
NOISE = re.compile(r"<Media omitted>|<This message was edited>|This message was deleted|You deleted this message|"
                   r"image omitted|video omitted|sticker omitted|audio omitted|document omitted", re.I)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".3gp")


@dataclass
class ExportMessage:
    when: datetime
    sender: str
    text: str
    media: List[str] = field(default_factory=list)
    omitted_media: bool = False


def _clean(line: str) -> str:
    # Mentions are exported as "@⁨Sarfraz Ahmed Ahmed⁩" (wrapped in invisible marks): keep them one word
    line = re.sub(r"@\u2068([^\u2069]*)\u2069", lambda m: "@" + (re.sub(r"[^\w]+", "", m.group(1)) or "contact"), line)
    return line.translate(ARABIC_TO_WESTERN_DIGITS).replace("‎", "").replace("‏", "").replace("‪", "").replace("‬", "")


def _date_order(lines: List[str]) -> str:
    """'dmy' or 'mdy': a first number above 12 means day first, a second one month first."""
    for line in lines:
        m = ANDROID.match(line) or IPHONE.match(line)
        if m and int(m.group(1)) > 12:
            return "dmy"
        if m and int(m.group(2)) > 12:
            return "mdy"
    return "dmy"   # UAE phones


def parse_chat(text: str) -> List[ExportMessage]:
    lines = [_clean(l) for l in text.splitlines()]
    order = _date_order(lines)
    out: List[ExportMessage] = []
    cur: Optional[ExportMessage] = None
    for line in lines:
        m = ANDROID.match(line) or IPHONE.match(line)
        if m:
            a, b, y, h, mi, ap, rest = m.groups()
            d, mo = (int(a), int(b)) if order == "dmy" else (int(b), int(a))
            y = int(y) + (2000 if int(y) < 100 else 0)
            h = int(h)
            if ap:
                h = h % 12 + (12 if ap.lower().startswith("p") else 0)
            sender, sep, body = rest.partition(": ")
            if not sep:          # "Mohan created group ...", "You were added": not a message
                cur = None
                continue
            try:
                when = datetime(y, mo, d, h, int(mi), tzinfo=DUBAI_TZ)
            except ValueError:
                cur = None
                continue
            cur = ExportMessage(when, sender.strip().lstrip("~").strip(), body)
            out.append(cur)
        elif cur is not None:
            cur.text += "\n" + line
    for msg in out:
        msg.media = [a or b for a, b in ATTACHED.findall(msg.text)]
        msg.omitted_media = bool(re.search(r"<Media omitted>|\w+ omitted", msg.text, re.I))
        msg.text = NOISE.sub("", ATTACHED.sub("", msg.text)).strip()
    return [m for m in out if m.text or m.media or m.omitted_media]


def _sender(raw: str) -> str:
    """Contact name as saved on the phone; a bare phone number is never stored as a name."""
    digits = re.sub(r"\D", "", raw)
    if digits and len(digits) >= 7 and not re.search(r"[A-Za-z]", raw):
        return f"Unknown ({digits})"   # reports show "Unknown", never the number
    return raw


def _badge(msg: ExportMessage) -> str:
    names = [n.lower() for n in msg.media]
    if any(n.endswith(VIDEO_EXT) for n in names):
        return "🎬 Video"
    if any(n.endswith(IMAGE_EXT) for n in names) or msg.omitted_media:
        return "📷 Image"
    if any(n.endswith(".pdf") for n in names):
        return "📄 Document"
    if any(n.endswith(".opus") for n in names):
        return "🎵 Audio"
    return "💬 Text"


def _thumbnail(name: str, data: bytes) -> Optional[bytes]:
    """JPEG in the bot's thumbnail box from a photo, or a frame of a video (needs ffmpeg)."""
    from PIL import Image
    try:
        if name.lower().endswith(VIDEO_EXT):
            with tempfile.TemporaryDirectory() as tmp:
                src, frame = Path(tmp) / ("v" + Path(name).suffix), Path(tmp) / "f.jpg"
                src.write_bytes(data)
                for seek in ("1", "0"):
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", seek, "-i", str(src), "-frames:v", "1", str(frame)],
                                   timeout=60, check=False)
                    if frame.exists() and frame.stat().st_size:
                        break
                if not frame.exists():
                    return None
                data = frame.read_bytes()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail(THUMB_BOX)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=75)
        return out.getvalue()
    except Exception:
        return None


def _read_export(path: Path) -> List[Tuple[str, str, Dict[str, bytes]]]:
    """(export name, chat text, media files by name) for a .txt, a .zip, or zips inside a zip."""
    if path.suffix.lower() == ".txt":
        return [(path.stem, path.read_text(encoding="utf-8", errors="replace"), {})]
    found = []

    def walk(zf: zipfile.ZipFile, label: str) -> None:
        media = {Path(i.filename).name: i for i in zf.infolist() if not i.filename.lower().endswith((".txt", ".zip"))}
        for info in zf.infolist():
            if info.filename.lower().endswith(".zip"):
                walk(zipfile.ZipFile(io.BytesIO(zf.read(info))), Path(info.filename).stem)
            elif info.filename.lower().endswith(".txt"):
                chat_name = Path(info.filename).stem if Path(info.filename).stem != "_chat" else label
                found.append((chat_name, zf.read(info).decode("utf-8", errors="replace"),
                              {n: _LazyMember(zf, i) for n, i in media.items()}))

    walk(zipfile.ZipFile(path), path.stem)
    return found


class _LazyMember:
    """Media is read from the zip only for messages that are imported."""

    def __init__(self, zf: zipfile.ZipFile, info: zipfile.ZipInfo):
        self.zf, self.info = zf, info

    def read(self) -> bytes:
        return self.zf.read(self.info)


def _group_for(export_name: str, override: Optional[str]) -> Optional[str]:
    groups = list(get_config().get("groups", {}).values())
    wanted = (override or re.sub(r"^.*WhatsApp Chat (?:with|-)\s*", "", export_name)).strip().lower()
    for g in groups:
        if wanted == g.lower() or wanted.rstrip("_ ") == re.sub(r"[^\w&\- ]", "", g).strip().lower():
            return g
    for g in groups:   # file names lose emojis ("Meydan FM Team_")
        if re.sub(r"[^a-z0-9]", "", wanted).startswith(re.sub(r"[^a-z0-9]", "", g.lower())):
            return g
    return None


def import_exports(paths: List[Path], db_path: Path, since: Optional[datetime] = None, apply: bool = False,
                   group_override: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    summary: Dict[str, Dict[str, int]] = {}
    with sqlite3.connect(str(db_path)) as c:
        messages_db.ensure_schema(c)
        for path in paths:
            for export_name, chat, media in _read_export(path):
                group = _group_for(export_name, group_override)
                if not group:
                    summary[f"(skipped) {export_name}"] = {"not a report group": 1}
                    continue
                first = c.execute("SELECT MIN(ts_utc_ms) FROM messages WHERE lower(group_name) = lower(?) AND message_id NOT LIKE 'EXP%'",
                                  (group,)).fetchone()[0]
                s = summary.setdefault(group, {"in export": 0, "already recorded by the bot": 0, "before --since": 0,
                                               "new": 0, "with picture": 0})
                seen: Dict[str, int] = {}
                for msg in parse_chat(chat):
                    s["in export"] += 1
                    ts = int(msg.when.timestamp() * 1000)
                    if first is not None and ts >= first - 60_000:
                        s["already recorded by the bot"] += 1
                        continue
                    if since and msg.when < since:
                        s["before --since"] += 1
                        continue
                    thumb = None
                    for name in msg.media:
                        if name in media and name.lower().endswith(IMAGE_EXT + VIDEO_EXT):
                            thumb = _thumbnail(name, media[name].read()) if apply else b"x"
                            if thumb:
                                break
                    s["with picture"] += 1 if thumb else 0
                    s["new"] += 1
                    if not apply:
                        continue
                    # Same message in two exports -> same id; real repeats in one minute stay apart
                    base = f"{group}|{msg.when.isoformat()}|{msg.sender}|{msg.text}"
                    seen[base] = seen.get(base, 0) + 1
                    key = f"{base}|{seen[base]}"
                    c.execute(
                        f"INSERT OR IGNORE INTO messages ({messages_db.COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        ("EXP" + hashlib.sha1(key.encode()).hexdigest()[:29], msg.when.strftime("%m/%d/%Y, %I:%M:%S %p"),
                         group, _sender(msg.sender), msg.text or f"({_badge(msg).split(' ', 1)[1]})", _badge(msg),
                         None, thumb, None, ts),
                    )
        if apply:
            c.commit()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Import WhatsApp chat exports (older messages) into the message store")
    ap.add_argument("exports", nargs="+", help=".zip or .txt files from WhatsApp 'Export chat'")
    ap.add_argument("--since", help="only messages from this date (DD-MM-YYYY)")
    ap.add_argument("--group", help="report group name, when the file name does not say it")
    ap.add_argument("--apply", action="store_true", help="write to the message store (default: dry run)")
    args = ap.parse_args()
    since = datetime.strptime(args.since, "%d-%m-%Y").replace(tzinfo=DUBAI_TZ) if args.since else None
    db = messages_db.db_path_for(messages_db.EXCEL_FILE)
    summary = import_exports([Path(p) for p in args.exports], db, since, args.apply, args.group)
    for group, counts in summary.items():
        print(f"{group}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print("Imported. Now run: python3 -m reports_engine.reprocess --apply" if args.apply
          else "Dry run: nothing written. Add --apply to import.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
