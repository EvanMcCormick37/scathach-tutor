"""
Stats dashboard — renders progress information for all topics.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()


def render_stats(conn: sqlite3.Connection) -> None:
    """Render the full stats dashboard to the terminal."""
    topics = _get_topics_stats(conn)
    queue_stats = _get_queue_stats(conn)
    score_dist = _get_score_distribution(conn)

    if not topics:
        console.print("[yellow]No topics yet. Run [bold]scathach ingest <file>[/bold] to get started.[/yellow]")
        return

    console.print(Panel("[bold cyan]scathach Progress Dashboard[/bold cyan]", border_style="cyan"))

    # --- Topics table ---
    topic_table = Table(title="Topics", show_lines=True)
    topic_table.add_column("Name", style="bold cyan")
    topic_table.add_column("Questions", justify="right")
    topic_table.add_column("Root", justify="right")
    topic_table.add_column("Created")

    for t in topics:
        topic_table.add_row(
            t["name"],
            str(t["total_questions"]),
            str(t["root_questions"]),
            str(t["created_at"]),
        )
    console.print(topic_table)

    # --- Review queue stats ---
    queue_table = Table(title="Review Queue", show_lines=True)
    queue_table.add_column("Queue")
    queue_table.add_column("Total", justify="right")
    queue_table.add_column("Due Now", justify="right")
    queue_table.add_column("Due This Week", justify="right")

    for q in queue_stats:
        queue_table.add_row(q["queue"], str(q["total"]), str(q["due_now"]), str(q["due_week"]))
    console.print(queue_table)

    # --- Score distribution ---
    if score_dist:
        dist_table = Table(title="Score Distribution (by Difficulty)", show_lines=True)
        dist_table.add_column("Difficulty")
        dist_table.add_column("Attempts", justify="right")
        dist_table.add_column("Avg Raw", justify="right")
        dist_table.add_column("Avg Final", justify="right")
        dist_table.add_column("Time-penalized", justify="right")

        for row in score_dist:
            dist_table.add_row(
                f"Level {row['difficulty']}",
                str(row["attempts"]),
                f"{row['avg_raw']:.1f}",
                f"{row['avg_final']:.1f}",
                str(row["penalized"]),
            )
        console.print(dist_table)


def _get_topics_stats(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT t.name, t.created_at,
               COUNT(q.id) AS total_questions,
               SUM(q.is_root) AS root_questions
        FROM topics t
        LEFT JOIN questions q ON q.topic_id = t.id
        GROUP BY t.id
        ORDER BY t.created_at DESC
    """).fetchall()
    return [dict(r) for r in rows]


def _get_queue_stats(conn: sqlite3.Connection) -> list[dict]:
    now = datetime.now(UTC).isoformat()
    week = (datetime.now(UTC).replace(hour=23, minute=59, second=59)).isoformat()

    result = []
    for queue_name, table in [("timed", "timed_review_queue"), ("untimed", "untimed_review_queue")]:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        due_now = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE next_review_at IS NULL OR next_review_at <= ?",
            (now,)
        ).fetchone()[0]
        due_week = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE next_review_at IS NULL OR next_review_at <= ?",
            (week,)
        ).fetchone()[0]
        result.append({"queue": queue_name, "total": total, "due_now": due_now, "due_week": due_week})
    return result


def _get_score_distribution(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT q.difficulty,
               COUNT(a.id) AS attempts,
               ROUND(AVG(a.raw_score), 1) AS avg_raw,
               ROUND(AVG(a.final_score), 1) AS avg_final,
               SUM(a.time_penalty) AS penalized
        FROM attempts a
        JOIN questions q ON q.id = a.question_id
        GROUP BY q.difficulty
        ORDER BY q.difficulty
    """).fetchall()
    return [dict(r) for r in rows]
