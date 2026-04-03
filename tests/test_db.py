"""
Unit tests for the database layer using an in-memory SQLite DB.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from scathach.db.models import Attempt, Question, ReviewEntry, Topic
from scathach.db.repository import (
    get_children,
    get_due_questions,
    get_latest_attempt,
    get_review_entry,
    get_root_questions,
    get_topic_by_id,
    get_topic_by_name,
    insert_question,
    list_topics,
    record_attempt,
    upsert_review_entry,
    upsert_topic,
)
from scathach.db.schema import apply_schema, get_connection


@pytest.fixture
def conn() -> sqlite3.Connection:
    """Provide a fresh in-memory DB with schema applied."""
    c = get_connection(":memory:")
    apply_schema(c)
    return c


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


def test_upsert_topic_insert(conn: sqlite3.Connection) -> None:
    t = upsert_topic(conn, Topic(name="Physics 101", content="Force = ma"))
    assert t.id is not None
    assert t.name == "Physics 101"


def test_upsert_topic_update(conn: sqlite3.Connection) -> None:
    upsert_topic(conn, Topic(name="Physics 101", content="old content"))
    t2 = upsert_topic(conn, Topic(name="Physics 101", content="new content"))
    assert t2.content == "new content"
    # Should still be one row
    assert len(list_topics(conn)) == 1


def test_get_topic_by_name_found(conn: sqlite3.Connection) -> None:
    upsert_topic(conn, Topic(name="Chemistry", content="H2O"))
    t = get_topic_by_name(conn, "Chemistry")
    assert t is not None
    assert t.name == "Chemistry"


def test_get_topic_by_name_missing(conn: sqlite3.Connection) -> None:
    assert get_topic_by_name(conn, "Nonexistent") is None


def test_get_topic_by_id(conn: sqlite3.Connection) -> None:
    inserted = upsert_topic(conn, Topic(name="Bio", content="DNA"))
    fetched = get_topic_by_id(conn, inserted.id)
    assert fetched is not None
    assert fetched.name == "Bio"


def test_list_topics_empty(conn: sqlite3.Connection) -> None:
    assert list_topics(conn) == []


def test_list_topics_multiple(conn: sqlite3.Connection) -> None:
    upsert_topic(conn, Topic(name="A", content="a"))
    upsert_topic(conn, Topic(name="B", content="b"))
    topics = list_topics(conn)
    assert len(topics) == 2


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@pytest.fixture
def topic(conn: sqlite3.Connection) -> Topic:
    return upsert_topic(conn, Topic(name="Test Topic", content="content"))


def test_insert_question(conn: sqlite3.Connection, topic: Topic) -> None:
    q = insert_question(
        conn,
        Question(
            topic_id=topic.id,
            difficulty=1,
            body="What is force?",
            ideal_answer="F = ma",
            is_root=True,
        ),
    )
    assert q.id is not None
    assert q.difficulty == 1


def test_get_children(conn: sqlite3.Connection, topic: Topic) -> None:
    parent = insert_question(
        conn,
        Question(topic_id=topic.id, difficulty=3, body="parent", ideal_answer="ans", is_root=True),
    )
    child1 = insert_question(
        conn,
        Question(topic_id=topic.id, difficulty=2, body="child1", ideal_answer="a1", parent_id=parent.id),
    )
    child2 = insert_question(
        conn,
        Question(topic_id=topic.id, difficulty=2, body="child2", ideal_answer="a2", parent_id=parent.id),
    )
    children = get_children(conn, parent.id)
    assert len(children) == 2
    assert all(c.parent_id == parent.id for c in children)


def test_get_root_questions(conn: sqlite3.Connection, topic: Topic) -> None:
    for d in range(1, 7):
        insert_question(
            conn,
            Question(topic_id=topic.id, difficulty=d, body=f"Q{d}", ideal_answer="A", is_root=True),
        )
    # Also add a non-root
    root = get_root_questions(conn, topic.id)[0]
    insert_question(
        conn,
        Question(topic_id=topic.id, difficulty=1, body="sub", ideal_answer="A", parent_id=root.id),
    )
    roots = get_root_questions(conn, topic.id)
    assert len(roots) == 6
    assert all(q.is_root for q in roots)


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------


@pytest.fixture
def question(conn: sqlite3.Connection, topic: Topic) -> Question:
    return insert_question(
        conn,
        Question(topic_id=topic.id, difficulty=2, body="Q", ideal_answer="A", is_root=True),
    )


def test_record_attempt(conn: sqlite3.Connection, question: Question) -> None:
    a = record_attempt(
        conn,
        Attempt(
            question_id=question.id,
            session_id="sess-001",
            answer_text="my answer",
            raw_score=8,
            final_score=8,
            passed=True,
        ),
    )
    assert a.id is not None
    assert a.passed is True


def test_get_latest_attempt(conn: sqlite3.Connection, question: Question) -> None:
    record_attempt(
        conn,
        Attempt(
            question_id=question.id, session_id="s1", answer_text="first",
            raw_score=5, final_score=5, passed=False,
        ),
    )
    record_attempt(
        conn,
        Attempt(
            question_id=question.id, session_id="s1", answer_text="second",
            raw_score=9, final_score=9, passed=True,
        ),
    )
    latest = get_latest_attempt(conn, question.id)
    assert latest is not None
    assert latest.answer_text == "second"


def test_get_latest_attempt_none(conn: sqlite3.Connection, question: Question) -> None:
    assert get_latest_attempt(conn, question.id) is None


def test_attempt_time_penalty(conn: sqlite3.Connection, question: Question) -> None:
    a = record_attempt(
        conn,
        Attempt(
            question_id=question.id, session_id="s1", answer_text="slow",
            raw_score=8, final_score=4, passed=False,
            time_taken_s=90.0, time_penalty=True, timed=True,
        ),
    )
    latest = get_latest_attempt(conn, question.id)
    assert latest.time_penalty is True
    assert latest.final_score == 4


# ---------------------------------------------------------------------------
# Review Queues
# ---------------------------------------------------------------------------


def test_upsert_review_entry_insert(conn: sqlite3.Connection, question: Question) -> None:
    entry = ReviewEntry(question_id=question.id, queue="timed", last_score=8, stability=1.5)
    upsert_review_entry(conn, entry)
    fetched = get_review_entry(conn, question.id, "timed")
    assert fetched is not None
    assert fetched.last_score == 8
    assert fetched.stability == 1.5


def test_upsert_review_entry_update(conn: sqlite3.Connection, question: Question) -> None:
    upsert_review_entry(conn, ReviewEntry(question_id=question.id, queue="untimed", last_score=5))
    upsert_review_entry(conn, ReviewEntry(question_id=question.id, queue="untimed", last_score=9))
    fetched = get_review_entry(conn, question.id, "untimed")
    assert fetched.last_score == 9


def test_timed_and_untimed_queues_are_independent(
    conn: sqlite3.Connection, question: Question
) -> None:
    upsert_review_entry(conn, ReviewEntry(question_id=question.id, queue="timed", last_score=7))
    upsert_review_entry(conn, ReviewEntry(question_id=question.id, queue="untimed", last_score=3))
    timed = get_review_entry(conn, question.id, "timed")
    untimed = get_review_entry(conn, question.id, "untimed")
    assert timed.last_score == 7
    assert untimed.last_score == 3


def test_get_due_questions_new(conn: sqlite3.Connection, question: Question) -> None:
    """New entries (no next_review_at) should always be returned."""
    upsert_review_entry(conn, ReviewEntry(question_id=question.id, queue="timed"))
    due = get_due_questions(conn, "timed")
    assert len(due) == 1
    assert due[0].id == question.id


def test_get_due_questions_past_due(conn: sqlite3.Connection, question: Question) -> None:
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    upsert_review_entry(
        conn,
        ReviewEntry(question_id=question.id, queue="untimed", next_review_at=past),
    )
    due = get_due_questions(conn, "untimed", now=datetime.now(UTC))
    assert len(due) == 1


def test_get_due_questions_future(conn: sqlite3.Connection, question: Question) -> None:
    future = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    upsert_review_entry(
        conn,
        ReviewEntry(question_id=question.id, queue="timed", next_review_at=future),
    )
    due = get_due_questions(conn, "timed", now=datetime.now(UTC))
    assert len(due) == 0
