"""
Review session UI.

`run_review_session` — levels 1–2, FSRS scheduling, no Hydra protocol.
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scathach.config import OnFailedReview
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

REVIEW_MIN = 1
REVIEW_MAX = 2


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
    source_path: Optional[str] = None,
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

    # Pre-load source paths for all topics (used by Ctrl+O in answer prompt)
    _sp: dict[int, Optional[str]] = {
        r["id"]: r["source_path"]
        for r in conn.execute("SELECT id, source_path FROM topics").fetchall()
    }

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
            answer_text, time_taken_s = await _collect_answer(question, timing, source_path=_sp.get(question.topic_id))
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
        update_schedule(conn, question.id, attempt.final_score, queue, difficulty=question.difficulty)
        _show_result(attempt, diagnosis, question.ideal_answer)

        if not attempt.passed and _should_repeat(on_failed):
            queue_list.append(question)

    if all_attempts:
        _render_summary(all_attempts, title="Review Summary")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _collect_answer(question: Question, timing: TimingMode, source_path: Optional[str] = None) -> tuple[str, Optional[float]]:
    if timing == TimingMode.TIMED:
        return await _get_answer_timed(question, allow_toss=True, source_path=source_path)
    return await _get_answer_untimed(question, allow_toss=True, source_path=source_path)


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
