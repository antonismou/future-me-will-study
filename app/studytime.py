"""Χρόνος μελέτης: χρονόμετρο ανά εργασία, χειροκίνητες καταγραφές και στατιστικά."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MAX_SESSION = timedelta(hours=6)   # αν ξεχαστεί το χρονόμετρο, δεν μετράμε πάνω από 6 ώρες
MAX_MANUAL_MINUTES = 12 * 60
MAX_GOAL_HOURS = 100


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def running(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT s.*, t.topic AS task_topic, c.name AS course_name, c.slug AS course_slug
           FROM study_session s JOIN course c ON c.id = s.course_id LEFT JOIN task t ON t.id = s.task_id
           WHERE s.ended_at IS NULL ORDER BY s.id DESC LIMIT 1""").fetchone()


def stop(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Σταματά όποιο χρονόμετρο τρέχει. Επιστρέφει τα δευτερόλεπτα που καταγράφηκαν."""
    now = now or utcnow()
    total = 0
    for s in conn.execute("SELECT id, started_at FROM study_session WHERE ended_at IS NULL").fetchall():
        started = _parse(s["started_at"])
        dur = min(max(now - started, timedelta(0)), MAX_SESSION)
        conn.execute("UPDATE study_session SET ended_at=?, seconds=? WHERE id=?",
                     (_iso(started + dur), int(dur.total_seconds()), s["id"]))
        total += int(dur.total_seconds())
    return total


def start(conn: sqlite3.Connection, task_id: int, now: datetime | None = None) -> None:
    """Ξεκινά χρονόμετρο για την εργασία (σταματά πρώτα όποιο άλλο τρέχει)."""
    now = now or utcnow()
    t = conn.execute("SELECT id, course_id, status FROM task WHERE id=?", (task_id,)).fetchone()
    if not t:
        raise ValueError("task")
    stop(conn, now)
    conn.execute("INSERT INTO study_session(course_id, task_id, started_at, kind) VALUES(?,?,?,'timer')",
                 (t["course_id"], task_id, _iso(now)))
    if t["status"] == "Όχι":
        conn.execute("UPDATE task SET status='Σε εξέλιξη' WHERE id=?", (task_id,))


def add_manual(conn: sqlite3.Connection, task_id: int, minutes: int, now: datetime | None = None) -> None:
    now = now or utcnow()
    if not 1 <= minutes <= MAX_MANUAL_MINUTES:
        raise ValueError("minutes")
    t = conn.execute("SELECT course_id FROM task WHERE id=?", (task_id,)).fetchone()
    if not t:
        raise ValueError("task")
    conn.execute(
        "INSERT INTO study_session(course_id, task_id, started_at, ended_at, seconds, kind) VALUES(?,?,?,?,?,'manual')",
        (t["course_id"], task_id, _iso(now - timedelta(minutes=minutes)), _iso(now), minutes * 60))


def _seconds(row: sqlite3.Row, now: datetime) -> int:
    if row["ended_at"] is None:
        return int(min(max(now - _parse(row["started_at"]), timedelta(0)), MAX_SESSION).total_seconds())
    return int(row["seconds"])


def task_seconds(conn: sqlite3.Connection, task_ids: list[int], now: datetime | None = None) -> dict[int, int]:
    now = now or utcnow()
    out = {i: 0 for i in task_ids}
    if not task_ids:
        return out
    q = ",".join("?" * len(task_ids))
    for r in conn.execute(f"SELECT task_id, started_at, ended_at, seconds FROM study_session WHERE task_id IN ({q})",
                          task_ids):
        out[r["task_id"]] += _seconds(r, now)
    return out


@dataclass
class Stats:
    today: int
    week: int
    total: int
    days: list[tuple[date, int]]          # τελευταίες N ημέρες (παλαιότερη πρώτη)
    by_week: list[tuple[str, int]]        # (τίτλος εβδομάδας πλάνου, δευτερόλεπτα)
    by_task: list[tuple[sqlite3.Row, int]]
    recent: list[sqlite3.Row]
    avg_day: int                          # μέσος όρος στις N ημέρες


def course_stats(conn: sqlite3.Connection, course_id: int | None, tz: ZoneInfo, days: int = 14,
                 now: datetime | None = None) -> Stats:
    now = now or utcnow()
    today = now.astimezone(tz).date()
    where, args = ("WHERE s.course_id=?", [course_id]) if course_id is not None else ("", [])
    rows = conn.execute(
        f"""SELECT s.*, t.topic AS task_topic, t.week_id, t.day_no, c.name AS course_name
            FROM study_session s JOIN course c ON c.id = s.course_id LEFT JOIN task t ON t.id = s.task_id
            {where} ORDER BY s.started_at DESC""", args).fetchall()
    per_day: dict[date, int] = {}
    per_week: dict[int | None, int] = {}
    per_task: dict[int, int] = {}
    total = 0
    for r in rows:
        sec = _seconds(r, now)
        d = _parse(r["started_at"]).astimezone(tz).date()
        per_day[d] = per_day.get(d, 0) + sec
        per_week[r["week_id"]] = per_week.get(r["week_id"], 0) + sec
        if r["task_id"] is not None:
            per_task[r["task_id"]] = per_task.get(r["task_id"], 0) + sec
        total += sec
    day_list = [(today - timedelta(days=i), per_day.get(today - timedelta(days=i), 0)) for i in range(days - 1, -1, -1)]
    week_total = sum(per_day.get(today - timedelta(days=i), 0) for i in range(7))
    by_week: list[tuple[str, int]] = []
    if course_id is not None:
        for w in conn.execute("SELECT id, title FROM week WHERE course_id=? ORDER BY position, id", (course_id,)):
            by_week.append((w["title"], per_week.get(w["id"], 0)))
        if per_week.get(None):
            by_week.append(("Χωρίς εβδομάδα", per_week[None]))
    top_ids = sorted(per_task, key=per_task.get, reverse=True)[:8]
    tasks = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM task WHERE id IN ({','.join('?' * len(top_ids))})", top_ids)} if top_ids else {}
    by_task = [(tasks[i], per_task[i]) for i in top_ids if i in tasks]
    return Stats(today=per_day.get(today, 0), week=week_total, total=total, days=day_list, by_week=by_week,
                 by_task=by_task, recent=rows[:15], avg_day=sum(s for _, s in day_list) // max(days, 1))


def parse_goal_hours(text: str) -> int | None:
    """Ώρες (π.χ. «8» ή «7,5») σε λεπτά. Κενό = χωρίς στόχο (0). None αν η τιμή δεν είναι έγκυρη."""
    text = text.strip().replace(",", ".")
    if not text:
        return 0
    try:
        hours = float(text)
    except ValueError:
        return None
    if not 0 <= hours <= MAX_GOAL_HOURS:   # απορρίπτει και nan/inf
        return None
    return round(hours * 60)


@dataclass
class Goal:
    done: int          # δευτερόλεπτα μέσα στην εβδομάδα (Δευτέρα–Κυριακή)
    target: int        # δευτερόλεπτα
    days_left: int     # ημέρες που απομένουν, μαζί με τη σημερινή

    @property
    def pct(self) -> int:
        return min(100, 100 * self.done // self.target)

    @property
    def remaining(self) -> int:
        return max(0, self.target - self.done)

    @property
    def per_day(self) -> int:
        """Πόσο χρειάζεται κάθε μέρα που μένει, στρογγυλεμένο προς τα πάνω σε ολόκληρο λεπτό."""
        return -(-self.remaining // (self.days_left * 60)) * 60


def goal_progress(conn: sqlite3.Connection, course: sqlite3.Row, tz: ZoneInfo, day: date,
                  now: datetime | None = None) -> Goal | None:
    """Πρόοδος του εβδομαδιαίου στόχου για την εβδομάδα που περιέχει το `day`. None αν δεν υπάρχει στόχος."""
    if not course["weekly_goal_minutes"]:
        return None
    now = now or utcnow()
    monday = day - timedelta(days=day.weekday())
    done = 0
    for r in conn.execute("SELECT started_at, ended_at, seconds FROM study_session WHERE course_id=?",
                          (course["id"],)):
        if monday <= _parse(r["started_at"]).astimezone(tz).date() < monday + timedelta(days=7):
            done += _seconds(r, now)
    return Goal(done=done, target=course["weekly_goal_minutes"] * 60, days_left=7 - day.weekday())


def fmt_duration(seconds: int | None) -> str:
    m = int(seconds or 0) // 60
    h, m = divmod(m, 60)
    if h and m:
        return f"{h} ώ {m:02d} λ"
    if h:
        return f"{h} ώ"
    return f"{m} λ"
