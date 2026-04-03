# Known Bugs

## B1: Timer display may produce visual artifacts in some terminals

**Severity:** Non-blocking cosmetic issue  
**Location:** `scathach/cli/session_ui.py` — `_get_answer_timed()`  
**Description:** The timer display loop (`display_timer()`) uses `asyncio.gather()` to run
concurrently with `prompt_toolkit`'s `prompt_async()`. Both write to stdout, which can produce
overlapping output in terminals that don't support ANSI cursor control well (e.g., basic Windows
CMD, some CI environments). Works correctly in Windows Terminal, iTerm2, and most Linux terminals.  
**Workaround:** Use `--untimed` flag or set `SCATHACH_MAIN_TIMING=untimed` in `.env`.  
**Fix:** Replace with a full `prompt_toolkit Application` layout (see SUGGESTIONS.md S2).

---

## B2: `scathach config --set-model` / `--set-review-timing` requires restart

**Severity:** Minor UX issue  
**Location:** `scathach/cli/main.py` — `_write_env_var()`  
**Description:** Writing to `.env` takes effect only after restarting the CLI process. The
currently-running process reads settings at import time and does not re-read them after
`_write_env_var()` modifies the file.  
**Workaround:** Restart the shell or re-run the next command.  
**Fix:** Consider using `~/.scathach/config.toml` instead (see SUGGESTIONS.md S3), or display
a note to the user that a restart is needed.

---

## B3: Session interrupt (Ctrl+C) discards in-progress session state

**Severity:** Minor data loss risk  
**Location:** `scathach/cli/main.py` — `session()` command, `asyncio.run(runner.run())`  
**Description:** Pressing Ctrl+C during a session raises `KeyboardInterrupt` and exits without
saving which questions were answered. Questions that were cleared and written to the review
queue are preserved (they were committed on each pass), but the remaining question stack is lost.  
**Workaround:** None for MVP. Resume feature is planned (see SUGGESTIONS.md S1).  
**Fix:** Wrap `asyncio.run(runner.run())` in a try/except KeyboardInterrupt and save the
session's remaining stack to a `sessions` table for resume.
