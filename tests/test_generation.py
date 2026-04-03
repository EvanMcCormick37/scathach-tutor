"""
Tests for the question generation pipeline (LLM calls mocked).
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from scathach.core.session import GenerationError, generate_root_questions
from scathach.db.models import Topic
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
    return upsert_topic(conn, Topic(name="Physics", content="Newton's laws of motion."))


def _make_client(response: str) -> LLMClient:
    """Return a mock LLMClient that always returns the given string."""
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(return_value=response)
    return client


def _make_questions_json(num: int = 6) -> str:
    """Build a valid question generation JSON response."""
    return json.dumps([
        {
            "difficulty": i + 1,
            "body": f"Question at level {i + 1}?",
            "ideal_answer": f"Ideal answer for level {i + 1}.",
        }
        for i in range(num)
    ])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_root_questions_returns_6(conn: sqlite3.Connection, topic: Topic) -> None:
    client = _make_client(_make_questions_json(6))
    questions = await generate_root_questions(conn, client, topic.id)
    assert len(questions) == 6


@pytest.mark.asyncio
async def test_generate_root_questions_ordered_by_difficulty(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    client = _make_client(_make_questions_json(6))
    questions = await generate_root_questions(conn, client, topic.id)
    difficulties = [q.difficulty for q in questions]
    assert difficulties == sorted(difficulties)


@pytest.mark.asyncio
async def test_generate_root_questions_persisted(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    client = _make_client(_make_questions_json(6))
    await generate_root_questions(conn, client, topic.id)
    db_questions = get_root_questions(conn, topic.id)
    assert len(db_questions) == 6
    assert all(q.is_root for q in db_questions)


@pytest.mark.asyncio
async def test_generate_root_questions_ideal_answers_populated(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    client = _make_client(_make_questions_json(6))
    questions = await generate_root_questions(conn, client, topic.id)
    assert all(q.ideal_answer for q in questions)


@pytest.mark.asyncio
async def test_generate_root_questions_respects_num_levels(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    client = _make_client(_make_questions_json(6))
    questions = await generate_root_questions(conn, client, topic.id, num_levels=3)
    assert len(questions) == 3
    assert all(q.difficulty <= 3 for q in questions)


# ---------------------------------------------------------------------------
# JSON with markdown fences (fallback parsing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_handles_markdown_fenced_json(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    fenced = "```json\n" + _make_questions_json(6) + "\n```"
    client = _make_client(fenced)
    questions = await generate_root_questions(conn, client, topic.id)
    assert len(questions) == 6


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_raises_on_unknown_topic(conn: sqlite3.Connection) -> None:
    client = _make_client(_make_questions_json(6))
    with pytest.raises(GenerationError, match="not found"):
        await generate_root_questions(conn, client, topic_id=9999)


@pytest.mark.asyncio
async def test_generate_raises_on_invalid_json_after_retry(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    client = _make_client("this is not json at all %%%")
    with pytest.raises(GenerationError):
        await generate_root_questions(conn, client, topic.id)


@pytest.mark.asyncio
async def test_generate_raises_on_wrong_schema(
    conn: sqlite3.Connection, topic: Topic
) -> None:
    bad = json.dumps([{"level": 1, "question": "missing fields"}])
    client = _make_client(bad)
    with pytest.raises(GenerationError):
        await generate_root_questions(conn, client, topic.id)
