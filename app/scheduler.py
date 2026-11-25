"""Scheduler ειδοποιήσεων.

* `plan_due()` είναι καθαρή συνάρτηση: δεδομένης της ώρας, των ρυθμίσεων, της κατάστασης και των ήδη
  καταγεγραμμένων κλειδιών, επιστρέφει ποιες ειδοποιήσεις οφείλονται τώρα.
* `run_tick()` καλείται κάθε ~30s: γράφει πρώτα στο `notification_log` (UNIQUE key) και μετά στέλνει,
  ώστε ένα restart να μη στείλει ποτέ διπλή ειδοποίηση. Όσες «χάθηκαν» όσο ο server ήταν κάτω στέλνονται
  αν είμαστε ακόμα μέσα στο παράθυρο ανοχής (grace_minutes), αλλιώς παραλείπονται.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

log = logging.getLogger("study.scheduler")
DONE = "Ναι"
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class TaskSnap:
    id: int
    date: date
    status: str


@dataclass
class CourseSnap:
    id: int
    notify: bool
    tasks: list[TaskSnap] = field(default_factory=list)


@dataclass(frozen=True)
class Due:
    key: str
    kind: str  # morning | evening | weekly
    course_id: int
    task_id: int | None
    local_date: date
    scheduled: datetime
    skip_reason: str | None = None


def parse_hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_quiet(t: time, start: time, end: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end


def local_dt(d: date, t: time, tz: ZoneInfo) -> datetime:
    """Τοπική ώρα -> aware datetime. Ανύπαρκτες ώρες (αλλαγή ώρας) μετατοπίζονται, διφορούμενες = 1η εμφάνιση."""
    return datetime.combine(d, t).replace(tzinfo=tz).astimezone(timezone.utc).astimezone(tz)


def _targets(kind: str, c: CourseSnap, d: date, s: dict[str, Any]) -> list[int | None]:
    if kind == "morning":
        todays = [t.id for t in c.tasks if t.date == d and t.status != DONE]
        if todays:
            return todays
        if s.get("overdue_enabled") and any(t.date < d and t.status != DONE for t in c.tasks):
            return [None]
        return []
    if kind == "evening":
        return [t.id for t in c.tasks if t.date == d and t.status != DONE]
    if kind == "weekly":
        relevant = any(d - timedelta(days=6) <= t.date <= d + timedelta(days=7) for t in c.tasks) or any(
            t.date < d and t.status != DONE for t in c.tasks)
        return [None] if relevant else []
    return []


def plan_due(now: datetime, s: dict[str, Any], courses: Iterable[CourseSnap], done_keys: set[str]) -> list[Due]:
    tz = ZoneInfo(s["timezone"])
    local = now.astimezone(tz)
    grace = timedelta(minutes=int(s.get("grace_minutes", 60)))
    quiet_on = bool(s.get("quiet_enabled"))
    q_start, q_end = parse_hm(s["quiet_start"]), parse_hm(s["quiet_end"])
    courses = list(courses)
    out: list[Due] = []
    for d in (local.date() - timedelta(days=1), local.date()):
        if d.weekday() not in s.get("weekdays", []):
            continue
        slots: list[tuple[str, str, str]] = []
        if s.get("morning_enabled"):
            slots.append(("morning", "morning", s["morning_time"]))
        if s.get("evening_enabled"):
            slots += [("evening", f"evening{i}", t) for i, t in enumerate(s.get("evening_times", []))]
        if s.get("weekly_enabled") and d.weekday() == int(s.get("weekly_day", 6)):
            slots.append(("weekly", "weekly", s["weekly_time"]))
        for kind, slot, hm in slots:
            sched = local_dt(d, parse_hm(hm), tz)
            if not (sched <= now < sched + grace):
                continue
            quiet = quiet_on and (in_quiet(sched.time(), q_start, q_end) or in_quiet(local.time(), q_start, q_end))
            for c in courses:
                if not c.notify:
                    continue
                for tid in _targets(kind, c, d, s):
                    key = f"{slot}:{c.id}:{d.isoformat()}:{tid if tid is not None else '-'}"
                    if key in done_keys:
                        continue
                    out.append(Due(key, kind, c.id, tid, d, sched, "quiet" if quiet else None))
    return out


# ---------------------------------------------------------------- runner

def snapshot(conn: sqlite3.Connection) -> list[CourseSnap]:
    snaps: dict[int, CourseSnap] = {}
    for c in conn.execute("SELECT id, notify FROM course WHERE active=1"):
        snaps[c["id"]] = CourseSnap(c["id"], bool(c["notify"]))
    for t in conn.execute("SELECT t.id, t.course_id, t.date, t.status FROM task t JOIN course c ON c.id=t.course_id "
                          "WHERE c.active=1"):
        snaps[t["course_id"]].tasks.append(TaskSnap(t["id"], date.fromisoformat(t["date"]), t["status"]))
    return list(snaps.values())


def done_keys(conn: sqlite3.Connection, since: date) -> set[str]:
    rows = conn.execute("SELECT key, status, attempts FROM notification_log WHERE local_date >= ?",
                        (since.isoformat(),))
    # 'pending' μετράει ως σταλμένο: αν κόπηκε το process ανάμεσα σε εγγραφή και αποστολή, προτιμάμε
    # να μη στείλουμε δεύτερη φορά (at-most-once).
    return {r["key"] for r in rows if r["status"] != "failed" or r["attempts"] >= MAX_ATTEMPTS}


Publisher = Callable[[dict[str, Any], dict[str, Any]], None]


def run_tick(conn: sqlite3.Connection, now: datetime, publish: Publisher | None = None) -> list[str]:
    from . import notify
    from .settings_store import get_settings

    publish = publish or notify.publish
    s = get_settings(conn)
    tz = ZoneInfo(s["timezone"])
    since = now.astimezone(tz).date() - timedelta(days=2)
    due = plan_due(now, s, snapshot(conn), done_keys(conn, since))
    sent: list[str] = []
    for item in due:
        if item.skip_reason:
            conn.execute(
                """INSERT INTO notification_log(key, kind, course_id, task_id, local_date, status, error)
                   VALUES(?,?,?,?,?,'skipped',?) ON CONFLICT(key) DO NOTHING""",
                (item.key, item.kind, item.course_id, item.task_id, item.local_date.isoformat(), item.skip_reason))
            conn.commit()
            continue
        msg = notify.compose(conn, item, s)
        if msg is None:
            continue
        cur = conn.execute(
            """INSERT INTO notification_log(key, kind, course_id, task_id, local_date, status, attempts, title)
               VALUES(?,?,?,?,?,'pending',1,?)
               ON CONFLICT(key) DO UPDATE SET status='pending', attempts=attempts+1
               WHERE notification_log.status='failed'""",
            (item.key, item.kind, item.course_id, item.task_id, item.local_date.isoformat(), msg["title"]))
        conn.commit()
        if cur.rowcount == 0:
            continue
        try:
            publish(s, msg)
            conn.execute("UPDATE notification_log SET status='sent', sent_at=?, error='' WHERE key=?",
                         (datetime.now(timezone.utc).isoformat(timespec="seconds"), item.key))
            sent.append(item.key)
        except Exception as e:  # noqa: BLE001
            log.warning("Αποτυχία αποστολής %s: %s", item.key, e)
            conn.execute("UPDATE notification_log SET status='failed', error=? WHERE key=?", (str(e)[:500], item.key))
        conn.commit()
    conn.execute("INSERT INTO meta(key, value) VALUES('last_tick', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (now.isoformat(),))
    conn.commit()
    return sent


class SchedulerThread(threading.Thread):
    def __init__(self, interval: float = 30.0):
        super().__init__(name="study-scheduler", daemon=True)
        self.interval = interval
        self.stop_event = threading.Event()

    def run(self) -> None:
        from .db import connect

        log.info("Scheduler ξεκίνησε")
        while not self.stop_event.is_set():
            try:
                conn = connect()
                try:
                    run_tick(conn, datetime.now(timezone.utc))
                finally:
                    conn.close()
            except Exception:  # noqa: BLE001
                log.exception("Σφάλμα στο tick του scheduler")
            self.stop_event.wait(self.interval)

    def stop(self) -> None:
        self.stop_event.set()
