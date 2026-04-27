"""
scathach CLI entry point.
All top-level commands are registered here via Typer.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scathach import __version__
from scathach.config import CONFIG_DIR, ENV_FILE, settings
from scathach.core.question import TimingMode
from scathach.core.session import SessionConfig, SessionRunner
from scathach.db.repository import (
    delete_session,
    delete_topic,
    get_session_record,
    get_topic_by_id,
    get_topic_by_name,
    list_active_sessions,
    rename_topic,
    set_topic_target_level,
)
from scathach.db.schema import open_db
from scathach.ingestion.ingestor import IngestionError, ingest_file, ingest_text, ingest_url
from scathach.llm.client import make_client

app = typer.Typer(
    name="scathach",
    help="A spaced-repetition, LLM-powered terminal learning application.",
    add_completion=False,
    rich_markup_mode="rich",
)

# ---------------------------------------------------------------------------
# Topic sub-group
# ---------------------------------------------------------------------------

topic_app = typer.Typer(
    name="topic",
    help="Manage ingested topics (rename, delete, set review level).",
    add_completion=False,
    rich_markup_mode="rich",
)
app.add_typer(topic_app, name="topic")

console = Console()


# ---------------------------------------------------------------------------
# Document opener
# ---------------------------------------------------------------------------


def open_document(path: str | Path) -> None:
    """Open a file or URL with the system's default application."""
    path_str = str(path)
    if path_str.startswith(("http://", "https://")):
        import webbrowser
        try:
            webbrowser.open(path_str)
        except Exception as exc:
            console.print(f"[yellow]Could not open URL: {exc}[/yellow]")
        return
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


def _make_client():
    return make_client(
        api_key=settings.openrouter_api_key,
        model=settings.model,
        base_url=settings.openrouter_base_url,
    )


_BANNER = """
[bold cyan]  ┌─────────────────────────────────────────┐
  │  🐍  [white]scathach[/white]  —  Slay the Hydra           │
  │       spaced-repetition · LLM-powered   │
  └─────────────────────────────────────────┘[/bold cyan]

Quick start:
  [bold]scathach ingest[/bold]                     Ingest all new docs from [dim]~/.scathach/docs/[/dim]
  [bold]scathach ingest[/bold] [dim]<file>[/dim]              Ingest a specific document
  [bold]scathach session[/bold] [dim]<topic>[/dim]            Quest (adaptive, all levels, Hydra)
  [bold]scathach session[/bold] [dim]<topic>[/dim] [bold]--drill[/bold]   Drill a single difficulty level
  [bold]scathach review[/bold]                     Interactive review mode selector
  [bold]scathach review --flash-cards[/bold]       Level 1–2 FSRS review
  [bold]scathach review --long-answers[/bold]      Level 3–6 FSRS review
  [bold]scathach review --topics[/bold]            Quest for each due topic
  [bold]scathach review --new-questions[/bold]     Fresh questions for weak spots
  [bold]scathach stats[/bold]                      Progress dashboard

Tip: drop documents into [bold]~/.scathach/docs/[/bold] and run [bold]scathach ingest[/bold] to pick them all up.
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
    """
    if ctx is not None and ctx.invoked_subcommand is None:
        console.print(_BANNER)
        conn = open_db(settings.db_path)
        try:
            from scathach.cli.stats_ui import render_stats
            render_stats(conn)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

_DOCS_DIR = CONFIG_DIR / "docs"
_SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".htm", ".txt", ".md", ".markdown", ".rst"}


@app.command()
def ingest(
    path: Optional[str] = typer.Argument(
        None,
        help="Path to a document to ingest. Omit to scan ~/.scathach/docs/ for new files.",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Custom topic name."
    ),
    paste: bool = typer.Option(
        False, "--paste", "-p", help="Paste raw text instead of providing a file path."
    ),
    url: Optional[str] = typer.Option(
        None, "--url", "-u", help="URL of a web page or PDF to fetch and ingest."
    ),
) -> None:
    """Ingest documents into scathach.

    With no arguments, scans [bold]~/.scathach/docs/[/] for any files not yet ingested.
    Pass a file path, [bold]--paste[/bold] for raw text, or [bold]--url[/bold] for a web page.
    """
    mode_count = sum([paste, path is not None, url is not None])
    if mode_count > 1:
        console.print("[red]Use only one of: a file path argument, --paste, or --url.[/red]")
        raise typer.Exit(code=1)

    conn = open_db(settings.db_path)
    try:
        if paste:
            if name is None:
                name = typer.prompt("Topic name")
            console.print("[cyan]Paste your text below. Press Ctrl+D (or Ctrl+Z on Windows) when done.[/]")
            text = sys.stdin.read()
            topic = ingest_text(conn, text, topic_name=name)
            console.print(f"[green]Ingested topic '{topic.name}' (id={topic.id}) from pasted text.[/]")

        elif url is not None:
            with console.status(f"[cyan]Fetching {url}…[/]"):
                topic = ingest_url(conn, url, topic_name=name)
            console.print(
                f"[green]Ingested topic '[bold]{topic.name}[/]' (id={topic.id}) from URL.[/]"
            )

        elif path is not None:
            with console.status(f"[cyan]Ingesting {Path(path).name}…[/]"):
                topic = ingest_file(conn, path, topic_name=name)
            console.print(
                f"[green]Ingested topic '[bold]{topic.name}[/]' (id={topic.id}).[/]"
            )

        else:
            _ingest_docs_folder(conn)

        # Show updated topics table after any successful ingest
        console.print()
        from scathach.cli.stats_ui import render_stats
        render_stats(conn)

    except IngestionError as exc:
        console.print(f"[red]Ingestion failed:[/] {exc}")
        raise typer.Exit(code=1)
    finally:
        conn.close()


def _ingest_docs_folder(conn) -> None:
    """Scan ~/.scathach/docs/ for supported files not yet ingested and import them."""
    if not _DOCS_DIR.exists():
        console.print(
            f"[yellow]Docs folder [bold]{_DOCS_DIR}[/bold] not found.[/yellow]\n"
            "Create a [bold]~/.scathach/docs/[/bold] folder and drop "
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

    already_ingested: set[str] = {
        row["source_path"]
        for row in conn.execute(
            "SELECT source_path FROM topics WHERE source_path IS NOT NULL"
        ).fetchall()
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
            console.print(f"  [green]✓[/green] [bold]{topic.name}[/bold] (id={topic.id})")
            ingested_count += 1
        except IngestionError as exc:
            console.print(f"  [red]✗[/red] {file_path.name}: {exc}")
            failed_count += 1

    console.print(
        f"\n[bold]Done.[/bold] Ingested {ingested_count} new topic(s)"
        + (f", {failed_count} failed." if failed_count else ".")
    )


# ---------------------------------------------------------------------------
# session  (quest + drill)
# ---------------------------------------------------------------------------


@app.command()
def session(
    topic: Optional[str] = typer.Argument(None, help="Topic name to study."),
    drill: bool = typer.Option(
        False, "--drill", help="Drill mode: flat quiz at a single difficulty level."
    ),
    level: Optional[int] = typer.Option(
        None, "--level", "-l", min=1, max=6,
        help="Difficulty level for drill (1–6). Required with --drill.",
    ),
    count: int = typer.Option(
        5, "--count", "-c", min=1,
        help="Number of questions to generate in drill mode.",
    ),
    levels: Optional[int] = typer.Option(
        None, "--levels", min=1, max=6,
        help="Max difficulty levels for quest mode (default 6).",
    ),
    timed: Optional[bool] = typer.Option(
        None, "--timed/--untimed", help="Override default timing."
    ),
    hydra: Optional[bool] = typer.Option(
        None, "--hydra/--no-hydra",
        help="Enable Hydra Protocol on failure. Quest default: on. Drill default: on.",
    ),
    threshold: Optional[int] = typer.Option(
        None, "--threshold", min=5, max=10,
        help="Override pass threshold (5–10).",
    ),
    wizard: bool = typer.Option(
        False, "--wizard",
        help="Run the pre-session setup wizard to configure timing, threshold, and levels.",
    ),
    open_doc: Optional[bool] = typer.Option(
        None, "--open-doc/--no-open-doc",
        help="Open source document before starting.",
    ),
    resume: Optional[str] = typer.Option(
        None, "--resume", "-r", help="Resume an interrupted quest by its ID."
    ),
    delete: Optional[str] = typer.Option(
        None, "--delete", "-d", help="Delete a quest and all its questions by ID."
    ),
    list_sessions: bool = typer.Option(
        False, "--list", help="List all unfinished quests."
    ),
) -> None:
    """Start a learning session (quest or drill).

    [bold]Quest[/bold] (default): adaptive, all difficulty levels, Hydra Protocol on failure.
    [bold]Drill[/bold] ([bold]--drill --level N[/bold]): flat quiz at one difficulty level.

    Use [bold]--list[/bold], [bold]--resume[/bold], and [bold]--delete[/bold] to manage saved quests."""
    import asyncio
    from scathach.cli.session_ui import handle_event, make_answer_provider, pre_session_wizard

    conn = open_db(settings.db_path)
    try:
        # ---- List ----
        if list_sessions:
            active = list_active_sessions(conn)
            if not active:
                console.print("[dim]No unfinished quests.[/dim]")
                return
            tbl = Table(title="Unfinished Quests", show_lines=True)
            tbl.add_column("Quest ID", style="cyan", no_wrap=True)
            tbl.add_column("Topic", style="bold")
            tbl.add_column("Timing")
            tbl.add_column("Started")
            tbl.add_column("Remaining", justify="right")
            import json as _json
            for rec in active:
                t_obj = get_topic_by_id(conn, rec.topic_id)
                t_name = t_obj.name if t_obj else f"id={rec.topic_id}"
                remaining = 0
                if rec.question_stack:
                    try:
                        frames = _json.loads(rec.question_stack)
                        remaining = sum(len(f["question_ids"]) for f in frames)
                    except Exception:
                        pass
                tbl.add_row(
                    rec.session_id, t_name, rec.timing,
                    str(rec.created_at)[:16] if rec.created_at else "—",
                    str(remaining),
                )
            console.print(tbl)
            return

        # ---- Delete ----
        if delete is not None:
            rec = get_session_record(conn, delete)
            if rec is None:
                console.print(f"[red]Quest '{delete}' not found.[/red]")
                raise typer.Exit(code=1)
            t_obj = get_topic_by_id(conn, rec.topic_id)
            t_label = f"[bold]{t_obj.name}[/bold]" if t_obj else f"topic id={rec.topic_id}"
            n = delete_session(conn, delete)
            console.print(
                f"[green]Deleted quest [bold]{delete}[/bold] "
                f"({t_label}, {n} question(s) removed).[/green]"
            )
            return

        # ---- Resume ----
        if resume is not None:
            _require_api_key()
            rec = get_session_record(conn, resume)
            if rec is None:
                console.print(f"[red]Quest '{resume}' not found.[/red]")
                raise typer.Exit(code=1)
            if rec.status != "active":
                console.print(f"[yellow]Quest '{resume}' is already complete.[/yellow]")
                raise typer.Exit(code=1)
            if not rec.question_stack:
                console.print(f"[red]Quest '{resume}' has no saved state to resume.[/red]")
                raise typer.Exit(code=1)
            t_obj = get_topic_by_id(conn, rec.topic_id)
            should_open = open_doc if open_doc is not None else settings.open_doc_on_session
            _maybe_open_doc(t_obj.source_path if t_obj else None, should_open)
            from scathach.core.question import TimingMode as _TM
            timing_mode = _TM.TIMED if rec.timing == "timed" else _TM.UNTIMED
            cfg = SessionConfig(
                topic_id=rec.topic_id,
                timing=timing_mode,
                threshold=settings.quality_threshold,
                num_levels=rec.num_levels,
                hydra_retry_parent=True,
            )
            asyncio.run(SessionRunner(
                conn=conn, client=_make_client(), config=cfg,
                answer_provider=make_answer_provider(cfg.timing),
                event_handler=handle_event,
                restored_record=rec,
            ).run())
            return

        # ---- Drill mode ----
        if drill:
            if topic is None:
                console.print("[red]Provide a topic name: scathach session <topic> --drill --level N[/red]")
                raise typer.Exit(code=1)
            if level is None:
                console.print("[red]--level N is required in drill mode.[/red]")
                raise typer.Exit(code=1)
            _require_api_key()
            t_obj = get_topic_by_name(conn, topic)
            if t_obj is None:
                console.print(
                    f"[red]Topic '{topic}' not found.[/red] "
                    "Run [bold]scathach stats[/bold] to see available topics."
                )
                raise typer.Exit(code=1)
            from scathach.cli.drill_ui import run_drill_session
            from scathach.core.drill import DRILL_MAX_QUESTIONS
            cap = DRILL_MAX_QUESTIONS[level]
            if count > cap:
                console.print(f"[yellow]Count capped at {cap} for level {level}.[/yellow]")
            timing_mode = _resolve_timing(timed, settings.timing)
            hydra_enabled = hydra if hydra is not None else settings.hydra_in_drill
            asyncio.run(run_drill_session(
                conn=conn, client=_make_client(),
                topic_id=t_obj.id, level=level, count=count,
                timing=timing_mode,
                threshold=threshold if threshold is not None else settings.quality_threshold,
                hydra_enabled=hydra_enabled,
            ))
            return

        # ---- Quest mode ----
        if topic is None:
            console.print(
                "[red]Provide a topic name, or use --list / --resume.[/red]\n"
                "  scathach session [bold]<topic>[/bold]\n"
                "  scathach session [bold]--list[/bold]\n"
                "  scathach session [bold]--resume <id>[/bold]"
            )
            raise typer.Exit(code=1)
        _require_api_key()
        t_obj = get_topic_by_name(conn, topic)
        if t_obj is None:
            console.print(
                f"[red]Topic '{topic}' not found.[/red] "
                "Run [bold]scathach stats[/bold] to see available topics."
            )
            raise typer.Exit(code=1)
        should_open = open_doc if open_doc is not None else settings.open_doc_on_session
        _maybe_open_doc(t_obj.source_path, should_open)
        timing_mode = _resolve_timing(timed, settings.timing)
        hydra_enabled = hydra if hydra is not None else True
        cfg = SessionConfig(
            topic_id=t_obj.id,
            timing=timing_mode,
            threshold=threshold if threshold is not None else settings.quality_threshold,
            num_levels=levels or 6,
            hydra_retry_parent=True,
        )
        if wizard:
            cfg = pre_session_wizard(cfg)
        asyncio.run(SessionRunner(
            conn=conn, client=_make_client(), config=cfg,
            answer_provider=make_answer_provider(cfg.timing),
            event_handler=handle_event,
        ).run())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# review  (flash-cards / long-answers / topics / new-questions)
# ---------------------------------------------------------------------------


def _interactive_review_selector(
    conn,
    queue: str,
    threshold: int,
) -> Optional[str]:
    """Show the interactive review mode selector. Returns a mode string or None to quit."""
    from scathach.core.scheduler import get_scheduled_questions
    from scathach.core.topic_review import get_eligible_pairs
    from scathach.db.repository import get_due_topics

    now = datetime.now(UTC)
    flash_count = len(get_scheduled_questions(
        conn, queue, limit=999, now=now, min_difficulty=1, max_difficulty=2,
    ))
    long_count = len(get_scheduled_questions(
        conn, queue, limit=999, now=now, min_difficulty=3, max_difficulty=6,
    ))
    topics_due = len(get_due_topics(conn, now))
    new_q_count = len(get_eligible_pairs(conn, threshold))

    def _count(n: int, label: str = "due") -> str:
        col = "green" if n > 0 else "dim"
        return f"[{col}]{n} {label}[/{col}]"

    console.print(Panel(
        "\n".join([
            "  What would you like to review?\n",
            f"  [[bold]f[/bold]]  Flash cards    — {_count(flash_count)}  (levels 1–2)",
            f"  [[bold]l[/bold]]  Long answers   — {_count(long_count)}  (levels 3–6)",
            f"  [[bold]t[/bold]]  Topics         — {_count(topics_due, 'due')}",
            f"  [[bold]n[/bold]]  New questions  — {_count(new_q_count, 'eligible pairs')}",
            "  [[bold]a[/bold]]  All            — flash cards + long answers",
            "  [[bold]e[/bold]]  Everything     — all four modes",
            "  [[bold]q[/bold]]  Quit",
        ]),
        title="Review",
        border_style="cyan",
    ))

    if flash_count + long_count + topics_due + new_q_count == 0:
        console.print("[green]Nothing due across all modes. Great work![/green]")
        return None

    choice = console.input("  Choice [f/l/t/n/a/e/q]: ").strip().lower()
    return {
        "f": "flash-cards",
        "l": "long-answers",
        "t": "topics",
        "n": "new-questions",
        "a": "all",
        "e": "everything",
        "q": None,
    }.get(choice)


async def _run_review_mode(
    mode: str,
    conn,
    client,
    queue: str,
    timing_mode: TimingMode,
    threshold: int,
    limit: int,
    hydra_enabled: bool,
    on_failed,
    topic_id: Optional[int],
) -> None:
    from scathach.cli.review_ui import run_review_session, run_super_review_session
    from scathach.core.scheduler import get_scheduled_questions
    from scathach.db.repository import get_due_topics

    now = datetime.now(UTC)

    async def _flash():
        if get_scheduled_questions(conn, queue, limit=1, now=now,
                                   min_difficulty=1, max_difficulty=2, topic_id=topic_id):
            await run_review_session(conn, client, queue, timing_mode, threshold,
                                     limit, on_failed, topic_id=topic_id)
        else:
            console.print("[green]Flash cards: nothing due.[/green]")

    async def _long():
        if get_scheduled_questions(conn, queue, limit=1, now=now,
                                   min_difficulty=3, max_difficulty=6, topic_id=topic_id):
            await run_super_review_session(conn, client, queue, timing_mode, threshold,
                                           limit, hydra_enabled, on_failed, topic_id=topic_id)
        else:
            console.print("[green]Long answers: nothing due.[/green]")

    async def _topics():
        from scathach.cli.session_ui import handle_event, make_answer_provider
        from scathach.cli.topic_review_ui import run_topic_review
        if get_due_topics(conn, now):
            await run_topic_review(conn, client, timing_mode, threshold,
                                   True, handle_event, make_answer_provider)
        else:
            console.print("[green]Topics: nothing due.[/green]")

    async def _new_q():
        from scathach.cli.new_question_review_ui import run_new_question_review_session
        from scathach.core.topic_review import get_eligible_pairs
        if get_eligible_pairs(conn, threshold):
            await run_new_question_review_session(conn, client, threshold, timing_mode)
        else:
            console.print("[green]New questions: no eligible pairs.[/green]")

    if mode == "flash-cards":
        await run_review_session(conn, client, queue, timing_mode, threshold,
                                 limit, on_failed, topic_id=topic_id)
    elif mode == "long-answers":
        await run_super_review_session(conn, client, queue, timing_mode, threshold,
                                       limit, hydra_enabled, on_failed, topic_id=topic_id)
    elif mode == "topics":
        from scathach.cli.session_ui import handle_event, make_answer_provider
        from scathach.cli.topic_review_ui import run_topic_review
        await run_topic_review(conn, client, timing_mode, threshold,
                               True, handle_event, make_answer_provider)
    elif mode == "new-questions":
        from scathach.cli.new_question_review_ui import run_new_question_review_session
        await run_new_question_review_session(conn, client, threshold, timing_mode)
    elif mode == "all":
        await _flash()
        await _long()
    elif mode == "everything":
        await _flash()
        await _long()
        await _topics()
        await _new_q()


@app.command()
def review(
    flash_cards: bool = typer.Option(False, "--flash-cards", "-f",
        help="FSRS review: levels 1–2 (flash cards)."),
    long_answers: bool = typer.Option(False, "--long-answers", "-L",
        help="FSRS review: levels 3–6 (long answers), worst performers first."),
    topics_mode: bool = typer.Option(False, "--topics", "-t",
        help="Quest for each topic due for scheduled review."),
    new_questions: bool = typer.Option(False, "--new-questions", "-n",
        help="Generate fresh questions for struggling or stale topic+level pairs."),
    all_fsrs: bool = typer.Option(False, "--all", "-a",
        help="Run flash cards then long answers, skipping whichever has nothing due."),
    everything: bool = typer.Option(False, "--everything", "-e",
        help="Run all four modes in sequence, skipping any with nothing due."),
    timed: Optional[bool] = typer.Option(
        None, "--timed/--untimed", help="Override default timing."),
    limit: int = typer.Option(
        20, "--limit", "-l", help="Max questions per FSRS mode."),
    hydra: Optional[bool] = typer.Option(
        None, "--hydra/--no-hydra",
        help="Enable Hydra Protocol for long-answers and topics (overrides config)."),
    on_fail: Optional[str] = typer.Option(
        None, "--on-fail",
        help="Behaviour on failed FSRS question: repeat | skip | choose."),
    topic_filter: Optional[str] = typer.Option(
        None, "--topic",
        help="Restrict flash-cards and long-answers to a single topic."),
    open_doc: Optional[bool] = typer.Option(
        None, "--open-doc/--no-open-doc",
        help="Open source documents before starting."),
) -> None:
    """Review due questions.

    Run with no mode flag for an interactive selector showing live due-counts.

    Modes: [bold]--flash-cards[/bold]  [bold]--long-answers[/bold]  [bold]--topics[/bold]  [bold]--new-questions[/bold]
    Combos: [bold]--all[/bold] (flash+long)  [bold]--everything[/bold] (all four)"""
    import asyncio
    from scathach.config import OnFailedReview

    _require_api_key()

    # Validate mutual exclusivity
    active_flags = sum([flash_cards, long_answers, topics_mode, new_questions, all_fsrs, everything])
    if active_flags > 1:
        console.print("[red]Specify only one mode flag at a time.[/red]")
        raise typer.Exit(code=1)

    # Resolve on_fail override
    on_failed = settings.on_failed_review
    if on_fail is not None:
        val = on_fail.lower().strip()
        if val not in ("repeat", "skip", "choose"):
            console.print("[red]--on-fail must be repeat, skip, or choose.[/red]")
            raise typer.Exit(code=1)
        on_failed = OnFailedReview(val)

    timing_mode = _resolve_timing(timed, settings.timing)
    queue = timing_mode.value
    hydra_enabled = hydra if hydra is not None else settings.hydra_in_review
    threshold = settings.quality_threshold

    conn = open_db(settings.db_path)
    try:
        # Resolve --topic filter
        topic_id: Optional[int] = None
        if topic_filter is not None:
            t_obj = get_topic_by_name(conn, topic_filter)
            if t_obj is None:
                console.print(
                    f"[red]Topic '{topic_filter}' not found.[/red] "
                    "Run [bold]scathach stats[/bold] to see available topics."
                )
                raise typer.Exit(code=1)
            topic_id = t_obj.id

        # Determine mode
        if active_flags == 0:
            mode = _interactive_review_selector(conn, queue, threshold)
            if mode is None:
                return
        else:
            mode = (
                "flash-cards" if flash_cards else
                "long-answers" if long_answers else
                "topics" if topics_mode else
                "new-questions" if new_questions else
                "all" if all_fsrs else
                "everything"
            )

        # Open docs if requested (flash/long only — others open their own docs)
        if open_doc or (open_doc is None and settings.open_doc_on_session):
            if mode in ("flash-cards", "all", "everything"):
                _open_docs_for_due_questions(conn, queue, min_d=1, max_d=2)
            if mode in ("long-answers", "all", "everything"):
                _open_docs_for_due_questions(conn, queue, min_d=3, max_d=6)

        asyncio.run(_run_review_mode(
            mode, conn, _make_client(), queue, timing_mode,
            threshold, limit, hydra_enabled, on_failed, topic_id,
        ))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@app.command()
def stats(
    topic: Optional[str] = typer.Option(
        None, "--topic", "-t",
        help="Per-level breakdown for a single topic.",
    ),
) -> None:
    """Display the progress dashboard.

    Pass [bold]--topic <name>[/bold] to drill into per-level stats for one topic."""
    conn = open_db(settings.db_path)
    try:
        if topic is not None:
            from scathach.cli.stats_ui import render_topic_stats
            render_topic_stats(conn, topic)
        else:
            from scathach.cli.stats_ui import render_stats
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
    set_timing: Optional[str] = typer.Option(
        None, "--set-timing",
        help="Set the default timing for all sessions: 'timed' or 'untimed'.",
    ),
    test: bool = typer.Option(False, "--test", help="Send a canary prompt to verify the API key."),
    show: bool = typer.Option(False, "--show", help="Print current configuration."),
) -> None:
    """View or update scathach configuration."""
    import asyncio

    if show or not any([set_model, set_timing, test]):
        table = Table(title="Current Configuration", show_lines=True)
        table.add_column("Setting", style="bold")
        table.add_column("Value")
        table.add_row("API key", "[green]set[/green]" if settings.openrouter_api_key else "[red]NOT SET[/red]")
        table.add_row("Model", settings.model)
        table.add_row("Timing", settings.timing.value)
        table.add_row("Quality threshold", str(settings.quality_threshold))
        table.add_row("Hydra in review", str(settings.hydra_in_review))
        table.add_row("Hydra in drill", str(settings.hydra_in_drill))
        table.add_row("On failed review", settings.on_failed_review.value)
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

    if set_timing:
        val = set_timing.lower().strip()
        if val not in ("timed", "untimed"):
            console.print("[red]Timing must be 'timed' or 'untimed'.[/red]")
            raise typer.Exit(code=1)
        _write_env_var("SCATHACH_TIMING", val)
        console.print(f"[green]Timing set to:[/green] {val}")

    if test:
        _require_api_key()
        console.print(f"[cyan]Testing connection to {settings.model}…[/cyan]")
        try:
            result = asyncio.run(_make_client().generate(
                system_prompt="You are a helpful assistant.",
                user_prompt="Reply with exactly: 'scathach API test OK'",
                max_tokens=20,
            ))
            console.print(f"[green]API test passed.[/green] Response: {result.strip()}")
        except Exception as exc:
            console.print(f"[red]API test failed:[/red] {exc}")
            raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# topic sub-commands
# ---------------------------------------------------------------------------


@topic_app.command("rename")
def topic_rename(
    old_name: str = typer.Argument(..., help="Current topic name."),
    new_name: str = typer.Argument(..., help="New topic name."),
) -> None:
    """Rename a topic."""
    conn = open_db(settings.db_path)
    try:
        if get_topic_by_name(conn, old_name) is None:
            console.print(
                f"[red]Topic '{old_name}' not found.[/red] "
                "Run [bold]scathach stats[/bold] to see available topics."
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


@topic_app.command("delete")
def topic_delete(
    name: str = typer.Argument(..., help="Topic name to permanently delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Permanently delete a topic and all its questions, attempts, and review entries."""
    conn = open_db(settings.db_path)
    try:
        t_obj = get_topic_by_name(conn, name)
        if t_obj is None:
            console.print(
                f"[red]Topic '{name}' not found.[/red] "
                "Run [bold]scathach stats[/bold] to see available topics."
            )
            raise typer.Exit(code=1)
        if not yes:
            console.print(
                f"[yellow]This will permanently delete topic '[bold]{name}[/bold]' and all "
                "associated questions, attempts, and review entries. This cannot be undone.[/yellow]"
            )
            confirm = console.input("Type the topic name to confirm: ").strip()
            if confirm != name:
                console.print("[dim]Cancelled.[/dim]")
                return
        n = delete_topic(conn, t_obj.id)
        console.print(
            f"[green]Deleted topic '[bold]{name}[/bold]' ({n} root question(s) removed).[/green]"
        )
    finally:
        conn.close()


@topic_app.command("set-level")
def topic_set_level(
    name: str = typer.Argument(..., help="Topic name."),
    level: int = typer.Argument(..., help="Target difficulty level (1–6)."),
) -> None:
    """Set the target difficulty level used during topic review quests."""
    if not 1 <= level <= 6:
        console.print("[red]Level must be between 1 and 6.[/red]")
        raise typer.Exit(code=1)
    conn = open_db(settings.db_path)
    try:
        t_obj = get_topic_by_name(conn, name)
        if t_obj is None:
            console.print(
                f"[red]Topic '{name}' not found.[/red] "
                "Run [bold]scathach stats[/bold] to see available topics."
            )
            raise typer.Exit(code=1)
        set_topic_target_level(conn, t_obj.id, level)
        console.print(f"[green]Target level for '[bold]{name}[/bold]' set to {level}.[/green]")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_docs_for_due_questions(conn, queue: str, min_d: int, max_d: int) -> None:
    from scathach.core.scheduler import get_scheduled_questions
    now = datetime.now(UTC)
    questions = get_scheduled_questions(conn, queue, limit=50, now=now,
                                        min_difficulty=min_d, max_difficulty=max_d)
    seen: set[int] = set()
    for q in questions:
        if q.topic_id in seen:
            continue
        seen.add(q.topic_id)
        row = conn.execute(
            "SELECT source_path FROM topics WHERE id = ?", (q.topic_id,)
        ).fetchone()
        if row and row["source_path"]:
            _maybe_open_doc(row["source_path"], open_doc=True)


def _write_env_var(key: str, value: str) -> None:
    """Write or update a key=value line in ~/.scathach/.env."""
    env_path = ENV_FILE
    env_path.parent.mkdir(parents=True, exist_ok=True)
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
