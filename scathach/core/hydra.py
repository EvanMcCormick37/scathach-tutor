"""
Hydra Protocol — sub-question spawning.

When a student fails a question, `spawn_subquestions` asks the tutor LLM to
generate a set of 1–5 sub-questions at any difficulty levels strictly below the
parent question. The LLM selects the number and difficulty levels such that
answering all sub-questions gives the student the understanding needed to answer
the parent question.
"""

from __future__ import annotations

import sqlite3

from typing import Optional

from scathach.db.models import Question
from scathach.db.repository import get_questions_below_difficulty, insert_question
from scathach.llm.client import LLMClient, LLMError
from scathach.llm.parsing import ParseError, QUESTIONS_RESPONSE_SCHEMA, validate_questions_response
from scathach.llm.prompts import render_hydra_prompt


class HydraError(Exception):
    """Raised when sub-question generation fails."""


async def spawn_subquestions(
    conn: sqlite3.Connection,
    client: LLMClient,
    parent_question: Question,
    student_answer: str,
    diagnosis: str,
    session_id: Optional[str] = None,
) -> list[Question]:
    """
    Spawn sub-questions targeting the diagnosed gaps in understanding.

    The LLM chooses both the number (1–5) and the difficulty level of each
    sub-question. Every sub-question will have difficulty strictly less than
    parent_question.difficulty. Any questions returned by the LLM that violate
    this constraint are silently discarded.

    Args:
        conn:            Open SQLite connection.
        client:          Initialised LLMClient.
        parent_question: The question the student failed.
        student_answer:  The student's failing answer text.
        diagnosis:       Conceptual gap diagnosis from the scorer.

    Returns:
        List of Question objects with ids set and parent_id = parent_question.id.

    Raises:
        HydraError: If sub-question generation fails after retry.
    """
    if parent_question.id is None:
        raise HydraError("Parent question has no id — must be persisted before spawning.")

    if parent_question.difficulty <= 1:
        # Cannot spawn sub-questions below difficulty 1.
        return []

    existing_below = get_questions_below_difficulty(
        conn, parent_question.topic_id, parent_question.difficulty
    )

    system_prompt, user_prompt = render_hydra_prompt(
        parent_body=parent_question.body,
        parent_difficulty=parent_question.difficulty,
        student_answer=student_answer,
        diagnosis=diagnosis,
        existing_questions=existing_below or None,
    )

    try:
        result = await client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=QUESTIONS_RESPONSE_SCHEMA,
        )
        parsed = validate_questions_response(result)
    except (LLMError, ParseError) as exc:
        raise HydraError(f"Sub-question generation failed: {exc}") from exc

    # Enforce the difficulty constraint.
    parsed = [q for q in parsed if q["difficulty"] < parent_question.difficulty]

    questions: list[Question] = []
    for q_data in parsed:
        q = insert_question(
            conn,
            Question(
                topic_id=parent_question.topic_id,
                parent_id=parent_question.id,
                session_id=session_id,
                difficulty=q_data["difficulty"],
                body=q_data["body"],
                ideal_answer=q_data["ideal_answer"],
                is_root=False,
            ),
        )
        questions.append(q)

    return questions
