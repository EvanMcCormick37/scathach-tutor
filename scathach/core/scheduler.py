"""
Simplified FSRS-inspired spaced repetition scheduler.

Implements the pragmatic score-bracket approximation described in the roadmap.
Full FSRS-5 is a post-MVP extension.

Score brackets (final_score):
  0–4  → relearning: next review in 1 day, stability halved
  5–6  → weak pass: next review in max(1, stability * 0.5) days
  7–8  → good:      next review in stability * 1.5 days
  9–10 → easy:      next review in stability * 2.5 days
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Literal, Optional

from scathach.db.models import ReviewEntry
from scathach.db.repository import (
    get_due_questions,
    get_review_entry,
    upsert_review_entry,
)
from scathach.db.models import Question

# FSRS states
STATE_NEW = "new"
STATE_LEARNING = "learning"
STATE_REVIEW = "review"
STATE_RELEARNING = "relearning"


def _next_stability(current: float, final_score: int) -> float:
    """Return the new stability value based on the score bracket."""
    if final_score <= 4:
        return max(0.25, current * 0.5)
    if final_score <= 6:
        return max(0.5, current * 0.8)
    if final_score <= 8:
        return current * 1.5
    return current * 2.5


def _next_interval_days(stability: float, final_score: int) -> float:
    """Return the review interval in days."""
    if final_score <= 4:
        return 1.0
    if final_score <= 6:
        return max(1.0, stability * 0.5)
    if final_score <= 8:
        return stability * 1.5
    return stability * 2.5


def _next_state(current_state: str, final_score: int) -> str:
    if final_score <= 4:
        return STATE_RELEARNING
    if current_state in (STATE_NEW, STATE_LEARNING):
        return STATE_REVIEW if final_score >= 7 else STATE_LEARNING
    return STATE_REVIEW


def update_schedule(
    conn: sqlite3.Connection,
    question_id: int,
    final_score: int,
    queue: Literal["timed", "untimed"],
    now: Optional[datetime] = None,
) -> ReviewEntry:
    """
    Update the review schedule for a question after an attempt.

    Args:
        conn:         Open SQLite connection.
        question_id:  The question that was just answered.
        final_score:  The final score (0–10) after time penalty.
        queue:        Which queue to update ("timed" or "untimed").
        now:          Current time (injectable for testing; defaults to UTC now).

    Returns:
        The updated ReviewEntry.
    """
    now = now or datetime.now(UTC)
    existing = get_review_entry(conn, question_id, queue)

    current_stability = existing.stability if existing else 1.0
    current_state = existing.state if existing else STATE_NEW

    new_stability = _next_stability(current_stability, final_score)
    interval_days = _next_interval_days(new_stability, final_score)
    next_review = now + timedelta(days=interval_days)
    new_state = _next_state(current_state, final_score)

    entry = ReviewEntry(
        question_id=question_id,
        queue=queue,
        last_score=final_score,
        last_attempted_at=now.isoformat(),
        next_review_at=next_review.isoformat(),
        stability=new_stability,
        difficulty_fsrs=existing.difficulty_fsrs if existing else 0.3,
        state=new_state,
    )
    upsert_review_entry(conn, entry)
    return entry


def get_scheduled_questions(
    conn: sqlite3.Connection,
    queue: Literal["timed", "untimed"],
    limit: int = 20,
    now: Optional[datetime] = None,
    min_difficulty: int = 1,
    max_difficulty: int = 2,
    order_by_score: bool = False,
) -> list[Question]:
    """
    Return questions due for review, filtered by difficulty range.

    Standard `review` sessions use levels 1-2 (default).
    `super_review` sessions call this with min_difficulty=3, max_difficulty=6
    and order_by_score=True so worst performers surface first.

    Args:
        min_difficulty:  Minimum difficulty to include (default 1).
        max_difficulty:  Maximum difficulty to include (default 2 for standard review).
        order_by_score:  If True, sort by (difficulty ASC, last_score ASC) — worst
                         performers within each tier come first. Used for super-review.
    """
    now = now or datetime.now(UTC)
    return get_due_questions(
        conn,
        queue,
        limit=limit,
        now=now,
        min_difficulty=min_difficulty,
        max_difficulty=max_difficulty,
        order_by_score=order_by_score,
    )
