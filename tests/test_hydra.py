"""
Tests for the Hydra Protocol sub-question spawning.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from scathach.core.hydra import HydraError, spawn_subquestions
from scathach.db.models import Question, Topic
from scathach.db.repository import get_children, insert_question, upsert_topic
from scathach.db.schema import apply_schema, get_connection
from scathach.llm.client import LLMClient


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    apply_schema(c)
    return c


@pytest.fixture
def topic(conn: sqlite3.Connection) -> Topic:
    return upsert_topic(conn, Topic(name="Bio", content="Cell biology notes."))


@pytest.fixture
def parent_q(conn: sqlite3.Connection, topic: Topic) -> Question:
    return insert_question(
        conn,
        Question(
            topic_id=topic.id,
            difficulty=3,
            body="Explain cellular respiration.",
            ideal_answer="ATP production via glycolysis, Krebs cycle, ETC.",
            is_root=True,
        ),
    )


def _make_client(response: str) -> LLMClient:
    client = LLMClient.__new__(LLMClient)
    client.generate = AsyncMock(return_value=response)
    return client


def _make_hydra_json(n: int = 3, difficulty: int = 2) -> str:
    return json.dumps([
        {
            "difficulty": difficulty,
            "body": f"Sub-question {i + 1}?",
            "ideal_answer": f"Sub-answer {i + 1}.",
        }
        for i in range(n)
    ])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_returns_3_questions(conn: sqlite3.Connection, parent_q: Question) -> None:
    client = _make_client(_make_hydra_json(3, difficulty=2))
    subs = await spawn_subquestions(conn, client, parent_q, "I don't know.", "No understanding.")
    assert len(subs) == 3


@pytest.mark.asyncio
async def test_spawn_links_to_parent(conn: sqlite3.Connection, parent_q: Question) -> None:
    client = _make_client(_make_hydra_json(3, difficulty=2))
    subs = await spawn_subquestions(conn, client, parent_q, "A", "D")
    assert all(s.parent_id == parent_q.id for s in subs)


@pytest.mark.asyncio
async def test_spawn_difficulty_is_parent_minus_1(
    conn: sqlite3.Connection, parent_q: Question
) -> None:
    """Parent is difficulty 3 → subs should be difficulty 2."""
    client = _make_client(_make_hydra_json(3, difficulty=2))
    subs = await spawn_subquestions(conn, client, parent_q, "A", "D")
    assert all(s.difficulty == 2 for s in subs)


@pytest.mark.asyncio
async def test_spawn_difficulty_clamped_at_1(conn: sqlite3.Connection, topic: Topic) -> None:
    """Parent at difficulty 1 → subs also at difficulty 1."""
    d1_q = insert_question(
        conn,
        Question(topic_id=topic.id, difficulty=1, body="Q", ideal_answer="A", is_root=True),
    )
    client = _make_client(_make_hydra_json(3, difficulty=1))
    subs = await spawn_subquestions(conn, client, d1_q, "A", "D")
    assert all(s.difficulty == 1 for s in subs)


@pytest.mark.asyncio
async def test_spawn_persists_to_db(conn: sqlite3.Connection, parent_q: Question) -> None:
    client = _make_client(_make_hydra_json(3, difficulty=2))
    await spawn_subquestions(conn, client, parent_q, "A", "D")
    children = get_children(conn, parent_q.id)
    assert len(children) == 3


@pytest.mark.asyncio
async def test_spawn_ideal_answers_populated(
    conn: sqlite3.Connection, parent_q: Question
) -> None:
    client = _make_client(_make_hydra_json(3, difficulty=2))
    subs = await spawn_subquestions(conn, client, parent_q, "A", "D")
    assert all(s.ideal_answer for s in subs)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_handles_more_than_3(conn: sqlite3.Connection, parent_q: Question) -> None:
    """If LLM returns 5 items, only take the first 3."""
    client = _make_client(_make_hydra_json(5, difficulty=2))
    subs = await spawn_subquestions(conn, client, parent_q, "A", "D")
    assert len(subs) == 3


@pytest.mark.asyncio
async def test_spawn_handles_fenced_json(conn: sqlite3.Connection, parent_q: Question) -> None:
    fenced = "```json\n" + _make_hydra_json(3) + "\n```"
    client = _make_client(fenced)
    subs = await spawn_subquestions(conn, client, parent_q, "A", "D")
    assert len(subs) == 3


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_raises_on_no_id() -> None:
    q_no_id = Question(topic_id=1, difficulty=2, body="Q", ideal_answer="A")
    conn = get_connection(":memory:")
    apply_schema(conn)
    client = _make_client(_make_hydra_json(3))
    with pytest.raises(HydraError, match="no id"):
        await spawn_subquestions(conn, client, q_no_id, "A", "D")


@pytest.mark.asyncio
async def test_spawn_raises_on_bad_json_after_retry(
    conn: sqlite3.Connection, parent_q: Question
) -> None:
    client = _make_client("not json %%%")
    with pytest.raises(HydraError):
        await spawn_subquestions(conn, client, parent_q, "A", "D")
