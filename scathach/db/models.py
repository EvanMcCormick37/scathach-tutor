"""
Python dataclasses mirroring the scathach SQLite tables.
These are plain data containers — no DB logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


@dataclass
class Topic:
    name: str
    content: str
    source_path: Optional[str] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    exam_support: float = 1.0          # stability updated by closed-book (--exam) sessions
    practice_support: float = 0.0      # accumulator updated by open-book sessions
    next_review_at: Optional[str] = None  # ISO datetime; NULL = never reviewed; only set on topic-review completion
    target_level: int = 4              # quest level cap used by topic-review


@dataclass
class Question:
    topic_id: int
    difficulty: int  # 1–6
    body: str
    ideal_answer: str
    parent_id: Optional[int] = None
    session_id: Optional[str] = None
    is_root: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None


@dataclass
class Attempt:
    question_id: int
    session_id: str
    answer_text: str
    raw_score: int   # 0–10, LLM quality score
    final_score: int  # 0–10, after time penalty
    passed: bool
    time_taken_s: Optional[float] = None
    time_penalty: bool = False
    timed: bool = False
    id: Optional[int] = None
    attempted_at: Optional[datetime] = None


@dataclass
class SessionRecord:
    session_id: str
    topic_id: int
    status: str = "active"         # 'active' | 'complete'
    session_type: str = "quest"    # 'quest' | 'drill'
    timing: str = "untimed"        # 'timed' | 'untimed'
    threshold: int = 7
    num_levels: int = 6
    is_exam: bool = False
    drill_level: Optional[int] = None   # set for drills; None for quests
    question_stack: Optional[str] = None   # JSON
    cleared_ids: Optional[str] = None      # JSON list of ints
    root_ids: Optional[str] = None         # JSON list of ints
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ReviewEntry:
    question_id: int
    queue: Literal["timed", "untimed"]
    last_score: Optional[int] = None
    last_attempted_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None
    stability: float = 1.0
    difficulty_fsrs: float = 0.3
    state: str = "new"  # new | learning | review | relearning
