"""Πρόταση αναπρογραμματισμού: νέες ημερομηνίες για όσες εργασίες δεν χωράνε στις διαθέσιμες ώρες.

Είναι μόνο πρόταση: τίποτα δεν αλλάζει αν δεν το δεχτείς, και η τελευταία αποδοχή αναιρείται.

Κανόνες:
- Κάθε εργασία μένει στην ημερομηνία της αν χωράει. Μετακινούνται οι καθυστερημένες και όσες περισσεύουν.
- Τα μαθήματα με πιο κοντινή εξέταση παίρνουν πρώτα τις διαθέσιμες ώρες.
- Η σειρά των εργασιών κάθε μαθήματος διατηρείται και όλες μπαίνουν πριν από την εξέταση.
- Μια εργασία πιο μεγάλη από κάθε ημέρα σου μπαίνει μόνη της σε μία από τις μεγαλύτερες.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import balance, services, studytime

_UNDO = "reschedule_undo"


@dataclass
class Move:
    task: sqlite3.Row
    course: sqlite3.Row
    old: date
    new: date

    @property
    def key(self) -> str:
        """Ταυτότητα της αλλαγής στη φόρμα: αν αλλάξει η πρόταση, η παλιά αποδοχή δεν ισχύει."""
        return f"{self.task['id']}:{self.old.isoformat()}:{self.new.isoformat()}"


@dataclass
class Proposal:
    moves: list[Move] = field(default_factory=list)
    unplaced: list[tuple[sqlite3.Row, sqlite3.Row]] = field(default_factory=list)   # (μάθημα, εργασία)

    def by_course(self) -> list[tuple[sqlite3.Row, list[Move]]]:
        out: dict[int, tuple[sqlite3.Row, list[Move]]] = {}
        for m in self.moves:
            out.setdefault(m.course["id"], (m.course, []))[1].append(m)
        return list(out.values())


class _Calendar:
    """Ελεύθερος χρόνος ανά ημέρα (αριθμημένες από το 0 = σήμερα)."""

    def __init__(self, weekday_minutes: list[int], today: date, studied_today: int):
        self.capacity = [weekday_minutes[(today + timedelta(days=i)).weekday()] * 60
                         for i in range(balance.MAX_HORIZON)]
        self.biggest = max(self.capacity)
        self.used = {0: studied_today} if studied_today else {}

    def fits(self, day: int, size: int, extra: dict[int, int]) -> bool:
        cap = self.capacity[day]
        used = self.used.get(day, 0) + extra.get(day, 0)
        if size <= self.biggest:
            return used + size <= cap
        return used == 0 and cap == self.biggest and cap > 0

    def first(self, start: int, end: int, size: int, extra: dict[int, int]) -> int | None:
        """Πρώτη ημέρα στο [start, end] που χωράει. Αν end < start, ψάχνει προς τα πίσω."""
        step = 1 if end >= start else -1
        return next((d for d in range(start, end + step, step) if self.fits(d, size, extra)), None)


def _latest(cal: _Calendar, sizes: list[int], end: int) -> list[int] | None:
    """Η πιο αργή ημέρα που μπορεί να μπει κάθε εργασία ώστε να προλάβουν όλες. None αν δεν χωράνε."""
    out = [0] * len(sizes)
    extra: dict[int, int] = {}
    day = end
    for i in range(len(sizes) - 1, -1, -1):
        found = cal.first(day, 0, sizes[i], extra) if day >= 0 else None
        if found is None:
            return None
        day = out[i] = found
        extra[day] = extra.get(day, 0) + sizes[i]
    return out


def _place(cal: _Calendar, preferred: list[int], sizes: list[int], end: int) -> list[int | None]:
    latest = _latest(cal, sizes, end)
    placed: list[int | None] = []
    prev = 0
    for i, size in enumerate(sizes):
        if latest is not None:     # όσο πιο κοντά στην ημερομηνία της, χωρίς να στριμώξει τις επόμενες
            day = cal.first(max(prev, min(preferred[i], latest[i])), latest[i], size, {})
        else:                      # δεν χωράνε όλες: βάλε όσες γίνεται, όσο πιο νωρίς
            day = cal.first(prev, end, size, {}) if prev <= end else None
        placed.append(day)
        if day is not None:
            cal.used[day] = cal.used.get(day, 0) + size
            prev = day
    return placed


def propose(conn: sqlite3.Connection, weekday_minutes: list[int], tz: ZoneInfo, now: datetime | None = None) -> Proposal:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(tz).date()
    proposal = Proposal()
    if not any(weekday_minutes):
        return proposal

    studied: dict[int | None, int] = {}
    studied_today = 0
    for s in conn.execute("""SELECT s.* FROM study_session s JOIN course c ON c.id=s.course_id WHERE c.active=1"""):
        seconds = studytime._seconds(s, now)
        studied[s["task_id"]] = studied.get(s["task_id"], 0) + seconds
        if balance._local_date(s["started_at"], tz) == today:
            studied_today += seconds

    courses = []
    for c in services.courses(conn, active_only=True):
        exam = date.fromisoformat(c["exam_date"]) if c["exam_date"] else None
        if exam and exam < today:
            continue
        courses.append((exam or date.max, c["name"], c))

    cal = _Calendar(weekday_minutes, today, studied_today)
    for exam, _, course in sorted(courses, key=lambda x: x[:2]):
        tasks = [t for t in services.tasks(conn, course["id"]) if t["status"] != services.DONE]
        sizes = []
        for t in tasks:
            estimate = balance.estimate_seconds(course, t)
            sizes.append(max(estimate - studied.get(t["id"], 0), min(estimate, balance.MIN_TASK_SECONDS)))
        preferred = [max((date.fromisoformat(t["date"]) - today).days, 0) for t in tasks]
        end = min((exam - today).days, balance.MAX_HORIZON) - 1   # τελευταία ημέρα: η παραμονή
        for t, day in zip(tasks, _place(cal, preferred, sizes, end)):
            if day is None:
                proposal.unplaced.append((course, t))
                continue
            old, new = date.fromisoformat(t["date"]), today + timedelta(days=day)
            if old != new:
                proposal.moves.append(Move(t, course, old, new))
    return proposal


def apply(conn: sqlite3.Connection, moves: list[Move]) -> int:
    """Εφαρμόζει τις αλλαγές που δέχτηκε ο χρήστης και κρατά ό,τι χρειάζεται για αναίρεση."""
    record = []
    for m in moves:
        record.append([m.task["id"], m.old.isoformat(), m.task["original_date"], m.new.isoformat()])
        services.postpone(conn, m.task["id"], m.new)
    if record:
        conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (_UNDO, json.dumps(record)))
    return len(record)


def undo_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (_UNDO,)).fetchone()
    return len(json.loads(row["value"])) if row else 0


def undo(conn: sqlite3.Connection) -> int:
    """Επαναφέρει την τελευταία αποδοχή. Όποια εργασία άλλαξε ημερομηνία από τότε, μένει όπως είναι."""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (_UNDO,)).fetchone()
    if not row:
        return 0
    restored = 0
    for task_id, old, original, new in json.loads(row["value"]):
        restored += conn.execute("UPDATE task SET date=?, original_date=? WHERE id=? AND date=?",
                                 (old, original, task_id, new)).rowcount
    conn.execute("DELETE FROM meta WHERE key=?", (_UNDO,))
    return restored
