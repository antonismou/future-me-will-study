"""Αυτόματη σύνδεση εργασιών με αρχεία βάσει ονομάτων (π.χ. «L07», «Lecture 8.pdf», «Διάλεξη 7 - Ασκήσεις»)."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Iterable, Mapping

EXT_RE = re.compile(r"\.(pdf|jpe?g|png|webp|gif|txt|md)$", re.I)


def norm(s: str) -> str:
    s = EXT_RE.sub("", s.strip())
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).replace("ς", "σ")
    s = re.sub(r"[_\s]+", " ", s)
    return s.strip(" -/\\")


def split_source(source: str) -> list[str]:
    out = []
    for part in re.split(r"\s*\+\s*|\s*,\s*", source or ""):
        part = part.strip().rstrip("/\\")
        if not part or part == "-":
            continue
        out.append(re.split(r"[\\/]", part)[-1].strip())
    return [p for p in out if p]


def match_files(hint: str, files: Iterable[Mapping]) -> list[Mapping]:
    h = norm(hint)
    if not h:
        return []
    exact, prefix, group = [], [], []
    for f in files:
        stem = norm(f["name"])
        if stem == h:
            exact.append(f)
        elif stem.startswith(h) and not stem[len(h)].isdigit():
            prefix.append(f)
        elif f["grp"] and norm(f["grp"]) == h:
            group.append(f)
    return exact or prefix or group


def relink_course(conn: sqlite3.Connection, course_id: int) -> int:
    """Προσθέτει συνδέσεις που λείπουν. Δεν αφαιρεί χειροκίνητες, δεν αλλάζει υπάρχον κύριο αρχείο."""
    files = [dict(r) for r in conn.execute("SELECT id, name, grp, mime FROM file WHERE course_id=?", (course_id,))]
    if not files:
        return 0
    added = 0
    for t in conn.execute("SELECT id, file_hints FROM task WHERE course_id=?", (course_id,)).fetchall():
        try:
            hints = json.loads(t["file_hints"] or "[]")
        except json.JSONDecodeError:
            hints = []
        linked = {r["file_id"]: r["is_primary"] for r in
                  conn.execute("SELECT file_id, is_primary FROM task_file WHERE task_id=?", (t["id"],))}
        has_primary = any(linked.values())
        for hint in hints:
            for f in match_files(hint, files):
                if f["id"] in linked:
                    continue
                primary = not has_primary and f["mime"] == "application/pdf"
                conn.execute("INSERT INTO task_file(task_id, file_id, is_primary) VALUES(?,?,?)",
                             (t["id"], f["id"], int(primary)))
                linked[f["id"]] = int(primary)
                has_primary = has_primary or primary
                added += 1
    return added
