# Architecture: scathach

scathach is a terminal-based adaptive learning and spaced repetition system. A user ingests a document; the system generates comprehension questions at six difficulty levels, drills them, and schedules reviews via FSRS so the material sticks.

---

## Directory structure

```
scathach/
├── __init__.py             # Package metadata (version)
├── config.py               # Pydantic Settings singleton
├── cli/                    # Terminal UI (Typer + Rich + prompt_toolkit)
│   ├── main.py             # Top-level command definitions + topic sub-group
│   ├── session_ui.py       # Learning session TUI (timers, input, event rendering)
│   ├── review_ui.py        # Review TUI (flash-cards & long-answers)
│   ├── drill_ui.py         # Drill session UI (placeholder; drills run via SessionRunner)
│   ├── topic_review_ui.py  # Topic-scheduled quest UI
│   ├── new_question_review_ui.py  # Fresh-question review UI
│   └── stats_ui.py         # Progress dashboard
├── core/                   # Business logic (no I/O)
│   ├── question.py         # Domain enums/types: DifficultyLevel, TimingMode, TimerZone
│   ├── session.py          # SessionRunner state machine
│   ├── hydra.py            # Hydra Protocol (sub-question spawning)
│   ├── scheduler.py        # FSRS-based spaced repetition
│   ├── scoring.py          # LLM answer evaluation + time-penalty logic
│   ├── drill.py            # Drill question generation
│   ├── topic_review.py     # Eligible-pair detection + single-question generation
│   └── topic_support.py    # Topic-level exam/practice support + next_review_at finalization
├── db/                     # SQLite persistence
│   ├── schema.py           # DDL, versioning, connection management
│   ├── models.py           # Dataclasses: Topic, Question, Attempt, SessionRecord, ReviewEntry
│   └── repository.py       # CRUD operations for all entities
├── ingestion/              # Document ingestion pipeline
│   ├── ingestor.py         # File/URL/paste → markdown → Topic
│   └── chunker.py          # MVP stub (future RAG/chunking)
└── llm/                    # LLM interaction layer
    ├── client.py           # Async OpenAI-compatible wrapper (retry/backoff)
    ├── prompts.py          # Version-pinned prompt templates
    ├── parsing.py          # Robust JSON extraction with fallbacks
    └── providers.py        # Provider registry (Gemini, Kimi, Arcee, Qwen)
```

---

## Layer overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLI  (Typer / Rich)                          │
│  main.py · session_ui.py · review_ui.py · drill_ui.py · stats_ui.py│
└──────────┬──────────────────────────────────────────────────────────┘
           │  callbacks / events
┌──────────▼──────────────────────────────────────────────────────────┐
│                      CORE  (pure business logic)                    │
│  session.py · scoring.py · hydra.py · scheduler.py · question.py   │
│  drill.py · topic_review.py · topic_support.py                     │
└────┬─────────────┬────────────────────────────────────┬────────────┘
     │             │                                    │
┌────▼────┐  ┌─────▼──────┐                   ┌────────▼────────┐
│   DB    │  │    LLM     │                   │   INGESTION     │
│ SQLite  │  │ OpenRouter │                   │   (docling)     │
└─────────┘  └────────────┘                   └─────────────────┘
     ▲              ▲                                  ▲
     └──────────────┴──────────────────────────────────┘
                    CONFIG  (pydantic-settings, ~/.scathach/.env)
```

The core layer is I/O-agnostic. `SessionRunner` receives async callbacks for answer collection and event rendering, so the same logic can be driven by the TUI or a test harness.

---

## CLI layer (`cli/`)

### `main.py`

Defines the Typer application and all top-level commands. Thin orchestration only — resolves config, opens a DB connection, and hands off to core logic or UI helpers.

Running `scathach` with no arguments prints the banner and renders the stats dashboard.

#### Top-level commands

| Command | Purpose |
|---------|---------|
| `ingest [srcpath] [name]` | Ingest a file or URL with an optional topic name; omit both to scan `~/.scathach/docs/`; shows updated topics table on success |
| `session quest <topic>` | Start an adaptive quest (Hydra, levels 1–4 by default); opens source doc by default |
| `session quest <topic> --exam` | Closed-book quest; source doc not opened; updates exam support |
| `session drill <topic> --level N` | Fixed-level quiz; stored in sessions table, resumable |
| `session list` | List all unfinished sessions |
| `session resume <id>` | Resume an interrupted session |
| `session delete <id>` | Permanently delete a session and its questions |
| `review` | Interactive review mode selector (shows live due-counts) |
| `review --flash-cards` | FSRS review: levels 1–2 |
| `review --long-answers` | FSRS review: levels 3–6, worst performers first |
| `review --topics` | Quest for each topic due for scheduled review |
| `review --new-questions` | Fresh questions for struggling/stale topic+level pairs |
| `review --all` | Flash-cards then long-answers, skipping if nothing due |
| `review --everything` | All four review modes in sequence, skipping empty modes |
| `stats` | Progress dashboard (topics, queues, score distribution) |
| `stats --topic <name>` | Per-level breakdown for a single topic |
| `config --show` | Print current configuration |
| `config --set-model` | Change the active LLM model |
| `config --set-timing` | Set the default timing for all commands |
| `config --test` | Send a canary prompt to verify the API key |

#### `topic` sub-group

| Command | Purpose |
|---------|---------|
| `topic rename <old> <new>` | Rename a topic |
| `topic delete <name>` | Permanently delete a topic and all its data |
| `topic set-level <name> <level>` | Set the target difficulty level for topic review quests |

#### `session quest` flags

| Flag | Default | Notes |
|------|---------|-------|
| `--levels N` | 4 | Max difficulty levels. |
| `--timed/--untimed` | config | Override timing. |
| `--hydra/--no-hydra` | on | Hydra Protocol on failure. |
| `--threshold N` | config | Override pass threshold (5–10). |
| `--wizard` | off | Run the pre-session setup wizard (opt-in). |
| `--exam` | off | Closed-book mode: source doc not opened; updates exam_support. |

#### `session drill` flags

| Flag | Default | Notes |
|------|---------|-------|
| `--level N` | _(required)_ | Difficulty level (1–6). |
| `--count N` | 5 | Questions to generate. |
| `--timed/--untimed` | config | Override timing. |
| `--hydra/--no-hydra` | config | Hydra Protocol on failure. |
| `--threshold N` | config | Override pass threshold (5–10). |
| `--exam` | off | Closed-book mode: source doc not opened; updates exam_support. |

#### `review` flags

| Flag | Notes |
|------|-------|
| `--timed/--untimed` | Override timing. |
| `--limit N` | Max questions per FSRS mode (default 20). |
| `--hydra/--no-hydra` | Hydra for long-answers and topics. |
| `--on-fail repeat\|skip\|choose` | Override failure behaviour for FSRS modes. |
| `--topic <name>` | Restrict flash-cards and long-answers to one topic. |

---

### `session_ui.py`

Drives the interactive learning session in the terminal.

- **`pre_session_wizard()`** — Prompts for timing mode, quality threshold, and difficulty range. Opt-in via `--wizard` (off by default).
- **`handle_event(event)`** — Async event handler passed to `SessionRunner`. Renders each `SessionEvent` subclass to the terminal using Rich panels and spinners. The ideal answer is always shown after scoring — green border on pass, yellow on fail.
- **`DualZoneTimer`** — Live toolbar with two visual zones:
  - **NORMAL** (0 – t): green, full score.
  - **PENALTY** (t – 2t): yellow, score halved on submit.
  - **EXPIRED** (>2t): red, auto-fail.
- **Answer collectors** — `_get_answer_untimed()` and `_get_answer_timed()` both use prompt_toolkit for multiline input. The timed variant runs the timer bar concurrently and kills input on expiry.

#### External editor (`Ctrl+E`)

Both answer collectors register `Ctrl+E`. When pressed:

1. The current buffer text is written to a temporary `.md` file.
2. The user's editor is launched synchronously via `run_in_terminal` (which suspends the prompt_toolkit application and hands the terminal back).
3. When the editor closes the buffer is populated with the file contents and the user can submit normally.

Editor resolution order: `$VISUAL` → `$EDITOR` → `notepad` (Windows) / `nano` (Unix/macOS).

VS Code users should set `VISUAL="code --wait"` so the process blocks until the window is closed.

The timer is **not** paused when the editor is open in timed mode — time continues to run.

---

### `review_ui.py`

- **`run_review_session()`** — Levels 1–2 FSRS review. Accepts an optional `topic_id` to restrict to one topic. On failure, behaviour is controlled by `SCATHACH_ON_FAILED_REVIEW`.
- **`run_super_review_session()`** — Levels 3–6, worst-score-first per difficulty tier, optional Hydra. Accepts `topic_id` filter.
- **`_show_result()`** — Renders score, diagnosis, and ideal answer after every question. Ideal answer is always displayed (green panel on pass, yellow on fail).

---

### `drill_ui.py`

Runs a flat quiz of freshly generated questions at a single difficulty level. Uses a mutable `queue_list` so Hydra sub-questions can be inserted mid-session. Hydra is enabled by default (`SCATHACH_HYDRA_IN_DRILL=true`).

---

### `stats_ui.py`

Renders three Rich tables: topic inventory (ID, name, question count, avg difficulty, avg score, source, created), review queue health (total / due now / due this week, filtered to `state = 'review'`), and score distribution by difficulty.

`render_topic_stats()` renders a per-level breakdown for a single topic including FSRS queue state.

---

## Core layer (`core/`)

### `question.py` — Domain types

```
DifficultyLevel  (1–6)
  metadata per level:
    time_limit_s   —  30 · 60 · 300 · 600 · 900 · 1800
    answer_format  —  word/phrase · sentence · paragraph · …
    coverage       —  narrow fact · key concept · section · …

TimingMode       —  TIMED | UNTIMED
TimerZone        —  NORMAL | PENALTY | EXPIRED
```

`DifficultyLevel` carries `DifficultyMeta` so both the prompt templates and the TUI can derive time limits and answer format expectations from the same source of truth.

### `session.py` — SessionRunner

The central state machine for both quest and drill sessions. Orchestrates the full learning loop:

```
IDLE
 └─► GENERATING          generate_root_questions() via LLM
      └─► QUESTION_PRESENTED
           └─► AWAITING_ANSWER    (answer_provider callback)
                └─► SCORING       score_answer() via LLM
                     └─► SHOWING_RESULT
                          ├── PASS ──► upsert_review_entry (both queues)
                          │           └─► QUESTION_PRESENTED (next)
                          └── FAIL ──► HYDRA_SPAWNING
                                        └─► spawn_subquestions() via LLM
                                             └─► QUESTION_PRESENTED (sub-questions)
                                                  └─► … (parent retried after sub-tree cleared)
SESSION_COMPLETE  or  ABORTED
```

**Persistence:** After every question, the full question stack, cleared IDs, and session metadata are serialized to JSON and written to the `sessions` table. `Ctrl+C` is caught; the session survives and can be resumed with `scathach session --resume <id>`.

**Hydra retry:** The parent question is always re-asked after its sub-tree is cleared (`hydra_retry_parent = True` is the enforced design — the student must answer the original question correctly to clear it).

**Events emitted** (consumed by `handle_event` in the CLI):

| Event | Data |
|-------|------|
| `GeneratingCurriculum` | topic_name |
| `CurriculumReady` | num_questions |
| `QuestionPresented` | question, index, total, depth |
| `AnswerScored` | attempt, diagnosis, ideal_answer |
| `HydraSpawning` | parent_question |
| `HydraSpawned` | subquestions, parent_question, num_levels |
| `SessionComplete` | cleared_questions, attempts |
| `SessionAborted` | reason |

### `scoring.py` — Answer evaluation

1. Renders the scoring prompt (question body + difficulty + student answer; **never** passes timing data to the LLM).
2. Injects context depending on the call site:
   - **Session / drill**: full source document (`document_content`) so the scorer can verify factual accuracy.
   - **Review**: stored `ideal_answer` so the scorer has a reference without needing the full document.
3. LLM returns `{score: 0–10, diagnosis: str}`.
4. `apply_time_penalty()` applies pure application logic:
   - `EXPIRED` → auto-fail (score 0).
   - `PENALTY` → `floor(raw_score / 2)`.
   - `NORMAL` → `raw_score` unchanged.
5. Returns `(Attempt, diagnosis_str)`.

### `hydra.py` — Hydra Protocol

When a student fails a question:

1. Fetches all existing questions with difficulty **strictly below** the parent's difficulty for the same topic (used for deduplication across all eligible levels).
2. Renders a prompt embedding: the parent question, the student's answer, the LLM's diagnosis, the difficulty rubric for all sub-levels, and the existing questions grouped by level.
3. LLM selects both the **number** (1–5) and **difficulty level** of each sub-question — any level strictly below the parent. The set is designed to be necessary and sufficient to understand the parent (e.g., a level-4 failure might produce one level-1 definition and two level-2 concepts if those are the actual gaps).
4. Any returned questions violating the difficulty constraint are discarded; the list is capped at 5.
5. Sub-questions are inserted into the DB as children of the parent.
6. They are pushed onto the session stack; the parent remains at position 0 and is retried immediately after its sub-tree is cleared.
7. The tree can branch multiple levels deep.

### `scheduler.py` — FSRS-based spaced repetition

Simplified FSRS using stability as the core parameter:

| Final score | State | Stability multiplier | Min interval |
|-------------|-------|----------------------|--------------|
| 0–4 | relearning | 0.5× | 1 day |
| 5–6 | learning | 0.8× | 1 day |
| 7–8 | review | 2.5× | — |
| 9–10 | review | 3.5× | — |

- **`update_schedule(conn, question_id, score, queue)`** — Computes new stability, interval, and next review timestamp, then upserts into the appropriate queue table.
- **`get_scheduled_questions(conn, queue, ...)`** — Returns questions where `next_review_at <= now` (or `NULL` for never-seen). The `worst_first` flag sorts by `last_score ASC` within each difficulty tier, surfacing problem areas first. Accepts an optional `topic_id` to filter to a single topic.

Two queues (`timed_review_queue`, `untimed_review_queue`) track the same question independently — a question can have different FSRS state depending on whether it was answered under time pressure.

**Stats filtering:** The "Due Now" and "Due This Week" counts in the stats dashboard only include questions with `state = 'review'`, excluding `new` / `learning` / `relearning` entries that have not yet graduated.

---

## Database layer (`db/`)

### `schema.py`

Opens a SQLite connection, sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`, then runs idempotent `CREATE TABLE IF NOT EXISTS` DDL. A `schema_version` table tracks the applied migration version.

### Tables

| Table | Key columns |
|-------|------------|
| `topics` | `id`, `name` (UNIQUE), `source_path`, `content`, `exam_support`, `practice_support`, `next_review_at`, `target_level`, `created_at` |
| `questions` | `id`, `topic_id` (FK), `parent_id` (FK self — Hydra hierarchy), `difficulty` (1–6), `body`, `ideal_answer`, `is_root` |
| `attempts` | `id`, `question_id` (FK), `session_id`, `answer_text`, `raw_score`, `final_score`, `time_taken_s`, `time_penalty`, `timed`, `passed` |
| `sessions` | `id`, `topic_id` (FK), `status` (active/complete), `session_type` (quest/drill), `timing`, `threshold`, `num_levels`, `is_exam`, `drill_level`, `question_stack` (JSON), `cleared_ids` (JSON), `root_ids` (JSON) |
| `timed_review_queue` | `question_id` (PK/FK), `last_score`, `next_review_at`, `stability`, `difficulty_fsrs`, `state` (new/learning/review/relearning) |
| `untimed_review_queue` | (same structure as timed) |

Questions form a tree: root questions have `parent_id = NULL` and `is_root = TRUE`; Hydra sub-questions have `parent_id` pointing to their parent question.

### `repository.py`

All DB access goes through typed functions in this module. No SQL appears elsewhere.

Key operations:
- `upsert_topic()` / `delete_topic()` — insert-or-update or permanently delete a topic (cascades to all questions, attempts, review entries, and sessions).
- `insert_question()` / `get_children()` / `delete_question()` (cascades to attempts + review entries via recursive CTE).
- `record_attempt()` / `get_latest_attempt()`.
- `upsert_review_entry()` / `get_due_questions()` — `get_due_questions` accepts `topic_id` to restrict results to a single topic.
- `create_session_record()` / `update_session_state()` / `complete_session()` / `list_active_sessions()`.

---

## LLM layer (`llm/`)

### `client.py`

Async wrapper around the `openai` SDK pointed at `https://openrouter.ai/api/v1`.

- **`generate(system_prompt, user_prompt)`** — Single public method. Returns assistant text.
- **Retry policy** — Exponential backoff (`2^n` seconds) on HTTP 429, 500, 502, 503, 504; max 3 retries.
- **`make_client(api_key, model)`** — Factory that reads provider defaults (max_tokens, temperature) from the provider registry.

### `prompts.py`

All prompt templates are version-pinned. Bumping the version signals that generated outputs may differ and downstream parsing/tests need re-verification.

| Template | Inputs | Expected output |
|----------|--------|----------------|
| `render_question_generation_prompt()` | topic content, prior questions, num_levels | JSON array: `[{difficulty, body, ideal_answer}, …]` |
| `render_hydra_prompt()` | parent question, answer, diagnosis, all existing questions below parent level | JSON array: `[{difficulty, body, ideal_answer}, …]` (1–5 items, mixed levels) |
| `render_scoring_prompt()` | question body, difficulty, student answer, document_content or ideal_answer | JSON object: `{score, diagnosis}` |
| `render_drill_prompt()` | topic content, level, count, prior questions | JSON array of exactly `count` questions all at `level` |

The scoring prompt injects the source document for session/drill scoring, and the stored ideal answer for review scoring. Timing data is never passed to the LLM — penalties are applied in application code after the fact.

### `parsing.py`

Robust extraction handles models that wrap JSON in markdown code fences or add prose:

1. Direct `json.loads()`.
2. Regex extraction of the first `[…]` block.
3. Regex extraction of the first `{…}` block.
4. Raises `ParseError` if all strategies fail.

### `providers.py`

Registry of `ProviderConfig` objects (model_id, display_name, max_tokens, temperature, is_free). `get_provider(model_id)` returns the config or a generic fallback.

---

## Ingestion pipeline (`ingestion/`)

### `ingestor.py`

```
User input
  ├─ filepath (.pdf / .docx / .pptx / .html)  ─► docling DocumentConverter → markdown
  ├─ filepath (.txt / .md / .rst)             ─► plain-text read (UTF-8 + latin-1 fallback)
  └─ URL (http/https)                          ─► httpx.get → temp file → docling → markdown
         └─► upsert_topic(conn, Topic(name, content, source_path))
```

Topic name defaults: filename stem for files, `<title>` tag for URLs, user-supplied for pastes. After ingestion the CLI automatically renders the full stats dashboard.

### `chunker.py`

MVP stub — returns the full document as a single chunk. Placeholder for a future RAG/embedding pipeline when documents exceed LLM context limits.

---

## Configuration (`config.py`)

Pydantic `BaseSettings` singleton. Source priority: environment variables > `~/.scathach/.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `SCATHACH_OPENROUTER_API_KEY` | _(required)_ | OpenRouter API key |
| `SCATHACH_MODEL` | `google/gemini-3.1-flash-lite-preview` | LLM model identifier |
| `SCATHACH_QUALITY_THRESHOLD` | `7` | Minimum score (5–10) to pass a question |
| `SCATHACH_TIMING` | `untimed` | Default timing mode for all sessions and reviews |
| `SCATHACH_HYDRA_IN_REVIEW` | `false` | Enable Hydra in long-answer and topic reviews |
| `SCATHACH_HYDRA_IN_DRILL` | `true` | Enable Hydra in drill sessions |
| `SCATHACH_ON_FAILED_REVIEW` | `choose` | `repeat` / `skip` / `choose` |
| `SCATHACH_MAX_PRACTICE_SUPPORT` | `14.0` | Sigmoid asymptote for practice support contribution (days) |
| `SCATHACH_DB_PATH` | `~/.scathach/scathach.db` | SQLite database path |

Runtime changes (`--set-model`, `--set-timing`) write to `~/.scathach/.env` via `_write_env_var()`.

---

## Data flow: end-to-end session

```
1. scathach ingest notes.pdf "My Notes"
       ingestor.py ──► docling ──► Topic.content ──► db: topics
       cli: render_stats() ──► topics table printed

2. scathach session "notes"
       SessionRunner.run()
         ├─ generate_root_questions()
         │     prompts.render_question_generation_prompt(topic.content, prior_qs)
         │     llm.generate() ──► OpenRouter ──► raw JSON
         │     parsing.parse_questions_response() ──► [Question, …]
         │     db: insert questions
         │
         └─ for each question in stack:
               TUI: render question panel
               answer_provider() ──► prompt_toolkit (+ optional $EDITOR via Ctrl+E)
                                  ──► (text, elapsed_s)
               scoring.score_answer(question, answer, elapsed_s, document_content=topic.content)
                 ├─ prompts.render_scoring_prompt(…, document_content=topic.content)
                 ├─ llm.generate() ──► {score, diagnosis}
                 └─ apply_time_penalty(score, elapsed_s, time_limit)
               db: record_attempt()
               TUI: show score + diagnosis + ideal_answer (always)
               ├─ PASS:  upsert_review_entry (timed + untimed queues)
               └─ FAIL:  hydra.spawn_subquestions()
                           llm.generate() ──► 1–5 sub-questions at any sub-level
                           db: insert sub-questions (parent_id = failed question)
                           push sub-questions onto session stack
                           (parent stays at position 0; retried after sub-tree clears)

3. scathach review --flash-cards
       get_scheduled_questions(queue="untimed", min_d=1, max_d=2, state='review')
       for each due question:
         answer → score_answer(…, ideal_answer=question.ideal_answer)
               → update_schedule() ──► upsert_review_entry()
         TUI: show score + diagnosis + ideal_answer (always)
```

---

## Key design decisions

**I/O via callbacks** — `SessionRunner` never touches the terminal directly. It receives an async `answer_provider` coroutine and an `event_handler` coroutine, keeping core logic testable without a TTY.

**Scoring context injection** — The LLM scorer receives different context depending on the mode: the full source document during session/drill (so it can verify facts against the material), and the stored ideal answer during reviews (a lighter reference that doesn't require the full document). Timing data is never sent to the LLM.

**Ideal answer always shown** — After every scored attempt the ideal answer is displayed unconditionally, using a green panel for passes and yellow for failures. There is no reason to withhold it.

**Open-book vs. closed-book support** — Each topic tracks two independent support metrics. `exam_support` is updated only in `--exam` (closed-book) sessions using FSRS-style stability brackets (×0.5 on fail, ×1.5/×2.0 on pass). `practice_support` is updated in all regular (open-book) sessions and drills: each first-time root question adds or subtracts a difficulty-scaled delta (+1 at target level, +(1/3)^n for n levels below, no penalty for above-target failures). The scheduled review interval is `exam_support + sigmoid(practice_support) × MAX_PRACTICE_SUPPORT`. `next_review_at` is only written once, when the user completes a topic-review quest (`review --topics`), not after every question.

**Two FSRS queues** — Timed and untimed reviews are tracked independently. Answering under pressure is a different retrieval condition than answering freely; both are worth tracking separately for accurate scheduling.

**Due-count filtering** — Stats "Due Now" counts only include questions with `state = 'review'`. Questions in `new` / `learning` / `relearning` states (with `next_review_at IS NULL` or a future date set by relearning) are excluded so the dashboard reflects genuine review obligations.

**Session stack as JSON** — The full question queue is serialized into the `sessions` row after every question. This makes mid-session `Ctrl+C` safe: no in-memory state is lost.

**Hydra retry enforced** — The parent question is always retried after its Hydra sub-tree is cleared. This is the intended learning design: sub-questions build the understanding needed to answer the parent, and the parent must be answered correctly to clear it.

**$EDITOR for rich input** — `Ctrl+E` in any answer collector opens a `.md` temp file in the user's configured editor (`$VISUAL` → `$EDITOR` → system fallback), enabling LaTeX, Markdown, and any editor-specific tooling for complex answers. The file is cleaned up after reading. In timed mode the clock continues to run.

**Prompt version-pinning** — Every prompt template carries a version string. Changing prompt wording bumps the version so that downstream parsing and evaluation tests know to re-verify outputs.
