"""Σύνθεση και αποστολή μηνυμάτων ntfy (JSON publish, με action buttons)."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

import httpx

from . import services, tokens
from .config import get_config
from .settings_store import effective_token

ACTION_TTL = 3 * 24 * 3600
FILE_TTL = 36 * 3600
ACTIONS = {"done": "Ναι", "progress": "Σε εξέλιξη", "postpone": "postpone"}


def action_url(task: sqlite3.Row, action: str) -> str:
    tok = tokens.sign("act", task["id"], action, task["date"], ttl_seconds=ACTION_TTL)
    return f"{get_config().base_url}/a/{tok}"


def file_url(file_id: int) -> str:
    return f"{get_config().base_url}/f/{tokens.sign('file', file_id, ttl_seconds=FILE_TTL)}"


def task_actions(task: sqlite3.Row) -> list[dict[str, Any]]:
    return [
        {"action": "http", "label": "✅ Έγινε", "url": action_url(task, "done"), "method": "POST", "clear": True},
        {"action": "http", "label": "⏳ Σε εξέλιξη", "url": action_url(task, "progress"), "method": "POST",
         "clear": True},
        {"action": "http", "label": "📅 Αναβολή", "url": action_url(task, "postpone"), "method": "POST",
         "clear": True},
    ]


def _extras(conn: sqlite3.Connection, course: sqlite3.Row, s: dict[str, Any], today: date,
            *, overdue: bool, countdown: bool, streak: bool) -> list[str]:
    lines = []
    if countdown and s.get("countdown_enabled"):
        left = services.days_left(course["exam_date"], today)
        if left is not None and left >= 0:
            lines.append("🎯 Η εξέταση είναι σήμερα!" if left == 0 else f"⏳ Μένουν {left} μέρες για την εξέταση")
    if overdue and s.get("overdue_enabled"):
        n = len(services.overdue(conn, today, course["id"]))
        if n:
            lines.append(f"⚠️ Έμειναν πίσω: {n} εργασ{'ία' if n == 1 else 'ίες'}")
    if streak and s.get("streak_enabled"):
        st = services.streak_from(services.tasks(conn, course["id"]), today)
        if st:
            lines.append(f"🔥 Streak: {st} μέρ{'α' if st == 1 else 'ες'}")
    return lines


def _task_lines(task: sqlite3.Row, pf: sqlite3.Row | None) -> list[str]:
    lines = []
    if task["description"]:
        lines.append(f"📝 {task['description']}")
    if task["source"] and task["source"] != "-":
        lines.append(f"📂 Πηγή: {task['source']}")
    if pf:
        lines.append(f"📄 {pf['name']} (πάτα την ειδοποίηση)")
    if task["original_date"]:
        lines.append(f"↪️ Μεταφέρθηκε από {task['original_date']}")
    return lines


def _title(course: sqlite3.Row, task: sqlite3.Row) -> str:
    day = f"Day {task['day_no']}: " if task["day_no"] else ""
    return f"{course['name']} · {day}{task['topic'] or 'Εργασία'}"


def compose(conn: sqlite3.Connection, due, s: dict[str, Any]) -> dict[str, Any] | None:
    course = conn.execute("SELECT * FROM course WHERE id=?", (due.course_id,)).fetchone()
    if not course:
        return None
    base = get_config().base_url
    d = due.local_date
    if due.kind == "weekly":
        return weekly_message(conn, course, s, d)
    if due.task_id is None:  # πρωινή χωρίς σημερινή εργασία, μόνο καθυστερήσεις
        extra = _extras(conn, course, s, d, overdue=True, countdown=True, streak=False)
        return {"title": f"{course['name']} · Καμία νέα εργασία σήμερα",
                "message": "\n".join(["Ευκαιρία να καλύψεις όσα έμειναν πίσω."] + extra),
                "priority": int(s["priority"]), "tags": ["books"], "click": f"{base}/overdue"}
    task = conn.execute("SELECT * FROM task WHERE id=?", (due.task_id,)).fetchone()
    if not task or task["status"] == "Ναι" or task["date"] != d.isoformat():
        return None
    pf = services.primary_file(conn, task["id"])
    click = file_url(pf["id"]) if pf else f"{base}/"
    if due.kind == "morning":
        lines = _task_lines(task, pf) + _extras(conn, course, s, d, overdue=True, countdown=True, streak=False)
        return {"title": _title(course, task), "message": "\n".join(lines) or "Καλό διάβασμα!",
                "priority": int(s["priority"]), "tags": ["books"], "click": click, "actions": task_actions(task)}
    # evening
    state = "δεν έχει ξεκινήσει" if task["status"] == "Όχι" else "είναι σε εξέλιξη"
    lines = [f"Η σημερινή εργασία {state}."] + _task_lines(task, pf)[:1] + _extras(
        conn, course, s, d, overdue=False, countdown=False, streak=True)
    return {"title": f"Υπενθύμιση · {_title(course, task)}", "message": "\n".join(lines),
            "priority": int(s["evening_priority"]), "tags": ["alarm_clock"], "click": click,
            "actions": task_actions(task)}


def weekly_message(conn: sqlite3.Connection, course: sqlite3.Row, s: dict[str, Any], d: date) -> dict[str, Any]:
    all_tasks = services.tasks(conn, course["id"])
    start = d - timedelta(days=6)
    week = [t for t in all_tasks if start <= date.fromisoformat(t["date"]) <= d]
    nxt = [t for t in all_tasks if d < date.fromisoformat(t["date"]) <= d + timedelta(days=7)]
    tot = services.progress(all_tasks)
    wk = services.progress(week)
    lines = [f"📊 Τελευταίες 7 μέρες: {wk['done']}/{wk['total']} ολοκληρωμένες",
             f"📈 Συνολικά: {tot['done']}/{tot['total']} ({tot['pct']}%)"]
    from zoneinfo import ZoneInfo

    from . import studytime

    st = studytime.course_stats(conn, course["id"], ZoneInfo(s["timezone"]), days=7)
    if st.week:
        lines.append(f"⏱️ Χρόνος μελέτης 7 ημερών: {studytime.fmt_duration(st.week)}")
    lines += _extras(conn, course, s, d + timedelta(days=1), overdue=True, countdown=True, streak=True)
    if nxt:
        lines.append(f"🗓️ Επόμενες 7 μέρες: {len(nxt)} εργασίες")
        lines += [f"  • {t['topic']}" for t in nxt[:3]]
    return {"title": f"Εβδομαδιαία σύνοψη · {course['name']}", "message": "\n".join(lines),
            "priority": int(s["priority"]), "tags": ["bar_chart"], "click": f"{get_config().base_url}/c/{course['slug']}"}


def test_message(conn: sqlite3.Connection, s: dict[str, Any]) -> dict[str, Any]:
    today = services.local_today(conn)
    row = conn.execute(
        """SELECT t.*, c.name AS course_name FROM task t JOIN course c ON c.id=t.course_id
           WHERE c.active=1 AND t.date=? AND t.status!='Ναι' ORDER BY t.id LIMIT 1""", (today.isoformat(),)).fetchone()
    msg: dict[str, Any] = {"title": "🔔 Δοκιμαστική ειδοποίηση",
                           "message": "Οι ειδοποιήσεις λειτουργούν σωστά.",
                           "priority": int(s["priority"]), "tags": ["white_check_mark"],
                           "click": f"{get_config().base_url}/"}
    if row:
        pf = services.primary_file(conn, row["id"])
        msg["message"] += f"\nΣήμερα: {row['course_name']} · {row['topic']}\nΤα κουμπιά αλλάζουν πραγματικά την κατάσταση."
        msg["actions"] = task_actions(row)
        if pf:
            msg["click"] = file_url(pf["id"])
    return msg


def publish(s: dict[str, Any], msg: dict[str, Any], timeout: float = 15.0) -> None:
    body = {"topic": s["ntfy_topic"], **msg}
    headers = {}
    token = effective_token(s)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.post(s["ntfy_url"].rstrip("/") + "/", json=body, headers=headers, timeout=timeout)
    if r.status_code >= 300:
        raise RuntimeError(f"ntfy HTTP {r.status_code}: {r.text[:200]}")
