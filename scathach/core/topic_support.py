"""
Topic-level FSRS support scheduling.

Each time a student answers a root question for the first time, the parent
topic's support value is updated using the same stability brackets as the
per-question FSRS scheduler, scaled by difficulty relative to the topic's
target level:

    scale = (1/3) ^ max(0, target_level - question_difficulty)

Questions above target_level use scale=1.0 for correct answers, and carry
no penalty on incorrect answers. Support is bounded below by MIN_SUPPORT.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from scathach.core.scheduler import _next_stability
from scathach.db.repository import update_topic_support

MIN_SUPPORT = 1.0


def compute_new_support(
    current_support: float,
    final_score: int,
    question_difficulty: int,
    target_level: int,
) -> float:
    """
    Return the updated topic support after answering a new root question.

    - At or above target_level: full FSRS stability delta applied.
    - Below target_level: delta scaled by (1/3)^(target_level - difficulty).
    - Incorrect (score below threshold) above target_level: no change.
    - Support never drops below MIN_SUPPORT.
    """
    is_above_target = question_difficulty > target_level
    effective_level = min(question_difficulty, target_level)
    scale = (1 / 3) ** (target_level - effective_level)

    full_new = _next_stability(current_support, final_score)
    delta = full_new - current_support

    if delta < 0 and is_above_target:
        return current_support

    return max(MIN_SUPPORT, current_support + delta * scale)


def apply_topic_support_update(
    conn: sqlite3.Connection,
    topic_id: int,
    new_support: float,
) -> None:
    """Persist the new support value and compute next_review_at = now + support days."""
    next_review_at = (
        datetime.now(UTC) + timedelta(days=new_support)
    ).isoformat()
    update_topic_support(conn, topic_id, new_support, next_review_at)
