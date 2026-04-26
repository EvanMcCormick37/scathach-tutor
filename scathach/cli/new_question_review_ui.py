"""
New-Question Review UI.

Identifies struggling or stale topic+level combinations, generates one fresh
question per eligible pair, then presents them topic-by-topic in ascending
difficulty order for a flat answer/score loop.

Passed questions are added to both FSRS review queues.
"""

from __future__ import annotations

import secrets
import sqlite3

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scathach.core.question import DifficultyLevel, TimingMode
from scathach.core.scheduler import update_schedule
from scathach.core.scoring import ScoringError, score_answer
from scathach.core.topic_review import (
    TopicReviewError,
    generate_topic_review_question,
    get_eligible_pairs,
)
from scathach.db.models import Attempt, Question
from scathach.db.repository import get_topic_by_id, record_attempt
from scathach.llm.client import LLMClient

from scathach.cli.session_ui import (
    _difficulty_stars,
    _get_answer_timed,
    _get_answer_untimed,
    _start_spinner,
    _stop_spinner,
)
from scathach.cli.review_ui import _render_summary, _show_result

console = Console()


async def run_new_question_review_session(
    conn: sqlite3.Connection,
    client: LLMClient,
    threshold: int,
    timing: TimingMode,
) -> None:
    """
    Run a new-question review session.

    1. Find all topic+level pairs with >1 attempt that are either struggling
       (avg score < threshold) or stale (last question > 30 days old).
    2. Generate one new question per eligible pair.
    3. Present questions topic-by-topic in ascending difficulty order.
    4. Score each answer and update both FSRS queues.
    """
    eligible = get_eligible_pairs(conn, threshold)
    if not eligible:
        console.print(
            "[green]Nothing to review![/green] "
            "All topics are performing above the threshold and have fresh questions."
        )
        return

    # Resolve topic names and group levels by topic
    topic_names: dict[int, str] = {}
    topic_levels: dict[int, list[int]] = {}
    for stat in eligible:
        if stat.topic_id not in topic_names:
            topic = get_topic_by_id(conn, stat.topic_id)
            topic_names[stat.topic_id] = topic.name if topic else f"topic {stat.topic_id}"
            topic_levels[stat.topic_id] = []
        topic_levels[stat.topic_id].append(stat.difficulty)

    # Preview table
    preview = Table(title="New-Question Review — Eligible Pairs", show_lines=True)
    preview.add_column("Topic", style="bold cyan")
    preview.add_column("Avg Score", justify="right")
    preview.add_column("Levels to Review")
    preview.add_column("Count", justify="right")

    topic_avg: dict[int, float] = {}
    for stat in eligible:
        prev = topic_avg.get(stat.topic_id)
        topic_avg[stat.topic_id] = (
            min(prev, stat.avg_final_score) if prev is not None else stat.avg_final_score
        )

    for topic_id in sorted(topic_levels.keys(), key=lambda tid: topic_names[tid]):
        levels_sorted = sorted(topic_levels[topic_id])
        preview.add_row(
            topic_names[topic_id],
            f"{topic_avg[topic_id]:.1f}/10",
            ", ".join(str(l) for l in levels_sorted),
            str(len(levels_sorted)),
        )
    console.print(preview)
    console.print()

    # Generate one question per eligible pair
    questions_by_topic: dict[int, list[Question]] = {}
    failed_pairs = 0

    for stat in eligible:
        topic_name = topic_names[stat.topic_id]
        await _start_spinner(
            f"Generating level {stat.difficulty} question for '{topic_name}'…"
        )
        try:
            q = await generate_topic_review_question(
                conn=conn,
                client=client,
                topic_id=stat.topic_id,
                level=stat.difficulty,
            )
        except TopicReviewError:
            await _stop_spinner()
            console.print(
                f"[yellow]⚠ Failed to generate level {stat.difficulty} question "
                f"for '{topic_name}' — skipping.[/yellow]"
            )
            failed_pairs += 1
            continue

        await _stop_spinner()
        if q is None:
            failed_pairs += 1
            continue

        questions_by_topic.setdefault(stat.topic_id, []).append(q)

    if not questions_by_topic:
        console.print("[red]No questions could be generated. Try again later.[/red]")
        return

    total_questions = sum(len(qs) for qs in questions_by_topic.values())
    skip_note = f" ({failed_pairs} skipped)" if failed_pairs else ""
    console.print(
        f"[green]✓[/green] [dim]{total_questions} question(s) ready{skip_note}[/dim]"
    )

    session_id = secrets.token_hex(3)[:5]
    all_attempts: list[Attempt] = []
    global_idx = 0

    for topic_id in sorted(questions_by_topic.keys(), key=lambda tid: topic_names[tid]):
        topic_questions = sorted(questions_by_topic[topic_id], key=lambda q: q.difficulty)
        topic_name = topic_names[topic_id]

        console.print()
        console.print(Panel(
            f"[bold white]{topic_name}[/bold white]",
            title="Topic",
            border_style="blue",
            expand=False,
        ))

        for q in topic_questions:
            global_idx += 1
            dl = DifficultyLevel.from_int(q.difficulty)
            console.print()
            console.print(Panel(
                q.body,
                title=(
                    f"New-Question Review {global_idx}/{total_questions} — "
                    f"{_difficulty_stars(q.difficulty)} ({dl.label})"
                ),
                border_style="cyan",
            ))

            if timing == TimingMode.TIMED:
                answer_text, time_taken_s = await _get_answer_timed(q, allow_toss=False)
            else:
                answer_text, time_taken_s = await _get_answer_untimed(q, allow_toss=False)

            try:
                attempt, diagnosis = await score_answer(
                    conn=conn,
                    client=client,
                    question=q,
                    session_id=session_id,
                    answer_text=answer_text,
                    time_taken_s=time_taken_s,
                    timed=timing == TimingMode.TIMED,
                    threshold=threshold,
                )
            except ScoringError as exc:
                console.print(f"[red]Scoring failed:[/red] {exc}. Skipping.")
                continue

            attempt = record_attempt(conn, attempt)
            all_attempts.append(attempt)
            _show_result(attempt, diagnosis, q.ideal_answer)

            for queue in ("timed", "untimed"):
                update_schedule(conn, q.id, attempt.final_score, queue)

    if all_attempts:
        _render_summary(all_attempts, title="New-Question Review Summary")
