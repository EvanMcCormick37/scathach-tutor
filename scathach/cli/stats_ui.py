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


def render_stats(
    conn: sqlite3.Connection,
    show_topics: bool = True,
    show_review: bool = True,
) -> None:
    """Render the stats dashboard. Pass show_topics/show_review=False to restrict output."""
    topics = _get_topics_stats(conn) if show_topics else None

    if show_topics and not topics:
        console.print("[yellow]No topics yet. Run [bold]scathach ingest <file>[/bold] to get started.[/yellow]")
        return

    if show_topics and show_review:
        console.print(Panel("[bold cyan]scathach Progress Dashboard[/bold cyan]", border_style="cyan"))

    if show_topics and topics is not None:
        # --- Topics table ---
        topic_table = Table(title="Topics", show_lines=True)
        topic_table.add_column("ID", style="dim", width=6)
        topic_table.add_column("Name", style="bold cyan")
        topic_table.add_column("Questions", justify="right")
        topic_table.add_column("Avg Difficulty", justify="right")
        topic_table.add_column("Avg Score", justify="right")
        topic_table.add_column("Source", style="dim")
        topic_table.add_column("Created", style="dim")

        for t in topics:
            avg_difficulty = f"{t['avg_difficulty']:.1f}" if t["avg_difficulty"] is not None else "—"
            avg_score = f"{t['avg_score']}/10" if t["avg_score"] is not None else "—"
            topic_table.add_row(
                str(t["id"]),
                t["name"],
                str(t["total_questions"]),
                avg_difficulty,
                avg_score,
                t["source_path"] or "(pasted text)",
                str(t["created_at"]),
            )
        console.print(topic_table)

    if show_review:
        # --- Review queue stats ---
        queue_stats = _get_queue_stats(conn)
        queue_table = Table(title="Review Queue", show_lines=True)
        queue_table.add_column("Mode")
        queue_table.add_column("Total", justify="right")
        queue_table.add_column("Due Now", justify="right")
        queue_table.add_column("Due This Week", justify="right")

        def _fmt(val: int | None) -> str:
            return "—" if val is None else str(val)

        for q in queue_stats:
            queue_table.add_row(_fmt(q["queue"]), _fmt(q["total"]), _fmt(q["due_now"]), _fmt(q["due_week"]))
        console.print(queue_table)

    # --- Score distribution (only shown when topics are visible) ---
    score_dist = _get_score_distribution(conn) if show_topics else []

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
        SELECT t.id, t.name, t.source_path, t.created_at,
               COUNT(DISTINCT q.id) AS total_questions,
               ROUND(AVG(q.difficulty), 1) AS avg_difficulty,
               ROUND(AVG(a.final_score), 1) AS avg_score
        FROM topics t
        LEFT JOIN questions q ON q.topic_id = t.id
        LEFT JOIN attempts a ON a.question_id = q.id
        GROUP BY t.id
        ORDER BY t.created_at DESC
    """).fetchall()
    return [dict(r) for r in rows]


def _get_queue_stats(conn: sqlite3.Connection) -> list[dict]:
    from scathach.config import settings
    from scathach.core.topic_review import get_eligible_pairs

    now = datetime.now(UTC)
    now_str = now.isoformat()
    week_str = now.replace(hour=23, minute=59, second=59).isoformat()

    def _range_counts(min_d: int, max_d: int) -> tuple[int, int, int]:
        """Return (total, due_now, due_week) for questions in difficulty range, across both queues."""
        def _count(extra_where: str, extra_params: list) -> int:
            sql = f"""
                SELECT COUNT(DISTINCT question_id) FROM (
                    SELECT rq.question_id FROM timed_review_queue rq
                    JOIN questions q ON q.id = rq.question_id
                    WHERE q.difficulty BETWEEN ? AND ? {extra_where}
                    UNION
                    SELECT rq.question_id FROM untimed_review_queue rq
                    JOIN questions q ON q.id = rq.question_id
                    WHERE q.difficulty BETWEEN ? AND ? {extra_where}
                )
            """
            params = [min_d, max_d] + extra_params + [min_d, max_d] + extra_params
            return conn.execute(sql, params).fetchone()[0]

        total = _count("", [])
        due_now = _count("AND rq.state = 'review' AND rq.next_review_at <= ?", [now_str])
        due_week = _count("AND rq.state = 'review' AND rq.next_review_at <= ?", [week_str])
        return total, due_now, due_week

    result = []

    for label, min_d, max_d in [("Flash Cards", 1, 2), ("Long Answers", 3, 6)]:
        total, due_now, due_week = _range_counts(min_d, max_d)
        result.append({"queue": label, "total": total, "due_now": due_now, "due_week": due_week})

    # Topics (next_review_at IS NULL means never reviewed → counts as due)
    total_topics = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    due_now_topics = conn.execute(
        "SELECT COUNT(*) FROM topics WHERE next_review_at IS NULL OR next_review_at <= ?",
        (now_str,),
    ).fetchone()[0]
    due_week_topics = conn.execute(
        "SELECT COUNT(*) FROM topics WHERE next_review_at IS NULL OR next_review_at <= ?",
        (week_str,),
    ).fetchone()[0]
    result.append({"queue": "Topics", "total": total_topics, "due_now": due_now_topics, "due_week": due_week_topics})

    # New Questions — eligibility-based, no time dimension
    eligible = len(get_eligible_pairs(conn, settings.quality_threshold))
    result.append({"queue": "New Questions", "total": None, "due_now": eligible, "due_week": None})

    return result


def render_topic_stats(conn: sqlite3.Connection, topic_name: str) -> None:
    """Render a per-level breakdown for a single topic."""
    from scathach.db.repository import get_topic_by_name
    from scathach.core.question import DifficultyLevel

    topic = get_topic_by_name(conn, topic_name)
    if topic is None:
        console.print(
            f"[red]Topic '{topic_name}' not found.[/red] "
            "Run [bold]scathach stats[/bold] to see available topics."
        )
        return

    now_str = datetime.now(UTC).isoformat()

    # Per-level attempt stats
    level_rows = conn.execute(
        """
        SELECT
            q.difficulty,
            COUNT(DISTINCT q.id)                              AS question_count,
            COUNT(a.id)                                       AS attempt_count,
            COALESCE(ROUND(AVG(a.final_score), 1), 0.0)      AS avg_final,
            COALESCE(ROUND(AVG(a.raw_score),   1), 0.0)      AS avg_raw,
            COALESCE(SUM(CASE WHEN a.passed      THEN 1 ELSE 0 END), 0) AS passed_count,
            COALESCE(SUM(CASE WHEN a.time_penalty THEN 1 ELSE 0 END), 0) AS penalized_count
        FROM questions q
        LEFT JOIN attempts a ON a.question_id = q.id
        WHERE q.topic_id = ?
        GROUP BY q.difficulty
        ORDER BY q.difficulty
        """,
        (topic.id,),
    ).fetchall()

    if not level_rows:
        console.print(f"[yellow]No questions found for topic '{topic_name}'.[/yellow]")
        return

    # FSRS queue stats per level
    def _queue_by_level(table: str) -> dict[int, dict]:
        rows = conn.execute(
            f"""
            SELECT q.difficulty,
                   COUNT(rq.question_id) AS in_queue,
                   SUM(CASE WHEN rq.state = 'review'
                                AND rq.next_review_at <= ? THEN 1 ELSE 0 END) AS due_now
            FROM {table} rq
            JOIN questions q ON q.id = rq.question_id
            WHERE q.topic_id = ?
            GROUP BY q.difficulty
            """,
            (now_str, topic.id),
        ).fetchall()
        return {r["difficulty"]: dict(r) for r in rows}

    timed_q   = _queue_by_level("timed_review_queue")
    untimed_q = _queue_by_level("untimed_review_queue")

    # Summary header
    total_qs       = sum(r["question_count"] for r in level_rows)
    total_attempts = sum(r["attempt_count"]  for r in level_rows)
    all_finals     = [r["avg_final"] for r in level_rows if r["attempt_count"] > 0]
    overall_avg    = sum(all_finals) / len(all_finals) if all_finals else 0.0

    console.print(Panel(
        f"[bold cyan]{topic.name}[/bold cyan]\n"
        f"[dim]Questions: {total_qs}  ·  Attempts: {total_attempts}  ·  "
        f"Overall avg: {overall_avg:.1f}/10[/dim]",
        title="Topic Stats",
        border_style="cyan",
        expand=False,
    ))

    def _queue_cell(qmap: dict, level: int) -> str:
        info = qmap.get(level)
        if not info or info["in_queue"] == 0:
            return "—"
        return f"{info['in_queue']} ({info['due_now']} due)"

    table = Table(title="Per-Level Breakdown", show_lines=True)
    table.add_column("Level",           style="bold")
    table.add_column("Qs",              justify="right")
    table.add_column("Attempts",        justify="right")
    table.add_column("Avg Score",       justify="right")
    table.add_column("Pass Rate",       justify="right")
    table.add_column("Penalized",       justify="right")
    table.add_column("Timed Q (due)",   justify="right")
    table.add_column("Untimed Q (due)", justify="right")

    for r in level_rows:
        lvl  = r["difficulty"]
        dl   = DifficultyLevel.from_int(lvl)
        stars = "★" * lvl + "☆" * (6 - lvl)

        avg_str = _colorize_score_str(r["avg_final"])

        pass_rate = (
            f"{100 * r['passed_count'] // r['attempt_count']}%"
            if r["attempt_count"] > 0 else "—"
        )

        table.add_row(
            f"{stars} L{lvl} {dl.label}",
            str(r["question_count"]),
            str(r["attempt_count"]) if r["attempt_count"] > 0 else "—",
            avg_str if r["attempt_count"] > 0 else "—",
            pass_rate,
            str(r["penalized_count"]) if r["penalized_count"] > 0 else "—",
            _queue_cell(timed_q,   lvl),
            _queue_cell(untimed_q, lvl),
        )

    console.print(table)


def _colorize_score_str(score: float) -> str:
    if score <= 4:
        return f"[red]{score:.1f}/10[/red]"
    if score <= 6:
        return f"[yellow]{score:.1f}/10[/yellow]"
    return f"[green]{score:.1f}/10[/green]"


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
