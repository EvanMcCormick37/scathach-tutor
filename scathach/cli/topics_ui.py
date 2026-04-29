"""
Topics table UI — detailed per-topic dashboard.

Displays all topic metadata, support values, and aggregate stats.
Used by `scathach topics` (active only by default; --all / --retired to filter).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.table import Table

from scathach.db.repository import get_topics_stats

console = Console()


def _fmt_date(iso_str: Optional[str]) -> str:
    """Format an ISO datetime string as M/D/YY, or '—' if None."""
    if iso_str is None:
        return "[dim]—[/dim]"
    try:
        dt = datetime.fromisoformat(iso_str)
        return f"{dt.month}/{dt.day}/{str(dt.year)[-2:]}"
    except (ValueError, TypeError):
        return iso_str[:10] if iso_str else "—"


def render_topics_table(
    conn: sqlite3.Connection,
    status_filter: str = "active",
) -> None:
    """
    Render a detailed topics table.

    status_filter: 'active' (default) | 'retired' | 'all'
    """
    topics = get_topics_stats(conn, status_filter=status_filter)

    if not topics:
        label = {"active": "active", "retired": "retired", "all": ""}.get(status_filter, "")
        noun = f"{label} topics" if label else "topics"
        console.print(f"[yellow]No {noun} found.[/yellow]")
        return

    show_status = status_filter != "active"

    label_map = {"active": "Active Topics", "retired": "Retired Topics", "all": "All Topics"}
    title = label_map.get(status_filter, "Topics")

    table = Table(title=title, show_lines=True)
    table.add_column("ID", style="dim", width=5)
    table.add_column("Name", style="bold cyan", min_width=16)
    if show_status:
        table.add_column("Status", justify="center")
    table.add_column("Qs", justify="right")
    table.add_column("Avg Diff.", justify="right")
    table.add_column("Avg Score", justify="right")
    table.add_column("Exam Supp.", justify="right")
    table.add_column("Prac. Supp.", justify="right")
    table.add_column("Target Lvl", justify="right")
    table.add_column("Next Review", justify="right")

    for t in topics:
        avg_diff = f"{t['avg_difficulty']:.1f}" if t["avg_difficulty"] is not None else "—"
        avg_score = f"{t['avg_score']:.1f}/10" if t["avg_score"] is not None else "—"
        exam_sup = f"{t['exam_support']:.2f}"
        prac_sup = f"{t['practice_support']:.2f}"
        next_rev = _fmt_date(t["next_review_at"])

        if show_status:
            status_str = (
                "[green]active[/green]" if t["status"] == "active"
                else "[dim]retired[/dim]"
            )
            table.add_row(
                str(t["id"]), t["name"], status_str,
                str(t["total_questions"]), avg_diff, avg_score,
                exam_sup, prac_sup, str(t["target_level"]), next_rev,
            )
        else:
            table.add_row(
                str(t["id"]), t["name"],
                str(t["total_questions"]), avg_diff, avg_score,
                exam_sup, prac_sup, str(t["target_level"]), next_rev,
            )

    console.print(table)
