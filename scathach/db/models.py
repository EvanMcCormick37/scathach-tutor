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


@dataclass
class Question:
    topic_id: int
    difficulty: int  # 1–6
    body: str
    ideal_answer: str
    parent_id: Optional[int] = None
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
class ReviewEntry:
    question_id: int
    queue: Literal["timed", "untimed"]
    last_score: Optional[int] = None
    last_attempted_at: Optional[datetime] = None
    next_review_at: Optional[datetime] = None
    stability: float = 1.0
    difficulty_fsrs: float = 0.3
    state: str = "new"  # new | learning | review | relearning
