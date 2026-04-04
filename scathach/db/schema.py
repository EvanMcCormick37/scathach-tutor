"""
SQLite schema definitions and migration management.

Schema versioning is done via a simple `schema_version` table.
apply_schema() is idempotent — safe to call on every startup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

CURRENT_SCHEMA_VERSION = 1

# DDL executed in order — every statement is idempotent via CREATE TABLE IF NOT EXISTS
SCHEMA_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    source_path TEXT,
    content     TEXT NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL REFERENCES topics(id),
    parent_id       INTEGER REFERENCES questions(id),
    difficulty      INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 6),
    body            TEXT NOT NULL,
    ideal_answer    TEXT NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_root         BOOLEAN NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id     INTEGER NOT NULL REFERENCES questions(id),
    session_id      TEXT NOT NULL,
    answer_text     TEXT NOT NULL,
    raw_score       INTEGER NOT NULL CHECK (raw_score BETWEEN 0 AND 10),
    final_score     INTEGER NOT NULL CHECK (final_score BETWEEN 0 AND 10),
    time_taken_s    REAL,
    time_penalty    BOOLEAN NOT NULL DEFAULT 0,
    timed           BOOLEAN NOT NULL DEFAULT 0,
    passed          BOOLEAN NOT NULL,
    attempted_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS timed_review_queue (
    question_id         INTEGER PRIMARY KEY REFERENCES questions(id),
    last_score          INTEGER,
    last_attempted_at   DATETIME,
    next_review_at      DATETIME,
    stability           REAL DEFAULT 1.0,
    difficulty_fsrs     REAL DEFAULT 0.3,
    state               TEXT DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS untimed_review_queue (
    question_id         INTEGER PRIMARY KEY REFERENCES questions(id),
    last_score          INTEGER,
    last_attempted_at   DATETIME,
    next_review_at      DATETIME,
    stability           REAL DEFAULT 1.0,
    difficulty_fsrs     REAL DEFAULT 0.3,
    state               TEXT DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    topic_id        INTEGER NOT NULL REFERENCES topics(id),
    status          TEXT NOT NULL DEFAULT 'active',
    timing          TEXT NOT NULL DEFAULT 'untimed',
    threshold       INTEGER NOT NULL DEFAULT 7,
    num_levels      INTEGER NOT NULL DEFAULT 6,
    question_stack  TEXT,
    cleared_ids     TEXT,
    root_ids        TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with row_factory set to Row."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist and record schema version."""
    conn.executescript(SCHEMA_DDL)

    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = row[0] if row and row[0] is not None else 0

    if current < CURRENT_SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (CURRENT_SCHEMA_VERSION,),
        )
        conn.commit()


def open_db(db_path: Path | str) -> sqlite3.Connection:
    """Open (or create) the database and apply the current schema."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    apply_schema(conn)
    return conn
