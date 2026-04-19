"""
Answer scoring pipeline.

`score_answer` calls the LLM to evaluate a student's answer, then applies the
time-penalty logic purely in application code (no LLM involvement with timing).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from scathach.db.models import Attempt, Question
from scathach.core.question import DifficultyLevel, TimerZone
from scathach.llm.client import LLMClient, LLMError
from scathach.llm.parsing import ParseError, SCORE_RESPONSE_SCHEMA, validate_score_response
from scathach.llm.prompts import render_scoring_prompt


class ScoringError(Exception):
    """Raised when scoring fails and cannot recover."""


def apply_time_penalty(
    raw_score: int,
    difficulty: int,
    time_taken_s: Optional[float],
    timed: bool,
    threshold: int,
) -> tuple[int, bool, bool]:
    """
    Apply time-penalty logic to a raw LLM score.

    This is pure application logic — the LLM never sees timing information.

    Returns:
        (final_score, time_penalty, passed)
    """
    if not timed or time_taken_s is None:
        return raw_score, False, raw_score >= threshold

    dl = DifficultyLevel.from_int(difficulty)
    zone = dl.timer_zone(time_taken_s)

    if zone == TimerZone.EXPIRED:
        # Auto-fail: exceeded 2t
        return 0, False, False
    elif zone == TimerZone.PENALTY:
        # Halve the score (floor division)
        final = raw_score // 2
        return final, True, final >= threshold
    else:
        # NORMAL: within base time limit
        return raw_score, False, raw_score >= threshold


async def score_answer(
    conn: sqlite3.Connection,
    client: LLMClient,
    question: Question,
    session_id: str,
    answer_text: str,
    time_taken_s: Optional[float],
    timed: bool,
    threshold: int,
) -> tuple[Attempt, str]:
    """
    Score a student's answer to a question.

    Calls the LLM to get a quality score and diagnosis, then applies the
    time-penalty rules in application code.

    Args:
        conn:         Open SQLite connection.
        client:       Initialised LLMClient.
        question:     The Question being answered.
        session_id:   UUID string for the current session.
        answer_text:  The student's answer.
        time_taken_s: Elapsed seconds, or None for untimed.
        timed:        Whether this attempt is under a timer.
        threshold:    Minimum score to pass (0–10).

    Returns:
        (Attempt, diagnosis_str) — Attempt has all fields populated.
        The Attempt is NOT persisted here; callers must call record_attempt().

    Raises:
        ScoringError: If the LLM fails or response cannot be parsed after retry.
    """
    if question.id is None:
        raise ScoringError("Question has no id — it must be persisted before scoring.")

    system_prompt, user_prompt = render_scoring_prompt(
        question_body=question.body,
        difficulty=question.difficulty,
        answer_text=answer_text,
    )

    # Auto-fail if time already expired (no need to call LLM)
    if timed and time_taken_s is not None:
        dl = DifficultyLevel.from_int(question.difficulty)
        if dl.timer_zone(time_taken_s) == TimerZone.EXPIRED:
            attempt = Attempt(
                question_id=question.id,
                session_id=session_id,
                answer_text=answer_text,
                raw_score=0,
                final_score=0,
                passed=False,
                time_taken_s=time_taken_s,
                time_penalty=False,
                timed=True,
            )
            return attempt, "Answer auto-failed: time limit exceeded (> 2× base limit)."

    try:
        result = await client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=SCORE_RESPONSE_SCHEMA,
            max_tokens=256,
            temperature=0.1,
        )
        score_data = validate_score_response(result)
    except (LLMError, ParseError) as exc:
        raise ScoringError(f"Scoring failed: {exc}") from exc

    raw_score: int = score_data["score"]
    diagnosis: str = score_data["diagnosis"]

    final_score, time_penalty, passed = apply_time_penalty(
        raw_score=raw_score,
        difficulty=question.difficulty,
        time_taken_s=time_taken_s,
        timed=timed,
        threshold=threshold,
    )

    attempt = Attempt(
        question_id=question.id,
        session_id=session_id,
        answer_text=answer_text,
        raw_score=raw_score,
        final_score=final_score,
        passed=passed,
        time_taken_s=time_taken_s,
        time_penalty=time_penalty,
        timed=timed,
    )
    return attempt, diagnosis
