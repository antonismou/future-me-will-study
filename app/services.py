"""Ερωτήματα και λογική πάνω στη βάση (πρόοδος, καθυστερήσεις, streak, αλλαγές κατάστασης)."""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .settings_store import get_settings

STATUSES = ("Όχι", "Σε εξέλιξη", "Ναι")
DONE = "Ναι"


def local_now(conn: sqlite3.Connection) -> datetime:
    return datetime.now(ZoneInfo(get_settings(conn)["timezone"]))


def local_today(conn: sqlite3.Connection) -> date:
    return local_now(conn).date()


def slugify(name: str) -> str:
    table = {"α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i",
             "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s",
             "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o"}
    s = unicodedata.normalize("NFKD", name.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(table.get(c, c) for c in s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "course"


def unique_slug(conn: sqlite3.Connection, name: str) -> str:
    base = slugify(name)[:40]
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM course WHERE slug=?", (slug,)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def courses(conn: sqlite3.Connection, active_only: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM course" + (" WHERE active=1" if active_only else "") + " ORDER BY active DESC, name"
    return conn.execute(q).fetchall()


def course_by_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM course WHERE slug=?", (slug,)).fetchone()


def weeks(conn: sqlite3.Connection, course_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM week WHERE course_id=? ORDER BY position, id", (course_id,)).fetchall()


def tasks(conn: sqlite3.Connection, course_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM task WHERE course_id=? ORDER BY date, COALESCE(day_no, 9999), id", (course_id,)
    ).fetchall()


def task_files(conn: sqlite3.Connection, task_ids: list[int]) -> dict[int, list[sqlite3.Row]]:
    out: dict[int, list[sqlite3.Row]] = {i: [] for i in task_ids}
    if not task_ids:
        return out
    q = ",".join("?" * len(task_ids))
    for r in conn.execute(
        f"""SELECT tf.task_id, tf.is_primary, f.* FROM task_file tf JOIN file f ON f.id=tf.file_id
            WHERE tf.task_id IN ({q}) ORDER BY tf.is_primary DESC, f.category, f.grp, f.name""", task_ids):
        out[r["task_id"]].append(r)
    return out


def primary_file(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT f.* FROM task_file tf JOIN file f ON f.id=tf.file_id WHERE tf.task_id=?
           ORDER BY tf.is_primary DESC, (f.mime='application/pdf') DESC, f.name LIMIT 1""", (task_id,)).fetchone()


def progress(task_rows: list[sqlite3.Row]) -> dict[str, Any]:
    total = len(task_rows)
    done = sum(1 for t in task_rows if t["status"] == DONE)
    prog = sum(1 for t in task_rows if t["status"] == "Σε εξέλιξη")
    return {"total": total, "done": done, "in_progress": prog,
            "pct": round(100 * done / total) if total else 0}


def week_progress(conn: sqlite3.Connection, course_id: int) -> list[dict[str, Any]]:
    all_tasks = tasks(conn, course_id)
    out = []
    for w in weeks(conn, course_id):
        items = [t for t in all_tasks if t["week_id"] == w["id"]]
        out.append({"week": w, "tasks": items, **progress(items)})
    loose = [t for t in all_tasks if t["week_id"] is None]
    if loose:
        out.append({"week": None, "tasks": loose, **progress(loose)})
    return out


def overdue(conn: sqlite3.Connection, today: date, course_id: int | None = None) -> list[sqlite3.Row]:
    q = """SELECT t.*, c.name AS course_name, c.slug AS course_slug, c.color AS course_color
           FROM task t JOIN course c ON c.id=t.course_id
           WHERE c.active=1 AND t.date < ? AND t.status != ?"""
    args: list[Any] = [today.isoformat(), DONE]
    if course_id is not None:
        q += " AND t.course_id=?"
        args.append(course_id)
    return conn.execute(q + " ORDER BY t.date, t.id", args).fetchall()


def streak_from(task_rows: list[Any], today: date) -> int:
    """Συνεχόμενες ημέρες (με εργασίες) όπου όλες οι εργασίες είναι «Ναι».
    Η σημερινή μετράει μόνο αν έχει ήδη ολοκληρωθεί· μέρες χωρίς εργασίες δεν σπάνε το streak."""
    by_day: dict[date, list[str]] = {}
    for t in task_rows:
        d = t["date"] if isinstance(t["date"], date) else date.fromisoformat(t["date"])
        if d <= today:
            by_day.setdefault(d, []).append(t["status"])
    count = 0
    for d in sorted(by_day, reverse=True):
        all_done = all(s == DONE for s in by_day[d])
        if d == today and not all_done:
            continue
        if not all_done:
            break
        count += 1
    return count


def days_left(exam_date: str | None, today: date) -> int | None:
    if not exam_date:
        return None
    try:
        return (date.fromisoformat(exam_date) - today).days
    except ValueError:
        return None


def set_status(conn: sqlite3.Connection, task_id: int, status: str) -> bool:
    if status not in STATUSES:
        return False
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """UPDATE task SET status=?, completed_at=CASE WHEN ?='Ναι' THEN COALESCE(completed_at, ?) ELSE NULL END,
           updated_at=? WHERE id=?""", (status, status, now, now, task_id))
    return cur.rowcount > 0


def set_notes(conn: sqlite3.Connection, task_id: int, notes: str) -> None:
    conn.execute("UPDATE task SET notes=?, updated_at=? WHERE id=?",
                 (notes[:5000], datetime.now().isoformat(timespec="seconds"), task_id))


def postpone(conn: sqlite3.Connection, task_id: int, new_date: date) -> bool:
    t = conn.execute("SELECT date, original_date FROM task WHERE id=?", (task_id,)).fetchone()
    if not t:
        return False
    conn.execute(
        "UPDATE task SET date=?, original_date=COALESCE(original_date, ?), updated_at=? WHERE id=?",
        (new_date.isoformat(), t["date"], datetime.now().isoformat(timespec="seconds"), task_id))
    return True


def default_postpone_date(task_date: date, today: date) -> date:
    return max(task_date, today) + timedelta(days=1)


# ---------------------------------------------------------------- Δυσκολίες

def review_items(conn: sqlite3.Connection, course_id: int) -> list[sqlite3.Row]:
    """Εργασίες με σημειώσεις ή σημειωμένες ως δύσκολες (η λίστα για την τελική επανάληψη)."""
    return conn.execute(
        """SELECT * FROM task WHERE course_id=? AND (hard=1 OR TRIM(notes) != '')
           ORDER BY reviewed_at IS NOT NULL, date, COALESCE(day_no, 9999), id""", (course_id,)).fetchall()


def set_hard(conn: sqlite3.Connection, task_id: int, hard: bool) -> None:
    conn.execute("UPDATE task SET hard=? WHERE id=?", (int(hard), task_id))


def set_reviewed(conn: sqlite3.Connection, task_id: int, reviewed: bool) -> None:
    conn.execute("UPDATE task SET reviewed_at=? WHERE id=?",
                 (datetime.now().isoformat(timespec="seconds") if reviewed else None, task_id))
