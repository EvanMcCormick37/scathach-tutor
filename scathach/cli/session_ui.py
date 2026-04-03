"""
Terminal UI for interactive learning sessions.

Provides:
- Pre-session configuration wizard
- DualZoneTimer: rich progress bar with penalty zone
- Answer input via prompt_toolkit multiline editor
- Event handler rendering all SessionRunner events
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from scathach.core.question import DifficultyLevel, TimerZone, TimingMode
from scathach.core.session import (
    AnswerScored,
    HydraSpawned,
    QuestionPresented,
    SessionAborted,
    SessionComplete,
    SessionConfig,
    SessionEvent,
    SessionRunner,
)
from scathach.db.models import Question

console = Console()


# ---------------------------------------------------------------------------
# Stars helper
# ---------------------------------------------------------------------------


def _difficulty_stars(level: int) -> str:
    return "★" * level + "☆" * (6 - level)


# ---------------------------------------------------------------------------
# DualZoneTimer
# ---------------------------------------------------------------------------


class DualZoneTimer:
    """
    Counts down from 0 to t (normal zone), then t to 2t (penalty zone).
    Tracks elapsed time for submission to score_answer.
    """

    def __init__(self, time_limit_s: int) -> None:
        self._t = time_limit_s
        self._start: float = 0.0

    def start(self) -> None:
        self._start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def zone(self) -> TimerZone:
        e = self.elapsed()
        if e <= self._t:
            return TimerZone.NORMAL
        if e <= self._t * 2:
            return TimerZone.PENALTY
        return TimerZone.EXPIRED

    def is_expired(self) -> bool:
        return self.elapsed() > self._t * 2

    def render_progress(self) -> str:
        """Return a one-line timer string for display."""
        e = self.elapsed()
        zone = self.zone()
        if zone == TimerZone.NORMAL:
            remaining = max(0.0, self._t - e)
            return f"[green]Time remaining: {remaining:.0f}s[/green]"
        elif zone == TimerZone.PENALTY:
            remaining = max(0.0, self._t * 2 - e)
            return f"[yellow bold]⚠ Over time — score halved. Time before auto-fail: {remaining:.0f}s[/yellow bold]"
        else:
            return "[red bold]⏰ Time expired — auto-fail[/red bold]"


# ---------------------------------------------------------------------------
# Answer input
# ---------------------------------------------------------------------------


async def _get_answer_untimed(question: Question) -> tuple[str, Optional[float]]:
    """Collect a multiline answer without a timer. Escape+Enter to submit."""
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def submit(event):  # type: ignore[no-untyped-def]
        event.current_buffer.validate_and_handle()

    session: PromptSession = PromptSession(key_bindings=kb)
    console.print(
        "[dim]Type your answer below. Press [bold]Escape then Enter[/bold] to submit.[/dim]"
    )
    answer = await session.prompt_async("> ", multiline=True)
    return answer.strip(), None


async def _get_answer_timed(question: Question) -> tuple[str, float]:
    """Collect an answer under the dual-zone timer."""
    dl = DifficultyLevel.from_int(question.difficulty)
    timer = DualZoneTimer(dl.time_limit_s)
    timer.start()

    console.print(
        f"[dim]Type your answer below. Press [bold]Escape then Enter[/bold] to submit. "
        f"Time limit: {dl.time_limit_s}s[/dim]"
    )

    kb = KeyBindings()

    @kb.add("escape", "enter")
    def submit(event):  # type: ignore[no-untyped-def]
        event.current_buffer.validate_and_handle()

    session: PromptSession = PromptSession(key_bindings=kb)

    # Run timer display and input collection concurrently
    answer_holder: list[str] = [""]

    async def collect_input() -> None:
        answer_holder[0] = await session.prompt_async("> ", multiline=True)

    async def display_timer() -> None:
        while not timer.is_expired():
            console.print(timer.render_progress(), end="\r")
            await asyncio.sleep(0.5)
            if answer_holder[0]:  # submitted
                break

    await asyncio.gather(
        collect_input(),
        display_timer(),
    )
    console.print()  # newline after timer display

    elapsed = timer.elapsed()
    return answer_holder[0].strip(), elapsed


# ---------------------------------------------------------------------------
# Pre-session wizard
# ---------------------------------------------------------------------------


def pre_session_wizard(defaults: SessionConfig) -> SessionConfig:
    """Interactive CLI wizard to configure the session before starting."""
    console.print(Panel("[bold cyan]Session Setup[/bold cyan]", expand=False))

    # Timing
    timing_choice = console.input(
        f"[1] Timed  [2] Untimed  (default: {'1' if defaults.timing == TimingMode.TIMED else '2'}): "
    ).strip()
    timing = TimingMode.TIMED if timing_choice == "1" else TimingMode.UNTIMED

    # Threshold
    threshold_str = console.input(
        f"Quality threshold 5–10 (default: {defaults.threshold}): "
    ).strip()
    try:
        threshold = int(threshold_str) if threshold_str else defaults.threshold
        threshold = max(5, min(10, threshold))
    except ValueError:
        threshold = defaults.threshold

    # Levels
    levels_str = console.input(
        f"Difficulty levels to include 3–6 (default: {defaults.num_levels}): "
    ).strip()
    try:
        num_levels = int(levels_str) if levels_str else defaults.num_levels
        num_levels = max(3, min(6, num_levels))
    except ValueError:
        num_levels = defaults.num_levels

    return SessionConfig(
        topic_id=defaults.topic_id,
        timing=timing,
        threshold=threshold,
        num_levels=num_levels,
    )


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


async def handle_event(event: SessionEvent) -> None:
    """Render a SessionEvent to the terminal."""
    if isinstance(event, QuestionPresented):
        _render_question(event)
    elif isinstance(event, AnswerScored):
        _render_scored(event)
    elif isinstance(event, HydraSpawned):
        _render_hydra(event)
    elif isinstance(event, SessionComplete):
        _render_complete(event)
    elif isinstance(event, SessionAborted):
        console.print(f"[red bold]Session aborted:[/red bold] {event.reason}")


def _render_question(event: QuestionPresented) -> None:
    dl = DifficultyLevel.from_int(event.question.difficulty)
    depth_label = f"[Hydra depth {event.depth}] " if event.depth > 0 else ""
    title = (
        f"{depth_label}Question {event.index}/{event.total} — "
        f"Difficulty {_difficulty_stars(event.question.difficulty)} ({dl.label})"
    )
    console.print()
    console.print(Panel(
        Text(event.question.body, style="bold white"),
        title=title,
        border_style="cyan",
        expand=True,
    ))


def _render_scored(event: AnswerScored) -> None:
    a = event.attempt
    raw = a.raw_score
    final = a.final_score

    if a.time_penalty:
        score_line = (
            f"[yellow]Raw: {raw}/10 → Final: {final}/10 [½ time penalty][/yellow]"
        )
    elif not a.timed or a.time_taken_s is None:
        score_line = _colorize_score(final)
    else:
        score_line = _colorize_score(final)

    result_label = "[green]PASSED[/green]" if a.passed else "[red]FAILED[/red]"
    console.print(f"\n{result_label}  {score_line}")
    console.print(f"[dim]Diagnosis: {event.diagnosis}[/dim]")

    if not a.passed:
        console.print(Panel(
            event.ideal_answer,
            title="[yellow]Ideal Answer[/yellow]",
            border_style="yellow",
            expand=True,
        ))


def _colorize_score(score: int) -> str:
    if score <= 4:
        return f"[red]{score}/10[/red]"
    if score <= 6:
        return f"[yellow]{score}/10[/yellow]"
    return f"[green]{score}/10[/green]"


def _render_hydra(event: HydraSpawned) -> None:
    n = len(event.subquestions)
    console.print(f"\n[cyan bold]🐍 Hydra Protocol:[/cyan bold] {n} sub-question{'s' if n != 1 else ''} spawned at difficulty {_difficulty_stars(event.subquestions[0].difficulty if event.subquestions else 1)}")
    console.print("[dim]Answer these to build understanding before retrying the parent question.[/dim]")


def _render_complete(event: SessionComplete) -> None:
    console.print()
    console.print(Panel("[bold green]Session Complete! 🎉[/bold green]", border_style="green"))

    table = Table(title="Session Summary", show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    total_attempts = len(event.attempts)
    passed_attempts = sum(1 for a in event.attempts if a.passed)
    penalized = sum(1 for a in event.attempts if a.time_penalty)
    avg_final = (
        sum(a.final_score for a in event.attempts) / total_attempts
        if total_attempts > 0 else 0
    )

    table.add_row("Questions cleared", str(len(event.cleared_questions)))
    table.add_row("Total attempts", str(total_attempts))
    table.add_row("Passed attempts", str(passed_attempts))
    table.add_row("Time-penalized attempts", str(penalized))
    table.add_row("Average final score", f"{avg_final:.1f}/10")

    console.print(table)


# ---------------------------------------------------------------------------
# Answer provider factory
# ---------------------------------------------------------------------------


def make_answer_provider(timing: TimingMode):
    """Return the appropriate answer-collection coroutine for the given timing mode."""
    async def provider(question: Question) -> tuple[str, Optional[float]]:
        if timing == TimingMode.TIMED:
            return await _get_answer_timed(question)
        return await _get_answer_untimed(question)
    return provider
