"""
Tests for SessionRunner state machine with mocked LLM and answer providers.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from scathach.core.question import TimingMode
from scathach.core.session import (
    AnswerScored,
    HydraSpawned,
    QuestionPresented,
    SessionAborted,
    SessionComplete,
    SessionConfig,
    SessionEvent,
    SessionRunner,
    generate_root_questions,
)
from scathach.db.models import Question, Topic
from scathach.db.repository import get_root_questions, upsert_topic
from scathach.db.schema import apply_schema, get_connection
from scathach.llm.client import LLMClient


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    apply_schema(c)
    return c


@pytest.fixture
def topic(conn: sqlite3.Connection) -> Topic:
    return upsert_topic(conn, Topic(name="Physics", content="Newton's laws."))


def _make_llm_client(question_response: str, score_response: str) -> LLMClient:
    """Build a mock LLMClient that alternates between question and score responses."""
    client = LLMClient.__new__(LLMClient)
    # First call generates questions; subsequent calls score answers
    client.generate = AsyncMock(side_effect=[question_response, score_response] * 20)
    return client


def _questions_json(n: int = 6) -> str:
    return json.dumps([
        {"difficulty": i + 1, "body": f"Q{i+1}?", "ideal_answer": f"A{i+1}."}
        for i in range(n)
    ])


def _score_json(score: int = 8, diagnosis: str = "Good.") -> str:
    return json.dumps({"score": score, "diagnosis": diagnosis})


def _always_pass_provider():
    """Returns a fixed answer instantly (no timing)."""
    async def provider(question: Question) -> tuple[str, Optional[float]]:
        return "my answer", None
    return provider


def _always_fail_provider():
    """Returns a low-quality answer (will score below threshold)."""
    async def provider(question: Question) -> tuple[str, Optional[float]]:
        return "I don't know.", None
    return provider


def _collect_events() -> tuple[list[SessionEvent], AsyncMock]:
    events: list[SessionEvent] = []
    async def handler(event: SessionEvent) -> None:
        events.append(event)
    return events, handler


# ---------------------------------------------------------------------------
# generate_root_questions (standalone)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_root_questions_basic(conn: sqlite3.Connection, topic: Topic) -> None:
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(return_value=_questions_json(6))
    questions = await generate_root_questions(conn, client, topic.id)
    assert len(questions) == 6
    assert all(q.is_root for q in questions)


# ---------------------------------------------------------------------------
# SessionRunner — happy path (all pass)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_complete_all_pass(conn: sqlite3.Connection, topic: Topic) -> None:
    """Session where the student passes every question on the first attempt."""
    # LLM: first call generates questions, subsequent calls return passing scores
    responses = [_questions_json(3)] + [_score_json(9)] * 10
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(side_effect=responses)

    events, handler = _collect_events()
    config = SessionConfig(topic_id=topic.id, timing=TimingMode.UNTIMED, threshold=7, num_levels=3)
    runner = SessionRunner(
        conn=conn, client=client, config=config,
        answer_provider=_always_pass_provider(),
        event_handler=handler,
    )
    await runner.run()

    event_types = [type(e).__name__ for e in events]
    assert "SessionComplete" in event_types
    assert "SessionAborted" not in event_types

    complete = next(e for e in events if isinstance(e, SessionComplete))
    assert len(complete.cleared_questions) == 3


@pytest.mark.asyncio
async def test_session_emits_question_presented_events(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    responses = [_questions_json(2)] + [_score_json(9)] * 10
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(side_effect=responses)

    events, handler = _collect_events()
    config = SessionConfig(topic_id=topic.id, timing=TimingMode.UNTIMED, threshold=7, num_levels=2)
    runner = SessionRunner(
        conn=conn, client=client, config=config,
        answer_provider=_always_pass_provider(),
        event_handler=handler,
    )
    await runner.run()

    presented = [e for e in events if isinstance(e, QuestionPresented)]
    assert len(presented) >= 2


# ---------------------------------------------------------------------------
# SessionRunner — generation failure aborts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_aborts_on_generation_failure(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(return_value="not json %%%")  # unparseable after retry

    events, handler = _collect_events()
    config = SessionConfig(topic_id=topic.id, timing=TimingMode.UNTIMED, threshold=7)
    runner = SessionRunner(
        conn=conn, client=client, config=config,
        answer_provider=_always_pass_provider(),
        event_handler=handler,
    )
    await runner.run()

    aborted = [e for e in events if isinstance(e, SessionAborted)]
    assert len(aborted) == 1


# ---------------------------------------------------------------------------
# SessionRunner — Hydra spawning on failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_spawns_hydra_on_fail(conn: sqlite3.Connection, topic: Topic) -> None:
    """When student fails, HydraSpawned event should be emitted."""
    # Generate 1 question (level 1), fail it once, pass sub-questions, then pass parent
    hydra_json = json.dumps([
        {"difficulty": 1, "body": f"Sub Q{i}?", "ideal_answer": f"Sub A{i}."}
        for i in range(3)
    ])
    # Sequence: generate q → score fail → generate hydra subs → score sub pass×3 → score parent pass
    responses = [
        _questions_json(1),        # generate 1 root question
        _score_json(3, "poor"),    # first answer → fail (score 3 < 7)
        hydra_json,                # hydra generation
        _score_json(9),            # sub q1
        _score_json(9),            # sub q2
        _score_json(9),            # sub q3
        _score_json(9),            # parent retry → pass
    ]
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(side_effect=responses)

    events, handler = _collect_events()
    config = SessionConfig(topic_id=topic.id, timing=TimingMode.UNTIMED, threshold=7, num_levels=1)
    runner = SessionRunner(
        conn=conn, client=client, config=config,
        answer_provider=_always_pass_provider(),
        event_handler=handler,
    )
    await runner.run()

    hydra_events = [e for e in events if isinstance(e, HydraSpawned)]
    assert len(hydra_events) == 1
    assert len(hydra_events[0].subquestions) == 3


# ---------------------------------------------------------------------------
# SessionConfig defaults
# ---------------------------------------------------------------------------


def test_session_config_defaults() -> None:
    config = SessionConfig(topic_id=1)
    assert config.timing == TimingMode.UNTIMED
    assert config.threshold == 7
    assert config.num_levels == 6
