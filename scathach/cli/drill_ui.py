"""
Drill session UI.

A drill is a flat, fixed-level quiz of freshly generated questions.
No FSRS scheduling, no Hydra. Passed questions feed into the review queues.
"""

from __future__ import annotations

import secrets
import sqlite3

from rich.console import Console
from rich.panel import Panel

from scathach.core.drill import DRILL_MAX_QUESTIONS, DrillError, generate_drill_questions
from scathach.core.question import DifficultyLevel, TimingMode
from scathach.core.scheduler import update_schedule
from scathach.core.scoring import ScoringError, score_answer
from scathach.db.models import Attempt
from scathach.db.repository import record_attempt
from scathach.llm.client import LLMClient

from scathach.cli.session_ui import (
    TossQuestion,
    _colorize_score,
    _difficulty_stars,
    _get_answer_timed,
    _get_answer_untimed,
)
from scathach.cli.review_ui import _render_summary, _show_result

console = Console()


async def run_drill_session(
    conn: sqlite3.Connection,
    client: LLMClient,
    topic_id: int,
    level: int,
    count: int,
    timing: TimingMode,
    threshold: int,
) -> None:
    """
    Generate `count` fresh questions at `level` and run a flat answer/score loop.

    Passed questions are entered into both FSRS queues. No Hydra, no repeat-on-fail.
    """
    dl = DifficultyLevel.from_int(level)
    max_q = DRILL_MAX_QUESTIONS[level]
    actual_count = min(count, max_q)

    console.print(Panel(
        f"[bold cyan]Generating {actual_count} level-{level} ({dl.label}) question(s)…[/bold cyan]",
        title="Drill",
        border_style="cyan",
    ))

    try:
        questions = await generate_drill_questions(
            conn=conn, client=client,
            topic_id=topic_id, level=level, count=actual_count,
        )
    except DrillError as exc:
        console.print(f"[red]Drill setup failed:[/red] {exc}")
        return

    if not questions:
        console.print("[yellow]No questions were generated. Try again.[/yellow]")
        return

    console.print(Panel(
        f"[bold cyan]{len(questions)} question(s) ready[/bold cyan] "
        f"— level {level} ({dl.label}), {timing.value} mode.",
        title="Drill",
        border_style="cyan",
    ))

    session_id = secrets.token_hex(3)[:5]
    all_attempts: list[Attempt] = []

    for i, question in enumerate(questions, start=1):
        console.print()
        console.print(Panel(
            question.body,
            title=f"Drill {i}/{len(questions)} — {_difficulty_stars(level)} ({dl.label})",
            border_style="blue",
        ))

        try:
            if timing == TimingMode.TIMED:
                answer_text, time_taken_s = await _get_answer_timed(question, allow_toss=False)
            else:
                answer_text, time_taken_s = await _get_answer_untimed(question, allow_toss=False)
        except TossQuestion:
            continue

        try:
            attempt, diagnosis = await score_answer(
                conn=conn, client=client, question=question,
                session_id=session_id, answer_text=answer_text,
                time_taken_s=time_taken_s,
                timed=timing == TimingMode.TIMED,
                threshold=threshold,
            )
        except ScoringError as exc:
            console.print(f"[red]Scoring failed:[/red] {exc}. Skipping.")
            continue

        attempt = record_attempt(conn, attempt)
        all_attempts.append(attempt)
        _show_result(attempt, diagnosis, question.ideal_answer)

        for queue in ("timed", "untimed"):
            update_schedule(conn, question.id, attempt.final_score, queue)

    if all_attempts:
        _render_summary(all_attempts, title="Drill Summary")
