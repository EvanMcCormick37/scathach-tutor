# scathach

A terminal-based adaptive learning and spaced repetition application. Drop in a document, and scathach generates comprehension questions, drills you on them, and schedules review sessions so the material actually sticks.

---

## How it works

### 1. Ingest a document

Link a file path or URL. Scathach reads the source material — PDF, DOCX, PPTX, Markdown, plain text, and more — and stores it as a **topic** in a local SQLite database.

### 2. Start a learning session

Start a **quest** and scathach generates open-ended questions from the topic at up to six difficulty levels:

| Level | Format | Time limit | Description |
|-------|--------|-----------|-------------|
| 1 | Single word or phrase | 30 s | A narrow fact or definition |
| 2 | One to two sentences | 60 s | A key concept or relationship |
| 3 | One paragraph | 5 min | A single section or theme |
| 4 | One to two paragraphs | 10 min | Multiple related concepts |
| 5 | Multiple paragraphs | 15 min | A major argument or framework; requires outside domain knowledge |
| 6 | Comprehensive essay | 30 min | Integrates the whole document; requires deep domain expertise |

By default, quests generate questions up to **level 4**. You type your answers in the terminal. Each answer is scored 0–10 by the LLM against the source material.

### 3. The Hydra Protocol

If you fail a question, scathach generates **targeted sub-questions** whose combined answers give you what you need to answer the parent. The LLM selects both the number and difficulty level of each sub-question — a level-4 failure might produce one level-1 definition and two level-2 concepts if those are the specific gaps. Clear the sub-questions and you return to the parent. The tree can branch multiple levels deep.

### 4. Timed mode

Sessions can be timed or untimed. In timed mode each question has a time limit proportional to its difficulty:

- **Normal zone** (0 – t): full score.
- **Penalty zone** (t – 2t): score halved on submission.
- **Expired** (>2t): automatic fail.

### 5. Open-book vs. closed-book

By default, sessions open the source document automatically so you can refer to it. Pass `--exam` to run closed-book: the document is not opened, and the session updates the topic's **exam support** metric rather than practice support.

### 6. Spaced repetition

Every question you clear is scheduled for future review via an FSRS-based algorithm. Initial stability is seeded from the question's difficulty level (a level-4 question starts at stability 4.0). Two independent queues track timed and untimed performance separately.

- **Flash cards** (`review --flash-cards`) — levels 1–2, FSRS-scheduled.
- **Long answers** (`review --long-answers`) — levels 3–6, worst performers first, Hydra on failure.
- **Topic review** (`review --topics`) — full quest for each topic due for scheduled review.

### 7. Session persistence

Sessions are saved to the database after every question. Quit mid-session with `Ctrl+C` and resume exactly where you left off with `session resume <id>`.

---

## Install

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

```bash
# Create ~/.scathach/.env
echo "SCATHACH_OPENROUTER_API_KEY=your_key_here" >> ~/.scathach/.env
```

**Verify the setup:**

```bash
scathach config --test
```

---

## Quick start

```bash
# Ingest a document
scathach ingest my_notes.pdf "My Notes"

# Or drop files into ~/.scathach/docs/ and ingest all at once
scathach ingest

# Start a quest (opens source doc; Ctrl+O to re-open it while answering)
scathach session quest "my notes"

# Closed-book exam mode
scathach session quest "my notes" --exam

# Drill a single difficulty level
scathach session drill "my notes" --level 3

# List and resume interrupted sessions
scathach session list
scathach session resume <session_id>

# Review due questions
scathach review

# View detailed topic information
scathach topics

# View progress dashboard
scathach stats
```

---

## Commands

### Top-level

| Command | Description |
|---------|-------------|
| `scathach ingest [srcpath] [name]` | Ingest a file or URL; omit both to scan `~/.scathach/docs/` |
| `scathach topics` | Detailed topics table (active topics by default) |
| `scathach topics --all` | All topics including retired |
| `scathach topics --retired` | Retired topics only |
| `scathach review` | Interactive review mode selector with live due-counts |
| `scathach review --flash-cards` | FSRS review: levels 1–2 |
| `scathach review --long-answers` | FSRS review: levels 3–6, worst performers first |
| `scathach review --topics` | Quest for each active topic due for scheduled review |
| `scathach review --all` | Flash-cards then long-answers |
| `scathach review --everything` | All three review modes in sequence |
| `scathach stats` | Progress dashboard |
| `scathach stats --topic <name>` | Per-level breakdown for a single topic |
| `scathach config --show` | Print current configuration |
| `scathach config --set-model <model>` | Change the active LLM model |
| `scathach config --set-timing timed\|untimed` | Set the default timing |
| `scathach config --test` | Send a canary prompt to verify the API key |

### `session` sub-group

| Command | Description |
|---------|-------------|
| `session quest <topic>` | Adaptive quest (Hydra Protocol, levels 1–4 by default) |
| `session quest <topic> --exam` | Closed-book quest; updates exam support |
| `session quest <topic> --levels N` | Set max difficulty level (1–6, default 4) |
| `session drill <topic> --level N` | Fixed-level quiz at a single difficulty |
| `session drill <topic> --level N --count N` | Control the number of questions |
| `session list` | List all unfinished sessions |
| `session resume <id>` | Resume an interrupted session |
| `session delete <id>` | Permanently delete a session and its questions |

### `topic` sub-group

| Command | Description |
|---------|-------------|
| `topic rename <old> <new>` | Rename a topic |
| `topic delete <name>` | Permanently delete a topic and all associated data |
| `topic set-level <name> <level>` | Set the target difficulty for topic review quests |
| `topic retire <name>` | Retire a topic from scheduled review; questions remain in FSRS queues |
| `topic unretire <name>` | Reactivate a retired topic |

Run `scathach <command> --help` for full option details.

---

## Answer input hotkeys

Available in every answer prompt:

| Key | Action |
|-----|--------|
| `Escape+Enter` | Submit answer |
| `Ctrl+E` | Open current answer text in `$EDITOR` for rich editing |
| `Ctrl+O` | Open the source document (not available in `--exam` mode) |
| `Ctrl+T` | Toss (permanently delete) the current question |

`$EDITOR` resolution order: `$VISUAL` → `$EDITOR` → `notepad` (Windows) / `nano` (Unix/macOS). VS Code users should set `VISUAL="code --wait"`.

---

## Configuration

All settings can be set via environment variables or `~/.scathach/.env`. Every variable is prefixed `SCATHACH_`.

| Variable | Default | Description |
|----------|---------|-------------|
| `SCATHACH_OPENROUTER_API_KEY` | _(required)_ | OpenRouter API key |
| `SCATHACH_MODEL` | `google/gemini-flash-1.5` | LLM model identifier |
| `SCATHACH_QUALITY_THRESHOLD` | `7` | Minimum score (0–10) to pass a question |
| `SCATHACH_TIMING` | `untimed` | Default timing mode (`timed` / `untimed`) |
| `SCATHACH_HYDRA_IN_REVIEW` | `false` | Enable Hydra Protocol in long-answer reviews |
| `SCATHACH_HYDRA_IN_DRILL` | `true` | Enable Hydra Protocol in drill sessions |
| `SCATHACH_ON_FAILED_REVIEW` | `choose` | `repeat` / `skip` / `choose` on a failed review question |
| `SCATHACH_MAX_PRACTICE_SUPPORT` | `14.0` | Sigmoid asymptote for practice support contribution (days) |
| `SCATHACH_DB_PATH` | `~/.scathach/scathach.db` | SQLite database path |

Runtime changes:

```bash
scathach config --set-model google/gemini-flash-1.5
scathach config --set-timing timed
```

---

## Supported file formats

PDF, DOCX, PPTX, HTML, TXT, Markdown (`.md`, `.markdown`), reStructuredText (`.rst`)
