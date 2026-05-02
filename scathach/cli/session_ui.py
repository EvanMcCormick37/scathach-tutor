"""
Terminal UI for interactive learning sessions.

Provides:
- Pre-session configuration wizard
- DualZoneTimer: rich progress bar with penalty zone
- Answer input via prompt_toolkit TextArea (arrow-key navigation, Ctrl+S to submit)
- Event handler rendering all SessionRunner events
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Optional

from prompt_toolkit import Application
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
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

_BAR_WIDTH = 40


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
# External editor helper
# ---------------------------------------------------------------------------


def _open_in_editor(current_text: str) -> str:
    """
    Write `current_text` to a temp .md file, open the user's preferred editor,
    and return the file contents after the editor closes.

    Editor resolution order:
      $VISUAL → $EDITOR → notepad (Windows) / nano (Unix/macOS)

    VS Code users should set VISUAL="code --wait" so the process blocks until
    the window is closed.
    """
    editor_cmd = (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or ("notepad" if sys.platform == "win32" else "nano")
    )
    cmd = shlex.split(editor_cmd)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(current_text)
        tmp_path = f.name

    try:
        subprocess.run(cmd + [tmp_path], check=False)
        with open(tmp_path, encoding="utf-8") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _add_editor_binding(kb: KeyBindings, text_area: TextArea) -> None:
    """Register Ctrl+E on `kb` to open the current TextArea content in $EDITOR."""
    @kb.add("c-e")
    def open_editor(event):  # type: ignore[no-untyped-def]
        current = text_area.text

        def _launch() -> None:
            new_text = _open_in_editor(current)
            text_area.text = new_text.rstrip("\n")

        run_in_terminal(_launch)


def _open_source_doc(source_path: str) -> None:
    """Open the source document with the system default application."""
    if source_path.startswith(("http://", "https://")):
        try:
            webbrowser.open(source_path)
        except Exception:
            pass
        return
    p = Path(source_path)
    if not p.exists():
        return
    try:
        if sys.platform == "win32":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception:
        pass


def _add_doc_binding(kb: KeyBindings, source_path: Optional[str]) -> None:
    """Register Ctrl+O on `kb` to open the source document. No-op if source_path is None."""
    if not source_path:
        return

    @kb.add("c-o")
    def open_doc(event):  # type: ignore[no-untyped-def]
        run_in_terminal(lambda: _open_source_doc(source_path))


# ---------------------------------------------------------------------------
# Visual line navigation
# ---------------------------------------------------------------------------


def _visual_layout(text: str, width: int) -> list[int]:
    """
    Return the start absolute position of each visual row.

    prompt_toolkit's default up/down only moves across logical lines (newlines).
    This function computes wrap points so we can override that behaviour.
    """
    rows = [0]
    col = 0
    for i, ch in enumerate(text):
        if ch == "\n":
            rows.append(i + 1)
            col = 0
        else:
            col += 1
            if col >= width and i + 1 < len(text):
                rows.append(i + 1)
                col = 0
    return rows


def _move_cursor_visual(text: str, cursor_pos: int, width: int, direction: int) -> int:
    """Move cursor up (direction=-1) or down (+1) by one visual line."""
    rows = _visual_layout(text, width)
    n = len(rows)

    cur_row = 0
    for i in range(n - 1, -1, -1):
        if rows[i] <= cursor_pos:
            cur_row = i
            break

    col = cursor_pos - rows[cur_row]
    target_row = cur_row + direction

    if target_row < 0 or target_row >= n:
        return cursor_pos

    tstart = rows[target_row]
    tend = rows[target_row + 1] if target_row + 1 < n else len(text) + 1
    return tstart + min(col, max(0, tend - tstart - 1))


def _add_visual_navigation(kb: KeyBindings) -> None:
    """
    Override up/down arrows to navigate visual (wrap-aware) lines.

    eager=True ensures these fire before prompt_toolkit's built-in emacs
    bindings, which only navigate logical (newline-delimited) lines.
    """
    @kb.add("up", eager=True)
    def _up(event):  # type: ignore[no-untyped-def]
        buff = event.current_buffer
        # Subtract 1 for the scrollbar column.
        width = max(1, event.app.output.get_size().columns - 1)
        buff.cursor_position = _move_cursor_visual(buff.text, buff.cursor_position, width, -1)

    @kb.add("down", eager=True)
    def _down(event):  # type: ignore[no-untyped-def]
        buff = event.current_buffer
        width = max(1, event.app.output.get_size().columns - 1)
        buff.cursor_position = _move_cursor_visual(buff.text, buff.cursor_position, width, 1)


# ---------------------------------------------------------------------------
# Answer input
# ---------------------------------------------------------------------------


async def _get_answer_untimed(
    question: Question, allow_toss: bool = False, source_path: Optional[str] = None
) -> tuple[str, Optional[float]]:
    """Collect a multiline answer without a timer. Ctrl+S to submit."""
    kb = KeyBindings()
    text_area = TextArea(
        multiline=True,
        wrap_lines=True,
        scrollbar=True,
        height=8,
        focus_on_click=True,
    )

    @kb.add("c-s")
    def _submit(event):  # type: ignore[no-untyped-def]
        event.app.exit(result=text_area.text)

    if allow_toss:
        @kb.add("c-t")
        def _toss(event):  # type: ignore[no-untyped-def]
            event.app.exit(exception=TossQuestion())

    _add_visual_navigation(kb)
    _add_editor_binding(kb, text_area)
    _add_doc_binding(kb, source_path)

    hints = ["[bold]Ctrl+S[/bold] submit", "[bold]Ctrl+E[/bold] editor"]
    if source_path:
        hints.append("[bold]Ctrl+O[/bold] open doc")
    if allow_toss:
        hints.append("[bold]Ctrl+T[/bold] toss")
    console.print("[dim]" + "  ·  ".join(hints) + "[/dim]")

    app = Application(
        layout=Layout(text_area),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
    )
    answer = await app.run_async()
    return (answer or "").strip(), None


async def _get_answer_timed(
    question: Question, allow_toss: bool = False, source_path: Optional[str] = None
) -> tuple[str, float]:
    """Collect an answer under the dual-zone timer."""
    dl = DifficultyLevel.from_int(question.difficulty)
    timer = DualZoneTimer(dl.time_limit_s)

    kb = KeyBindings()
    text_area = TextArea(
        multiline=True,
        wrap_lines=True,
        scrollbar=True,
        height=8,
        focus_on_click=True,
    )

    expired = [False]

    @kb.add("c-s")
    def _submit(event):  # type: ignore[no-untyped-def]
        event.app.exit(result=text_area.text)

    if allow_toss:
        @kb.add("c-t")
        def _toss(event):  # type: ignore[no-untyped-def]
            event.app.exit(exception=TossQuestion())

    _add_visual_navigation(kb)
    _add_editor_binding(kb, text_area)
    _add_doc_binding(kb, source_path)

    def get_toolbar_text() -> HTML:
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
            return HTML(
                f'{bar}  <ansiyellow><b>⚠ Over time — score halved. '
                f'{remaining:.0f}s before auto-fail</b></ansiyellow>'
            )
        else:
            bar = "░" * _BAR_WIDTH
            return HTML(f'<ansired>{bar}  ⏰ Time expired — auto-fail</ansired>')

    toolbar = Window(
        height=1,
        content=FormattedTextControl(get_toolbar_text),
        dont_extend_height=True,
    )

    hints = ["[bold]Ctrl+S[/bold] submit", "[bold]Ctrl+E[/bold] editor", f"Time limit: {dl.time_limit_s}s"]
    if source_path:
        hints.append("[bold]Ctrl+O[/bold] open doc")
    if allow_toss:
        hints.append("[bold]Ctrl+T[/bold] toss")
    console.print("[dim]" + "  ·  ".join(hints) + "[/dim]")

    app = Application(
        layout=Layout(HSplit([text_area, toolbar])),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
    )

    timer.start()

    async def timer_watcher() -> None:
        while True:
            await asyncio.sleep(0.5)
            app.invalidate()
            if timer.is_expired():
                expired[0] = True
                app.exit(result=text_area.text)
                return

    watcher_task = asyncio.create_task(timer_watcher())
    try:
        answer = await app.run_async()
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass

    elapsed = timer.elapsed()

    if expired[0]:
        console.print("\n[red bold]⏰ Time expired! Input closed.[/red bold]")

    return (answer or "").strip(), elapsed


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
        if event.session_type == "drill":
            msg = f"Generating {event.drill_count} level-{event.drill_level} question(s) for '{event.topic_name}'…"
        else:
            msg = f"Generating curriculum for '{event.topic_name}'…"
        await _start_spinner(msg)
    elif isinstance(event, CurriculumReady):
        label = "Drill ready" if event.session_type == "drill" else "Curriculum ready"
        await _stop_spinner(
            f"[green]✓[/green] [dim]{label} — {event.num_questions} question(s) generated.[/dim]"
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
    elif event.is_retry:
        prefix = f"[RETRY] Question {event.index}/{event.total}"
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


def make_answer_provider(timing: TimingMode, source_path: Optional[str] = None):
    """Return the appropriate answer-collection coroutine for the given timing mode."""
    async def provider(question: Question, timed: bool) -> tuple[str, Optional[float]]:
        if timed:
            return await _get_answer_timed(question, source_path=source_path)
        return await _get_answer_untimed(question, source_path=source_path)
    return provider
