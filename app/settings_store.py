"""Ρυθμίσεις που αλλάζουν από το dashboard. Διαβάζονται σε κάθε tick του scheduler, άρα ισχύουν χωρίς restart."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import get_config

WEEKDAYS = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]
HM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_EVENING = 4


def defaults() -> dict[str, Any]:
    cfg = get_config()
    return {
        "timezone": cfg.default_tz,
        "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "morning_enabled": True,
        "morning_time": "08:00",
        "evening_enabled": True,
        "evening_times": ["20:00", "22:30"],
        "weekly_enabled": True,
        "weekly_day": 6,
        "weekly_time": "20:00",
        "overdue_enabled": True,
        "countdown_enabled": True,
        "streak_enabled": True,
        "quiet_enabled": False,
        "quiet_start": "23:30",
        "quiet_end": "07:00",
        "grace_minutes": 60,
        "ntfy_url": cfg.ntfy_url_default,
        "ntfy_topic": cfg.ntfy_topic_default,
        "ntfy_token": "",
        "priority": 3,
        "evening_priority": 4,
    }


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    s = defaults()
    for row in conn.execute("SELECT key, value FROM setting"):
        if row["key"] in s:
            try:
                s[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                pass
    return s


def effective_token(s: dict[str, Any]) -> str:
    return s.get("ntfy_token") or get_config().ntfy_token_default


def save_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    known = defaults()
    for k, v in values.items():
        if k in known:
            conn.execute(
                "INSERT INTO setting(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, json.dumps(v, ensure_ascii=False)),
            )


def _hm(v: str, label: str, errors: list[str]) -> str:
    v = (v or "").strip()
    if len(v) == 4 and v[1] == ":":
        v = "0" + v
    if not HM_RE.match(v):
        errors.append(f"Μη έγκυρη ώρα στο «{label}»: {v or '(κενό)'}")
    return v


def parse_form(form: dict[str, Any], getlist) -> tuple[dict[str, Any], list[str]]:
    """Μετατρέπει τη φόρμα ρυθμίσεων σε dict. Επιστρέφει (τιμές, λάθη)."""
    errors: list[str] = []
    out: dict[str, Any] = {}
    tz = (form.get("timezone") or "").strip()
    try:
        ZoneInfo(tz)
        out["timezone"] = tz
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"Άγνωστη ζώνη ώρας: {tz}")
    days = sorted({int(d) for d in getlist("weekdays") if str(d).isdigit() and 0 <= int(d) <= 6})
    out["weekdays"] = days
    for flag in ("morning_enabled", "evening_enabled", "weekly_enabled", "overdue_enabled",
                 "countdown_enabled", "streak_enabled", "quiet_enabled"):
        out[flag] = form.get(flag) == "on"
    out["morning_time"] = _hm(form.get("morning_time", ""), "Πρωινή", errors)
    try:
        count = max(0, min(MAX_EVENING, int(form.get("evening_count", "0"))))
    except ValueError:
        count = 0
    out["evening_times"] = [
        _hm(form.get(f"evening_time_{i}", ""), f"Βραδινή {i + 1}", errors) for i in range(count)
    ]
    try:
        out["weekly_day"] = max(0, min(6, int(form.get("weekly_day", "6"))))
    except ValueError:
        errors.append("Μη έγκυρη ημέρα σύνοψης")
    out["weekly_time"] = _hm(form.get("weekly_time", ""), "Εβδομαδιαία σύνοψη", errors)
    out["quiet_start"] = _hm(form.get("quiet_start", ""), "Ησυχία από", errors)
    out["quiet_end"] = _hm(form.get("quiet_end", ""), "Ησυχία έως", errors)
    for key, lo, hi, label in (("grace_minutes", 0, 720, "Ανοχή"), ("priority", 1, 5, "Προτεραιότητα"),
                               ("evening_priority", 1, 5, "Προτεραιότητα βραδινής")):
        try:
            val = int(form.get(key, ""))
            if not lo <= val <= hi:
                raise ValueError
            out[key] = val
        except ValueError:
            errors.append(f"Το «{label}» πρέπει να είναι από {lo} έως {hi}")
    url = (form.get("ntfy_url") or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", url):
        errors.append("Το ntfy server πρέπει να ξεκινά με http:// ή https://")
    out["ntfy_url"] = url
    topic = (form.get("ntfy_topic") or "").strip()
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", topic):
        errors.append("Το topic επιτρέπει μόνο λατινικά, αριθμούς, - και _ (έως 64)")
    out["ntfy_topic"] = topic
    token = (form.get("ntfy_token") or "").strip()
    if form.get("ntfy_token_clear") == "on":
        out["ntfy_token"] = ""
    elif token:  # κενό πεδίο = κράτα το υπάρχον
        out["ntfy_token"] = token
    return out, errors
