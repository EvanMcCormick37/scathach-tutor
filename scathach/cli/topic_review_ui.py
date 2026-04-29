"""
Topic Review UI — quest-based scheduled review.

Fetches all topics whose next_review_at is due, then runs a full quest
(via SessionRunner) for each one using that topic's target_level as the
difficulty cap.  Topics are presented most-overdue first.

Topic support is updated automatically through the SessionRunner hook
as new root questions are answered.
"""

from __future__ import annotations

import asyncio
import sqlite3

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scathach.config import settings
from scathach.core.question import TimingMode
from scathach.core.session import SessionConfig, SessionRunner
from scathach.core.topic_support import finalize_topic_next_review
from scathach.db.repository import get_due_topics
from scathach.llm.client import LLMClient

console = Console()


async def run_topic_review(
    conn: sqlite3.Connection,
    client: LLMClient,
    timing: TimingMode,
    threshold: int,
    hydra_retry_parent: bool,
    handle_event,
    make_answer_provider,
) -> None:
    """
    Run a scheduled quest for every due topic.

    Due = next_review_at IS NULL (never reviewed) or next_review_at <= now.
    Topics are ordered most-overdue first (NULLs first).
    Each quest uses topic.target_level as num_levels.
    """
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    due = get_due_topics(conn, now)

    if not due:
        console.print(
            "[green]No topics due for review. Great work![/green]"
        )
        return

    # Preview
    table = Table(title="Topic Review — Due Topics", show_lines=True)
    table.add_column("Topic", style="bold cyan")
    table.add_column("Target Level", justify="right")
    table.add_column("Exam Supp.", justify="right")
    table.add_column("Practice Supp.", justify="right")
    table.add_column("Due")

    for t in due:
        due_str = t.next_review_at[:10] if t.next_review_at else "[dim]never reviewed[/dim]"
        table.add_row(
            t.name,
            str(t.target_level),
            f"{t.exam_support:.2f}",
            f"{t.practice_support:.2f}",
            due_str,
        )
    console.print(table)
    console.print()

    for topic in due:
        console.print(Panel(
            f"[bold white]{topic.name}[/bold white]\n"
            f"[dim]Target level: {topic.target_level}  ·  "
            f"Exam support: {topic.exam_support:.2f}  ·  "
            f"Practice support: {topic.practice_support:.2f}[/dim]",
            title="Topic Review Quest",
            border_style="magenta",
            expand=False,
        ))

        config = SessionConfig(
            topic_id=topic.id,
            timing=timing,
            threshold=threshold,
            num_levels=topic.target_level,
            hydra_retry_parent=hydra_retry_parent,
        )
        runner = SessionRunner(
            conn=conn,
            client=client,
            config=config,
            answer_provider=make_answer_provider(timing),
            event_handler=handle_event,
        )
        await runner.run()
        finalize_topic_next_review(conn, topic.id, settings.max_practice_support)
        console.print()
