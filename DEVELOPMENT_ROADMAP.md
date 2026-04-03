# DEVELOPMENT ROADMAP: scathach

### A Spaced-Repetition, LLM-Powered Terminal Learning Application

---

## Project Overview

**scathach** is a terminal-based application that ingests documents or topic summaries, uses a free LLM API to generate a hierarchical set of open-ended questions, and guides the user through an adaptive review session. Failed questions "spawn" easier sub-questions (the **Hydra Protocol**), building conceptual scaffolding before returning the user to the parent question. A global SQLite-backed review queue applies spaced repetition logic across all learned topics.

**Target Audience:** Students. Zero-cost to use beyond hardware.

**Core Design Constraints:**

- Python, terminal UI (no web frontend for MVP)
- Free-tier LLM API (primary: Kimi-K2 or Arcee via OpenRouter / provider-native endpoint)
- SQLite for persistence
- Docling for document ingestion
- No vector DB for MVP — full document passed as context

---

## Technology Stack

| Layer              | Tool                                                              | Rationale                                                                                                                  |
| ------------------ | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Language           | Python 3.11+                                                      | Broad ecosystem, async support, wide student familiarity                                                                   |
| Terminal UI        | `rich` + `prompt_toolkit`                                         | `rich` for rendering (panels, progress, tables, markdown); `prompt_toolkit` for interactive input, keybindings, and timers |
| LLM API            | OpenRouter (Kimi-K2 / Arcee Blaze)                                | Free-tier models; OpenAI-compatible API means easy provider swapping                                                       |
| Document Ingestion | `docling`                                                         | Handles PDF, DOCX, PPTX, HTML, images with OCR                                                                             |
| Database           | SQLite via `sqlite3` (stdlib) + `sqlalchemy` (optional ORM later) | Zero-dependency, file-local, sufficient for MVP                                                                            |
| Config             | `pydantic-settings` + `.env` / `config.toml`                      | Clean config management, easy for students to set API keys                                                                 |
| Testing            | `pytest` + `pytest-asyncio`                                       | Standard, well-documented                                                                                                  |
| Packaging          | `pyproject.toml` + `pipx` installable                             | Easy distribution, single `scathach` CLI command                                                                           |

---

## Architecture Overview

```
scathach/
│
├── cli/                    # Entry points and TUI orchestration
│   ├── main.py             # Typer app, top-level commands
│   ├── session_ui.py       # Rich/prompt_toolkit session renderer
│   └── review_ui.py        # Global review queue renderer
│
├── core/                   # Business logic (no I/O)
│   ├── question.py         # Question dataclass, difficulty enum
│   ├── session.py          # Session state machine
│   ├── scoring.py          # Score parsing, time penalty logic, threshold evaluation
│   ├── hydra.py            # Sub-question spawning logic
│   └── scheduler.py        # FSRS-inspired review scheduling
│
├── llm/                    # LLM client abstraction
│   ├── client.py           # OpenAI-compatible async client wrapper
│   ├── prompts.py          # All system/user prompt templates
│   └── providers.py        # Provider configs (Kimi, Arcee, etc.)
│
├── ingestion/              # Document ingestion pipeline
│   ├── ingestor.py         # Docling wrapper, text extraction
│   └── chunker.py          # (MVP: no-op; Future: chunking for large docs)
│
├── db/                     # Persistence layer
│   ├── schema.py           # SQLite schema definitions (DDL)
│   ├── models.py           # Python dataclasses mirroring DB tables
│   └── repository.py       # CRUD operations (topics, questions, attempts)
│
├── config.py               # Pydantic settings (API keys, defaults)
└── __init__.py
```

---

## Database Schema

```sql
-- Topics represent ingested documents or summaries
CREATE TABLE topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    source_path TEXT,                  -- original file path, nullable for pasted text
    content     TEXT NOT NULL,         -- full extracted text
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Questions are generated per-session and stored globally.
-- ideal_answer is always populated at generation time for root questions,
-- and at scoring time (on failure) for Hydra-spawned sub-questions.
CREATE TABLE questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        INTEGER NOT NULL REFERENCES topics(id),
    parent_id       INTEGER REFERENCES questions(id),  -- NULL = root question
    difficulty      INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 6),
    body            TEXT NOT NULL,
    ideal_answer    TEXT NOT NULL,     -- always present; shown to user only on failure
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_root         BOOLEAN NOT NULL DEFAULT 0  -- 1 = one of the original 6 generated
);

-- Attempts record every time the user answers a question.
-- raw_score is the LLM-assigned score before any time penalty is applied.
-- final_score is what is actually recorded (floor(raw_score * 0.5) if time penalty applies).
CREATE TABLE attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id     INTEGER NOT NULL REFERENCES questions(id),
    session_id      TEXT NOT NULL,     -- UUID per session
    answer_text     TEXT NOT NULL,
    raw_score       INTEGER NOT NULL CHECK (raw_score BETWEEN 0 AND 10),
    final_score     INTEGER NOT NULL CHECK (final_score BETWEEN 0 AND 10),
    time_taken_s    REAL,              -- NULL if untimed
    time_penalty    BOOLEAN NOT NULL DEFAULT 0,  -- 1 if answered between t and 2t
    timed           BOOLEAN NOT NULL DEFAULT 0,  -- 1 if this attempt was under a timer
    passed          BOOLEAN NOT NULL,
    attempted_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Timed review queue: tracks scheduling state for questions reviewed under a timer.
-- One row per question; upserted after every timed review attempt.
CREATE TABLE timed_review_queue (
    question_id         INTEGER PRIMARY KEY REFERENCES questions(id),
    last_score          INTEGER,       -- final_score of the most recent timed attempt
    last_attempted_at   DATETIME,
    next_review_at      DATETIME,      -- FSRS-derived next review time
    stability           REAL DEFAULT 1.0,   -- FSRS stability parameter
    difficulty_fsrs     REAL DEFAULT 0.3,   -- FSRS intrinsic difficulty (0-1)
    state               TEXT DEFAULT 'new'  -- new | learning | review | relearning
);

-- Untimed review queue: identical structure, tracks untimed review history separately.
-- Timed and untimed performance are treated as independent learning signals.
CREATE TABLE untimed_review_queue (
    question_id         INTEGER PRIMARY KEY REFERENCES questions(id),
    last_score          INTEGER,       -- final_score of the most recent untimed attempt
    last_attempted_at   DATETIME,
    next_review_at      DATETIME,
    stability           REAL DEFAULT 1.0,
    difficulty_fsrs     REAL DEFAULT 0.3,
    state               TEXT DEFAULT 'new'
);
```

---

## Timing Model

The timing system has two zones per question, derived from the base time limit `t` for its difficulty level:

| Difficulty            | Base limit `t` | Penalty zone | Auto-fail |
| --------------------- | -------------- | ------------ | --------- |
| 1 — Easy short answer | 30 s           | 30–60 s      | > 60 s    |
| 2 — Hard short answer | 60 s           | 60–120 s     | > 120 s   |
| 3 — Easy paragraph    | 5 min          | 5–10 min     | > 10 min  |
| 4 — Hard paragraph    | 10 min         | 10–20 min    | > 20 min  |
| 5 — Easy long         | 15 min         | 15–30 min    | > 30 min  |
| 6 — Hard long         | 30 min         | 30–60 min    | > 60 min  |

**Zone behaviour:**

- **Before `t`:** Normal scoring. `final_score = raw_score`.
- **Between `t` and `2t` (penalty zone):** Answer is accepted. The UI immediately notifies the user they are over time. `final_score = floor(raw_score * 0.5)`. The LLM evaluator receives no indication of the time penalty — it scores the answer on its merits alone.
- **After `2t`:** Auto-fail. `final_score = 0`, `passed = False`. The user's typed text (if any) is preserved in the attempt record.

**Timer display:** A `rich` progress bar counts down from `t` to zero. Once `t` is exceeded, the bar transitions to a second "penalty" bar counting from `t` to `2t`, styled in a distinct warning color. This gives the user continuous awareness of which zone they are in without interrupting their typing.

**Timing applicability:** Timing only applies to attempts where `timed = True`. Whether a given attempt is timed depends on the session configuration. Hydra-spawned sub-questions inherit the timing setting of their parent session.

---

## Session Modes

The user configures two independent timing choices:

| Setting                  | Options         | Controls                                                                           |
| ------------------------ | --------------- | ---------------------------------------------------------------------------------- |
| **Main question timing** | Timed / Untimed | Whether the root questions and Hydra sub-questions in a learning session are timed |
| **Review timing**        | Timed / Untimed | Whether questions in `scathach review` sessions are timed                          |

These two choices are stored in the user's config and can be overridden per-session. They determine which review queue table is written to after each review attempt: a timed review updates `timed_review_queue`; an untimed review updates `untimed_review_queue`. Both queues maintain independent scheduling state — a question can have separate stability and next-review timestamps in each.

---

## Phase 1 — Foundation (Week 1–2)

**Goal:** A working skeleton with no LLM calls. All logic is stubbed.

### Milestones

#### 1.1 Project Scaffolding — STATUS: COMPLETED

- [x] Initialize `pyproject.toml` with dependencies and `[project.scripts]` entry for `scathach` CLI
- [x] Set up `config.py` with `pydantic-settings`: API key, default quality threshold (7), default main timing (untimed), default review timing (untimed), model selection
- [x] Add `.env.example` with documentation for obtaining a free OpenRouter API key
- [x] Configure `pytest` and write a smoke test

#### 1.2 Database Layer — STATUS: COMPLETED

- [x] Implement `schema.py`: create all five tables, migrations via a simple version table
- [x] Implement `repository.py`: typed functions for all CRUD operations
  - `upsert_topic()`, `get_topic_by_name()`, `list_topics()`
  - `insert_question()`, `get_question()`, `get_children(question_id)`
  - `record_attempt()`, `get_latest_attempt(question_id)`
  - `upsert_review_entry(queue: Literal["timed", "untimed"], ...)`, `get_due_questions(queue, limit)`
- [x] Write unit tests for all repository functions using an in-memory SQLite DB

#### 1.3 Core Data Models — STATUS: COMPLETED

- [x] Define `Question` dataclass: `id`, `topic_id`, `parent_id`, `difficulty`, `body`, `ideal_answer`, `is_root`
- [x] Define `Attempt` dataclass: `question_id`, `raw_score`, `final_score`, `time_taken_s`, `time_penalty`, `timed`, `passed`
- [x] Define `DifficultyLevel` enum (1–6) with metadata: label, base time limit `t` (seconds), answer length descriptor
- [x] Define `TimingMode` enum: `TIMED`, `UNTIMED` — used independently for main sessions and reviews
- [x] Define `TimerZone` enum: `NORMAL`, `PENALTY`, `EXPIRED` — runtime state used by the timer component

#### 1.4 Document Ingestion (Docling) — STATUS: COMPLETED

- [x] Implement `ingestor.py`:
  - Accept file path (PDF, DOCX, PPTX, TXT, MD) or raw pasted text string
  - Use `docling` `DocumentConverter` to extract clean markdown text
  - Store extracted text in `topics` table
  - Handle docling errors gracefully (fallback to raw text read for `.txt`/`.md`)
- [x] Write integration test with a sample PDF and DOCX (mocked; real docling integration deferred to manual QA)

**Deliverable:** `scathach ingest <file>` stores the document. `scathach topics` lists stored topics. All DB operations verified.

---

## Phase 2 — LLM Integration (Week 2–3)

**Goal:** Real question generation and answer scoring via free API.

### Milestones

#### 2.1 LLM Client — STATUS: COMPLETED

- [x] Implement `client.py`: async wrapper around `httpx` or `openai` SDK pointed at OpenRouter base URL
  - `generate(system_prompt, user_prompt, model, max_tokens, temperature) -> str`
  - Retry logic: exponential backoff on 429/503, up to 3 retries
  - Streaming support (optional for MVP, useful for long answers)
- [x] Add `providers.py` with configs for:
  - **Kimi-K2** (`moonshotai/kimi-k2` on OpenRouter) — primary
  - **Arcee Blaze** (`arcee-ai/arcee-blaze`) — secondary
  - **Fallback:** `google/gemini-flash-1.5` (generous free tier)
  - Provider is selectable in config; client is provider-agnostic

#### 2.2 Prompt Engineering — STATUS: COMPLETED

All prompts live in `prompts.py` as formatted string templates. Design and test each prompt independently before integration.

- [ ] **Question Generation Prompt**

  The model generates all 6 questions and their ideal answers in a single call. This keeps generation cost to one API call per session and ensures the ideal answer is contextually coherent with the question as written.

  ```
  System: You are a rigorous academic tutor. Given the following document, generate exactly
  6 open-ended questions — one for each difficulty level 1 through 6 — that test deep
  understanding of the material. For each question, also provide an ideal 10/10 answer
  that a perfect student would give.
  [difficulty rubric embedded, including expected answer length per level]
  Output: strict JSON array of 6 objects: {difficulty, body, ideal_answer}
  ```

  - Include few-shot examples for each difficulty level in the prompt
  - Request JSON output; parse with fallback regex if JSON is malformed
  - `ideal_answer` length should match the difficulty level's expected answer format
    (single word/phrase for level 1, single sentence for level 2, paragraph for levels 3–4,
    multiple paragraphs for levels 5–6)

- [ ] **Sub-question Generation Prompt (Hydra Protocol)**

  Sub-questions are also generated with their ideal answers in one call, for the same reasons as above.

  ```
  System: A student failed to answer the following question adequately.
  Their answer revealed these misunderstandings: [diagnosis].
  Generate exactly 3 questions at difficulty level [N-1 or 1] that address
  these specific gaps. For each question, also provide an ideal 10/10 answer.
  Output: JSON array of 3 objects: {difficulty, body, ideal_answer}
  ```

- [ ] **Answer Scoring Prompt**

  The scoring prompt does not receive any information about elapsed time or time penalties. Timing is a mechanical post-processing step applied after the LLM returns its score, keeping the evaluator focused purely on answer quality.

  ```
  System: You are a strict but fair academic evaluator...
  Score the following student answer to the given question on a scale of 0-10.
  Consider: accuracy, completeness relative to difficulty level, and clarity.
  Output: JSON object: {score: int, diagnosis: str}
  ```

  - `score` is the raw quality score (0–10), before any time penalty
  - `diagnosis` is a 1–2 sentence description of conceptual gaps; present even on passing answers, for use in Hydra spawning if the user later fails a related question
  - The `ideal_answer` is already stored on the question row from generation time — the scorer does not regenerate it

- [ ] **Prompt version-pinning:** store prompt versions as constants so future changes are traceable

#### 2.3 Question Generation Pipeline — STATUS: COMPLETED

- [x] Implement `session.py:generate_root_questions(topic_id)`:
  - Fetch topic content from DB
  - Call LLM with generation prompt
  - Parse JSON response (each object: `{difficulty, body, ideal_answer}`), handle malformed output
  - Insert all 6 questions into DB with `is_root=True`, `ideal_answer` populated immediately
  - Return list of `Question` objects ordered difficulty 1→6

#### 2.4 Answer Scoring Pipeline — STATUS: COMPLETED

- [x] Implement `scoring.py:score_answer(question, answer_text, time_taken_s, timed, threshold)`:
  - Call LLM with scoring prompt → receive `{raw_score, diagnosis}`
  - Apply time penalty logic in application code (no LLM involvement)
  - Return `Attempt` with both `raw_score` and `final_score` populated
  - The `ideal_answer` is already on the question; retrieve from DB rather than regenerating

**Deliverable:** `scathach generate <topic_name>` prints 6 generated questions each with an ideal answer. `scathach score` accepts a question + answer and returns a scored attempt with time penalty correctly applied. Both verified against real API calls.

---

## Phase 3 — Session Engine (Week 3–4)

**Goal:** Full interactive session with Hydra protocol, dual-zone timing, and state persistence.

### Milestones

#### 3.1 Session State Machine — STATUS: COMPLETED

- [x] `SessionRunner` class with full state machine
- [x] `SessionRunner.run()`: main async loop driving question stack
- [x] Question tree as a stack: parent re-queued after sub-question group is cleared
- [x] Recursive Hydra spawning supported
- [x] On failure: emits AnswerScored event with ideal_answer before HydraSpawned
- [x] On session complete: writes review queue entries for all cleared questions

#### 3.2 Hydra Protocol — STATUS: COMPLETED

- [x] `hydra.py:spawn_subquestions()`: target difficulty = max(1, parent - 1)
- [x] Parses 3 objects `{difficulty, body, ideal_answer}`, inserts with parent_id
- [x] Sub-questions inherit timing from parent session

#### 3.3 Dual-Zone Timer Implementation — STATUS: COMPLETED

- [x] `DualZoneTimer` in `cli/session_ui.py`: tracks elapsed time, zones, renders countdown
- [x] Zone 1 (green) and Zone 2 (amber warning) rendered inline
- [x] Auto-fail at 2t handled in scoring.py before LLM call
- [x] Untimed: plain prompt_toolkit multiline with Escape+Enter to submit

#### 3.4 Session Configuration (Pre-session wizard) — STATUS: COMPLETED

- [x] `scathach session <topic>` runs pre-session wizard (timing, threshold, levels)
- [x] CLI flags override wizard defaults
- [x] Review timing configured via `scathach config` (stub for Phase 5)

**Deliverable:** `scathach session <topic>` runs a complete end-to-end session. Dual-zone timer verified visually. Hydra spawning verified by intentionally failing a question. Time penalty correctly halves the score in the DB.

---

## Phase 4 — Review System (Week 4–5)

**Goal:** Global timed and untimed review queues with FSRS-inspired scheduling across all topics.

### Milestones

#### 4.1 FSRS-Inspired Scheduler — STATUS: COMPLETED

- [x] `update_schedule()`: score-bracket stability/interval logic, state transitions
- [x] `get_scheduled_questions()`: returns due questions from correct queue
- [x] Independent timed/untimed queue tracking

#### 4.2 Review Session UI — STATUS: COMPLETED

- [x] `scathach review`: prompts timing, reads correct queue, runs answer/score/timer flow
- [x] Summary table after each review session

#### 4.3 Progress Dashboard — STATUS: COMPLETED

- [x] `scathach stats`: topics table, queue stats, score distribution by difficulty

**Deliverable:** `scathach review` works end-to-end for both timed and untimed queues. Scheduler tested with mocked dates. Stats dashboard renders correctly, including time penalty stats.

---

## Phase 5 — Polish, Error Handling & Packaging (Week 5–6)

**Goal:** A robust, student-friendly application that fails gracefully.

### Milestones

#### 5.1 Error Handling & Resilience — STATUS: COMPLETED

- [x] Graceful API failures: LLMClient retries with backoff; ScoringError/GenerationError abort gracefully
- [x] WAL mode enabled in schema.py
- [x] Malformed LLM JSON: regex fallback parser + one-shot re-prompt in generation and scoring
- [ ] Session interrupt/resume: documented in BUGS.md B3 and SUGGESTIONS.md S1; deferred to post-MVP

#### 5.2 User Experience — STATUS: COMPLETED

- [x] Welcome banner on bare `scathach` invocation
- [x] Color-coded scoring: 0–4 red, 5–6 yellow, 7–10 green
- [x] Time penalty shown as `Raw: 8/10 → Final: 4/10 [½ time penalty]`
- [x] Hydra spawn notice with sub-question count
- [x] Ideal answer in distinct yellow panel on failure
- [x] Progress indicator: `Question N/Total — Difficulty ★★☆☆☆☆ (label)`
- [x] `scathach topics`: name, question count, avg score, source, created
- [x] `--help` text via Typer on all commands

#### 5.3 Provider Flexibility — STATUS: COMPLETED

- [x] `scathach config --set-model <provider>` writes to .env
- [x] `scathach config --test` sends canary prompt
- [x] `scathach config --set-review-timing` persists timing preference
- [x] `scathach config --show` prints all settings

#### 5.4 Packaging & Distribution — STATUS: COMPLETED

- [x] `pyproject.toml` with minimum version pins
- [x] `scathach --version` prints version string
- [x] Installable via `pip install -e .` (pipx verified locally)

#### 5.5 Documentation — STATUS: COMPLETED

- [x] README.md with quick-start
- [x] SUGGESTIONS.md with design notes
- [x] BUGS.md with known issues
- [x] Docstrings on all public functions

---

## Phase 6 — Future Extensions (Post-MVP)

These are explicitly out of scope for v1.0 but should inform architectural decisions now to avoid painful refactors.

### 6.1 Vector Database / Knowledge Graph

**Motivation:** Enable synthesis questions that draw on concepts from multiple documents.

- Replace full-context injection with a RAG pipeline (ChromaDB or LanceDB — both embeddable, no server required)
- Add a KG extraction step (via LLM or SpaCy NER) to map concepts across documents
- Synthesis questions become a new question type (difficulty 5–6 only)
- **Architectural note for MVP:** keep document content stored in the `topics` table as plain text. This makes future migration to RAG straightforward — just chunk and embed the existing `content` column.

### 6.2 Full FSRS-5 Implementation

- Integrate the `fsrs-py` library (or implement full FSRS-5 with all 17 weights)
- Add optimizer that tunes weights on the user's historical attempt data
- Timed and untimed queues may warrant separate weight tuning, since the `final_score` distributions will differ systematically between them

### 6.3 Multi-user / Sync

- Replace SQLite with a server-backed option (Turso / LibSQL for SQLite-compatible remote)
- Add basic auth so multiple students can share a machine

### 6.4 Export & Import

- `scathach export <topic> --format anki` — export questions as Anki deck (`.apkg`)
- `scathach import <anki_deck>` — import Anki cards as questions into the review queue

### 6.5 Multimedia Documents

- Extend docling pipeline to handle images within documents (diagrams, charts)
- Pass images to LLM as vision input for question generation about visual content

### 6.6 TUI Enhancement

- Optional: migrate terminal UI to `Textual` for a richer, mouse-interactive experience (without abandoning the terminal-native feel)

---

## Development Principles

**Prompt stability:** Treat LLM prompts as first-class artifacts. Every prompt change should be versioned and tested against a fixed set of example documents. Prompts are the most fragile part of this system.

**JSON contract discipline:** Every LLM call that returns structured data must have a defined schema and a fallback parser. Never assume well-formed JSON from the model. The generation prompt now returns `{difficulty, body, ideal_answer}` per question — the fallback parser must handle all three fields.

**DB as source of truth:** The session state machine should be reconstructable entirely from the DB. This enables resume, audit, and future sync.

**Score transparency:** Both `raw_score` and `final_score` are always stored. Never discard the LLM's quality evaluation. The time penalty is a mechanical multiplier applied in application code, not in the LLM prompt, so the raw quality signal is always recoverable for analysis.

**Provider agnosticism:** The LLM client must be trivially swappable. Never let a provider-specific SDK leak into business logic. Use the OpenAI-compatible interface everywhere.

**No silent failures:** If a question cannot be scored (API failure, parse failure), tell the user and give them the option to skip or retry. Never silently record a wrong score.

---

## Milestone Summary

| Phase                  | Weeks     | Deliverable                                                          |
| ---------------------- | --------- | -------------------------------------------------------------------- |
| 1 — Foundation         | 1–2       | DB (5 tables), ingestion, project scaffolding                        |
| 2 — LLM Integration    | 2–3       | Question + ideal answer generation; answer scoring with time penalty |
| 3 — Session Engine     | 3–4       | Full interactive session with Hydra protocol and dual-zone timer     |
| 4 — Review System      | 4–5       | Timed + untimed review queues with spaced repetition                 |
| 5 — Polish & Packaging | 5–6       | Production-ready, distributable CLI                                  |
| 6 — Future Extensions  | Post-v1.0 | RAG, full FSRS, Anki export, etc.                                    |

## Organization instructions:

After reading this Roadmap, take the first task shown here, memorize it, mark it as STATUS: WORKING, and create a more detailed version of it in CURRENT_TASK.md, breaking it up into more detailed sub-tasks. Then, go through the sub-tasks in CURRENT_TASK.md and work through them one-by-one (marking them as INCOMPLETE, WORKING, and COMPLETED in the same way you're doing here). Once all of the subtasks in CURRENT_TASK.md are completed, empty out the CURRENT_TASK.md tasklist, come back here, and mark the task as COMPLETED. Then start the next task on the list, following the same procedure.

## Bugs:

If you run into trouble or come across a bug while performing this update, write down the bug in BUGS.md. If the bug is something which must be fixed for work to continue, then do your best to fix it, and delete it from BUGS.md when you have fixed it. However, if it isn't necessary to fix it immediately, then just move on to the next task. I will look through the bugs later and work on them.

## Suggestions

If, during the course of development, you find that the specifications either don't make sense, or you can imagine a more efficient, effective way to handle a workflow, write your suggestion in SUGGESTIONS.md. I love constructive criticism, and feedback I can use to improve this application!

## Bottlenecks, Pain Points, and Tricky Decisions

This is an autonomous task, and I expect it may take awhile and involve a few nuanced or tricky decisions. Use your best judgement whenever possible. If you cannot complete a part of the task, write down the cause of difficulty and what needs to be done to fix it at BOTTLENECKS.md. Then mark the task as "TEMPORARILY SKIPPED" and continue with the parts of development that you can meaningfully complete. If you are unsure of how to complete a part of the task, and need specification from me, mark that section of the task as "TEMPORARILY SKIPPED" and write the question in QUESTIONS.md. Rather than skipping a task or section entirely, try to complete as much of the section as possible. Clearly outline what you cannot do or are unsure how to do. Additionally, use your lateral thinking and problem solving skills. If you can see a reasonable way to move forward with development, do so.

## One more thing

Don't commit these changes to git yet. Just leave them as is for now.
