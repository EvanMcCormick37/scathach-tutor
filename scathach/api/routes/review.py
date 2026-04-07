"""
Review routes.

GET  /review/due                        — fetch questions due for review
POST /review/{question_id}/answer       — score a review answer + update schedule
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from scathach.api.models import (
    QuestionResponse,
    ReviewAnswerRequest,
    ReviewAnswerResponse,
    ReviewQueueResponse,
)
from scathach.core.question import DifficultyLevel, TimingMode
from scathach.core.scoring import ScoringError, score_answer
from scathach.core.scheduler import get_scheduled_questions, update_schedule
from scathach.db.repository import get_question, record_attempt

router = APIRouter()

_REVIEW_LEVELS = {"review": (1, 2), "super-review": (3, 6)}


def _question_response(q) -> QuestionResponse:
    return QuestionResponse(
        id=q.id,
        topic_id=q.topic_id,
        difficulty=q.difficulty,
        body=q.body,
        parent_id=q.parent_id,
        is_root=q.is_root,
    )


@router.get("/due", response_model=ReviewQueueResponse)
async def get_due_questions(
    request: Request,
    queue: str = Query(default="untimed", pattern="^(timed|untimed)$"),
    mode: str = Query(default="review", pattern="^(review|super-review)$"),
    limit: int = Query(default=20, ge=1, le=100),
):
    conn = request.app.state.conn
    min_diff, max_diff = _REVIEW_LEVELS[mode]
    order_by_score = mode == "super-review"
    questions = get_scheduled_questions(
        conn,
        queue=queue,
        limit=limit,
        min_difficulty=min_diff,
        max_difficulty=max_diff,
        order_by_score=order_by_score,
    )
    return ReviewQueueResponse(
        questions=[_question_response(q) for q in questions],
        queue=queue,
        total_due=len(questions),
    )


@router.post("/{question_id}/answer", response_model=ReviewAnswerResponse)
async def submit_review_answer(
    request: Request,
    question_id: int,
    body: ReviewAnswerRequest,
):
    conn = request.app.state.conn
    client = request.app.state.client

    question = get_question(conn, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")

    # Determine effective timing
    timed = body.timed
    elapsed_s: Optional[float] = body.elapsed_s
    if timed and elapsed_s is not None:
        dl = DifficultyLevel.from_int(question.difficulty)
        max_plausible = dl.penalty_limit_s * 2 + 30
        elapsed_s = min(elapsed_s, max_plausible)

    try:
        attempt, diagnosis = await score_answer(
            conn=conn,
            client=client,
            question=question,
            session_id="review",
            answer_text=body.answer_text,
            time_taken_s=elapsed_s,
            timed=timed,
            threshold=7,  # review always uses default threshold
        )
    except ScoringError as exc:
        raise HTTPException(status_code=502, detail=f"Scoring failed: {exc}")

    attempt = record_attempt(conn, attempt)

    # Update review schedule
    review_entry = update_schedule(
        conn,
        question_id=question_id,
        final_score=attempt.final_score,
        queue=body.queue,
    )

    next_review_at: Optional[str] = None
    if review_entry and review_entry.next_review_at:
        next_review_at = str(review_entry.next_review_at)

    return ReviewAnswerResponse(
        raw_score=attempt.raw_score,
        final_score=attempt.final_score,
        passed=attempt.passed,
        time_penalty=attempt.time_penalty,
        diagnosis=diagnosis,
        ideal_answer=question.ideal_answer,
        next_review_at=next_review_at,
    )
