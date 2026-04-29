"""
Topic-level support scheduling.

Two metrics track how well a student knows a topic:

  exam_support     — updated by closed-book (--exam) sessions. Follows FSRS-style
                     stability brackets: multiplied up on success, halved on failure.
                     Represents demonstrated unaided recall.

  practice_support — updated by open-book sessions. A running accumulator: each
                     answered root question adds or subtracts a difficulty-scaled
                     delta. Represents consistency of open-book performance.

The effective review interval when finalizing a topic review is:

    days = exam_support + sigmoid(practice_support) * MAX_PRACTICE_SUPPORT

next_review_at is ONLY written when the user completes a topic-review session
(scathach review --topics), not after every question answer.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta

from scathach.db.repository import get_topic_by_id, update_topic_supports

MAX_PRACTICE_SUPPORT = 14.0  # sigmoid asymptote in days (2 weeks)
_MIN_EXAM_SUPPORT = 0.25


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# ---------------------------------------------------------------------------
# Exam support (closed-book)
# ---------------------------------------------------------------------------

def compute_new_exam_support(current: float, final_score: int) -> float:
    """
    Update exam_support using FSRS-style stability brackets.

    Score 0–4 (fail):    halve, floor 0.25
    Score 5–6 (weak):    ×0.8, floor 0.5
    Score 7–8 (pass):    ×1.5
    Score 9–10 (strong): ×2.0
    """
    if final_score <= 4:
        return max(_MIN_EXAM_SUPPORT, current * 0.5)
    if final_score <= 6:
        return max(_MIN_EXAM_SUPPORT * 2, current * 0.8)
    if final_score <= 8:
        return current * 1.5
    return current * 2.0


# ---------------------------------------------------------------------------
# Practice support (open-book)
# ---------------------------------------------------------------------------

def compute_practice_delta(
    passed: bool,
    question_difficulty: int,
    target_level: int,
) -> float:
    """
    Return the signed delta to apply to practice_support.

    Above target level: +1.0 on pass, 0.0 on fail (no penalty for struggling above target).
    At target level:    ±1.0.
    n levels below:     ±(1/3)^n.
    """
    if question_difficulty > target_level:
        return 1.0 if passed else 0.0
    n = target_level - question_difficulty
    delta = (1.0 / 3.0) ** n
    return delta if passed else -delta


# ---------------------------------------------------------------------------
# Finalization — only called on topic-review completion
# ---------------------------------------------------------------------------

def finalize_topic_next_review(
    conn: sqlite3.Connection,
    topic_id: int,
    max_practice_support: float = MAX_PRACTICE_SUPPORT,
) -> None:
    """
    Compute next_review_at from current support values and persist it.

    days = exam_support + sigmoid(practice_support) * max_practice_support

    Call this once per topic after its topic-review quest completes.
    Do NOT call it after every question answer.
    """
    topic = get_topic_by_id(conn, topic_id)
    if topic is None:
        return
    combined = topic.exam_support + _sigmoid(topic.practice_support) * max_practice_support
    next_review_at = (datetime.now(UTC) + timedelta(days=combined)).isoformat()
    conn.execute(
        "UPDATE topics SET next_review_at = ? WHERE id = ?",
        (next_review_at, topic_id),
    )
    conn.commit()
