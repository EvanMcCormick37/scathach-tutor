"""
Session orchestration: question generation and the SessionRunner state machine.

The SessionRunner is I/O-agnostic. It drives the learning session logic and
emits events that the TUI layer (session_ui.py) responds to.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Optional

from scathach.core.hydra import HydraError, spawn_subquestions
from scathach.core.question import DifficultyLevel, TimingMode
from scathach.core.scoring import ScoringError, score_answer
from scathach.core.topic_support import apply_topic_support_update, compute_new_support
from scathach.db.models import Attempt, Question, SessionRecord
from scathach.db.repository import (
    complete_session,
    create_session_record,
    get_latest_attempt,
    get_prior_root_questions,
    get_question,
    get_topic_by_id,
    insert_question,
    record_attempt,
    update_session_state,
    upsert_review_entry,
)
from scathach.db.models import ReviewEntry
from scathach.llm.client import LLMClient, LLMError
from scathach.llm.parsing import ParseError, QUESTIONS_RESPONSE_SCHEMA, validate_questions_response
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

    try:
        result = await client.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=QUESTIONS_RESPONSE_SCHEMA,
        )
        parsed = validate_questions_response(result)
    except (LLMError, ParseError) as exc:
        raise GenerationError(f"Question generation failed: {exc}") from exc

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
class GeneratingCurriculum(SessionEvent):
    topic_name: str


@dataclass
class CurriculumReady(SessionEvent):
    num_questions: int


@dataclass
class HydraSpawning(SessionEvent):
    parent_question: Question


@dataclass
class HydraSpawned(SessionEvent):
    subquestions: list[Question]
    parent_question: Question
    num_levels: int = 6


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
    hydra_retry_parent: bool = True


class SessionRunner:
    """
    Orchestrates a complete learning session.

    I/O-agnostic — all user interaction is provided via callbacks:
    - `answer_provider`: called with a Question, returns (answer_text, time_taken_s)
    - `event_handler`: called with each SessionEvent for rendering

    Pass `restored_record` to resume a previously interrupted session.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: LLMClient,
        config: SessionConfig,
        answer_provider: AnswerProvider,
        event_handler: EventHandler,
        restored_record: Optional[SessionRecord] = None,
    ) -> None:
        self.conn = conn
        self.client = client
        self.config = config
        self.answer_provider = answer_provider
        self.event_handler = event_handler

        if restored_record is not None:
            self.session_id = restored_record.session_id
            self._restored_record = restored_record
        else:
            self.session_id = secrets.token_hex(3)
            self._restored_record = None

        self.state = SessionState.IDLE
        self._cleared: list[Question] = []
        self._all_attempts: list[Attempt] = []

    # ------------------------------------------------------------------
    # State persistence helpers
    # ------------------------------------------------------------------

    def _serialize_stack(
        self, question_stack: list[tuple[list[Question], Optional[Question], int, list[int]]]
    ) -> str:
        """Serialize question stack to JSON (question IDs only)."""
        frames = []
        for q_list, parent_q, orig_size, seen_ids in question_stack:
            frames.append({
                "question_ids": [q.id for q in q_list],
                "parent_id": parent_q.id if parent_q is not None else None,
                "orig_size": orig_size,
                "seen_ids": seen_ids,
            })
        return json.dumps(frames)

    def _deserialize_stack(
        self, stack_json: str
    ) -> list[tuple[list[Question], Optional[Question], int, list[int]]]:
        """Restore question stack from JSON by fetching questions from DB."""
        frames = json.loads(stack_json)
        result: list[tuple[list[Question], Optional[Question], int, list[int]]] = []
        for frame in frames:
            questions: list[Question] = []
            for qid in frame["question_ids"]:
                q = get_question(self.conn, qid)
                if q is not None:
                    questions.append(q)
            parent_q: Optional[Question] = None
            if frame["parent_id"] is not None:
                parent_q = get_question(self.conn, frame["parent_id"])
            orig_size: int = frame.get("orig_size", len(questions))
            seen_ids: list[int] = frame.get("seen_ids", [])
            result.append((questions, parent_q, orig_size, seen_ids))
        return result

    def _persist_state(
        self, question_stack: list[tuple[list[Question], Optional[Question], int, list[int]]]
    ) -> None:
        """Write current stack + cleared list to the sessions table."""
        stack_json = self._serialize_stack(question_stack)
        cleared_json = json.dumps([q.id for q in self._cleared])
        update_session_state(self.conn, self.session_id, stack_json, cleared_json)

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """
        Execute the full session. On completion or abort, emits the terminal event.

        The question queue is a stack of lists. Each list is a group of questions
        at the same Hydra depth. When a group is cleared, we return to the parent
        group. The stack starts with the root questions (generated or restored).
        """
        # Fetch topic once for name display and support tracking.
        _topic = get_topic_by_id(self.conn, self.config.topic_id)
        _topic_target_level: int = _topic.target_level if _topic else 4
        _topic_support: float = _topic.support if _topic else 1.0

        if self._restored_record is not None:
            # --- Resume: restore stack from DB ---
            self.state = SessionState.GENERATING  # reuse state for "loading"
            root_questions = self._load_root_questions(self._restored_record)
            question_stack = self._deserialize_stack(self._restored_record.question_stack)
            # Restore cleared list
            if self._restored_record.cleared_ids:
                for qid in json.loads(self._restored_record.cleared_ids):
                    q = get_question(self.conn, qid)
                    if q is not None:
                        self._cleared.append(q)
        else:
            # --- Fresh start: generate questions and create session row ---
            self.state = SessionState.GENERATING
            topic = _topic
            topic_name = topic.name if topic else f"topic {self.config.topic_id}"
            await self.event_handler(GeneratingCurriculum(topic_name=topic_name))
            try:
                root_questions = await generate_root_questions(
                    self.conn, self.client, self.config.topic_id, self.config.num_levels
                )
            except GenerationError as exc:
                self.state = SessionState.ABORTED
                await self.event_handler(SessionAborted(reason=str(exc)))
                return
            await self.event_handler(CurriculumReady(num_questions=len(root_questions)))

            # Stack of (question_list, parent_question_or_None, orig_size, seen_ids)
            # NOTE: use a copy so root_questions stays an immutable snapshot for index lookups.
            question_stack: list[tuple[list[Question], Optional[Question], int, list[int]]] = [
                (list(root_questions), None, len(root_questions), [])
            ]

            # Persist initial session row
            initial_stack = self._serialize_stack(question_stack)
            root_ids_json = json.dumps([q.id for q in root_questions])
            create_session_record(self.conn, SessionRecord(
                session_id=self.session_id,
                topic_id=self.config.topic_id,
                timing=self.config.timing.value,
                threshold=self.config.threshold,
                num_levels=self.config.num_levels,
                question_stack=initial_stack,
                cleared_ids="[]",
                root_ids=root_ids_json,
            ))

        try:
            while question_stack:
                current_group, parent_q, orig_size, seen_ids = question_stack[-1]

                if not current_group:
                    # Hydra frame cleared — pop back to parent frame.
                    # The parent question stays in its frame; if hydra_retry_parent=True
                    # it is at position 0 and will be re-asked immediately.
                    question_stack.pop()
                    self._persist_state(question_stack)
                    continue

                question = current_group[0]
                depth = len(question_stack) - 1

                # --- Compute progress index for the header ---
                if depth == 0:
                    q_index = root_questions.index(self._root_ancestor(question, root_questions)) + 1
                    q_total = self.config.num_levels
                else:
                    # Track first-seen order so retries show the same ordinal.
                    if question.id not in seen_ids:
                        seen_ids.append(question.id)
                    q_index = seen_ids.index(question.id) + 1
                    q_total = orig_size

                # --- Present question ---
                self.state = SessionState.QUESTION_PRESENTED
                await self.event_handler(QuestionPresented(
                    question=question,
                    index=q_index,
                    total=q_total,
                    depth=depth,
                ))

                # --- Determine effective timing for this attempt ---
                # Only time if: session is timed, question is not a Hydra sub-question,
                # and the question has never been attempted before in this session.
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
                        document_content=_topic.content if _topic else None,
                    )
                except ScoringError as exc:
                    self.state = SessionState.ABORTED
                    await self.event_handler(SessionAborted(reason=f"Scoring failed: {exc}"))
                    return

                attempt = record_attempt(self.conn, attempt)
                self._all_attempts.append(attempt)

                # --- Update topic support for first-time root questions ---
                if not is_hydra and not has_prior_attempt:
                    _topic_support = compute_new_support(
                        _topic_support,
                        attempt.final_score,
                        question.difficulty,
                        _topic_target_level,
                    )
                    apply_topic_support_update(
                        self.conn, self.config.topic_id, _topic_support
                    )

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
                    await self.event_handler(HydraSpawning(parent_question=question))
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
                        num_levels=self.config.num_levels,
                    ))

                    if subquestions:
                        if self.config.hydra_retry_parent:
                            # Keep parent at position 0 — re-asked immediately after hydra clears.
                            pass
                        else:
                            # Drop parent from queue — session moves on after hydra.
                            current_group.pop(0)
                        question_stack.append((list(subquestions), question, len(subquestions), []))
                    else:
                        # Hydra generation failed — move parent to end for a later retry.
                        current_group.pop(0)
                        current_group.append(question)

                # Persist state after each answer so Ctrl+C can resume from here
                self._persist_state(question_stack)

        except (KeyboardInterrupt, asyncio.CancelledError):
            # Save state at point of interruption so session can be resumed
            self._persist_state(question_stack)
            self.state = SessionState.ABORTED
            await self.event_handler(SessionAborted(reason="Session interrupted. Resume with: scathach quest --resume " + self.session_id))
            return

        # --- Session complete ---
        complete_session(self.conn, self.session_id)
        self.state = SessionState.SESSION_COMPLETE
        await self.event_handler(SessionComplete(
            cleared_questions=self._cleared,
            attempts=self._all_attempts,
        ))

    def _load_root_questions(self, record: SessionRecord) -> list[Question]:
        """Reconstruct the root question list from a session record."""
        if not record.root_ids:
            return []
        ids: list[int] = json.loads(record.root_ids)
        questions: list[Question] = []
        for qid in ids:
            q = get_question(self.conn, qid)
            if q is not None:
                questions.append(q)
        return questions

    def _root_ancestor(self, question: Question, roots: list[Question]) -> Question:
        """Return the root question in `roots` that is an ancestor of `question`."""
        for r in roots:
            if r.id == question.id or r.id == question.parent_id:
                return r
        return roots[0]
