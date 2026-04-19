"""
CRUD operations for the scathach SQLite database.

All functions accept a sqlite3.Connection so callers control the connection
lifecycle (useful for testing with in-memory DBs).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal, Optional

from scathach.db.models import Attempt, Question, ReviewEntry, SessionRecord, Topic


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


def upsert_topic(conn: sqlite3.Connection, topic: Topic) -> Topic:
    """Insert or replace a topic by name. Returns the topic with id set."""
    cursor = conn.execute(
        """
        INSERT INTO topics (name, source_path, content)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            source_path = excluded.source_path,
            content     = excluded.content
        RETURNING id, created_at
        """,
        (topic.name, topic.source_path, topic.content),
    )
    row = cursor.fetchone()
    conn.commit()
    topic.id = row["id"]
    topic.created_at = row["created_at"]
    return topic


def get_topic_by_name(conn: sqlite3.Connection, name: str) -> Optional[Topic]:
    """Fetch a topic by name, or None if not found."""
    row = conn.execute(
        "SELECT id, name, source_path, content, created_at FROM topics WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        return None
    return Topic(
        id=row["id"],
        name=row["name"],
        source_path=row["source_path"],
        content=row["content"],
        created_at=row["created_at"],
    )


def get_topic_by_id(conn: sqlite3.Connection, topic_id: int) -> Optional[Topic]:
    """Fetch a topic by id, or None if not found."""
    row = conn.execute(
        "SELECT id, name, source_path, content, created_at FROM topics WHERE id = ?",
        (topic_id,),
    ).fetchone()
    if row is None:
        return None
    return Topic(
        id=row["id"],
        name=row["name"],
        source_path=row["source_path"],
        content=row["content"],
        created_at=row["created_at"],
    )


def list_topics(conn: sqlite3.Connection) -> list[Topic]:
    """Return all topics ordered by created_at desc."""
    rows = conn.execute(
        "SELECT id, name, source_path, content, created_at FROM topics ORDER BY created_at DESC"
    ).fetchall()
    return [
        Topic(
            id=r["id"],
            name=r["name"],
            source_path=r["source_path"],
            content=r["content"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def insert_question(conn: sqlite3.Connection, question: Question) -> Question:
    """Insert a new question and return it with id set."""
    cursor = conn.execute(
        """
        INSERT INTO questions (topic_id, parent_id, difficulty, body, ideal_answer, is_root)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id, created_at
        """,
        (
            question.topic_id,
            question.parent_id,
            question.difficulty,
            question.body,
            question.ideal_answer,
            int(question.is_root),
        ),
    )
    row = cursor.fetchone()
    conn.commit()
    question.id = row["id"]
    question.created_at = row["created_at"]
    return question


def get_question(conn: sqlite3.Connection, question_id: int) -> Optional[Question]:
    """Fetch a question by id."""
    row = conn.execute(
        """
        SELECT id, topic_id, parent_id, difficulty, body, ideal_answer, is_root, created_at
        FROM questions WHERE id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_question(row)


def delete_question(conn: sqlite3.Connection, question_id: int) -> None:
    """
    Permanently delete a question and all of its descendants (Hydra sub-questions),
    along with their attempts and review-queue entries.

    Uses a recursive CTE so the full subtree is handled regardless of depth.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT ?
            UNION ALL
            SELECT q.id FROM questions q
            JOIN descendants d ON q.parent_id = d.id
        )
        SELECT id FROM descendants
        """,
        (question_id,),
    ).fetchall()

    all_ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(all_ids))

    conn.execute(f"DELETE FROM timed_review_queue   WHERE question_id IN ({placeholders})", all_ids)
    conn.execute(f"DELETE FROM untimed_review_queue WHERE question_id IN ({placeholders})", all_ids)
    conn.execute(f"DELETE FROM attempts             WHERE question_id IN ({placeholders})", all_ids)
    # Delete children before parents to satisfy the self-referential FK constraint.
    # Auto-increment guarantees children always have higher IDs than their parents.
    for qid in sorted(all_ids, reverse=True):
        conn.execute("DELETE FROM questions WHERE id = ?", (qid,))
    conn.commit()


def get_children(conn: sqlite3.Connection, question_id: int) -> list[Question]:
    """Return all direct child questions of a given question."""
    rows = conn.execute(
        """
        SELECT id, topic_id, parent_id, difficulty, body, ideal_answer, is_root, created_at
        FROM questions WHERE parent_id = ? ORDER BY difficulty ASC
        """,
        (question_id,),
    ).fetchall()
    return [_row_to_question(r) for r in rows]


def get_questions_by_difficulty(
    conn: sqlite3.Connection,
    topic_id: int,
    difficulty: int,
) -> list[Question]:
    """Return all questions (root and sub) for a topic at a specific difficulty level."""
    rows = conn.execute(
        """
        SELECT id, topic_id, parent_id, difficulty, body, ideal_answer, is_root, created_at
        FROM questions WHERE topic_id = ? AND difficulty = ?
        ORDER BY created_at ASC
        """,
        (topic_id, difficulty),
    ).fetchall()
    return [_row_to_question(r) for r in rows]


def get_questions_below_difficulty(
    conn: sqlite3.Connection,
    topic_id: int,
    max_difficulty_exclusive: int,
) -> list[Question]:
    """Return all questions (root and sub) for a topic with difficulty < max_difficulty_exclusive."""
    rows = conn.execute(
        """
        SELECT id, topic_id, parent_id, difficulty, body, ideal_answer, is_root, created_at
        FROM questions WHERE topic_id = ? AND difficulty < ?
        ORDER BY difficulty ASC, created_at ASC
        """,
        (topic_id, max_difficulty_exclusive),
    ).fetchall()
    return [_row_to_question(r) for r in rows]


def get_root_questions(conn: sqlite3.Connection, topic_id: int) -> list[Question]:
    """Return all root questions for a topic, ordered by difficulty."""
    rows = conn.execute(
        """
        SELECT id, topic_id, parent_id, difficulty, body, ideal_answer, is_root, created_at
        FROM questions WHERE topic_id = ? AND is_root = 1
        ORDER BY difficulty ASC
        """,
        (topic_id,),
    ).fetchall()
    return [_row_to_question(r) for r in rows]


def get_prior_root_questions(
    conn: sqlite3.Connection,
    topic_id: int,
    limit_per_level: int = 25,
) -> list[Question]:
    """
    Return up to `limit_per_level` previously asked root questions per difficulty
    level for the given topic, ordered most-recent-first within each level.

    Used during question generation to prevent the LLM from repeating questions
    across sessions.
    """
    rows = conn.execute(
        """
        SELECT id, topic_id, parent_id, difficulty, body, ideal_answer, is_root, created_at
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY difficulty ORDER BY created_at DESC, id DESC
                   ) AS rn
            FROM questions
            WHERE topic_id = ? AND is_root = 1
        )
        WHERE rn <= ?
        ORDER BY difficulty ASC, created_at DESC
        """,
        (topic_id, limit_per_level),
    ).fetchall()
    return [_row_to_question(r) for r in rows]


def rename_topic(
    conn: sqlite3.Connection,
    old_name: str,
    new_name: str,
) -> Optional[Topic]:
    """
    Rename a topic. Returns the updated Topic, or None if old_name is not found.
    Raises sqlite3.IntegrityError if new_name is already taken.
    """
    conn.execute(
        "UPDATE topics SET name = ? WHERE name = ?",
        (new_name, old_name),
    )
    conn.commit()
    return get_topic_by_name(conn, new_name)


def _row_to_question(row: sqlite3.Row) -> Question:
    return Question(
        id=row["id"],
        topic_id=row["topic_id"],
        parent_id=row["parent_id"],
        difficulty=row["difficulty"],
        body=row["body"],
        ideal_answer=row["ideal_answer"],
        is_root=bool(row["is_root"]),
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------


def record_attempt(conn: sqlite3.Connection, attempt: Attempt) -> Attempt:
    """Insert an attempt record and return it with id set."""
    cursor = conn.execute(
        """
        INSERT INTO attempts
            (question_id, session_id, answer_text, raw_score, final_score,
             time_taken_s, time_penalty, timed, passed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, attempted_at
        """,
        (
            attempt.question_id,
            attempt.session_id,
            attempt.answer_text,
            attempt.raw_score,
            attempt.final_score,
            attempt.time_taken_s,
            int(attempt.time_penalty),
            int(attempt.timed),
            int(attempt.passed),
        ),
    )
    row = cursor.fetchone()
    conn.commit()
    attempt.id = row["id"]
    attempt.attempted_at = row["attempted_at"]
    return attempt


def get_latest_attempt(
    conn: sqlite3.Connection, question_id: int
) -> Optional[Attempt]:
    """Fetch the most recent attempt for a question."""
    row = conn.execute(
        """
        SELECT id, question_id, session_id, answer_text, raw_score, final_score,
               time_taken_s, time_penalty, timed, passed, attempted_at
        FROM attempts
        WHERE question_id = ?
        ORDER BY attempted_at DESC, id DESC
        LIMIT 1
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_attempt(row)


def get_attempts_for_question(
    conn: sqlite3.Connection, question_id: int
) -> list[Attempt]:
    """Return all attempts for a question, newest first."""
    rows = conn.execute(
        """
        SELECT id, question_id, session_id, answer_text, raw_score, final_score,
               time_taken_s, time_penalty, timed, passed, attempted_at
        FROM attempts WHERE question_id = ? ORDER BY attempted_at DESC
        """,
        (question_id,),
    ).fetchall()
    return [_row_to_attempt(r) for r in rows]


def _row_to_attempt(row: sqlite3.Row) -> Attempt:
    return Attempt(
        id=row["id"],
        question_id=row["question_id"],
        session_id=row["session_id"],
        answer_text=row["answer_text"],
        raw_score=row["raw_score"],
        final_score=row["final_score"],
        time_taken_s=row["time_taken_s"],
        time_penalty=bool(row["time_penalty"]),
        timed=bool(row["timed"]),
        passed=bool(row["passed"]),
        attempted_at=row["attempted_at"],
    )


# ---------------------------------------------------------------------------
# Review Queues
# ---------------------------------------------------------------------------

_QUEUE_TABLES = {
    "timed": "timed_review_queue",
    "untimed": "untimed_review_queue",
}


def _queue_table(queue: Literal["timed", "untimed"]) -> str:
    return _QUEUE_TABLES[queue]


def upsert_review_entry(conn: sqlite3.Connection, entry: ReviewEntry) -> None:
    """Insert or update a review queue entry."""
    table = _queue_table(entry.queue)
    conn.execute(
        f"""
        INSERT INTO {table}
            (question_id, last_score, last_attempted_at, next_review_at,
             stability, difficulty_fsrs, state)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(question_id) DO UPDATE SET
            last_score        = excluded.last_score,
            last_attempted_at = excluded.last_attempted_at,
            next_review_at    = excluded.next_review_at,
            stability         = excluded.stability,
            difficulty_fsrs   = excluded.difficulty_fsrs,
            state             = excluded.state
        """,
        (
            entry.question_id,
            entry.last_score,
            entry.last_attempted_at,
            entry.next_review_at,
            entry.stability,
            entry.difficulty_fsrs,
            entry.state,
        ),
    )
    conn.commit()


def get_review_entry(
    conn: sqlite3.Connection,
    question_id: int,
    queue: Literal["timed", "untimed"],
) -> Optional[ReviewEntry]:
    """Fetch the review entry for a specific question from the given queue."""
    table = _queue_table(queue)
    row = conn.execute(
        f"""
        SELECT question_id, last_score, last_attempted_at, next_review_at,
               stability, difficulty_fsrs, state
        FROM {table} WHERE question_id = ?
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_review_entry(row, queue)


def get_due_questions(
    conn: sqlite3.Connection,
    queue: Literal["timed", "untimed"],
    limit: int = 20,
    now: Optional[datetime] = None,
    min_difficulty: int = 1,
    max_difficulty: int = 6,
    order_by_score: bool = False,
) -> list[Question]:
    """
    Return questions due for review from the specified queue.

    Questions where next_review_at <= now are considered due.
    New entries (next_review_at IS NULL) are always included.

    Args:
        min_difficulty:  Only return questions at this difficulty or above (default 1).
        max_difficulty:  Only return questions at this difficulty or below (default 6).
        order_by_score:  If True, sort by (difficulty ASC, last_score ASC) so worst
                         performers within each difficulty tier appear first.
                         If False (default), sort by difficulty ASC only.
    """
    table = _queue_table(queue)
    now_str = (now or datetime.now(UTC)).isoformat()
    order_clause = "q.difficulty ASC, COALESCE(rq.last_score, 0) ASC" if order_by_score else "q.difficulty ASC"
    rows = conn.execute(
        f"""
        SELECT q.id, q.topic_id, q.parent_id, q.difficulty, q.body,
               q.ideal_answer, q.is_root, q.created_at
        FROM {table} rq
        JOIN questions q ON q.id = rq.question_id
        WHERE (rq.next_review_at IS NULL OR rq.next_review_at <= ?)
          AND q.difficulty BETWEEN ? AND ?
        ORDER BY {order_clause}
        LIMIT ?
        """,
        (now_str, min_difficulty, max_difficulty, limit),
    ).fetchall()
    return [_row_to_question(r) for r in rows]


def _row_to_review_entry(
    row: sqlite3.Row, queue: Literal["timed", "untimed"]
) -> ReviewEntry:
    return ReviewEntry(
        question_id=row["question_id"],
        queue=queue,
        last_score=row["last_score"],
        last_attempted_at=row["last_attempted_at"],
        next_review_at=row["next_review_at"],
        stability=row["stability"],
        difficulty_fsrs=row["difficulty_fsrs"],
        state=row["state"],
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session_record(conn: sqlite3.Connection, record: SessionRecord) -> SessionRecord:
    """Insert a new session row and return it."""
    conn.execute(
        """
        INSERT INTO sessions
            (id, topic_id, status, timing, threshold, num_levels,
             question_stack, cleared_ids, root_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.session_id,
            record.topic_id,
            record.status,
            record.timing,
            record.threshold,
            record.num_levels,
            record.question_stack,
            record.cleared_ids,
            record.root_ids,
        ),
    )
    conn.commit()
    return record


def update_session_state(
    conn: sqlite3.Connection,
    session_id: str,
    question_stack: str,
    cleared_ids: str,
) -> None:
    """Persist current question stack and cleared list for an active session."""
    conn.execute(
        """
        UPDATE sessions
        SET question_stack = ?, cleared_ids = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (question_stack, cleared_ids, session_id),
    )
    conn.commit()


def delete_session(conn: sqlite3.Connection, session_id: str) -> int:
    """
    Permanently delete a session and every question generated for it.

    Deletes all root questions (and their Hydra descendants) that were created
    as part of this session, then removes the session row itself.

    Returns the number of root questions deleted.
    """
    import json as _json

    row = conn.execute(
        "SELECT root_ids FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return 0

    root_ids: list[int] = _json.loads(row["root_ids"]) if row["root_ids"] else []
    for qid in root_ids:
        delete_question(conn, qid)

    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    return len(root_ids)


def complete_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Mark a session as complete."""
    conn.execute(
        "UPDATE sessions SET status = 'complete', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (session_id,),
    )
    conn.commit()


def get_session_record(
    conn: sqlite3.Connection, session_id: str
) -> Optional[SessionRecord]:
    """Fetch a session record by ID."""
    row = conn.execute(
        """
        SELECT id, topic_id, status, timing, threshold, num_levels,
               question_stack, cleared_ids, root_ids, created_at, updated_at
        FROM sessions WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def list_active_sessions(conn: sqlite3.Connection) -> list[SessionRecord]:
    """Return all sessions with status='active', newest first."""
    rows = conn.execute(
        """
        SELECT id, topic_id, status, timing, threshold, num_levels,
               question_stack, cleared_ids, root_ids, created_at, updated_at
        FROM sessions WHERE status = 'active' ORDER BY created_at DESC
        """,
    ).fetchall()
    return [_row_to_session(r) for r in rows]


def _row_to_session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["id"],
        topic_id=row["topic_id"],
        status=row["status"],
        timing=row["timing"],
        threshold=row["threshold"],
        num_levels=row["num_levels"],
        question_stack=row["question_stack"],
        cleared_ids=row["cleared_ids"],
        root_ids=row["root_ids"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
