from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import get_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS course(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    exam_date TEXT,
    color TEXT NOT NULL DEFAULT '#2563eb',
    active INTEGER NOT NULL DEFAULT 1,
    notify INTEGER NOT NULL DEFAULT 1,
    template_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS week(
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    sheet_name TEXT
);
CREATE TABLE IF NOT EXISTS task(
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    week_id INTEGER REFERENCES week(id) ON DELETE SET NULL,
    day_no INTEGER,
    date TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Όχι',
    notes TEXT NOT NULL DEFAULT '',
    completed_at TEXT,
    original_date TEXT,
    file_hints TEXT NOT NULL DEFAULT '[]',
    src_sheet TEXT,
    src_row INTEGER,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS task_course_date ON task(course_id, date);
CREATE TABLE IF NOT EXISTS file(
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    grp TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    stored TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(course_id, sha256)
);
CREATE TABLE IF NOT EXISTS task_file(
    task_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    file_id INTEGER NOT NULL REFERENCES file(id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(task_id, file_id)
);
CREATE TABLE IF NOT EXISTS setting(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_log(
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    course_id INTEGER,
    task_id INTEGER,
    local_date TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_session(
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES task(id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,      -- UTC ISO-8601
    ended_at TEXT,                 -- NULL όσο τρέχει το χρονόμετρο
    seconds INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'timer'   -- timer | manual
);
CREATE INDEX IF NOT EXISTS session_course ON study_session(course_id, started_at);
CREATE INDEX IF NOT EXISTS session_task ON study_session(task_id);
"""

# Στήλες που προστέθηκαν μετά την πρώτη έκδοση: (πίνακας, στήλη, ορισμός)
MIGRATIONS = [
    ("task", "hard", "INTEGER NOT NULL DEFAULT 0"),
    ("task", "reviewed_at", "TEXT"),
    ("course", "weekly_goal_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ("course", "medium_minutes", "INTEGER NOT NULL DEFAULT 60"),
    ("task", "effort", "REAL"),                 # μονάδες μεγέθους (1/2/3)· NULL = μεσαίο
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ddl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def connect() -> sqlite3.Connection:
    cfg = get_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.db_path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db() -> None:
    cfg = get_config()
    for d in (cfg.data_dir, cfg.files_dir, cfg.templates_dir, cfg.data_dir / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency."""
    with db() as conn:
        yield conn
