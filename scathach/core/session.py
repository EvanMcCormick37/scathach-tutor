"""
Session orchestration: question generation and the SessionRunner state machine.

The SessionRunner is I/O-agnostic. It drives the learning session logic and
emits events that the TUI layer (session_ui.py) responds to.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Optional

from scathach.core.hydra import HydraError, spawn_subquestions
from scathach.core.question import DifficultyLevel, TimingMode
from scathach.core.scoring import ScoringError, score_answer
from scathach.db.models import Attempt, Question
from scathach.db.repository import (
    get_latest_attempt,
    get_prior_root_questions,
    get_topic_by_id,
    insert_question,
    record_attempt,
    upsert_review_entry,
)
from scathach.db.models import ReviewEntry
from scathach.llm.client import LLMClient
from scathach.llm.parsing import ParseError, parse_questions_response
from scathach.llm.prompts import render_question_generation_prompt


class GenerationError(Exception):
    """Raised when question generation fails and cannot recover."""


# ---------------------------------------------------------------------------
# Question generation (standalone, also used by SessionRunner)
# ---------------------------------------------------------------------------


async def generate_root_questions(
    conn: sqlite3.Connection,
    client: LLMClient,
    topic_id: int,
    num_levels: int = 6,
) -> list[Question]:
    """
    Generate root questions for a topic using the LLM.

    Returns questions ordered difficulty 1 → num_levels with ids set.
    Raises GenerationError on failure.
    """
    topic = get_topic_by_id(conn, topic_id)
    if topic is None:
        raise GenerationError(f"Topic id={topic_id} not found.")

    prior_questions = get_prior_root_questions(conn, topic_id, limit_per_level=25)
    system_prompt, user_prompt = render_question_generation_prompt(
        topic.content,
        prior_questions=prior_questions or None,
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
                    + "\n\nIMPORTANT: Your previous response could not be parsed. "
                    "Respond with ONLY the raw JSON array, no other text."
                )
                continue
            raise GenerationError(
                f"LLM returned unparseable question generation response after retry. "
                f"Parse error: {exc}\nRaw (truncated):\n{(raw_response or '')[:500]}"
            ) from exc

    if parsed is None:
        raise GenerationError("Question generation produced no output.")

    parsed = [q for q in parsed if 1 <= q["difficulty"] <= num_levels]
    parsed.sort(key=lambda q: q["difficulty"])

    questions: list[Question] = []
    for q_data in parsed:
        q = insert_question(
            conn,
            Question(
                topic_id=topic_id,
                difficulty=q_data["difficulty"],
                body=q_data["body"],
                ideal_answer=q_data["ideal_answer"],
                is_root=True,
            ),
        )
        questions.append(q)

    return questions


# ---------------------------------------------------------------------------
# Session state machine
# ---------------------------------------------------------------------------


class SessionState(Enum):
    IDLE = auto()
    GENERATING = auto()
    QUESTION_PRESENTED = auto()
    AWAITING_ANSWER = auto()
    SCORING = auto()
    SHOWING_RESULT = auto()
    HYDRA_SPAWNING = auto()
    SESSION_COMPLETE = auto()
    ABORTED = auto()


@dataclass
class SessionEvent:
    """Base class for events emitted by SessionRunner."""


@dataclass
class QuestionPresented(SessionEvent):
    question: Question
    index: int        # 1-based position in current queue
    total: int        # size of current queue
    depth: int        # 0 = root, 1 = first Hydra level, etc.


@dataclass
class AnswerScored(SessionEvent):
    attempt: Attempt
    diagnosis: str
    ideal_answer: str   # shown on failure


@dataclass
class HydraSpawned(SessionEvent):
    subquestions: list[Question]
    parent_question: Question


@dataclass
class SessionComplete(SessionEvent):
    cleared_questions: list[Question]
    attempts: list[Attempt]


@dataclass
class SessionAborted(SessionEvent):
    reason: str


# Callback types
AnswerProvider = Callable[[Question, bool], Awaitable[tuple[str, Optional[float]]]]
EventHandler = Callable[[SessionEvent], Awaitable[None]]


@dataclass
class SessionConfig:
    topic_id: int
    timing: TimingMode = TimingMode.UNTIMED
    threshold: int = 7
    num_levels: int = 6


class SessionRunner:
    """
    Orchestrates a complete learning session.

    I/O-agnostic — all user interaction is provided via callbacks:
    - `answer_provider`: called with a Question, returns (answer_text, time_taken_s)
    - `event_handler`: called with each SessionEvent for rendering

    This allows the same SessionRunner to be driven by the TUI, tests, or any
    other interface.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: LLMClient,
        config: SessionConfig,
        answer_provider: AnswerProvider,
        event_handler: EventHandler,
    ) -> None:
        self.conn = conn
        self.client = client
        self.config = config
        self.answer_provider = answer_provider
        self.event_handler = event_handler

        self.session_id = str(uuid.uuid4())
        self.state = SessionState.IDLE
        self._cleared: list[Question] = []
        self._all_attempts: list[Attempt] = []

    async def run(self) -> None:
        """
        Execute the full session. On completion or abort, emits the terminal event.

        The question queue is a stack of lists. Each list is a group of questions
        at the same Hydra depth. When a group is cleared, we return to the parent
        group. The stack starts with the 6 root questions.
        """
        # --- Phase: Generate questions ---
        self.state = SessionState.GENERATING
        try:
            root_questions = await generate_root_questions(
                self.conn, self.client, self.config.topic_id, self.config.num_levels
            )
        except GenerationError as exc:
            self.state = SessionState.ABORTED
            await self.event_handler(SessionAborted(reason=str(exc)))
            return

        # Stack of (question_list, parent_question_or_None)
        # Each frame = (questions_to_answer, parent_that_was_failed)
        question_stack: list[tuple[list[Question], Optional[Question]]] = [
            (root_questions, None)
        ]

        while question_stack:
            current_group, parent_q = question_stack[-1]

            if not current_group:
                # Group cleared — pop back to parent
                question_stack.pop()
                # If parent was a root question, mark it cleared and continue
                if parent_q is not None and parent_q not in self._cleared:
                    self._cleared.append(parent_q)
                continue

            question = current_group[0]
            depth = len(question_stack) - 1

            # --- Present question ---
            self.state = SessionState.QUESTION_PRESENTED
            await self.event_handler(QuestionPresented(
                question=question,
                index=root_questions.index(self._root_ancestor(question, root_questions)) + 1
                      if depth == 0 else 1,
                total=self.config.num_levels,
                depth=depth,
            ))

            # --- Determine effective timing for this attempt ---
            # Only time if: session is timed, question is not a Hydra sub-question,
            # and the question has never been attempted before.
            is_hydra = question.parent_id is not None
            has_prior_attempt = get_latest_attempt(self.conn, question.id) is not None
            effective_timed = (
                self.config.timing == TimingMode.TIMED
                and not is_hydra
                and not has_prior_attempt
            )

            # --- Await answer ---
            self.state = SessionState.AWAITING_ANSWER
            answer_text, time_taken_s = await self.answer_provider(question, effective_timed)

            # --- Score ---
            self.state = SessionState.SCORING
            try:
                attempt, diagnosis = await score_answer(
                    conn=self.conn,
                    client=self.client,
                    question=question,
                    session_id=self.session_id,
                    answer_text=answer_text,
                    time_taken_s=time_taken_s,
                    timed=effective_timed,
                    threshold=self.config.threshold,
                )
            except ScoringError as exc:
                self.state = SessionState.ABORTED
                await self.event_handler(SessionAborted(reason=f"Scoring failed: {exc}"))
                return

            attempt = record_attempt(self.conn, attempt)
            self._all_attempts.append(attempt)

            # --- Emit result ---
            self.state = SessionState.SHOWING_RESULT
            await self.event_handler(AnswerScored(
                attempt=attempt,
                diagnosis=diagnosis,
                ideal_answer=question.ideal_answer,
            ))

            if attempt.passed:
                # Question cleared — advance in current group
                current_group.pop(0)
                if depth == 0:
                    self._cleared.append(question)
                # Write to both queues so both review and super-review see the result
                for queue_name in ("timed", "untimed"):
                    upsert_review_entry(self.conn, ReviewEntry(
                        question_id=question.id,
                        queue=queue_name,
                        last_score=attempt.final_score,
                        state="learning",
                    ))
            else:
                # Failed — spawn sub-questions and push them onto the stack
                self.state = SessionState.HYDRA_SPAWNING
                try:
                    subquestions = await spawn_subquestions(
                        conn=self.conn,
                        client=self.client,
                        parent_question=question,
                        student_answer=answer_text,
                        diagnosis=diagnosis,
                    )
                except HydraError:
                    # If Hydra fails, just re-queue the same question
                    subquestions = []

                await self.event_handler(HydraSpawned(
                    subquestions=subquestions,
                    parent_question=question,
                ))

                # Move the failed question to the end of its group (will retry after sub-questions)
                current_group.pop(0)
                current_group.append(question)

                if subquestions:
                    # Push sub-questions as a new frame
                    question_stack.append((list(subquestions), question))

        # --- Session complete ---
        self.state = SessionState.SESSION_COMPLETE
        await self.event_handler(SessionComplete(
            cleared_questions=self._cleared,
            attempts=self._all_attempts,
        ))

    def _root_ancestor(self, question: Question, roots: list[Question]) -> Question:
        """Return the root question in `roots` that is an ancestor of `question`."""
        for r in roots:
            if r.id == question.id or r.id == question.parent_id:
                return r
        return roots[0]
