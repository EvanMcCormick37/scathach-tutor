"""
Drill question generation.

A drill is a flat quiz of fresh questions all at a single difficulty level.
Unlike a session, drills don't use SessionRunner or Hydra — they're a direct
generate → answer → score loop driven by the CLI.
"""

from __future__ import annotations

import sqlite3

from typing import Optional

from scathach.db.models import Question
from scathach.db.repository import get_prior_root_questions, get_topic_by_id, insert_question
from scathach.llm.client import LLMClient, LLMError
from scathach.llm.parsing import ParseError, QUESTIONS_RESPONSE_SCHEMA, validate_questions_response
from scathach.llm.prompts import render_drill_prompt

# Maximum questions allowed per level — enforced before the LLM call.
DRILL_MAX_QUESTIONS: dict[int, int] = {1: 32, 2: 16, 3: 8, 4: 4, 5: 2, 6: 1}


class DrillError(Exception):
    """Raised when drill question generation fails."""


async def generate_drill_questions(
    conn: sqlite3.Connection,
    client: LLMClient,
    topic_id: int,
    level: int,
    count: int,
    session_id: Optional[str] = None,
) -> list[Question]:
    """
    Generate `count` fresh questions for `topic_id` all at `level`.

    Count is capped to DRILL_MAX_QUESTIONS[level] before the LLM call.
    Questions are inserted into the DB and returned with ids set.

    Raises:
        DrillError: If the topic is not found or the LLM call fails.
    """
    if not 1 <= level <= 6:
        raise DrillError(f"Level must be 1–6, got {level}.")

    max_q = DRILL_MAX_QUESTIONS[level]
    count = min(count, max_q)

    topic = get_topic_by_id(conn, topic_id)
    if topic is None:
        raise DrillError(f"Topic id={topic_id} not found.")

    prior = get_prior_root_questions(conn, topic_id, limit_per_level=50)
    prior_at_level = [q for q in prior if q.difficulty == level] if prior else None

    system_prompt, user_prompt = render_drill_prompt(
        document_content=topic.content,
        level=level,
        count=count,
        prior_questions=prior_at_level or None,
    )

    try:
        result = await client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=QUESTIONS_RESPONSE_SCHEMA,
        )
        parsed = validate_questions_response(result)
    except (LLMError, ParseError) as exc:
        raise DrillError(f"Question generation failed: {exc}") from exc

    parsed = [q for q in parsed if q["difficulty"] == level][:count]

    questions: list[Question] = []
    for q_data in parsed:
        q = insert_question(
            conn,
            Question(
                topic_id=topic_id,
                session_id=session_id,
                difficulty=q_data["difficulty"],
                body=q_data["body"],
                ideal_answer=q_data["ideal_answer"],
                is_root=True,
            ),
        )
        questions.append(q)

    return questions
