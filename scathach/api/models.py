"""
Pydantic request/response schemas for the scathach FastAPI layer.
Kept separate from scathach.db.models (SQLite dataclasses).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


class TopicResponse(BaseModel):
    id: int
    name: str
    source_path: Optional[str]
    created_at: str


class TopicListResponse(BaseModel):
    topics: list[TopicResponse]


class IngestPasteRequest(BaseModel):
    text: str = Field(..., min_length=1)
    topic_name: str = Field(..., min_length=1)


class TopicRenameRequest(BaseModel):
    new_name: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


class QuestionResponse(BaseModel):
    id: int
    topic_id: int
    difficulty: int
    body: str
    parent_id: Optional[int]
    is_root: bool


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    topic_id: int
    timing: str = Field(default="untimed", pattern="^(timed|untimed)$")
    threshold: int = Field(default=7, ge=5, le=10)
    num_levels: int = Field(default=6, ge=1, le=6)


class QuestionContext(BaseModel):
    """Metadata about where a question sits in the session."""
    index: int        # 1-based position of root question (always within root list)
    total: int        # total root questions in session
    depth: int        # 0 = root, 1 = first Hydra level, etc.
    is_timed: bool
    started_at: str   # ISO UTC timestamp — frontend starts JS timer from here


class SessionCreateResponse(BaseModel):
    session_id: str
    topic_id: int
    question: QuestionResponse
    context: QuestionContext


class SessionSummaryResponse(BaseModel):
    session_id: str
    topic_id: int
    topic_name: str
    status: str        # "active" | "complete"
    timing: str
    threshold: int
    num_levels: int
    cleared_count: int
    total_questions: int
    created_at: str
    updated_at: str


class AnswerSubmitRequest(BaseModel):
    answer_text: str = Field(..., min_length=1)
    elapsed_s: Optional[float] = Field(default=None, ge=0)


class AnswerResultResponse(BaseModel):
    raw_score: int
    final_score: int
    passed: bool
    time_penalty: bool
    diagnosis: str
    ideal_answer: str
    # Next state
    next_question: Optional[QuestionResponse]
    next_context: Optional[QuestionContext]
    hydra_spawned: bool
    subquestion_count: int
    is_complete: bool
    # On completion
    cleared_count: Optional[int] = None
    total_attempts: Optional[int] = None


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


class ReviewQueueResponse(BaseModel):
    questions: list[QuestionResponse]
    queue: str    # "timed" | "untimed"
    total_due: int


class ReviewAnswerRequest(BaseModel):
    answer_text: str = Field(..., min_length=1)
    elapsed_s: Optional[float] = Field(default=None, ge=0)
    queue: str = Field(default="untimed", pattern="^(timed|untimed)$")
    timed: bool = Field(default=False)


class ReviewAnswerResponse(BaseModel):
    raw_score: int
    final_score: int
    passed: bool
    time_penalty: bool
    diagnosis: str
    ideal_answer: str
    next_review_at: Optional[str]  # ISO UTC


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    model: str
    quality_threshold: int
    main_timing: str
    review_timing: str
    hydra_in_super_review: bool
    open_doc_on_session: bool
    has_api_key: bool  # true/false — never return the key itself


class ConfigPatchRequest(BaseModel):
    model: Optional[str] = None
    quality_threshold: Optional[int] = Field(default=None, ge=5, le=10)
    main_timing: Optional[str] = Field(default=None, pattern="^(timed|untimed)$")
    review_timing: Optional[str] = Field(default=None, pattern="^(timed|untimed)$")
    hydra_in_super_review: Optional[bool] = None
    open_doc_on_session: Optional[bool] = None
    api_key: Optional[str] = None  # written to .env; never echoed back


class ConfigTestResponse(BaseModel):
    ok: bool
    message: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = "ok"
