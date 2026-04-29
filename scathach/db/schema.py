"""
SQLite schema definitions and migration management.

Schema versioning is done via a simple `schema_version` table.
apply_schema() is idempotent — safe to call on every startup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

CURRENT_SCHEMA_VERSION = 5

# DDL executed in order — every statement is idempotent via CREATE TABLE IF NOT EXISTS
SCHEMA_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    source_path      TEXT,
    content          TEXT NOT NULL,
    exam_support     REAL NOT NULL DEFAULT 1.0,
    practice_support REAL NOT NULL DEFAULT 0.0,
    next_review_at   TEXT,
    target_level     INTEGER NOT NULL DEFAULT 4,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL REFERENCES topics(id),
    parent_id       INTEGER REFERENCES questions(id),
    session_id      TEXT REFERENCES sessions(id),
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
    session_type    TEXT NOT NULL DEFAULT 'quest',
    timing          TEXT NOT NULL DEFAULT 'untimed',
    threshold       INTEGER NOT NULL DEFAULT 7,
    num_levels      INTEGER NOT NULL DEFAULT 6,
    is_exam         BOOLEAN NOT NULL DEFAULT 0,
    drill_level     INTEGER,
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


# ALTER TABLE migrations for each schema version bump.
# Each list entry is executed exactly once when upgrading through that version.
# Wrapped in try/except so re-runs on an already-upgraded DB are harmless.
_MIGRATIONS: dict[int, list[str]] = {
    2: [
        "ALTER TABLE topics ADD COLUMN support REAL NOT NULL DEFAULT 1.0",
        "ALTER TABLE topics ADD COLUMN next_review_at TEXT",
        "ALTER TABLE topics ADD COLUMN target_level INTEGER NOT NULL DEFAULT 4",
    ],
    3: [
        "ALTER TABLE topics ADD COLUMN exam_support REAL NOT NULL DEFAULT 1.0",
        "ALTER TABLE topics ADD COLUMN practice_support REAL NOT NULL DEFAULT 0.0",
        # Migrate existing support values into exam_support (no-op on new DBs — caught by try/except)
        "UPDATE topics SET exam_support = support",
        "ALTER TABLE sessions ADD COLUMN is_exam BOOLEAN NOT NULL DEFAULT 0",
    ],
    4: [
        "ALTER TABLE sessions ADD COLUMN session_type TEXT NOT NULL DEFAULT 'quest'",
        "ALTER TABLE sessions ADD COLUMN drill_level INTEGER",
    ],
    5: [
        "ALTER TABLE questions ADD COLUMN session_id TEXT REFERENCES sessions(id)",
    ],
}


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist, then run any pending migrations."""
    conn.executescript(SCHEMA_DDL)

    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = row[0] if row and row[0] is not None else 0

    for version in sorted(_MIGRATIONS):
        if current < version:
            for stmt in _MIGRATIONS[version]:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # column already exists (e.g. fresh DB created from updated DDL)
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
            )
            conn.commit()
            current = version


def open_db(db_path: Path | str) -> sqlite3.Connection:
    """Open (or create) the database and apply the current schema."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    apply_schema(conn)
    return conn
