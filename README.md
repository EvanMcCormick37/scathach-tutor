# scathach

A spaced-repetition, LLM-powered terminal learning application. Drop in a document, and scathach generates adaptive questions, drills you on them, and schedules review sessions so the material actually sticks.

---

## How it works

### 1. Ingest a document
scathach reads your source material — PDF, DOCX, PPTX, Markdown, plain text, and more — and stores it as a **topic** in a local SQLite database.

### 2. Start a learning session
When you start a session, an LLM generates **6 open-ended questions** from the topic, one per difficulty level (1 = recall, 6 = synthesis). You type your answers in the terminal. Each answer is scored 0–10 by the LLM against the source material.

### 3. The 'Hydra Protocol'
If you fail a question, scathach spawns **3 targeted sub-questions** that address the specific gap in your understanding diagnosed by the scorer. Clear the sub-questions and you return to the parent question. The tree can branch multiple levels deep.

### 4. Timed mode
Sessions can be run timed or untimed. In timed mode, each question has a time limit proportional to its difficulty. Answering in the normal window gives full score. Answering in the penalty window (up to 2× the limit) halves your score. Running out of time is an automatic fail.

### 5. Spaced repetition
Every question you clear gets scheduled for future review using an FSRS-based algorithm. Two review commands keep your knowledge fresh:

- **`review`** — short-answer questions (levels 1–2), due questions only
- **`super-review`** — long-answer questions (levels 3–6), worst performers first, optionally with Hydra subquestion generation.

### 6. Session persistence
Sessions are saved to the database after every question. If you quit mid-session with Ctrl+C, the session is preserved and can be resumed exactly where you left off.

---

## Installation

### Windows executable (no Python required)

1. Download the latest `.exe` files from the [Releases](https://github.com/your-username/scathach-tutor/releases) page.
2. Place them in a folder of your choice and run `scathach.exe`.
3. On first launch, open the config menu and enter your [OpenRouter](https://openrouter.ai) API key when prompted. That's it — no `.env` file or terminal setup needed.

---

### Install from source

**Prerequisites:**
- Python 3.11 or later
- An [OpenRouter](https://openrouter.ai) API key (free tier is sufficient for the default model)

**Install from source:**

```bash
git clone https://github.com/your-username/scathach-tutor.git
cd scathach-tutor
pip install -e .
```

**Configure your API key:**

Create a `.env` file in the directory where you'll run scathach:

```
SCATHACH_OPENROUTER_API_KEY=your_key_here
```

Verify the setup:

```bash
scathach config --test
```

---

## Quick start

```bash
# Ingest a document
scathach ingest my_notes.pdf

# Start a session (a wizard will prompt for timing mode and difficulty range)
scathach session "my notes"

# Resume an interrupted session
scathach session --list
scathach session --resume <session_id>

# Review due questions
scathach review
scathach super-review

# View your progress
scathach stats
```

---

## Commands

| Command | Description |
|---|---|
| `scathach ingest [path]` | Ingest a document or all docs in `./docs/` |
| `scathach session <topic>` | Start a new learning session |
| `scathach session --list` | List all unfinished sessions |
| `scathach session --resume <id>` | Resume an interrupted session |
| `scathach review` | Review due level 1–2 questions |
| `scathach super-review` | Review due level 3–6 questions |
| `scathach topics` | List all ingested topics |
| `scathach stats` | Progress dashboard |
| `scathach config --show` | Show current configuration |

Run `scathach <command> --help` for full option details on any command.

---

## Configuration

All settings can be set via environment variables or a `.env` file. Every variable is prefixed with `SCATHACH_`.

| Variable | Default | Description |
|---|---|---|
| `SCATHACH_OPENROUTER_API_KEY` | *(required)* | Your OpenRouter API key |
| `SCATHACH_MODEL` | `qwen/qwen3.6-plus:free` | LLM model identifier |
| `SCATHACH_QUALITY_THRESHOLD` | `7` | Minimum score (0–10) to pass a question |
| `SCATHACH_MAIN_TIMING` | `untimed` | Default timing mode for sessions (`timed`/`untimed`) |
| `SCATHACH_REVIEW_TIMING` | `untimed` | Default timing mode for reviews |
| `SCATHACH_HYDRA_IN_SUPER_REVIEW` | `false` | Enable Hydra Protocol in super-review |
| `SCATHACH_OPEN_DOC_ON_SESSION` | `false` | Open source document at session start |
| `SCATHACH_DB_PATH` | `~/.scathach/scathach.db` | Path to the SQLite database |

The active model and review timing can also be changed at runtime:

```bash
scathach config --set-model google/gemini-flash-1.5
scathach config --set-review-timing timed
```

---

## Supported file formats

PDF, DOCX, PPTX, HTML, TXT, Markdown (`.md`, `.markdown`), reStructuredText (`.rst`)
