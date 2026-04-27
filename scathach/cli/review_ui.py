"""
Review and super-review session UIs.

`run_review_session`       — levels 1–2, FSRS scheduling, no Hydra protocol.
`run_super_review_session` — levels 3–6, FSRS scheduling, optional Hydra protocol.

Both share the same answer/score/timer flow and update the same FSRS queue tables.
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scathach.config import OnFailedReview
from scathach.core.hydra import HydraError, spawn_subquestions
from scathach.core.question import DifficultyLevel, TimingMode
from scathach.core.scheduler import get_scheduled_questions, update_schedule
from scathach.core.scoring import ScoringError, score_answer
from scathach.db.models import Attempt, Question
from scathach.db.repository import delete_question, record_attempt
from scathach.llm.client import LLMClient

from scathach.cli.session_ui import (
    TossQuestion,
    _colorize_score,
    _difficulty_stars,
    _get_answer_timed,
    _get_answer_untimed,
)

console = Console()

# Difficulty bands
REVIEW_MIN = 1
REVIEW_MAX = 2
SUPER_REVIEW_MIN = 3
SUPER_REVIEW_MAX = 6


# ---------------------------------------------------------------------------
# Standard review (levels 1–2, no Hydra)
# ---------------------------------------------------------------------------


async def run_review_session(
    conn: sqlite3.Connection,
    client: LLMClient,
    queue: str,
    timing: TimingMode,
    threshold: int,
    limit: int = 20,
    on_failed: OnFailedReview = OnFailedReview.CHOOSE,
    topic_id: Optional[int] = None,
) -> None:
    """
    Run a standard review session (difficulty 1–2).

    FSRS scheduling determines which questions are due.
    No Hydra Protocol — failed questions are scheduled sooner by FSRS.
    When a question is failed, `on_failed` controls whether it is repeated
    immediately ('repeat'), skipped ('skip'), or the user is asked ('choose').
    """
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    questions = get_scheduled_questions(
        conn, queue, limit=limit, now=now,
        min_difficulty=REVIEW_MIN, max_difficulty=REVIEW_MAX,
        order_by_score=False, topic_id=topic_id,
    )

    if not questions:
        console.print(
            f"[green]No level 1–2 questions due in the [bold]{queue}[/bold] queue. Great work![/green]"
        )
        return

    console.print(Panel(
        f"[bold cyan]{len(questions)} question(s) due[/bold cyan] "
        f"(levels 1–2) in the [bold]{queue}[/bold] review queue.",
        title="Review Session",
        border_style="cyan",
    ))

    session_id = secrets.token_hex(3)[:5]
    all_attempts: list[Attempt] = []

    queue_list = list(questions)
    i = 0

    while i < len(queue_list):
        question = queue_list[i]
        i += 1
        dl = DifficultyLevel.from_int(question.difficulty)
        console.print()
        console.print(Panel(
            question.body,
            title=f"Review {i}/{len(queue_list)} — {_difficulty_stars(question.difficulty)} ({dl.label})",
            border_style="blue",
        ))

        try:
            answer_text, time_taken_s = await _collect_answer(question, timing)
        except TossQuestion:
            delete_question(conn, question.id)
            console.print("[dim]Question tossed and permanently deleted.[/dim]")
            continue

        try:
            attempt, diagnosis = await score_answer(
                conn=conn, client=client, question=question,
                session_id=session_id, answer_text=answer_text,
                time_taken_s=time_taken_s,
                timed=timing == TimingMode.TIMED,
                threshold=threshold,
                ideal_answer=question.ideal_answer,
            )
        except ScoringError as exc:
            console.print(f"[red]Scoring failed:[/red] {exc}. Skipping.")
            continue

        attempt = record_attempt(conn, attempt)
        all_attempts.append(attempt)
        update_schedule(conn, question.id, attempt.final_score, queue)
        _show_result(attempt, diagnosis, question.ideal_answer)

        if not attempt.passed and _should_repeat(on_failed):
            queue_list.append(question)

    if all_attempts:
        _render_summary(all_attempts, title="Review Summary")


# ---------------------------------------------------------------------------
# Super-review (levels 3–6, optional Hydra)
# ---------------------------------------------------------------------------


async def run_super_review_session(
    conn: sqlite3.Connection,
    client: LLMClient,
    queue: str,
    timing: TimingMode,
    threshold: int,
    limit: int = 10,
    hydra_enabled: bool = False,
    on_failed: OnFailedReview = OnFailedReview.CHOOSE,
    topic_id: Optional[int] = None,
) -> None:
    """
    Run a super-review session (difficulty 3–6).

    Questions are ordered: difficulty ASC, then worst-score-first within each tier.
    Hydra Protocol is optional (controlled by `hydra_enabled`).
    FSRS scheduling determines which questions are due.
    When a question is failed, `on_failed` controls whether it is repeated
    immediately ('repeat'), skipped ('skip'), or the user is asked ('choose').
    """
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    questions = get_scheduled_questions(
        conn, queue, limit=limit, now=now,
        min_difficulty=SUPER_REVIEW_MIN, max_difficulty=SUPER_REVIEW_MAX,
        order_by_score=True, topic_id=topic_id,
    )

    if not questions:
        console.print(
            f"[green]No level 3–6 questions due in the [bold]{queue}[/bold] queue.[/green]"
        )
        return

    hydra_label = " [Hydra enabled]" if hydra_enabled else ""
    console.print(Panel(
        f"[bold magenta]{len(questions)} question(s) due[/bold magenta] "
        f"(levels 3–6) in the [bold]{queue}[/bold] super-review queue.{hydra_label}",
        title="Super-Review Session",
        border_style="magenta",
    ))

    session_id = secrets.token_hex(3)[:5]
    all_attempts: list[Attempt] = []

    # Use a list so Hydra sub-questions can be appended mid-session
    queue_list = list(questions)
    i = 0

    while i < len(queue_list):
        question = queue_list[i]
        i += 1
        total_visible = len(queue_list)

        dl = DifficultyLevel.from_int(question.difficulty)
        is_sub = question.parent_id is not None
        border = "yellow" if is_sub else "magenta"
        depth_label = "[Hydra sub-question] " if is_sub else ""
        console.print()
        console.print(Panel(
            question.body,
            title=f"{depth_label}Super-Review {i}/{total_visible} — {_difficulty_stars(question.difficulty)} ({dl.label})",
            border_style=border,
        ))

        try:
            answer_text, time_taken_s = await _collect_answer(question, timing)
        except TossQuestion:
            delete_question(conn, question.id)
            console.print("[dim]Question tossed and permanently deleted.[/dim]")
            continue

        try:
            attempt, diagnosis = await score_answer(
                conn=conn, client=client, question=question,
                session_id=session_id, answer_text=answer_text,
                time_taken_s=time_taken_s,
                timed=timing == TimingMode.TIMED,
                threshold=threshold,
                ideal_answer=question.ideal_answer,
            )
        except ScoringError as exc:
            console.print(f"[red]Scoring failed:[/red] {exc}. Skipping.")
            continue

        attempt = record_attempt(conn, attempt)
        all_attempts.append(attempt)
        update_schedule(conn, question.id, attempt.final_score, queue)
        _show_result(attempt, diagnosis, question.ideal_answer)

        # Hydra spawning on failure (only if enabled)
        if not attempt.passed and hydra_enabled:
            try:
                subquestions = await spawn_subquestions(
                    conn=conn, client=client,
                    parent_question=question,
                    student_answer=answer_text,
                    diagnosis=diagnosis,
                )
                if subquestions:
                    console.print(
                        f"\n[magenta bold]🐍 Hydra:[/magenta bold] {len(subquestions)} sub-question(s) added to this session."
                    )
                    # Insert sub-questions immediately after the current position
                    queue_list[i:i] = subquestions
            except HydraError as exc:
                console.print(f"[dim]Hydra spawn failed ({exc}), continuing.[/dim]")

        # Repeat on failure (appended after any Hydra sub-questions)
        if not attempt.passed and _should_repeat(on_failed):
            queue_list.append(question)

    if all_attempts:
        _render_summary(all_attempts, title="Super-Review Summary")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _collect_answer(question: Question, timing: TimingMode) -> tuple[str, Optional[float]]:
    if timing == TimingMode.TIMED:
        return await _get_answer_timed(question, allow_toss=True)
    return await _get_answer_untimed(question, allow_toss=True)


def _show_result(attempt: Attempt, diagnosis: str, ideal_answer: str) -> None:
    result = "[green]PASSED[/green]" if attempt.passed else "[red]FAILED[/red]"
    if attempt.time_penalty:
        score_str = f"[yellow]Raw: {attempt.raw_score}/10 → Final: {attempt.final_score}/10 [½ time penalty][/yellow]"
    else:
        score_str = _colorize_score(attempt.final_score)
    console.print(f"\n{result}  {score_str}")
    console.print(f"[dim]Diagnosis: {diagnosis}[/dim]")
    console.print(Panel(
        ideal_answer,
        title="Ideal Answer",
        border_style="green" if attempt.passed else "yellow",
    ))


def _should_repeat(on_failed: OnFailedReview) -> bool:
    """Return True if the failed question should be repeated immediately."""
    if on_failed == OnFailedReview.REPEAT:
        return True
    if on_failed == OnFailedReview.SKIP:
        return False
    # CHOOSE — prompt the user
    console.print("\n[yellow]Would you like to repeat this question?[/yellow] \\[Y/n] ", end="")
    raw = input().strip().lower()
    return raw in ("", "y", "yes")


def _render_summary(attempts: list[Attempt], title: str = "Summary") -> None:
    console.print()
    table = Table(title=title, show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    total = len(attempts)
    passed = sum(1 for a in attempts if a.passed)
    penalized = sum(1 for a in attempts if a.time_penalty)
    avg_raw = sum(a.raw_score for a in attempts) / total
    avg_final = sum(a.final_score for a in attempts) / total

    table.add_row("Questions reviewed", str(total))
    table.add_row("Passed", str(passed))
    table.add_row("Time-penalized", str(penalized))
    table.add_row("Avg raw score", f"{avg_raw:.1f}/10")
    table.add_row("Avg final score", f"{avg_final:.1f}/10")
    console.print(table)
