"""Ισορροπία μαθημάτων: προτείνει πόσο χρόνο να δώσεις σήμερα σε κάθε μάθημα. Δεν αλλάζει ημερομηνίες.

Για κάθε μάθημα υπολογίζεται ο χρόνος που απομένει (εκτίμηση ανά εργασία μείον ό,τι έχεις ήδη διαβάσει) και
μοιράζεται στις ημέρες μέχρι την εξέταση, ανάλογα με τις διαθέσιμες ώρες κάθε ημέρας.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import services, studytime
from .builder import MEDIUM

MIN_TASK_SECONDS = 15 * 60   # ανοιχτή εργασία: μένει τουλάχιστον τόσο (ή όλη η εκτίμηση, αν είναι μικρότερη)
STEP = 5 * 60                # οι προτάσεις στρογγυλεύονται σε πεντάλεπτα
MAX_HORIZON = 730


@dataclass
class CoursePace:
    course: sqlite3.Row
    remaining: int              # δευτερόλεπτα που απέμεναν στην αρχή της σημερινής ημέρας
    deadline: date              # η πρώτη ημέρα που δεν μετρά πια (η ημέρα της εξέτασης)
    capacity: int               # διαθέσιμα δευτερόλεπτα από σήμερα μέχρι το deadline (για όλα τα μαθήματα)
    pace: int = 0               # όσο χρειάζεται σήμερα για να μη μείνει πίσω
    suggested: int = 0          # όσο χωράει στη σημερινή ημέρα
    studied: int = 0            # σήμερα


@dataclass
class Shortfall:
    course_name: str            # το μάθημα με την εξέταση ως την οποία δεν βγαίνει ο χρόνος
    deadline: date
    missing: int


@dataclass
class Advice:
    courses: list[CoursePace] = field(default_factory=list)
    today_capacity: int = 0
    shortfall: Shortfall | None = None

    @property
    def suggested(self) -> int:
        return sum(c.suggested for c in self.courses)


def estimate_seconds(course: sqlite3.Row, task: sqlite3.Row) -> int:
    effort = task["effort"] if task["effort"] is not None else MEDIUM
    return round(course["medium_minutes"] * 60 * effort / MEDIUM)


def _local_date(stamp: str, tz: ZoneInfo) -> date:
    return datetime.fromisoformat(stamp).astimezone(tz).date()   # naive = ώρα του server


def _remaining(conn, course, tasks, tz: ZoneInfo, today: date, now: datetime) -> tuple[int, int]:
    """(χρόνος που απέμενε στην αρχή της ημέρας, χρόνος μελέτης σήμερα)."""
    before: dict[int, int] = {}
    studied_today = 0
    for s in conn.execute("SELECT task_id, started_at, ended_at, seconds FROM study_session WHERE course_id=?",
                          (course["id"],)):
        seconds = studytime._seconds(s, now)
        if _local_date(s["started_at"], tz) >= today:
            studied_today += seconds
        elif s["task_id"] is not None:
            before[s["task_id"]] = before.get(s["task_id"], 0) + seconds
    total = 0
    for t in tasks:
        done_today = bool(t["completed_at"]) and _local_date(t["completed_at"], tz) >= today
        if t["status"] == services.DONE and not done_today:
            continue
        estimate = estimate_seconds(course, t)
        total += max(estimate - before.get(t["id"], 0), min(estimate, MIN_TASK_SECONDS))
    return total, studied_today


def _round_up(seconds: float) -> int:
    return int(-(-seconds // STEP)) * STEP


def advise(conn: sqlite3.Connection, weekday_minutes: list[int], tz: ZoneInfo, now: datetime | None = None) -> Advice:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(tz).date()

    def capacity(until: date) -> int:
        span = min((until - today).days, MAX_HORIZON)
        return sum(weekday_minutes[(today + timedelta(days=i)).weekday()] * 60 for i in range(span))

    advice = Advice(today_capacity=weekday_minutes[today.weekday()] * 60)
    for c in services.courses(conn, active_only=True):
        tasks = services.tasks(conn, c["id"])
        if c["exam_date"]:
            deadline = date.fromisoformat(c["exam_date"])
            if deadline < today:
                continue
        else:   # χωρίς εξέταση: μετρά μέχρι και την τελευταία εργασία του πλάνου
            last = max((date.fromisoformat(t["date"]) for t in tasks), default=today)
            deadline = max(last, today) + timedelta(days=1)
        remaining, studied = _remaining(conn, c, tasks, tz, today, now)
        if not remaining:
            continue
        advice.courses.append(CoursePace(c, remaining, deadline, capacity(deadline), studied=studied))

    # Πρώτα η πιο κοντινή προθεσμία: παίρνει τον ρυθμό της, και ό,τι μένει πάει στις επόμενες.
    advice.courses.sort(key=lambda p: (p.deadline, p.course["name"]))
    left = advice.today_capacity
    needed = 0
    for p in advice.courses:
        if p.capacity and advice.today_capacity:
            p.pace = min(_round_up(p.remaining * advice.today_capacity / p.capacity), _round_up(p.remaining))
        p.suggested = min(p.pace, left)
        left -= p.suggested
        # Βγαίνει ο χρόνος; Όλη η δουλειά με προθεσμία ως εδώ πρέπει να χωρά στις ώρες ως εδώ.
        needed += p.remaining
        missing = needed - p.capacity
        if missing > 0 and (advice.shortfall is None or missing > advice.shortfall.missing):
            advice.shortfall = Shortfall(p.course["name"], p.deadline, missing)
    return advice
