"""
API-facing session controller.

Because SessionRunner.run() is a long-running coroutine with I/O callbacks,
the HTTP API instead drives the session step-by-step:

    POST /sessions          → create + generate questions → return first question
    POST /sessions/{id}/answer → score → maybe spawn Hydra → return next question or done

State is held in memory (InMemorySession) AND persisted to the sessions table
after every answer (mirrors what SessionRunner does) so the server can survive
a restart and callers can resume via GET /sessions/{id}.

The in-memory registry maps session_id → InMemorySession.  Entries are evicted
when the session is marked complete or aborted.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from scathach.core.hydra import HydraError, spawn_subquestions
from scathach.core.question import DifficultyLevel, TimingMode
from scathach.core.scoring import ScoringError, score_answer
from scathach.core.session import GenerationError, generate_root_questions
from scathach.db.models import Attempt, Question, ReviewEntry, SessionRecord
from scathach.db.repository import (
    complete_session,
    create_session_record,
    get_latest_attempt,
    get_question,
    get_session_record,
    list_active_sessions,
    record_attempt,
    update_session_state,
    upsert_review_entry,
)
from scathach.llm.client import LLMClient


# ---------------------------------------------------------------------------
# In-memory session state
# ---------------------------------------------------------------------------


@dataclass
class InMemorySession:
    session_id: str
    conn: sqlite3.Connection
    client: LLMClient
    topic_id: int
    timing: TimingMode
    threshold: int
    num_levels: int
    # Stack mirrors SessionRunner: list of (question_list, parent_question_or_None)
    question_stack: list[tuple[list[Question], Optional[Question]]]
    cleared: list[Question]
    all_attempts: list[Attempt]
    root_questions: list[Question]
    # Timing state for the *current* question
    current_started_at: Optional[datetime] = field(default=None)
    current_is_timed: bool = field(default=False)

    # ------------------------------------------------------------------
    # Stack helpers (mirror SessionRunner._serialize_stack)
    # ------------------------------------------------------------------

    def _serialize_stack(self) -> str:
        frames = []
        for q_list, parent_q in self.question_stack:
            frames.append({
                "question_ids": [q.id for q in q_list],
                "parent_id": parent_q.id if parent_q is not None else None,
            })
        return json.dumps(frames)

    def _persist_state(self) -> None:
        stack_json = self._serialize_stack()
        cleared_json = json.dumps([q.id for q in self.cleared])
        update_session_state(self.conn, self.session_id, stack_json, cleared_json)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def current_question(self) -> Optional[Question]:
        """Return the question at the front of the active stack frame, or None if done."""
        while self.question_stack:
            current_group, _ = self.question_stack[-1]
            if current_group:
                return current_group[0]
            # Empty group — pop and maybe mark parent cleared
            self.question_stack.pop()
            # The parent's group now has that question at position 0 (it was moved to the
            # back on failure, or cleared from the group on pass — handled in advance()).
            # Nothing to do here other than continue iterating.
        return None

    def current_depth(self) -> int:
        return max(0, len(self.question_stack) - 1)

    def root_index_of_current(self) -> int:
        """1-based index of the root ancestor of the current question."""
        q = self.current_question()
        if q is None:
            return 0
        # Walk up parent chain
        ancestor = q
        while ancestor.parent_id is not None:
            for root in self.root_questions:
                if root.id == ancestor.parent_id:
                    ancestor = root
                    break
            else:
                break
        for i, root in enumerate(self.root_questions, 1):
            if root.id == ancestor.id:
                return i
        return 1

    def compute_timing(self) -> bool:
        """
        Determine whether the current question should be timed, using the same
        rules as SessionRunner.run().
        """
        q = self.current_question()
        if q is None:
            return False
        is_hydra = q.parent_id is not None
        has_prior_attempt = get_latest_attempt(self.conn, q.id) is not None
        return (
            self.timing == TimingMode.TIMED
            and not is_hydra
            and not has_prior_attempt
        )

    def stamp_started(self) -> datetime:
        """Record and return the UTC timestamp when the current question was presented."""
        self.current_is_timed = self.compute_timing()
        self.current_started_at = datetime.now(timezone.utc)
        return self.current_started_at

    # ------------------------------------------------------------------
    # Answer submission
    # ------------------------------------------------------------------

    async def submit_answer(
        self, answer_text: str, elapsed_s: Optional[float]
    ) -> AnswerOutcome:
        """
        Score the current question and advance the session state.

        Returns an AnswerOutcome with all the information the API route needs to
        build the response body.
        """
        question = self.current_question()
        if question is None:
            raise ValueError("No active question to answer.")

        is_timed = self.current_is_timed

        # Validate elapsed_s if provided
        if is_timed and elapsed_s is not None:
            dl = DifficultyLevel.from_int(question.difficulty)
            max_plausible = dl.penalty_limit_s * 2 + 30  # generous grace window
            elapsed_s = min(elapsed_s, max_plausible)
        elif is_timed and elapsed_s is None:
            # Client didn't send elapsed — use server-side measurement
            if self.current_started_at is not None:
                elapsed_s = (datetime.now(timezone.utc) - self.current_started_at).total_seconds()

        # Score the answer
        try:
            attempt, diagnosis = await score_answer(
                conn=self.conn,
                client=self.client,
                question=question,
                session_id=self.session_id,
                answer_text=answer_text,
                time_taken_s=elapsed_s,
                timed=is_timed,
                threshold=self.threshold,
            )
        except ScoringError as exc:
            raise ScoringError(str(exc)) from exc

        attempt = record_attempt(self.conn, attempt)
        self.all_attempts.append(attempt)

        # Advance stack state
        current_group, _ = self.question_stack[-1]
        hydra_spawned = False
        subquestions: list[Question] = []

        if attempt.passed:
            current_group.pop(0)
            depth = self.current_depth()
            if depth == 0:
                self.cleared.append(question)
            # Update review queues for both timed and untimed
            for queue_name in ("timed", "untimed"):
                upsert_review_entry(self.conn, ReviewEntry(
                    question_id=question.id,
                    queue=queue_name,
                    last_score=attempt.final_score,
                    state="learning",
                ))
        else:
            # Move failed question to end of its group
            current_group.pop(0)
            current_group.append(question)

            # Spawn Hydra sub-questions
            try:
                subquestions = await spawn_subquestions(
                    conn=self.conn,
                    client=self.client,
                    parent_question=question,
                    student_answer=answer_text,
                    diagnosis=diagnosis,
                )
                if subquestions:
                    self.question_stack.append((list(subquestions), question))
                    hydra_spawned = True
            except HydraError:
                pass  # Re-queue the same question without sub-questions

        # Drain empty frames from the top
        while self.question_stack and not self.question_stack[-1][0]:
            _, parent_q = self.question_stack.pop()
            if parent_q is not None and parent_q not in self.cleared:
                self.cleared.append(parent_q)

        # Persist state
        self._persist_state()

        # Check completion
        is_complete = len(self.question_stack) == 0
        if is_complete:
            complete_session(self.conn, self.session_id)

        # Stamp the next question's start time (if any)
        next_q = self.current_question() if not is_complete else None
        next_started_at: Optional[datetime] = None
        next_is_timed = False
        if next_q is not None:
            next_started_at = self.stamp_started()
            next_is_timed = self.current_is_timed

        return AnswerOutcome(
            attempt=attempt,
            diagnosis=diagnosis,
            ideal_answer=question.ideal_answer,
            hydra_spawned=hydra_spawned,
            subquestions=subquestions,
            is_complete=is_complete,
            next_question=next_q,
            next_started_at=next_started_at,
            next_is_timed=next_is_timed,
            next_depth=self.current_depth() if not is_complete else 0,
            next_root_index=self.root_index_of_current() if not is_complete else 0,
            cleared_count=len(self.cleared),
            total_attempts=len(self.all_attempts),
        )


@dataclass
class AnswerOutcome:
    attempt: Attempt
    diagnosis: str
    ideal_answer: str
    hydra_spawned: bool
    subquestions: list[Question]
    is_complete: bool
    next_question: Optional[Question]
    next_started_at: Optional[datetime]
    next_is_timed: bool
    next_depth: int
    next_root_index: int
    cleared_count: int
    total_attempts: int


# ---------------------------------------------------------------------------
# In-memory registry
# ---------------------------------------------------------------------------

_registry: dict[str, InMemorySession] = {}


def get_session(session_id: str) -> Optional[InMemorySession]:
    return _registry.get(session_id)


def evict_session(session_id: str) -> None:
    _registry.pop(session_id, None)


# ---------------------------------------------------------------------------
# Factory: create a brand-new session
# ---------------------------------------------------------------------------


async def create_session(
    conn: sqlite3.Connection,
    client: LLMClient,
    topic_id: int,
    timing: str,
    threshold: int,
    num_levels: int,
) -> InMemorySession:
    """
    Generate root questions, persist session row, register in memory,
    and return the InMemorySession ready for the first question.
    """
    import uuid

    timing_mode = TimingMode.TIMED if timing == "timed" else TimingMode.UNTIMED
    session_id = str(uuid.uuid4())

    root_questions = await generate_root_questions(conn, client, topic_id, num_levels)

    question_stack: list[tuple[list[Question], Optional[Question]]] = [
        (list(root_questions), None)
    ]

    sess = InMemorySession(
        session_id=session_id,
        conn=conn,
        client=client,
        topic_id=topic_id,
        timing=timing_mode,
        threshold=threshold,
        num_levels=num_levels,
        question_stack=question_stack,
        cleared=[],
        all_attempts=[],
        root_questions=root_questions,
    )

    # Serialize initial stack for persistence
    initial_stack = sess._serialize_stack()
    root_ids_json = json.dumps([q.id for q in root_questions])
    create_session_record(conn, SessionRecord(
        session_id=session_id,
        topic_id=topic_id,
        timing=timing,
        threshold=threshold,
        num_levels=num_levels,
        question_stack=initial_stack,
        cleared_ids="[]",
        root_ids=root_ids_json,
    ))

    # Stamp first question's start time
    sess.stamp_started()

    _registry[session_id] = sess
    return sess


# ---------------------------------------------------------------------------
# Factory: resume a session from DB
# ---------------------------------------------------------------------------


def resume_session(
    conn: sqlite3.Connection,
    client: LLMClient,
    session_id: str,
) -> Optional[InMemorySession]:
    """
    Load an active session from the DB into the in-memory registry.
    Returns None if the session doesn't exist or is already complete.
    """
    # If already in memory, return it directly
    if session_id in _registry:
        return _registry[session_id]

    record = get_session_record(conn, session_id)
    if record is None or record.status != "active":
        return None

    timing_mode = TimingMode.TIMED if record.timing == "timed" else TimingMode.UNTIMED

    # Restore root questions
    root_questions: list[Question] = []
    if record.root_ids:
        for qid in json.loads(record.root_ids):
            q = get_question(conn, qid)
            if q is not None:
                root_questions.append(q)

    # Restore question stack
    question_stack: list[tuple[list[Question], Optional[Question]]] = []
    if record.question_stack:
        for frame in json.loads(record.question_stack):
            questions: list[Question] = []
            for qid in frame["question_ids"]:
                q = get_question(conn, qid)
                if q is not None:
                    questions.append(q)
            parent_q: Optional[Question] = None
            if frame.get("parent_id") is not None:
                parent_q = get_question(conn, frame["parent_id"])
            question_stack.append((questions, parent_q))

    # Restore cleared list
    cleared: list[Question] = []
    if record.cleared_ids:
        for qid in json.loads(record.cleared_ids):
            q = get_question(conn, qid)
            if q is not None:
                cleared.append(q)

    sess = InMemorySession(
        session_id=session_id,
        conn=conn,
        client=client,
        topic_id=record.topic_id,
        timing=timing_mode,
        threshold=record.threshold,
        num_levels=record.num_levels,
        question_stack=question_stack,
        cleared=cleared,
        all_attempts=[],
        root_questions=root_questions,
    )

    sess.stamp_started()
    _registry[session_id] = sess
    return sess
