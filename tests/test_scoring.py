"""
Tests for the answer scoring pipeline and time-penalty logic.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from scathach.core.scoring import ScoringError, apply_time_penalty, score_answer
from scathach.db.models import Question, Topic
from scathach.db.repository import insert_question, upsert_topic
from scathach.db.schema import apply_schema, get_connection
from scathach.llm.client import LLMClient


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    apply_schema(c)
    return c


@pytest.fixture
def topic(conn: sqlite3.Connection) -> Topic:
    return upsert_topic(conn, Topic(name="Physics", content="content"))


@pytest.fixture
def question(conn: sqlite3.Connection, topic: Topic) -> Question:
    return insert_question(
        conn,
        Question(
            topic_id=topic.id,
            difficulty=1,  # t=30s, 2t=60s
            body="What is force?",
            ideal_answer="F = ma",
            is_root=True,
        ),
    )


def _make_client(score: int, diagnosis: str) -> LLMClient:
    response = json.dumps({"score": score, "diagnosis": diagnosis})
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# apply_time_penalty (pure logic — no mocks needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "timed, time_taken_s, expected_final, expected_penalty, expected_passed",
    [
        # Untimed — no penalty ever
        (False, None,  8, False, True),
        (False, 999.0, 8, False, True),
        # Within base time (difficulty 1 → t=30s)
        (True,  29.9,  8, False, True),
        (True,  30.0,  8, False, True),
        # Penalty zone (30 < t ≤ 60)
        (True,  30.1,  4, True,  False),  # 8 // 2 = 4, threshold=7 → fail
        (True,  59.9,  4, True,  False),
        # Expired (> 60s)
        (True,  60.1,  0, False, False),  # auto-fail
        (True,  120.0, 0, False, False),
    ],
)
def test_apply_time_penalty(
    timed: bool,
    time_taken_s,
    expected_final: int,
    expected_penalty: bool,
    expected_passed: bool,
) -> None:
    final, penalty, passed = apply_time_penalty(
        raw_score=8, difficulty=1, time_taken_s=time_taken_s, timed=timed, threshold=7
    )
    assert final == expected_final
    assert penalty == expected_penalty
    assert passed == expected_passed


def test_apply_time_penalty_threshold_respected() -> None:
    # raw=10, penalty zone → final=5, threshold=5 → passes
    final, penalty, passed = apply_time_penalty(
        raw_score=10, difficulty=1, time_taken_s=35.0, timed=True, threshold=5
    )
    assert final == 5
    assert penalty is True
    assert passed is True


def test_apply_time_penalty_level6_limits() -> None:
    # Level 6: t=1800s (30 min), 2t=3600s
    final, _, passed = apply_time_penalty(
        raw_score=9, difficulty=6, time_taken_s=1799.0, timed=True, threshold=7
    )
    assert final == 9  # within normal zone
    final2, penalty, passed2 = apply_time_penalty(
        raw_score=9, difficulty=6, time_taken_s=1801.0, timed=True, threshold=7
    )
    assert final2 == 4  # penalty zone: 9 // 2
    assert penalty is True


# ---------------------------------------------------------------------------
# score_answer — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_answer_untimed(
    conn: sqlite3.Connection, question: Question
) -> None:
    client = _make_client(score=8, diagnosis="Good answer, minor gaps.")
    attempt, diagnosis = await score_answer(
        conn=conn,
        client=client,
        question=question,
        session_id="sess-001",
        answer_text="Force = mass × acceleration",
        time_taken_s=None,
        timed=False,
        threshold=7,
    )
    assert attempt.raw_score == 8
    assert attempt.final_score == 8
    assert attempt.passed is True
    assert attempt.time_penalty is False
    assert "gap" in diagnosis.lower() or "good" in diagnosis.lower()


@pytest.mark.asyncio
async def test_score_answer_timed_normal_zone(
    conn: sqlite3.Connection, question: Question
) -> None:
    client = _make_client(score=9, diagnosis="Excellent.")
    attempt, _ = await score_answer(
        conn=conn, client=client, question=question,
        session_id="s", answer_text="F = ma",
        time_taken_s=20.0, timed=True, threshold=7,
    )
    assert attempt.final_score == 9
    assert attempt.time_penalty is False
    assert attempt.passed is True


@pytest.mark.asyncio
async def test_score_answer_timed_penalty_zone(
    conn: sqlite3.Connection, question: Question
) -> None:
    client = _make_client(score=8, diagnosis="Late but correct.")
    attempt, _ = await score_answer(
        conn=conn, client=client, question=question,
        session_id="s", answer_text="F = ma",
        time_taken_s=45.0, timed=True, threshold=7,
    )
    assert attempt.raw_score == 8
    assert attempt.final_score == 4   # 8 // 2
    assert attempt.time_penalty is True
    assert attempt.passed is False    # 4 < 7


@pytest.mark.asyncio
async def test_score_answer_auto_fail_expired(
    conn: sqlite3.Connection, question: Question
) -> None:
    """When time is expired (>2t), LLM should NOT be called at all."""
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(side_effect=AssertionError("LLM should not be called"))
    attempt, diagnosis = await score_answer(
        conn=conn, client=client, question=question,
        session_id="s", answer_text="partial",
        time_taken_s=61.0, timed=True, threshold=7,
    )
    assert attempt.final_score == 0
    assert attempt.passed is False
    assert "auto-failed" in diagnosis.lower() or "exceeded" in diagnosis.lower()


# ---------------------------------------------------------------------------
# score_answer — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_answer_raises_on_bad_json(
    conn: sqlite3.Connection, question: Question
) -> None:
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(return_value="not json %%%")
    with pytest.raises(ScoringError):
        await score_answer(
            conn=conn, client=client, question=question,
            session_id="s", answer_text="answer",
            time_taken_s=None, timed=False, threshold=7,
        )


@pytest.mark.asyncio
async def test_score_answer_raises_on_missing_id() -> None:
    """A question without an id (not yet persisted) should raise ScoringError."""
    q_no_id = Question(topic_id=1, difficulty=1, body="Q", ideal_answer="A")
    client = _make_client(score=5, diagnosis="D")
    # Create a dummy conn — won't reach DB
    conn = get_connection(":memory:")
    apply_schema(conn)
    with pytest.raises(ScoringError, match="no id"):
        await score_answer(
            conn=conn, client=client, question=q_no_id,
            session_id="s", answer_text="A",
            time_taken_s=None, timed=False, threshold=7,
        )
