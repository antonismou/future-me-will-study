"""Εισαγωγή πλάνου από xlsx (μορφή AHK_Study_Tracker) και .ics."""
from __future__ import annotations

import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import openpyxl

STATUSES = ("Όχι", "Σε εξέλιξη", "Ναι")
HEADER_ALIASES = {
    "day": ("ημέρα", "ημερα", "day"),
    "date": ("ημερομηνία", "ημερομηνια", "date"),
    "topic": ("θέμα", "θεμα", "topic"),
    "description": ("εργασία", "εργασια", "task"),
    "source": ("αρχείο", "αρχειο", "πηγή", "πηγη", "source"),
    "status": ("ολοκληρώθηκε", "ολοκληρωθηκε", "κατάσταση", "status"),
    "notes": ("σημειώσεις", "σημειωσεις", "notes"),
}
DEFAULT_COLS = {"day": 1, "date": 2, "topic": 3, "description": 4, "source": 5, "status": 6, "notes": 7}


class ImportError_(ValueError):
    pass


@dataclass
class ParsedTask:
    day_no: int | None
    date: date
    topic: str
    description: str
    source: str
    status: str
    notes: str
    sheet: str
    row: int
    status_col: int = 6
    notes_col: int = 7
    date_col: int = 2


@dataclass
class ParsedWeek:
    sheet: str
    title: str
    summary: str
    tasks: list[ParsedTask] = field(default_factory=list)


@dataclass
class ParsedPlan:
    title: str
    weeks: list[ParsedWeek]

    @property
    def tasks(self) -> list[ParsedTask]:
        return [t for w in self.weeks for t in w.tasks]


def normalize_status(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ("ναι", "ναί", "yes", "done", "✓"):
        return "Ναι"
    if s.startswith("σε εξ") or s in ("in progress", "progress"):
        return "Σε εξέλιξη"
    return "Όχι"


def _cell_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _find_header(ws) -> tuple[int, dict[str, int]] | None:
    for r in range(1, min(ws.max_row, 20) + 1):
        cols: dict[str, int] = {}
        for c in range(1, min(ws.max_column, 30) + 1):
            text = _cell_str(ws.cell(r, c).value).lower()
            if not text:
                continue
            for key, aliases in HEADER_ALIASES.items():
                if key not in cols and any(text.startswith(a) for a in aliases):
                    cols[key] = c
                    break
        if "day" in cols and ("description" in cols or "topic" in cols):
            return r, {**DEFAULT_COLS, **cols}
    return None


_FULL_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_DM = re.compile(r"(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?")


def _infer_start(wb) -> date | None:
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 5)):
            for c in row:
                if isinstance(c.value, str):
                    m = _FULL_DATE.search(c.value)
                    if m:
                        d, mo, y = map(int, m.groups())
                        return date(y, mo, d)
    return None


def parse_xlsx(data: bytes, year: int | None = None) -> ParsedPlan:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception as e:  # noqa: BLE001
        raise ImportError_(f"Δεν ήταν δυνατό να διαβαστεί το xlsx: {e}") from e
    start = _infer_start(wb)
    cur_year = year or (start.year if start else date.today().year)
    last: date | None = start
    title = ""
    first = wb.worksheets[0]
    if isinstance(first["A1"].value, str):
        title = first["A1"].value.split("(")[0].strip()
    weeks: list[ParsedWeek] = []
    for ws in wb.worksheets:
        found = _find_header(ws)
        if not found:
            continue
        header_row, cols = found
        week = ParsedWeek(
            sheet=ws.title,
            title=_cell_str(ws["A1"].value) if header_row > 1 else ws.title,
            summary=_cell_str(ws["A2"].value) if header_row > 2 else "",
        )
        week.title = week.title or ws.title
        for r in range(header_row + 1, ws.max_row + 1):
            raw_day = _cell_str(ws.cell(r, cols["day"]).value)
            desc = _cell_str(ws.cell(r, cols["description"]).value)
            topic = _cell_str(ws.cell(r, cols["topic"]).value)
            if not raw_day and not desc and not topic:
                continue
            m = re.search(r"\d+", raw_day)
            day_no = int(m.group()) if m else None
            raw_date = ws.cell(r, cols["date"]).value
            if isinstance(raw_date, datetime):
                d = raw_date.date()
            elif isinstance(raw_date, date):
                d = raw_date
            else:
                dm = _DM.search(_cell_str(raw_date))
                if not dm:
                    raise ImportError_(f"Φύλλο «{ws.title}», γραμμή {r}: δεν βρέθηκε ημερομηνία ({raw_date!r})")
                dd, mm = int(dm.group(1)), int(dm.group(2))
                if dm.group(3):
                    yy = int(dm.group(3))
                    d = date(yy + 2000 if yy < 100 else yy, mm, dd)
                else:
                    d = date(cur_year, mm, dd)
                    if last and d < last and (last - d).days > 180:
                        d = date(cur_year + 1, mm, dd)  # αλλαγή χρονιάς Δεκ -> Ιαν
            cur_year = d.year
            last = d
            week.tasks.append(ParsedTask(
                day_no=day_no, date=d, topic=topic, description=desc,
                source=_cell_str(ws.cell(r, cols["source"]).value),
                status=normalize_status(ws.cell(r, cols["status"]).value),
                notes=_cell_str(ws.cell(r, cols["notes"]).value),
                sheet=ws.title, row=r, status_col=cols["status"], notes_col=cols["notes"],
                date_col=cols["date"],
            ))
        if week.tasks:
            weeks.append(week)
    if not weeks:
        raise ImportError_("Δεν βρέθηκε φύλλο με γραμμή κεφαλίδων «Ημέρα / Ημερομηνία / Θέμα / Εργασία».")
    return ParsedPlan(title=title, weeks=weeks)


# ---------------------------------------------------------------- ICS

def _ics_unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_ics(data: bytes) -> dict[int, dict[str, Any]]:
    """Επιστρέφει day_no -> {date, summary, notes_file}."""
    text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    result: dict[int, dict[str, Any]] = {}
    ev: dict[str, str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            ev = {}
        elif line == "END:VEVENT" and ev is not None:
            summary = _ics_unescape(ev.get("SUMMARY", ""))
            desc = _ics_unescape(ev.get("DESCRIPTION", ""))
            m = re.search(r"Day\s*(\d+)", summary)
            if m:
                day = int(m.group(1))
                dt = re.search(r"(\d{4})(\d{2})(\d{2})", ev.get("DTSTART", ""))
                nf = re.search(r"Ημερήσιες σημειώσεις:\s*(.+)", desc)
                notes_file = None
                if nf:
                    notes_file = re.split(r"[\\/]", nf.group(1).strip())[-1].strip()
                result[day] = {
                    "date": date(*map(int, dt.groups())) if dt else None,
                    "summary": summary,
                    "notes_file": notes_file or None,
                }
            ev = None
        elif ev is not None and ":" in line:
            k, v = line.split(":", 1)
            ev[k.split(";")[0].upper()] = v
    return result


# ---------------------------------------------------------------- εφαρμογή στη βάση

def build_hints(task: ParsedTask, ics: dict[int, dict[str, Any]] | None) -> list[str]:
    from .linker import split_source

    hints: list[str] = []
    if ics and task.day_no in ics and ics[task.day_no].get("notes_file"):
        hints.append(ics[task.day_no]["notes_file"])
    m = re.match(r"\s*(?:Lecture|Διάλεξη)\s*(\d+)", task.topic, re.I)
    if m:
        hints.append(f"L{int(m.group(1)):02d}")
    hints.extend(split_source(task.source))
    seen: set[str] = set()
    return [h for h in hints if not (h in seen or seen.add(h))]


def apply_plan(conn: sqlite3.Connection, course_id: int, plan: ParsedPlan,
               ics: dict[int, dict[str, Any]] | None = None, mode: str = "merge") -> dict[str, int]:
    """mode=merge: κρατά κατάσταση/σημειώσεις της εφαρμογής (εκτός αν είναι κενές).
    mode=replace: παίρνει κατάσταση/σημειώσεις από το xlsx."""
    stats = {"created": 0, "updated": 0, "weeks": 0}
    now = datetime.now().isoformat(timespec="seconds")
    for pos, w in enumerate(plan.weeks):
        row = conn.execute("SELECT id FROM week WHERE course_id=? AND sheet_name=?", (course_id, w.sheet)).fetchone()
        if row:
            week_id = row["id"]
            conn.execute("UPDATE week SET title=?, summary=?, position=? WHERE id=?", (w.title, w.summary, pos, week_id))
        else:
            week_id = conn.execute(
                "INSERT INTO week(course_id, position, title, summary, sheet_name) VALUES(?,?,?,?,?)",
                (course_id, pos, w.title, w.summary, w.sheet)).lastrowid
            stats["weeks"] += 1
        for t in w.tasks:
            hints = json.dumps(build_hints(t, ics), ensure_ascii=False)
            existing = conn.execute(
                "SELECT * FROM task WHERE course_id=? AND src_sheet=? AND src_row=?", (course_id, t.sheet, t.row)
            ).fetchone()
            if existing is None and t.day_no is not None:
                existing = conn.execute(
                    "SELECT * FROM task WHERE course_id=? AND day_no=? AND src_sheet IS NULL", (course_id, t.day_no)
                ).fetchone()
            completed_at = now if t.status == "Ναι" else None
            if existing is None:
                conn.execute(
                    """INSERT INTO task(course_id, week_id, day_no, date, topic, description, source, status, notes,
                       completed_at, file_hints, src_sheet, src_row, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (course_id, week_id, t.day_no, t.date.isoformat(), t.topic, t.description, t.source, t.status,
                     t.notes, completed_at, hints, t.sheet, t.row, now))
                stats["created"] += 1
                continue
            status, notes, done_at = existing["status"], existing["notes"], existing["completed_at"]
            take_xlsx = mode == "replace" or (status == "Όχι" and not notes)
            if take_xlsx:
                status, notes = t.status, t.notes
                done_at = existing["completed_at"] or completed_at if status == "Ναι" else None
            new_date = existing["date"] if existing["original_date"] else t.date.isoformat()
            conn.execute(
                """UPDATE task SET week_id=?, day_no=?, date=?, topic=?, description=?, source=?, status=?, notes=?,
                   completed_at=?, file_hints=?, src_sheet=?, src_row=?, updated_at=? WHERE id=?""",
                (week_id, t.day_no, new_date, t.topic, t.description, t.source, status, notes, done_at, hints,
                 t.sheet, t.row, now, existing["id"]))
            stats["updated"] += 1
    from .linker import relink_course

    relink_course(conn, course_id)
    return stats
