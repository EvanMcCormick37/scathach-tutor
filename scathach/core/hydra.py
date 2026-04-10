"""
Hydra Protocol — sub-question spawning.

When a student fails a question, `spawn_subquestions` generates a configurable
number of easier targeted sub-questions to build foundational understanding
before retrying (default: 3).
"""

from __future__ import annotations

import sqlite3

from scathach.db.models import Question
from scathach.db.repository import get_questions_by_difficulty, insert_question
from scathach.llm.client import LLMClient
from scathach.llm.parsing import ParseError, parse_questions_response
from scathach.llm.prompts import render_hydra_prompt


class HydraError(Exception):
    """Raised when sub-question generation fails."""


async def spawn_subquestions(
    conn: sqlite3.Connection,
    client: LLMClient,
    parent_question: Question,
    student_answer: str,
    diagnosis: str,
    count: int = 3,
) -> list[Question]:
    """
    Spawn sub-questions targeting the diagnosed gaps in understanding.

    Args:
        conn:            Open SQLite connection.
        client:          Initialised LLMClient.
        parent_question: The question the student failed.
        student_answer:  The student's failing answer text.
        diagnosis:       Conceptual gap diagnosis from the scorer.
        count:           Number of sub-questions to generate (default: 3).

    Returns:
        List of Question objects with ids set and parent_id = parent_question.id.

    Raises:
        HydraError: If sub-question generation fails after retry.
    """
    if parent_question.id is None:
        raise HydraError("Parent question has no id — must be persisted before spawning.")

    target_difficulty = max(1, parent_question.difficulty - 1)

    existing_at_level = get_questions_by_difficulty(
        conn, parent_question.topic_id, target_difficulty
    )

    system_prompt, user_prompt = render_hydra_prompt(
        parent_body=parent_question.body,
        parent_difficulty=parent_question.difficulty,
        student_answer=student_answer,
        diagnosis=diagnosis,
        target_difficulty=target_difficulty,
        existing_questions=existing_at_level or None,
        count=count,
    )

    raw_response: str | None = None
    parsed: list[dict] | None = None

    for attempt in range(2):
        try:
            raw_response = await client.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            parsed = parse_questions_response(raw_response)
            break
        except ParseError as exc:
            if attempt == 0:
                user_prompt = (
                    user_prompt
                    + "\n\nIMPORTANT: Respond with ONLY the raw JSON array, no other text."
                )
                continue
            raise HydraError(
                f"Sub-question generation returned unparseable response after retry. "
                f"Parse error: {exc}\nRaw (truncated): {(raw_response or '')[:400]}"
            ) from exc

    if parsed is None:
        raise HydraError("Sub-question generation produced no output.")

    # Take up to `count` questions; if LLM returns more or fewer, handle gracefully
    parsed = parsed[:count]

    questions: list[Question] = []
    for q_data in parsed:
        q = insert_question(
            conn,
            Question(
                topic_id=parent_question.topic_id,
                parent_id=parent_question.id,
                difficulty=target_difficulty,
                body=q_data["body"],
                ideal_answer=q_data["ideal_answer"],
                is_root=False,
            ),
        )
        questions.append(q)

    return questions
