"""
Unit tests for the spaced-repetition scheduler with mocked dates.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scathach.core.scheduler import (
    STATE_LEARNING,
    STATE_NEW,
    STATE_RELEARNING,
    STATE_REVIEW,
    _next_interval_days,
    _next_stability,
    _next_state,
    update_schedule,
    get_scheduled_questions,
)
from scathach.db.models import Question, Topic
from scathach.db.repository import get_review_entry, insert_question, upsert_topic, upsert_review_entry
from scathach.db.schema import apply_schema, get_connection


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    apply_schema(c)
    return c


@pytest.fixture
def topic(conn: sqlite3.Connection) -> Topic:
    return upsert_topic(conn, Topic(name="Test", content="content"))


@pytest.fixture
def question(conn: sqlite3.Connection, topic: Topic) -> Question:
    return insert_question(
        conn,
        Question(topic_id=topic.id, difficulty=2, body="Q", ideal_answer="A", is_root=True),
    )


# ---------------------------------------------------------------------------
# Stability bracket logic (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("score,initial_s,expected_op", [
    (0,  1.0, "decrease"),
    (3,  1.0, "decrease"),
    (5,  1.0, "decrease"),  # 5–6 → * 0.8 (still less than 1.0 * 1.5)
    (7,  1.0, "increase"),
    (8,  1.0, "increase"),
    (9,  1.0, "increase"),
    (10, 1.0, "increase"),
])
def test_next_stability_direction(score: int, initial_s: float, expected_op: str) -> None:
    new_s = _next_stability(initial_s, score)
    if expected_op == "increase":
        assert new_s > initial_s
    else:
        assert new_s <= initial_s


@pytest.mark.parametrize("score", [0, 1, 2, 3, 4])
def test_next_interval_fail_is_1_day(score: int) -> None:
    assert _next_interval_days(1.0, score) == 1.0


@pytest.mark.parametrize("score", [7, 8, 9, 10])
def test_next_interval_pass_exceeds_1_day(score: int) -> None:
    assert _next_interval_days(1.0, score) > 1.0


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("current,score,expected", [
    (STATE_NEW,        0, STATE_RELEARNING),
    (STATE_NEW,        5, STATE_LEARNING),
    (STATE_NEW,        7, STATE_REVIEW),
    (STATE_LEARNING,   4, STATE_RELEARNING),
    (STATE_LEARNING,   8, STATE_REVIEW),
    (STATE_REVIEW,     3, STATE_RELEARNING),
    (STATE_REVIEW,     9, STATE_REVIEW),
    (STATE_RELEARNING, 7, STATE_REVIEW),
])
def test_state_transitions(current: str, score: int, expected: str) -> None:
    assert _next_state(current, score) == expected


# ---------------------------------------------------------------------------
# update_schedule
# ---------------------------------------------------------------------------


def test_update_schedule_creates_entry(conn: sqlite3.Connection, question: Question) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    entry = update_schedule(conn, question.id, 8, "timed", now=now)
    assert entry.last_score == 8
    assert entry.state == STATE_REVIEW
    assert entry.next_review_at is not None


def test_update_schedule_fail_sets_1_day(conn: sqlite3.Connection, question: Question) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    entry = update_schedule(conn, question.id, 3, "untimed", now=now)
    assert entry.state == STATE_RELEARNING
    # next_review_at may be tz-aware or naive depending on isoformat — strip tz for comparison
    next_dt = datetime.fromisoformat(entry.next_review_at).replace(tzinfo=None)
    now_naive = now.replace(tzinfo=None)
    diff = next_dt - now_naive
    # Should be approximately 1 day (fail → 1-day interval)
    assert 0 < diff.total_seconds() < 2 * 86400


def test_update_schedule_increases_stability_on_good(
    conn: sqlite3.Connection, question: Question
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    e1 = update_schedule(conn, question.id, 9, "timed", now=now)
    e2 = update_schedule(conn, question.id, 9, "timed", now=now + timedelta(days=3))
    assert e2.stability > e1.stability


def test_update_schedule_independent_queues(
    conn: sqlite3.Connection, question: Question
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    update_schedule(conn, question.id, 9, "timed", now=now)
    update_schedule(conn, question.id, 3, "untimed", now=now)
    timed = get_review_entry(conn, question.id, "timed")
    untimed = get_review_entry(conn, question.id, "untimed")
    assert timed.last_score == 9
    assert untimed.last_score == 3
    assert timed.state != untimed.state


# ---------------------------------------------------------------------------
# get_scheduled_questions
# ---------------------------------------------------------------------------


def test_get_scheduled_questions_includes_new(
    conn: sqlite3.Connection, question: Question
) -> None:
    # Not yet in the queue (state='new') → should not appear until added
    # After update_schedule, it appears
    now = datetime(2026, 1, 1, tzinfo=UTC)
    update_schedule(conn, question.id, 9, "timed", now=now)
    # next_review is in the future — should NOT be due yet
    future = now + timedelta(days=1)
    due = get_scheduled_questions(conn, "timed", now=now)
    assert len(due) == 0

    # Query at a future date — should be due
    far_future = now + timedelta(days=100)
    due_later = get_scheduled_questions(conn, "timed", now=far_future)
    assert len(due_later) == 1


def test_get_scheduled_questions_returns_correct_queue(
    conn: sqlite3.Connection, question: Question
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    # Add to timed queue only
    update_schedule(conn, question.id, 9, "timed", now=now)
    due_untimed = get_scheduled_questions(conn, "untimed", now=now + timedelta(days=100))
    assert len(due_untimed) == 0


# ---------------------------------------------------------------------------
# Difficulty range filtering
# ---------------------------------------------------------------------------


def test_get_scheduled_questions_difficulty_range_excludes_out_of_range(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    """Questions outside the requested difficulty range should be excluded."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    far = now + timedelta(days=100)

    # Add a difficulty-2 question (already in fixture as `question`)
    q2 = insert_question(conn, Question(topic_id=topic.id, difficulty=2, body="Q2", ideal_answer="A", is_root=True))
    # Add a difficulty-5 question
    q5 = insert_question(conn, Question(topic_id=topic.id, difficulty=5, body="Q5", ideal_answer="A", is_root=True))

    update_schedule(conn, q2.id, 8, "timed", now=now)
    update_schedule(conn, q5.id, 8, "timed", now=now)

    # Default range (1-2): should only return q2
    due = get_scheduled_questions(conn, "timed", now=far, min_difficulty=1, max_difficulty=2)
    ids = [q.id for q in due]
    assert q2.id in ids
    assert q5.id not in ids

    # Super-review range (3-6): should only return q5
    due_super = get_scheduled_questions(conn, "timed", now=far, min_difficulty=3, max_difficulty=6)
    ids_super = [q.id for q in due_super]
    assert q5.id in ids_super
    assert q2.id not in ids_super


def test_get_scheduled_questions_order_by_score(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    """With order_by_score=True, worst-score questions come first within same difficulty."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    far = now + timedelta(days=100)

    q_good = insert_question(conn, Question(topic_id=topic.id, difficulty=4, body="Q good", ideal_answer="A", is_root=True))
    q_bad  = insert_question(conn, Question(topic_id=topic.id, difficulty=4, body="Q bad",  ideal_answer="A", is_root=True))

    # good question scored 9, bad question scored 3
    update_schedule(conn, q_good.id, 9, "untimed", now=now)
    update_schedule(conn, q_bad.id,  3, "untimed", now=now)

    due = get_scheduled_questions(
        conn, "untimed", now=far,
        min_difficulty=3, max_difficulty=6, order_by_score=True,
    )
    ids = [q.id for q in due]
    # Bad (score 3) should come before good (score 9)
    assert ids.index(q_bad.id) < ids.index(q_good.id)


def test_get_scheduled_questions_default_range_is_1_to_2(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    """Default call (no explicit difficulty args) should only return levels 1-2."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    far = now + timedelta(days=100)

    q1 = insert_question(conn, Question(topic_id=topic.id, difficulty=1, body="Q1", ideal_answer="A", is_root=True))
    q4 = insert_question(conn, Question(topic_id=topic.id, difficulty=4, body="Q4", ideal_answer="A", is_root=True))
    update_schedule(conn, q1.id, 8, "timed", now=now)
    update_schedule(conn, q4.id, 8, "timed", now=now)

    due = get_scheduled_questions(conn, "timed", now=far)
    ids = [q.id for q in due]
    assert q1.id in ids
    assert q4.id not in ids
