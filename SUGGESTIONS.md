# Suggestions & Design Notes

## S1: Session Interrupt + Resume (Phase 5.1 scope)

The roadmap specifies `Ctrl+C` interrupt handling and `scathach session --resume`.
Currently `SessionRunner` is I/O-agnostic but has no persistence of mid-session state.

**Suggested approach:** After each `record_attempt()`, serialize the question stack to a
`session_state` JSON column in a new `sessions` table. On `--resume`, reconstruct the stack
from the last unfinished session for that topic. The session_id UUID is already threaded through
all attempts, making reconstruction straightforward.

This was deferred because implementing it well requires schema work (new table) that would be
cleaner after end-to-end testing with a real API key.

---

## S2: Timed Input Architecture

The current dual-zone timer uses `asyncio.gather()` to run input collection and timer display
concurrently. This works but has a known limitation: the timer display loop writes to stdout
while prompt_toolkit also owns the terminal, which can cause visual artifacts in some terminals.

**Better approach for Phase 5 polish:** Use `prompt_toolkit`'s `Application` abstraction with a
custom `Layout` that renders both the timer bar and the input field atomically. Worth considering
if visual artifacts become a UX problem during testing.

---

## S3: `config` command writes to .env

The `scathach config --set-model` etc. currently writes directly to `.env`. This is fragile
(silently ignores OS env vars, doesn't work in containerized deployments).

**Better approach:** Use a dedicated user config file at `~/.scathach/config.toml` that
pydantic-settings reads at lower priority than env vars. The `_write_env_var()` helper in
`main.py` can be replaced with a TOML write. More conventional for user-facing CLIs.

---

## S4: Review Queue Gating (simplified in MVP)

The roadmap specifies difficulty 5–6 questions only surface in review if the user has cleared
all lower-difficulty questions. The MVP `get_scheduled_questions()` returns questions ordered
by difficulty without enforcing this gate.

**New approach:** The Spaced Repetition scheduling system will ONLY apply to questions of level 1 and 2 (short answer questions). The user will be able to optionally do a "super-review" in which they review the level 3-6 questions, in order from worst performance to best performance, easiest to hardest. Each "super-review" can optionally follow the Hydra protocol to spawn additional questions if the user gets the questions wrong. However, this does NOT trigger the hydra protocol by default, and the standard spaced repetition reviews do not trigger the Hydra protocol. This answers suggestion number 5 as well.

---

## S5: Hydra Protocol in Review Sessions

The roadmap specifies Hydra should apply in review sessions too. The current `review_ui.py`
does NOT spawn sub-questions on failure — it only scores and schedules. This is intentional
for MVP: during review, spawning new sub-questions would inflate the queue unpredictably.

**New approach:** We are splitting review sessions up into `review` (questions level 1-2) and `super_review` (questions level 3-6). Add a config flag `hydra_in_review: bool` (default False) that the user can enable during the super-review session. The Hydra protocol will NOT be followed during normal review. We will simply implement the FSRS algorithm.

## S6: `scathach generate` Debug Command

The roadmap mentions `scathach generate <topic_name>` as a Phase 2 deliverable, but generation
happens automatically inside `scathach session`. A standalone generate command is useful for
debugging prompts but was omitted to avoid complexity. Suggest adding as a `--dry-run` flag on
the `session` command instead, which prints the generated questions without starting a session.
