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
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from scathach.core.question import DifficultyLevel, TimerZone, TimingMode
from scathach.core.session import (
    AnswerScored,
    CurriculumReady,
    GeneratingCurriculum,
    HydraSpawned,
    HydraSpawning,
    QuestionPresented,
    SessionAborted,
    SessionComplete,
    SessionConfig,
    SessionEvent,
    SessionRunner,
)
from scathach.db.models import Question

console = Console()

# Active spinner task — started during long LLM calls, cancelled when done.
_spinner_task: Optional[asyncio.Task] = None


async def _run_spinner(message: str) -> None:
    """Display an animated spinner until cancelled."""
    renderable = Spinner("dots", text=Text(f" {message}", style="cyan"))
    with Live(renderable, console=console, refresh_per_second=12, transient=True):
        try:
            while True:
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass


async def _start_spinner(message: str) -> None:
    global _spinner_task
    await _stop_spinner()
    _spinner_task = asyncio.create_task(_run_spinner(message))


async def _stop_spinner(done_message: str = "") -> None:
    global _spinner_task
    if _spinner_task and not _spinner_task.done():
        _spinner_task.cancel()
        try:
            await _spinner_task
        except asyncio.CancelledError:
            pass
    _spinner_task = None
    if done_message:
        console.print(done_message)


# ---------------------------------------------------------------------------
# Toss sentinel
# ---------------------------------------------------------------------------


class TossQuestion(Exception):
    """Raised from answer collectors when the user presses Ctrl+T to toss a question."""


# ---------------------------------------------------------------------------
# Stars helper
# ---------------------------------------------------------------------------


def _difficulty_stars(level: int, total: int = 6) -> str:
    return "★" * level + "☆" * (total - level)


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


async def _get_answer_untimed(
    question: Question, allow_toss: bool = False
) -> tuple[str, Optional[float]]:
    """Collect a multiline answer without a timer. Escape+Enter to submit."""
    kb = KeyBindings()
    toss_flag = [False]

    @kb.add("escape", "enter")
    def submit(event):  # type: ignore[no-untyped-def]
        event.current_buffer.validate_and_handle()

    if allow_toss:
        @kb.add("c-t")
        def toss(event):  # type: ignore[no-untyped-def]
            toss_flag[0] = True
            event.current_buffer.validate_and_handle()

    session: PromptSession = PromptSession(key_bindings=kb)
    hint = "[dim]Type your answer. [bold]Escape+Enter[/bold] to submit"
    if allow_toss:
        hint += ", [bold]Ctrl+T[/bold] to toss"
    console.print(hint + ".[/dim]")
    answer = await session.prompt_async("> ", multiline=True)

    if toss_flag[0]:
        raise TossQuestion()

    return answer.strip(), None

async def _get_answer_timed(
    question: Question, allow_toss: bool = False
) -> tuple[str, float]:
    """Collect an answer under the dual-zone timer."""
    dl = DifficultyLevel.from_int(question.difficulty)
    timer = DualZoneTimer(dl.time_limit_s)
    timer.start()
    toss_flag = [False]

    hint = f"[dim]Type your answer. [bold]Escape+Enter[/bold] to submit"
    if allow_toss:
        hint += ", [bold]Ctrl+T[/bold] to toss"
    console.print(hint + f". Time limit: {dl.time_limit_s}s[/dim]")

    kb = KeyBindings()

    @kb.add("escape", "enter")
    def submit(event):  # type: ignore[no-untyped-def]
        event.current_buffer.validate_and_handle()

    if allow_toss:
        @kb.add("c-t")
        def toss(event):  # type: ignore[no-untyped-def]
            toss_flag[0] = True
            event.current_buffer.validate_and_handle()
    
    _BAR_WIDTH = 30

    def get_bottom_toolbar():
        """Dynamic toolbar evaluated every refresh_interval."""
        e = timer.elapsed()
        zone = timer.zone()

        if zone == TimerZone.NORMAL:
            fraction = max(0.0, (dl.time_limit_s - e) / dl.time_limit_s)
            filled = round(fraction * _BAR_WIDTH)
            bar = f'<ansigreen>{"█" * filled}</ansigreen>{"░" * (_BAR_WIDTH - filled)}'
            remaining = max(0.0, dl.time_limit_s - e)
            return HTML(f'{bar}  <ansigreen>{remaining:.0f}s remaining</ansigreen>')
        elif zone == TimerZone.PENALTY:
            fraction = max(0.0, (dl.time_limit_s * 2 - e) / dl.time_limit_s)
            filled = round(fraction * _BAR_WIDTH)
            bar = f'<ansiyellow>{"█" * filled}</ansiyellow>{"░" * (_BAR_WIDTH - filled)}'
            remaining = max(0.0, dl.time_limit_s * 2 - e)
            return HTML(f'{bar}  <ansiyellow><b>⚠ Over time — score halved. {remaining:.0f}s before auto-fail</b></ansiyellow>')
        else:
            bar = "░" * _BAR_WIDTH
            return HTML(f'<ansired>{bar}  ⏰ Time expired — auto-fail</ansired>')
    
    # refresh_interval ensures the toolbar updates twice a second,
    # even if the user isn't actively typing.
    session: PromptSession = PromptSession(
        key_bindings=kb,
        bottom_toolbar=get_bottom_toolbar,
        refresh_interval=0.5
    )

    # Wrap the prompt in a task so we can forcefully close it if time expires
    prompt_task = asyncio.create_task(session.prompt_async("> ", multiline=True))

    async def enforce_timeout() -> None:
        while not timer.is_expired():
            if prompt_task.done():
                return
            await asyncio.sleep(0.5)
        
        # If timer expires and the user hasn't submitted, kill the prompt
        if not prompt_task.done():
            prompt_task.cancel()

    timeout_task = asyncio.create_task(enforce_timeout())

    try:
        answer = await prompt_task
    except asyncio.CancelledError:
        # Re-caught from prompt_task.cancel()
        answer = ""
        console.print("\n[red bold]⏰ Time expired! Input closed.[/red bold]")

    elapsed = timer.elapsed()

    if toss_flag[0]:
        raise TossQuestion()

    return answer.strip(), elapsed


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
    if isinstance(event, GeneratingCurriculum):
        await _start_spinner(f"Generating curriculum for '{event.topic_name}'…")
    elif isinstance(event, CurriculumReady):
        await _stop_spinner(
            f"[green]✓[/green] [dim]Curriculum ready — {event.num_questions} question(s) generated.[/dim]"
        )
    elif isinstance(event, HydraSpawning):
        await _start_spinner("Spawning Hydra sub-questions…")
    elif isinstance(event, QuestionPresented):
        await _stop_spinner()
        _render_question(event)
    elif isinstance(event, AnswerScored):
        _render_scored(event)
    elif isinstance(event, HydraSpawned):
        await _stop_spinner()
        _render_hydra(event)
    elif isinstance(event, SessionComplete):
        _render_complete(event)
    elif isinstance(event, SessionAborted):
        await _stop_spinner()
        console.print(f"[red bold]Session aborted:[/red bold] {event.reason}")


def _render_question(event: QuestionPresented) -> None:
    dl = DifficultyLevel.from_int(event.question.difficulty)
    if event.depth > 0:
        prefix = f"[Hydra Depth {event.depth}] Question {event.index}/{event.total}"
        border = "magenta"
    else:
        prefix = f"Question {event.index}/{event.total}"
        border = "cyan"
    title = f"{prefix} — {_difficulty_stars(event.question.difficulty)} ({dl.label})"
    console.print()
    console.print(Panel(
        Text(event.question.body, style="bold white"),
        title=title,
        border_style=border,
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
    console.print(Panel(
        event.ideal_answer,
        title="Ideal Answer",
        border_style="green" if a.passed else "yellow",
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
    sub_difficulty = event.subquestions[0].difficulty if event.subquestions else 1
    console.print(f"\n[cyan bold]🐍 Hydra Protocol:[/cyan bold] {n} sub-question{'s' if n != 1 else ''} spawned at difficulty {_difficulty_stars(sub_difficulty, event.num_levels)}")
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
    async def provider(question: Question, timed: bool) -> tuple[str, Optional[float]]:
        if timed:
            return await _get_answer_timed(question)
        return await _get_answer_untimed(question)
    return provider
