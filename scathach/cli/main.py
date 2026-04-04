"""
scathach CLI entry point.
All top-level commands are registered here via Typer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scathach import __version__
from scathach.config import settings
from scathach.core.question import TimingMode
from scathach.core.session import SessionConfig, SessionRunner
from scathach.db.repository import (
    get_session_record,
    get_topic_by_id,
    get_topic_by_name,
    list_active_sessions,
    list_topics,
    rename_topic,
)
from scathach.db.schema import open_db
from scathach.ingestion.ingestor import IngestionError, ingest_file, ingest_text
from scathach.llm.client import make_client

app = typer.Typer(
    name="scathach",
    help="A spaced-repetition, LLM-powered terminal learning application.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


# ---------------------------------------------------------------------------
# Document opener
# ---------------------------------------------------------------------------


def open_document(path: str | Path) -> None:
    """Open a file with the system's default application (platform-agnostic)."""
    p = Path(path)
    if not p.exists():
        console.print(f"[yellow]Source file not found at {p} — skipping document open.[/yellow]")
        return
    try:
        if sys.platform == "win32":
            import os
            os.startfile(str(p))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception as exc:
        console.print(f"[yellow]Could not open document: {exc}[/yellow]")


def _maybe_open_doc(source_path: Optional[str], open_doc: bool) -> None:
    """Offer to open the source document if source_path is set and open_doc is True."""
    if not open_doc or not source_path:
        return
    console.print(f"[dim]Opening source document: {source_path}[/dim]")
    open_document(source_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def version_callback(value: bool) -> None:
    if value:
        console.print(f"scathach version [bold cyan]{__version__}[/]")
        raise typer.Exit()


def _require_api_key() -> None:
    if not settings.openrouter_api_key:
        console.print(
            "[red]No API key configured.[/red] "
            "Set [bold]SCATHACH_OPENROUTER_API_KEY[/bold] in your .env file.\n"
            "Run [bold]scathach config --show[/bold] for more information."
        )
        raise typer.Exit(code=1)


def _resolve_timing(timed: Optional[bool], default: TimingMode) -> TimingMode:
    if timed is True:
        return TimingMode.TIMED
    if timed is False:
        return TimingMode.UNTIMED
    return default


_BANNER = """
[bold cyan]  ┌─────────────────────────────────────────┐
  │  🐍  [white]scathach[/white]  —  Slay the Hydra           │
  │       spaced-repetition · LLM-powered   │
  └─────────────────────────────────────────┘[/bold cyan]

Quick start:
  [bold]scathach ingest[/bold]                  Ingest all new docs from [dim]./docs/[/dim]
  [bold]scathach ingest[/bold] [dim]<file>[/dim]           Ingest a specific document
  [bold]scathach session[/bold] [dim]<topic>[/dim]         Start a learning session
  [bold]scathach review[/bold]                  Review due level 1–2 questions
  [bold]scathach super-review[/bold]            Review due level 3–6 questions
  [bold]scathach stats[/bold]                   View progress dashboard

Tip: drop documents into [bold]./docs/[/bold] and run [bold]scathach ingest[/bold] to pick them all up.
"""


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context = typer.Option(None, hidden=True),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """
    [bold cyan]scathach[/] — Slay the hydra. Master your documents.

    Ingest a document, generate adaptive questions, and grind through
    spaced-repetition review sessions until you truly know the material.
    """
    if ctx is not None and ctx.invoked_subcommand is None:
        console.print(_BANNER)


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


_DOCS_DIR = Path("docs")
_SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm", ".txt", ".md", ".markdown", ".rst"}


@app.command()
def ingest(
    path: Optional[str] = typer.Argument(
        None,
        help="Path to a document (PDF, DOCX, PPTX, TXT, MD) to ingest. "
             "If omitted, all new documents in ./docs/ are ingested automatically.",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Custom topic name (defaults to filename). Only used with a file path argument."
    ),
    paste: bool = typer.Option(
        False, "--paste", "-p", help="Paste raw text instead of providing a file path."
    ),
) -> None:
    """Ingest documents into scathach.

    With no arguments, scans [bold]./docs/[/] for any files not yet ingested and
    imports them all. Drop files into that folder and run [bold]scathach ingest[/]
    to pick them up.

    Pass a specific file path to ingest just that document, or use [bold]--paste[/]
    to type/paste raw text directly.
    """
    conn = open_db(settings.db_path)
    try:
        if paste:
            if name is None:
                name = typer.prompt("Topic name")
            console.print("[cyan]Paste your text below. Press Ctrl+D (or Ctrl+Z on Windows) when done.[/]")
            text = sys.stdin.read()
            topic = ingest_text(conn, text, topic_name=name)
            console.print(f"[green]Ingested topic '{topic.name}' (id={topic.id}) from pasted text.[/]")

        elif path is not None:
            with console.status(f"[cyan]Ingesting {Path(path).name}…[/]"):
                topic = ingest_file(conn, path, topic_name=name)
            console.print(
                f"[green]Ingested topic '[bold]{topic.name}[/]' (id={topic.id}).[/]"
            )

        else:
            # Auto-scan ./docs/ for new documents
            _ingest_docs_folder(conn)

    except IngestionError as exc:
        console.print(f"[red]Ingestion failed:[/] {exc}")
        raise typer.Exit(code=1)
    finally:
        conn.close()


def _ingest_docs_folder(conn) -> None:
    """Scan ./docs/ for supported files not yet ingested and import them."""
    if not _DOCS_DIR.exists():
        console.print(
            f"[yellow]Docs folder [bold]{_DOCS_DIR.resolve()}[/bold] not found.[/yellow]\n"
            "Create a [bold]docs/[/bold] folder in your working directory and drop "
            "documents into it, then run [bold]scathach ingest[/bold] again."
        )
        return

    candidates = sorted(
        p for p in _DOCS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS
    )

    if not candidates:
        console.print(
            f"[yellow]No supported documents found in [bold]{_DOCS_DIR}[/bold].[/yellow]\n"
            f"Supported formats: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
        )
        return

    # Determine which files are already ingested by comparing resolved source paths
    already_ingested: set[str] = {
        row["source_path"]
        for row in conn.execute("SELECT source_path FROM topics WHERE source_path IS NOT NULL").fetchall()
    }

    new_files = [p for p in candidates if str(p.resolve()) not in already_ingested]
    skipped = len(candidates) - len(new_files)

    if not new_files:
        console.print(
            f"[green]All {len(candidates)} document(s) in [bold]{_DOCS_DIR}[/bold] "
            "are already ingested.[/green]"
        )
        return

    if skipped:
        console.print(f"[dim]Skipping {skipped} already-ingested file(s).[/dim]")

    ingested_count = 0
    failed_count = 0
    for file_path in new_files:
        try:
            with console.status(f"[cyan]Ingesting {file_path.name}…[/]"):
                topic = ingest_file(conn, file_path)
            console.print(
                f"  [green]✓[/green] [bold]{topic.name}[/bold] (id={topic.id})"
            )
            ingested_count += 1
        except IngestionError as exc:
            console.print(f"  [red]✗[/red] {file_path.name}: {exc}")
            failed_count += 1

    console.print(
        f"\n[bold]Done.[/bold] Ingested {ingested_count} new topic(s)"
        + (f", {failed_count} failed." if failed_count else ".")
    )


# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------


@app.command()
def topics() -> None:
    """List all ingested topics with question counts and avg scores."""
    conn = open_db(settings.db_path)
    try:
        all_topics = list_topics(conn)
        if not all_topics:
            console.print("[yellow]No topics ingested yet. Run [bold]scathach ingest <file>[/] to get started.[/]")
            return

        table = Table(title="Ingested Topics", show_lines=True)
        table.add_column("ID", style="dim", width=6)
        table.add_column("Name", style="bold cyan")
        table.add_column("Questions", justify="right")
        table.add_column("Avg Score", justify="right")
        table.add_column("Source", style="dim")
        table.add_column("Created", style="dim")

        for t in all_topics:
            stats_row = conn.execute(
                """
                SELECT COUNT(q.id) AS qcount,
                       ROUND(AVG(a.final_score), 1) AS avg_score
                FROM questions q
                LEFT JOIN attempts a ON a.question_id = q.id
                WHERE q.topic_id = ?
                """,
                (t.id,),
            ).fetchone()
            qcount = stats_row["qcount"] if stats_row else 0
            avg_score = f"{stats_row['avg_score']}/10" if stats_row and stats_row["avg_score"] is not None else "—"
            table.add_row(
                str(t.id),
                t.name,
                str(qcount),
                avg_score,
                t.source_path or "(pasted text)",
                str(t.created_at),
            )

        console.print(table)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


@app.command()
def rename(
    old_name: str = typer.Argument(..., help="Current topic name."),
    new_name: str = typer.Argument(..., help="New topic name."),
) -> None:
    """Rename a topic."""
    conn = open_db(settings.db_path)
    try:
        topic = get_topic_by_name(conn, old_name)
        if topic is None:
            console.print(
                f"[red]Topic '{old_name}' not found.[/red] "
                "Run [bold]scathach topics[/bold] to see available topics."
            )
            raise typer.Exit(code=1)

        try:
            updated = rename_topic(conn, old_name, new_name)
        except Exception:
            console.print(
                f"[red]Could not rename:[/red] a topic named '[bold]{new_name}[/bold]' already exists."
            )
            raise typer.Exit(code=1)

        console.print(
            f"[green]Renamed '[bold]{old_name}[/bold]' → '[bold]{updated.name}[/bold]'.[/green]"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


@app.command()
def session(
    topic: Optional[str] = typer.Argument(None, help="Topic name to start a session for."),
    timed: Optional[bool] = typer.Option(
        None, "--timed/--untimed", help="Override the default timing mode."
    ),
    threshold: Optional[int] = typer.Option(
        None, "--threshold", "-t", min=5, max=10, help="Override quality threshold (5–10)."
    ),
    levels: Optional[int] = typer.Option(
        None, "--levels", "-l", min=1, max=6, help="Number of difficulty levels to include (1–6)."
    ),
    resume: Optional[str] = typer.Option(
        None, "--resume", "-r", help="Resume an interrupted session by its ID."
    ),
    list_sessions: bool = typer.Option(
        False, "--list", help="List all unfinished sessions."
    ),
    wizard: bool = typer.Option(True, "--wizard/--no-wizard", help="Run the pre-session setup wizard."),
    open_doc: Optional[bool] = typer.Option(
        None, "--open-doc/--no-open-doc",
        help="Open source document before starting (overrides config default).",
    ),
) -> None:
    """Start or resume an interactive learning session."""
    import asyncio
    from scathach.cli.session_ui import handle_event, make_answer_provider, pre_session_wizard

    conn = open_db(settings.db_path)
    try:
        # ---- List unfinished sessions ----
        if list_sessions:
            active = list_active_sessions(conn)
            if not active:
                console.print("[dim]No unfinished sessions.[/dim]")
                return
            table = Table(title="Unfinished Sessions", show_lines=True)
            table.add_column("Session ID", style="cyan", no_wrap=True)
            table.add_column("Topic", style="bold")
            table.add_column("Timing")
            table.add_column("Started")
            table.add_column("Questions Remaining", justify="right")
            import json as _json
            for rec in active:
                topic_obj = get_topic_by_id(conn, rec.topic_id)
                topic_name = topic_obj.name if topic_obj else f"id={rec.topic_id}"
                remaining = 0
                if rec.question_stack:
                    try:
                        frames = _json.loads(rec.question_stack)
                        remaining = sum(len(f["question_ids"]) for f in frames)
                    except Exception:
                        pass
                table.add_row(
                    rec.session_id,
                    topic_name,
                    rec.timing,
                    str(rec.created_at)[:16] if rec.created_at else "—",
                    str(remaining),
                )
            console.print(table)
            return

        # ---- Resume existing session ----
        if resume is not None:
            _require_api_key()
            rec = get_session_record(conn, resume)
            if rec is None:
                console.print(f"[red]Session '{resume}' not found.[/red]")
                raise typer.Exit(code=1)
            if rec.status != "active":
                console.print(f"[yellow]Session '{resume}' is already complete.[/yellow]")
                raise typer.Exit(code=1)
            if not rec.question_stack:
                console.print(f"[red]Session '{resume}' has no saved state to resume.[/red]")
                raise typer.Exit(code=1)

            topic_obj = get_topic_by_id(conn, rec.topic_id)
            should_open = open_doc if open_doc is not None else settings.open_doc_on_session
            _maybe_open_doc(topic_obj.source_path if topic_obj else None, should_open)

            from scathach.core.question import TimingMode as _TM
            timing_mode = _TM.TIMED if rec.timing == "timed" else _TM.UNTIMED
            config = SessionConfig(
                topic_id=rec.topic_id,
                timing=timing_mode,
                threshold=rec.threshold,
                num_levels=rec.num_levels,
            )
            llm_client = make_client(
                api_key=settings.openrouter_api_key,
                model=settings.model,
                base_url=settings.openrouter_base_url,
            )
            runner = SessionRunner(
                conn=conn,
                client=llm_client,
                config=config,
                answer_provider=make_answer_provider(config.timing),
                event_handler=handle_event,
                restored_record=rec,
            )
            asyncio.run(runner.run())
            return

        # ---- Start new session ----
        if topic is None:
            console.print(
                "[red]Provide a topic name, or use --list / --resume.[/red]\n"
                "  scathach session [bold]<topic>[/bold]\n"
                "  scathach session [bold]--list[/bold]\n"
                "  scathach session [bold]--resume <session_id>[/bold]"
            )
            raise typer.Exit(code=1)

        _require_api_key()

        topic_obj = get_topic_by_name(conn, topic)
        if topic_obj is None:
            console.print(
                f"[red]Topic '{topic}' not found.[/red] "
                "Run [bold]scathach topics[/bold] to see available topics."
            )
            raise typer.Exit(code=1)

        should_open = open_doc if open_doc is not None else settings.open_doc_on_session
        _maybe_open_doc(topic_obj.source_path, should_open)

        timing_mode = _resolve_timing(timed, settings.main_timing)
        config = SessionConfig(
            topic_id=topic_obj.id,
            timing=timing_mode,
            threshold=threshold or settings.quality_threshold,
            num_levels=levels or 6,
        )

        if wizard:
            config = pre_session_wizard(config)

        llm_client = make_client(
            api_key=settings.openrouter_api_key,
            model=settings.model,
            base_url=settings.openrouter_base_url,
        )
        runner = SessionRunner(
            conn=conn,
            client=llm_client,
            config=config,
            answer_provider=make_answer_provider(config.timing),
            event_handler=handle_event,
        )
        asyncio.run(runner.run())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# review  (levels 1–2, no Hydra)
# ---------------------------------------------------------------------------


@app.command()
def review(
    timed: Optional[bool] = typer.Option(
        None, "--timed/--untimed", help="Override the default review timing mode."
    ),
    limit: int = typer.Option(20, "--limit", "-l", help="Max questions per session."),
    open_doc: Optional[bool] = typer.Option(
        None, "--open-doc/--no-open-doc",
        help="Open source documents before starting (overrides config default).",
    ),
) -> None:
    """Review due level 1–2 (short-answer) questions via FSRS spaced repetition."""
    import asyncio
    from scathach.cli.review_ui import run_review_session

    _require_api_key()

    timing_mode = _resolve_timing(timed, settings.review_timing)
    queue = timing_mode.value

    conn = open_db(settings.db_path)
    try:
        should_open = open_doc if open_doc is not None else settings.open_doc_on_session
        if should_open:
            _open_docs_for_due_questions(conn, queue, min_d=1, max_d=2)

        llm_client = make_client(
            api_key=settings.openrouter_api_key,
            model=settings.model,
            base_url=settings.openrouter_base_url,
        )
        asyncio.run(run_review_session(
            conn=conn, client=llm_client, queue=queue,
            timing=timing_mode, threshold=settings.quality_threshold, limit=limit,
        ))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# super-review  (levels 3–6, optional Hydra)
# ---------------------------------------------------------------------------


@app.command(name="super-review")
def super_review(
    timed: Optional[bool] = typer.Option(
        None, "--timed/--untimed", help="Override the default review timing mode."
    ),
    limit: int = typer.Option(10, "--limit", "-l", help="Max questions per session."),
    hydra: Optional[bool] = typer.Option(
        None, "--hydra/--no-hydra",
        help="Enable Hydra Protocol for failed answers (overrides config default).",
    ),
    open_doc: Optional[bool] = typer.Option(
        None, "--open-doc/--no-open-doc",
        help="Open source documents before starting (overrides config default).",
    ),
) -> None:
    """Review due level 3–6 (long-answer) questions. Worst performers surface first.
    Optionally enables the Hydra Protocol for failed answers."""
    import asyncio
    from scathach.cli.review_ui import run_super_review_session

    _require_api_key()

    timing_mode = _resolve_timing(timed, settings.review_timing)
    queue = timing_mode.value
    hydra_enabled = hydra if hydra is not None else settings.hydra_in_super_review

    conn = open_db(settings.db_path)
    try:
        should_open = open_doc if open_doc is not None else settings.open_doc_on_session
        if should_open:
            _open_docs_for_due_questions(conn, queue, min_d=3, max_d=6)

        llm_client = make_client(
            api_key=settings.openrouter_api_key,
            model=settings.model,
            base_url=settings.openrouter_base_url,
        )
        asyncio.run(run_super_review_session(
            conn=conn, client=llm_client, queue=queue,
            timing=timing_mode, threshold=settings.quality_threshold,
            limit=limit, hydra_enabled=hydra_enabled,
        ))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@app.command()
def stats() -> None:
    """Display a progress dashboard across all topics."""
    from scathach.cli.stats_ui import render_stats
    conn = open_db(settings.db_path)
    try:
        render_stats(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@app.command(name="config")
def config_cmd(
    set_model: Optional[str] = typer.Option(
        None, "--set-model", help="Set the active LLM model (writes to .env)."
    ),
    set_review_timing: Optional[str] = typer.Option(
        None, "--set-review-timing",
        help="Set the default review timing: 'timed' or 'untimed'.",
    ),
    test: bool = typer.Option(False, "--test", help="Send a canary prompt to verify the API key."),
    show: bool = typer.Option(False, "--show", help="Print current configuration."),
) -> None:
    """View or update scathach configuration."""
    import asyncio

    if show or not any([set_model, set_review_timing, test]):
        table = Table(title="Current Configuration", show_lines=True)
        table.add_column("Setting", style="bold")
        table.add_column("Value")
        table.add_row("API key", "[green]set[/green]" if settings.openrouter_api_key else "[red]NOT SET[/red]")
        table.add_row("Model", settings.model)
        table.add_row("Main timing", settings.main_timing.value)
        table.add_row("Review timing", settings.review_timing.value)
        table.add_row("Quality threshold", str(settings.quality_threshold))
        table.add_row("Hydra in super-review", str(settings.hydra_in_super_review))
        table.add_row("Open doc on session", str(settings.open_doc_on_session))
        table.add_row("DB path", str(settings.db_path))
        console.print(table)
        if not settings.openrouter_api_key:
            console.print(
                "\n[yellow]No API key set. Add [bold]SCATHACH_OPENROUTER_API_KEY[/bold] to your .env file.[/yellow]"
                "\nGet a free key at https://openrouter.ai"
            )

    if set_model:
        _write_env_var("SCATHACH_MODEL", set_model)
        console.print(f"[green]Model set to:[/green] {set_model}")

    if set_review_timing:
        val = set_review_timing.lower().strip()
        if val not in ("timed", "untimed"):
            console.print("[red]Review timing must be 'timed' or 'untimed'.[/red]")
            raise typer.Exit(code=1)
        _write_env_var("SCATHACH_REVIEW_TIMING", val)
        console.print(f"[green]Review timing set to:[/green] {val}")

    if test:
        _require_api_key()
        console.print(f"[cyan]Testing connection to {settings.model}…[/cyan]")
        llm_client = make_client(
            api_key=settings.openrouter_api_key,
            model=settings.model,
            base_url=settings.openrouter_base_url,
        )
        try:
            result = asyncio.run(llm_client.generate(
                system_prompt="You are a helpful assistant.",
                user_prompt="Reply with exactly: 'scathach API test OK'",
                max_tokens=20,
            ))
            console.print(f"[green]API test passed.[/green] Response: {result.strip()}")
        except Exception as exc:
            console.print(f"[red]API test failed:[/red] {exc}")
            raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_docs_for_due_questions(
    conn,
    queue: str,
    min_d: int,
    max_d: int,
) -> None:
    """
    Find the unique source documents for topics that have due questions in
    the given queue/difficulty range, then open each one.
    """
    from datetime import UTC, datetime
    from scathach.core.scheduler import get_scheduled_questions
    now = datetime.now(UTC)
    questions = get_scheduled_questions(
        conn, queue, limit=50, now=now,
        min_difficulty=min_d, max_difficulty=max_d,
    )
    if not questions:
        return

    seen_topic_ids: set[int] = set()
    for q in questions:
        if q.topic_id in seen_topic_ids:
            continue
        seen_topic_ids.add(q.topic_id)
        row = conn.execute(
            "SELECT source_path FROM topics WHERE id = ?", (q.topic_id,)
        ).fetchone()
        if row and row["source_path"]:
            _maybe_open_doc(row["source_path"], open_doc=True)


def _write_env_var(key: str, value: str) -> None:
    """Write or update a key=value line in the local .env file."""
    env_path = Path(".env")
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        updated = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
                new_lines.append(f"{key}={value}")
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")


if __name__ == "__main__":
    app()
