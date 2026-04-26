"""
Topic Review: generate one fresh question per struggling or stale topic+level pair.

A pair is eligible when:
  - It has more than one attempt on record, AND
  - Either the average final score is below the configured threshold,
    or the most recent question at that level was generated more than 30 days ago.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

from scathach.db.models import Question
from scathach.db.repository import (
    get_questions_by_difficulty,
    get_topic_by_id,
    get_topic_level_stats,
    insert_question,
)
from scathach.llm.client import LLMClient, LLMError
from scathach.llm.parsing import QUESTIONS_RESPONSE_SCHEMA, ParseError, validate_questions_response
from scathach.llm.prompts import render_drill_prompt

_STALE_THRESHOLD_DAYS = 30


class TopicReviewError(Exception):
    """Raised when topic-review question generation fails."""


@dataclass
class TopicLevelStat:
    topic_id: int
    difficulty: int
    attempt_count: int
    avg_final_score: float
    latest_question_created_at: Optional[str]


def get_eligible_pairs(
    conn: sqlite3.Connection,
    threshold: int,
) -> list[TopicLevelStat]:
    """
    Return topic+level pairs that warrant a new topic-review question.

    Eligible if: attempt_count > 1 AND (avg_score < threshold OR last question > 30 days old).
    """
    rows = get_topic_level_stats(conn)
    cutoff = datetime.now(UTC) - timedelta(days=_STALE_THRESHOLD_DAYS)

    eligible: list[TopicLevelStat] = []
    for row in rows:
        stat = TopicLevelStat(
            topic_id=row["topic_id"],
            difficulty=row["difficulty"],
            attempt_count=row["attempt_count"],
            avg_final_score=row["avg_final_score"],
            latest_question_created_at=row["latest_question_created_at"],
        )

        if stat.attempt_count <= 1:
            continue

        is_stale = True
        if stat.latest_question_created_at:
            try:
                last_dt = datetime.fromisoformat(stat.latest_question_created_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=UTC)
                is_stale = last_dt < cutoff
            except ValueError:
                pass

        is_struggling = stat.avg_final_score < threshold

        if is_struggling or is_stale:
            eligible.append(stat)

    return eligible


async def generate_topic_review_question(
    conn: sqlite3.Connection,
    client: LLMClient,
    topic_id: int,
    level: int,
) -> Optional[Question]:
    """
    Generate exactly one new question for the given topic+level.

    All existing questions at that level are passed to the prompt for deduplication.
    Returns None if the topic is missing or the LLM returns no usable candidates.
    Raises TopicReviewError on generation failure.
    """
    topic = get_topic_by_id(conn, topic_id)
    if topic is None:
        return None

    existing = get_questions_by_difficulty(conn, topic_id, level)

    system_prompt, user_prompt = render_drill_prompt(
        document_content=topic.content,
        level=level,
        count=1,
        prior_questions=existing or None,
    )

    try:
        result = await client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=QUESTIONS_RESPONSE_SCHEMA,
        )
        parsed = validate_questions_response(result)
    except (LLMError, ParseError) as exc:
        raise TopicReviewError(
            f"Generation failed for topic={topic_id} level={level}: {exc}"
        ) from exc

    candidates = [q for q in parsed if q["difficulty"] == level]
    if not candidates:
        return None

    q_data = candidates[0]
    return insert_question(
        conn,
        Question(
            topic_id=topic_id,
            difficulty=q_data["difficulty"],
            body=q_data["body"],
            ideal_answer=q_data["ideal_answer"],
            is_root=True,
        ),
    )
