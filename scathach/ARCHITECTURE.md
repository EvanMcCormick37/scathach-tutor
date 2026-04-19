# Architecture: scathach

scathach is a terminal-based adaptive learning and spaced repetition system. A user ingests a document; the system generates comprehension questions at six difficulty levels, drills them, and schedules reviews via FSRS so the material sticks.

---

## Directory structure

```
scathach/
├── __init__.py             # Package metadata (version)
├── config.py               # Pydantic Settings singleton
├── cli/                    # Terminal UI (Typer + Rich + prompt_toolkit)
│   ├── main.py             # Top-level command definitions
│   ├── session_ui.py       # Learning session TUI (timers, input, event rendering)
│   ├── review_ui.py        # Review & super-review TUI
│   └── stats_ui.py         # Progress dashboard
├── core/                   # Business logic (no I/O)
│   ├── question.py         # Domain enums/types: DifficultyLevel, TimingMode, TimerZone
│   ├── session.py          # SessionRunner state machine
│   ├── hydra.py            # Hydra Protocol (sub-question spawning)
│   ├── scheduler.py        # FSRS-based spaced repetition
│   └── scoring.py          # LLM answer evaluation + time-penalty logic
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
    └── providers.py        # Provider registry (Qwen, Kimi, Arcee, Gemini)
```

---

## Layer overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLI  (Typer / Rich)                          │
│       main.py · session_ui.py · review_ui.py · stats_ui.py         │
└──────────┬──────────────────────────────────────────────────────────┘
           │  callbacks / events
┌──────────▼──────────────────────────────────────────────────────────┐
│                      CORE  (pure business logic)                    │
│     session.py · scoring.py · hydra.py · scheduler.py · question.py│
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

| Command | Purpose |
|---------|---------|
| `ingest [path]` | Ingest file, URL, or pasted text into the topics table |
| `topics` | List all ingested topics with question/attempt counts |
| `rename <old> <new>` | Rename a topic |
| `session <topic>` | Start a new session (wizard → SessionRunner) |
| `session --list` | List all active (unfinished) sessions |
| `session --resume <id>` | Resume a persisted session |
| `review` | FSRS review for level 1–2 questions |
| `super-review` | FSRS review for level 3–6 questions (optional Hydra) |
| `stats` | Progress dashboard (topics, queues, score distribution) |
| `config --show / --set-model / --set-review-timing / --test` | View or modify settings |

### `session_ui.py`

Drives the interactive learning session in the terminal.

- **`pre_session_wizard()`** — Prompts for timing mode, quality threshold, and difficulty range before a session starts.
- **`handle_event(event)`** — Async event handler passed to `SessionRunner`. Renders each `SessionEvent` subclass to the terminal using Rich panels and spinners.
- **`DualZoneTimer`** — Live Rich progress bar with two visual zones:
  - **NORMAL** (0 – t): green, full score.
  - **PENALTY** (t – 2t): yellow, score halved on submit.
  - **EXPIRED** (>2t): red, auto-fail.
- **Answer collectors** — `_get_answer_untimed()` and `_get_answer_timed()` both use prompt_toolkit for multiline input. The timed variant runs the timer bar concurrently and kills input on expiry.

### `review_ui.py`

- **`run_review_session()`** — Level 1–2 questions due today, no Hydra. On failure, behavior is controlled by the `SCATHACH_ON_FAILED_REVIEW` setting (`repeat` / `skip` / `choose`).
- **`run_super_review_session()`** — Level 3–6 questions ordered worst-score-first per difficulty tier. Optional Hydra sub-question spawning on failure.

### `stats_ui.py`

Renders three Rich tables: topic inventory, review queue health (due now / due this week), and score distribution by difficulty.

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

The central state machine. Orchestrates the full learning loop:

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

**Persistence:** After every question, the full question stack, cleared IDs, and session metadata are serialized to JSON and written to the `sessions` table. `Ctrl+C` is caught; the session survives and can be resumed.

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
2. LLM returns `{score: 0–10, diagnosis: str}`.
3. `apply_time_penalty()` applies pure application logic:
   - `EXPIRED` → auto-fail (score 0).
   - `PENALTY` → `floor(raw_score / 2)`.
   - `NORMAL` → `raw_score` unchanged.
4. Returns `(Attempt, diagnosis_str)`.

### `hydra.py` — Hydra Protocol

When a student fails a question:

1. Fetches all existing questions with difficulty **strictly below** the parent's difficulty for the same topic (used for deduplication across all eligible levels).
2. Renders a prompt embedding: the parent question, the student's answer, the LLM's diagnosis, the difficulty rubric for all sub-levels, and the existing questions grouped by level.
3. LLM selects both the **number** (1–5) and **difficulty level** of each sub-question — any level strictly below the parent. The set is designed to be necessary and sufficient to understand the parent (e.g., a level-4 failure might produce one level-1 definition and two level-2 concepts if those are the actual gaps).
4. Any returned questions violating the difficulty constraint are discarded; the list is capped at 5.
5. Sub-questions are inserted into the DB as children of the parent.
6. They are pushed onto the session stack; the parent is moved to the end of the queue and retried after its sub-tree is cleared.
7. The tree can branch multiple levels deep.

### `scheduler.py` — FSRS-based spaced repetition

Simplified FSRS using stability as the core parameter:

| Final score | State | Stability multiplier | Min interval |
|-------------|-------|----------------------|--------------|
| 0–4 | relearning | 0.5× | 1 day |
| 5–6 | learning | 0.8× | 1 day |
| 7–8 | review | 1.5× | — |
| 9–10 | review | 2.5× | — |

- **`update_schedule(conn, question_id, score, queue)`** — Computes new stability, interval, and next review timestamp, then upserts into the appropriate queue table.
- **`get_scheduled_questions(conn, queue, ...)`** — Returns questions where `next_review_at <= now` (or `NULL` for never-seen). The `worst_first` flag sorts by `last_score ASC` within each difficulty tier, surfacing problem areas first.

Two queues (`timed_review_queue`, `untimed_review_queue`) track the same question independently — a question can have different FSRS state depending on whether it was answered under time pressure.

---

## Database layer (`db/`)

### `schema.py`

Opens a SQLite connection, sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`, then runs idempotent `CREATE TABLE IF NOT EXISTS` DDL. A `schema_version` table tracks the applied migration version.

### Tables

| Table | Key columns |
|-------|------------|
| `topics` | `id`, `name` (UNIQUE), `source_path`, `content`, `created_at` |
| `questions` | `id`, `topic_id` (FK), `parent_id` (FK self — Hydra hierarchy), `difficulty` (1–6), `body`, `ideal_answer`, `is_root` |
| `attempts` | `id`, `question_id` (FK), `session_id`, `answer_text`, `raw_score`, `final_score`, `time_taken_s`, `time_penalty`, `timed`, `passed` |
| `sessions` | `id`, `topic_id` (FK), `status` (active/complete), `timing`, `threshold`, `num_levels`, `question_stack` (JSON), `cleared_ids` (JSON), `root_ids` (JSON) |
| `timed_review_queue` | `question_id` (PK/FK), `last_score`, `next_review_at`, `stability`, `difficulty_fsrs`, `state` (new/learning/review/relearning) |
| `untimed_review_queue` | (same structure as timed) |

Questions form a tree: root questions have `parent_id = NULL` and `is_root = TRUE`; Hydra sub-questions have `parent_id` pointing to their parent question.

### `repository.py`

All DB access goes through typed functions in this module. No SQL appears elsewhere.

Key operations:
- `upsert_topic()` — insert or update by name.
- `insert_question()` / `get_children()` / `delete_question()` (cascades to attempts + review entries).
- `record_attempt()` / `get_latest_attempt()`.
- `upsert_review_entry()` / `get_due_questions()`.
- `create_session_record()` / `update_session_state()` / `complete_session()` / `list_active_sessions()`.

---

## LLM layer (`llm/`)

### `client.py`

Async wrapper around the `openai` SDK pointed at `https://openrouter.ai/api/v1`.

- **`generate(system_prompt, user_prompt)`** — Single public method. Returns assistant text.
- **Retry policy** — Exponential backoff (`2^n` seconds) on HTTP 429, 500, 502, 503, 504; max 3 retries.
- **`make_client(api_key, model)`** — Factory that reads provider defaults (max_tokens, temperature) from the provider registry.

### `prompts.py`

All prompt templates are version-pinned (currently `v1.0`). Bumping the version signals that generated outputs may differ and downstream parsing/tests need re-verification.

| Template | Inputs | Expected output |
|----------|--------|----------------|
| `render_question_generation_prompt()` | topic content, prior questions, num_levels | JSON array: `[{difficulty, body, ideal_answer}, …]` |
| `render_hydra_prompt()` | parent question, answer, diagnosis, all existing questions below parent level | JSON array: `[{difficulty, body, ideal_answer}, …]` (1–5 items, mixed levels) |
| `render_scoring_prompt()` | question body, difficulty, student answer | JSON object: `{score, diagnosis}` |

The question generation prompt embeds the full difficulty rubric so the LLM calibrates question complexity correctly. The scoring prompt deliberately omits the ideal answer and all timing data — scoring is purely about answer quality.

### `parsing.py`

Robust extraction handles models that wrap JSON in markdown code fences or add prose:

1. Direct `json.loads()`.
2. Regex extraction of the first `[…]` block.
3. Regex extraction of the first `{…}` block.
4. Raises `ParseError` if all strategies fail.

### `providers.py`

Registry of `ProviderConfig` objects (model_id, display_name, max_tokens, temperature, is_free). Supported free-tier providers: Qwen 3.6 Plus (default), Kimi K2, Arcee Blaze, Gemini Flash 1.5. `get_provider(model_id)` returns the config or a generic fallback.

---

## Ingestion pipeline (`ingestion/`)

### `ingestor.py`

```
User input
  ├─ filepath (.pdf / .docx / .pptx / .html)  ─► docling DocumentConverter → markdown
  ├─ filepath (.txt / .md / .rst)             ─► plain-text read (UTF-8 + latin-1 fallback)
  ├─ URL (http/https)                          ─► httpx.get → temp file → docling → markdown
  └─ pasted text                               ─► raw string
         └─► upsert_topic(conn, Topic(name, content, source_path))
```

Topic name defaults: filename stem for files, `<title>` tag for URLs, user-supplied for pastes.

### `chunker.py`

MVP stub — returns the full document as a single chunk. Placeholder for a future RAG/embedding pipeline when documents exceed LLM context limits.

---

## Configuration (`config.py`)

Pydantic `BaseSettings` singleton. Source priority: environment variables > `~/.scathach/.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `SCATHACH_OPENROUTER_API_KEY` | _(required)_ | OpenRouter API key |
| `SCATHACH_MODEL` | `qwen/qwen3-6b-plus:free` | LLM model identifier |
| `SCATHACH_QUALITY_THRESHOLD` | `7` | Minimum score (5–10) to pass a question |
| `SCATHACH_MAIN_TIMING` | `untimed` | Default timing mode for sessions |
| `SCATHACH_REVIEW_TIMING` | `untimed` | Default timing mode for reviews |
| `SCATHACH_HYDRA_IN_SUPER_REVIEW` | `false` | Enable Hydra in super-review |
| `SCATHACH_ON_FAILED_REVIEW` | `choose` | `repeat` / `skip` / `choose` |
| `SCATHACH_OPEN_DOC_ON_SESSION` | `false` | Open source document at session start |
| `SCATHACH_DB_PATH` | `~/.scathach/scathach.db` | SQLite database path |

Runtime changes (`--set-model`, `--set-review-timing`) write to `~/.scathach/.env` via `_write_env_var()`.

---

## Data flow: end-to-end session

```
1. scathach ingest notes.pdf
       ingestor.py ──► docling ──► Topic.content ──► db: topics

2. scathach session "notes"
       pre_session_wizard() ──► SessionConfig
       SessionRunner.run()
         ├─ generate_root_questions()
         │     prompts.render_question_generation_prompt(topic.content, prior_qs)
         │     llm.generate() ──► OpenRouter ──► raw JSON
         │     parsing.parse_questions_response() ──► [Question, …]
         │     db: insert questions
         │
         └─ for each question in stack:
               TUI: render question panel
               answer_provider() ──► DualZoneTimer + prompt_toolkit ──► (text, elapsed_s)
               scoring.score_answer(question, answer, elapsed_s)
                 ├─ prompts.render_scoring_prompt()
                 ├─ llm.generate() ──► {score, diagnosis}
                 └─ apply_time_penalty(score, elapsed_s, time_limit)
               db: record_attempt()
               ├─ PASS:  upsert_review_entry (timed + untimed queues)
               └─ FAIL:  hydra.spawn_subquestions()
                           fetch all existing questions below parent difficulty
                           llm.generate() ──► 1–5 sub-questions at any sub-level
                           db: insert sub-questions (parent_id = failed question)
                           push sub-questions onto session stack

3. scathach review
       get_due_questions(queue="untimed", max_difficulty=2)
       for each due question:
         answer → score → update_schedule() ──► upsert_review_entry()
```

---

## Key design decisions

**I/O via callbacks** — `SessionRunner` never touches the terminal directly. It receives an async `answer_provider` coroutine and an `event_handler` coroutine, keeping core logic testable without a TTY.

**Scoring is timing-unaware** — The LLM scores purely on answer quality. Time penalties are applied by application logic afterwards, keeping the rubric stable regardless of timing mode.

**Two FSRS queues** — Timed and untimed reviews are tracked independently. Answering under pressure is a different retrieval condition than answering freely; both are worth tracking separately for accurate scheduling.

**Session stack as JSON** — The full question queue is serialized into the `sessions` row after every question. This makes mid-session `Ctrl+C` safe: no in-memory state is lost.

**Prompt version-pinning** — Every prompt template carries a version string. Changing prompt wording bumps the version so that downstream parsing and evaluation tests know to re-verify outputs.
