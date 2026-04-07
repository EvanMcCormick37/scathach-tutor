"""
Session routes.

POST   /sessions                    — create + start a new session (generates questions)
GET    /sessions                    — list active sessions
GET    /sessions/{session_id}       — get session summary + current question
POST   /sessions/{session_id}/answer — submit an answer, get result + next question
DELETE /sessions/{session_id}       — abandon / delete a session
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from scathach.api.models import (
    AnswerResultResponse,
    AnswerSubmitRequest,
    QuestionContext,
    QuestionResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionSummaryResponse,
)
from scathach.api.session_controller import (
    InMemorySession,
    create_session,
    evict_session,
    get_session,
    resume_session,
)
from scathach.core.session import GenerationError
from scathach.core.scoring import ScoringError
from scathach.db.repository import (
    get_session_record,
    get_topic_by_id,
    list_active_sessions,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _question_response(q) -> QuestionResponse:
    return QuestionResponse(
        id=q.id,
        topic_id=q.topic_id,
        difficulty=q.difficulty,
        body=q.body,
        parent_id=q.parent_id,
        is_root=q.is_root,
    )


def _question_context(sess: InMemorySession) -> QuestionContext:
    return QuestionContext(
        index=sess.root_index_of_current(),
        total=sess.num_levels,
        depth=sess.current_depth(),
        is_timed=sess.current_is_timed,
        started_at=sess.current_started_at.isoformat() if sess.current_started_at else "",
    )


def _session_summary(conn, record) -> SessionSummaryResponse:
    import json
    topic = get_topic_by_id(conn, record.topic_id)
    topic_name = topic.name if topic else str(record.topic_id)
    cleared_ids = json.loads(record.cleared_ids) if record.cleared_ids else []
    return SessionSummaryResponse(
        session_id=record.session_id,
        topic_id=record.topic_id,
        topic_name=topic_name,
        status=record.status,
        timing=record.timing,
        threshold=record.threshold,
        num_levels=record.num_levels,
        cleared_count=len(cleared_ids),
        total_questions=record.num_levels,
        created_at=str(record.created_at),
        updated_at=str(record.updated_at),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=SessionCreateResponse, status_code=201)
async def start_session(request: Request, body: SessionCreateRequest):
    conn = request.app.state.conn
    client = request.app.state.client
    topic = get_topic_by_id(conn, body.topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    try:
        sess = await create_session(
            conn=conn,
            client=client,
            topic_id=body.topic_id,
            timing=body.timing,
            threshold=body.threshold,
            num_levels=body.num_levels,
        )
    except GenerationError as exc:
        raise HTTPException(status_code=502, detail=f"Question generation failed: {exc}")

    first_q = sess.current_question()
    if first_q is None:
        raise HTTPException(status_code=500, detail="No questions generated")

    return SessionCreateResponse(
        session_id=sess.session_id,
        topic_id=sess.topic_id,
        question=_question_response(first_q),
        context=_question_context(sess),
    )


@router.get("", response_model=list[SessionSummaryResponse])
async def list_sessions(request: Request):
    conn = request.app.state.conn
    records = list_active_sessions(conn)
    return [_session_summary(conn, r) for r in records]


@router.get("/{session_id}", response_model=SessionSummaryResponse)
async def get_session_info(request: Request, session_id: str):
    conn = request.app.state.conn
    client = request.app.state.client
    sess = resume_session(conn, client, session_id)
    if sess is None:
        record = get_session_record(conn, session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return _session_summary(conn, record)
    record = get_session_record(conn, session_id)
    return _session_summary(conn, record)


@router.post("/{session_id}/answer", response_model=AnswerResultResponse)
async def submit_answer(
    request: Request, session_id: str, body: AnswerSubmitRequest
):
    conn = request.app.state.conn
    client = request.app.state.client

    sess = get_session(session_id)
    if sess is None:
        # Try to resume from DB
        sess = resume_session(conn, client, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found or already complete")

    if sess.current_question() is None:
        raise HTTPException(status_code=409, detail="Session is already complete")

    try:
        outcome = await sess.submit_answer(body.answer_text, body.elapsed_s)
    except ScoringError as exc:
        raise HTTPException(status_code=502, detail=f"Scoring failed: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if outcome.is_complete:
        evict_session(session_id)

    return AnswerResultResponse(
        raw_score=outcome.attempt.raw_score,
        final_score=outcome.attempt.final_score,
        passed=outcome.attempt.passed,
        time_penalty=outcome.attempt.time_penalty,
        diagnosis=outcome.diagnosis,
        ideal_answer=outcome.ideal_answer,
        next_question=_question_response(outcome.next_question) if outcome.next_question else None,
        next_context=QuestionContext(
            index=outcome.next_root_index,
            total=sess.num_levels,
            depth=outcome.next_depth,
            is_timed=outcome.next_is_timed,
            started_at=outcome.next_started_at.isoformat() if outcome.next_started_at else "",
        ) if outcome.next_question else None,
        hydra_spawned=outcome.hydra_spawned,
        subquestion_count=len(outcome.subquestions),
        is_complete=outcome.is_complete,
        cleared_count=outcome.cleared_count if outcome.is_complete else None,
        total_attempts=outcome.total_attempts if outcome.is_complete else None,
    )


@router.delete("/{session_id}", status_code=204)
async def abandon_session(request: Request, session_id: str):
    conn = request.app.state.conn
    record = get_session_record(conn, session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    evict_session(session_id)
    # Mark as complete so it no longer appears in active list
    from scathach.db.repository import complete_session
    complete_session(conn, session_id)
