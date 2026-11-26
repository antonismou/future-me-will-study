"""Ιδιωτική ροή ημερολογίου (iCalendar) με το πλάνο των ενεργών μαθημάτων.

Οι εφαρμογές ημερολογίου δεν κάνουν login, οπότε το link προστατεύεται από ένα τυχαίο token. Φυλάσσεται στη
βάση, ώστε να μπορεί να αντικατασταθεί ή να καταργηθεί από τις Ρυθμίσεις.
"""
from __future__ import annotations

import hmac
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import services
from .config import get_config

_KEY = "calendar_token"


def current_token(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (_KEY,)).fetchone()
    return row["value"] if row else None


def new_token(conn: sqlite3.Connection) -> str:
    """Φτιάχνει νέο link. Το προηγούμενο παύει αμέσως να λειτουργεί."""
    tok = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (_KEY, tok))
    return tok


def disable(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM meta WHERE key=?", (_KEY,))


def check_token(conn: sqlite3.Connection, token: str) -> bool:
    expected = current_token(conn)
    return bool(expected) and hmac.compare_digest(expected.encode(), token.encode())


def feed_url(token: str) -> str:
    return f"{get_config().base_url}/cal/{token}.ics"


def _escape(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Γραμμές έως 75 bytes (RFC 5545), χωρίς να κόβεται χαρακτήρας UTF-8 στη μέση."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode())
        if size + n > 75:
            out.append(cur)
            cur, size = " ", 1
        cur += ch
        size += n
    out.append(cur)
    return "\r\n".join(out)


def _event(uid: str, stamp: str, day: date, summary: str, description: str, url: str) -> list[str]:
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
        f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        f"URL:{url}",
        "TRANSP:TRANSPARENT",       # ολοήμερο γεγονός που δεν σε δείχνει «απασχολημένο»
        "END:VEVENT",
    ]


def build(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = get_config().base_url
    host = urlsplit(base).hostname or "study"
    marks = {"Ναι": "✅ ", "Σε εξέλιξη": "⏳ "}
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//future-me-will-study//EL", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:future-me-will-study",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for c in services.courses(conn, active_only=True):
        url = f"{base}/c/{c['slug']}"
        for t in services.tasks(conn, c["id"]):
            details = [t["description"]]
            if t["source"] and t["source"] != "-":
                details.append(f"Πηγή: {t['source']}")
            details.append(f"{url}#task-{t['id']}")
            lines += _event(f"task-{t['id']}@{host}", stamp, date.fromisoformat(t["date"]),
                            f"{marks.get(t['status'], '')}{c['name']}: {t['topic'] or 'Εργασία'}",
                            "\n".join(d for d in details if d), f"{url}#task-{t['id']}")
        if services.days_left(c["exam_date"], date.min) is not None:
            lines += _event(f"exam-{c['id']}@{host}", stamp, date.fromisoformat(c["exam_date"]),
                            f"🎯 Εξέταση{' (εκτίμηση)' if c['exam_estimated'] else ''}: {c['name']}", url, url)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
