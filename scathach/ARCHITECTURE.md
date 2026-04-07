# scathach — GUI & Desktop Executable Architecture Proposal

> **Status:** Proposal  
> **Scope:** Transitioning scathach from a CLI Python module to a cross-platform desktop application with a graphical user interface, distributable as a standalone executable.

---

## 1. Executive Summary

scathach's Python core is already well-separated from its presentation layer — the `SessionRunner`, `score_answer`, `spawn_subquestions`, and FSRS scheduler are all I/O-agnostic. The CLI is thin and replaceable. This makes the transition to a GUI application low-risk: the domain logic stays untouched while the presentation layer is replaced entirely.

The proposed stack is:

- **UI Framework:** [Tauri](https://tauri.app) — a Rust-based desktop shell that hosts a web frontend (HTML/CSS/JS or a React SPA). Tauri communicates with a bundled Python backend via a local HTTP server (FastAPI).
- **Frontend:** React + Vite SPA, served from within the Tauri webview.
- **Backend:** FastAPI server (Python), bundled into the executable via PyInstaller, running on localhost on a random port. All existing domain logic runs here, unchanged.
- **Distribution:** A single-file executable per platform (`.exe` on Windows, `.dmg`/`.app` on macOS, AppImage on Linux) produced by Tauri's built-in bundler.

This architecture gives us:

- A native-feeling desktop app (system tray, window chrome, OS file pickers).
- No dependency on Python being installed on the user's machine.
- Full reuse of the existing domain layer — zero rewriting of core logic.
- A pleasant, approachable UI that replaces the terminal entirely.

---

## 2. Current Architecture Assessment

### What exists today

```
scathach/
├── cli/            # Typer commands + Rich/prompt_toolkit TUI — REPLACED
│   ├── main.py
│   ├── session_ui.py
│   ├── review_ui.py
│   └── stats_ui.py
├── core/           # Domain logic — KEPT UNCHANGED
│   ├── session.py      SessionRunner (I/O-agnostic state machine)
│   ├── scoring.py      score_answer()
│   ├── hydra.py        spawn_subquestions()
│   ├── scheduler.py    FSRS scheduling
│   └── question.py     DifficultyLevel, TimingMode, TimerZone
├── db/             # SQLite persistence — KEPT UNCHANGED
│   ├── schema.py
│   ├── repository.py
│   └── models.py
├── llm/            # LLM client + prompts — KEPT UNCHANGED
│   ├── client.py
│   ├── providers.py
│   ├── prompts.py
│   └── parsing.py
└── ingestion/      # File → text extraction — KEPT UNCHANGED
    └── ingestor.py
```

**Key insight:** The domain layer is already a library. The CLI is a thin adapter. The GUI will be a second adapter for the same library.

### What the CLI does that the GUI must replicate

| CLI Command                      | GUI Equivalent                              |
| -------------------------------- | ------------------------------------------- |
| `scathach ingest [path]`         | File picker dialog → ingest button          |
| `scathach ingest --paste`        | Paste-text modal                            |
| `scathach session <topic>`       | Topic card → "Start Session" → wizard modal |
| `scathach session --list`        | Sessions panel showing incomplete sessions  |
| `scathach session --resume <id>` | "Resume" button on session card             |
| `scathach review`                | "Review" button; filter: levels 1–2         |
| `scathach super-review`          | "Super-Review" button; filter: levels 3–6   |
| `scathach topics`                | Topics list/grid view                       |
| `scathach rename`                | Inline rename on topic card                 |
| `scathach stats`                 | Dashboard/stats tab                         |
| `scathach config --show`         | Settings panel                              |
| `scathach config --test`         | "Test Connection" button in settings        |

### The timed answer flow

The dual-zone timer and multiline answer input are the most interaction-heavy parts of the CLI. In the GUI, this becomes:

- A textarea for the answer.
- A visible progress bar that transitions from green → yellow → red as time zones change.
- The timer runs in-browser (JavaScript `setInterval`) synced against a start timestamp returned by the API. The elapsed time is sent with the answer to the backend for scoring.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram

```
┌─────────────────────────────────────────────────┐
│                 Tauri Shell (Rust)               │
│  ┌──────────────────────────────────────────┐   │
│  │         Webview  (React SPA)             │   │
│  │                                          │   │
│  │  ┌────────────┐   ┌───────────────────┐  │   │
│  │  │ Topics /   │   │  Session Screen   │  │   │
│  │  │ Dashboard  │   │  (Q&A + Timer)    │  │   │
│  │  └────────────┘   └───────────────────┘  │   │
│  │         │                   │             │   │
│  │         └─────────┬─────────┘             │   │
│  │                   │ fetch() to localhost   │   │
│  └───────────────────┼───────────────────────┘   │
│                      │ HTTP (REST + SSE)          │
│  ┌───────────────────▼───────────────────────┐   │
│  │       FastAPI Server  (Python)            │   │
│  │                                           │   │
│  │   scathach.core.*  (unchanged)            │   │
│  │   scathach.db.*    (unchanged)            │   │
│  │   scathach.llm.*   (unchanged)            │   │
│  │   scathach.ingestion.* (unchanged)        │   │
│  └───────────────────────────────────────────┘   │
│                      │                           │
│                  SQLite DB                        │
│           (~/.scathach/scathach.db)               │
└─────────────────────────────────────────────────┘
```

### 3.2 Component Breakdown

#### A. FastAPI Backend (`scathach/api/`)

A new thin API layer is added alongside (not replacing) the existing `cli/` package. It exposes the domain logic as HTTP endpoints consumed by the frontend.

```
scathach/api/
├── __init__.py
├── server.py        # FastAPI app factory; starts on a random free port
├── routes/
│   ├── topics.py    # GET /topics, POST /topics/ingest, POST /topics/paste, PATCH /topics/{id}
│   ├── sessions.py  # POST /sessions, GET /sessions (active), GET /sessions/{id}, DELETE /sessions/{id}
│   ├── questions.py # GET /sessions/{id}/next-question (SSE or polling)
│   ├── answers.py   # POST /sessions/{id}/answer
│   ├── review.py    # GET /review/due, POST /review/answer
│   └── config.py    # GET /config, PATCH /config, POST /config/test
└── models.py        # Pydantic request/response schemas (separate from db/models.py)
```

**Session flow over HTTP:**

The `SessionRunner` is stateful and async. The API wraps it by keeping an in-memory registry of active `SessionRunner` instances keyed by `session_id`. This is safe because the app is single-user (local desktop).

```
POST /sessions          → creates SessionRunner, generates questions, returns session_id + first question
POST /sessions/{id}/answer  → submits answer, returns score + diagnosis + next question (or completion)
GET  /sessions/{id}/stream  → SSE stream for real-time scoring progress (optional enhancement)
```

**Answer timing:** The backend records `started_at` (a UTC timestamp) when a question is presented. When the client POSTs an answer, it sends `elapsed_s` (computed in JS). The backend validates this is plausible (within 2× the time limit + a small grace window) before passing it to `score_answer()`. The JS timer is authoritative for UX display; the backend enforces the rule.

#### B. Tauri Shell (`src-tauri/`)

Tauri wraps the React frontend in a native webview and manages the Python subprocess lifecycle.

```
src-tauri/
├── Cargo.toml
├── tauri.conf.json      # Window config, bundle ID, file associations
└── src/
    └── main.rs          # Spawns the Python/FastAPI subprocess on startup;
                         # discovers a free port and passes it to the webview
                         # via a Tauri global variable injection.
```

Tauri's `sidecar` feature bundles the PyInstaller-compiled executable alongside the Tauri binary. On startup, `main.rs`:

1. Picks a random free TCP port.
2. Launches `scathach-server --port <port>` as a sidecar process.
3. Polls `http://localhost:<port>/health` until the server is up (typically < 1s).
4. Opens the webview, injecting `window.__SCATHACH_API_PORT__` for the React app to use.
5. On window close, terminates the sidecar.

#### C. React Frontend (`ui/`)

```
ui/
├── index.html
├── vite.config.ts
├── src/
│   ├── main.tsx
│   ├── api.ts           # Typed fetch wrappers; reads window.__SCATHACH_API_PORT__
│   ├── App.tsx          # Router: Dashboard | Session | Review | Settings
│   └── pages/
│       ├── Dashboard.tsx        # Topics grid + stats summary
│       ├── TopicDetail.tsx      # Topic info + "Start Session" / "Resume" buttons
│       ├── SessionWizard.tsx    # Pre-session config (timing, threshold, levels)
│       ├── SessionScreen.tsx    # Active Q&A: question panel + answer textarea + timer
│       ├── ReviewScreen.tsx     # Review queue (levels 1–2)
│       ├── SuperReviewScreen.tsx # Super-review queue (levels 3–6)
│       ├── StatsScreen.tsx      # Progress dashboard
│       └── Settings.tsx         # API key, model, timing defaults
```

**Key UI components:**

- **DualZoneTimer** — A horizontal progress bar that drains from full (green) to empty (yellow, penalty zone) then to a red expired state. The timer is entirely JavaScript; it doesn't poll the server. The server only validates elapsed time on answer submission.
- **AnswerEditor** — A resizable `<textarea>` with a character/word count hint. On timed questions, it is automatically disabled and the cursor locked when `elapsed_s > 2 × time_limit_s`.
- **HydraNotice** — A dismissible banner shown when sub-questions are spawned, explaining why the questions changed.
- **SessionProgress** — A sidebar showing which difficulty levels have been cleared (with star icons matching the current CLI output).

---

## 4. Packaging & Distribution

### 4.1 Python → Executable (PyInstaller)

The Python server is compiled to a single binary using PyInstaller. A new entry point is added:

```
scathach/api/server_entry.py   # if __name__ == "__main__": uvicorn.run(...)
```

```bash
pyinstaller \
  --onefile \
  --name scathach-server \
  --add-data "scathach:scathach" \
  scathach/api/server_entry.py
```

`docling`'s model weights and any native library dependencies (e.g. `sqlite3`) are handled via PyInstaller's `--collect-all docling` hook. The resulting `dist/scathach-server` binary is placed in `src-tauri/binaries/` where Tauri's sidecar system picks it up.

### 4.2 Tauri Bundler Output

```bash
cd ui && npm run build          # Vite → ui/dist/
cd src-tauri && cargo tauri build
```

Tauri bundles:

- The compiled Rust binary (webview shell)
- The React SPA (`ui/dist/`)
- The PyInstaller binary (`src-tauri/binaries/scathach-server`)

Output artifacts:
| Platform | Artifact |
|---|---|
| Windows | `scathach_0.1.0_x64.msi` + `scathach_0.1.0_x64-setup.exe` |
| macOS | `scathach_0.1.0_x64.dmg` (Intel) + `scathach_0.1.0_aarch64.dmg` (Apple Silicon) |
| Linux | `scathach_0.1.0_amd64.AppImage` + `.deb` |

### 4.3 CI/CD (GitHub Actions)

A workflow with three matrix jobs (windows-latest, macos-latest, ubuntu-latest) runs on every tag push:

```
.github/workflows/release.yml
  jobs:
    build:
      strategy:
        matrix:
          os: [windows-latest, macos-latest, ubuntu-latest]
      steps:
        - Install Python + pip install pyinstaller + project deps
        - Run PyInstaller to produce scathach-server binary
        - Install Node + npm install in ui/
        - Run cargo tauri build
        - Upload artifacts to GitHub Release
```

---

## 5. Data & Configuration Migration

- The SQLite database path (`~/.scathach/scathach.db`) is unchanged. Existing data from CLI usage carries over automatically.
- The `.env` file is superseded by a GUI settings panel that writes to the same environment variables (or a `~/.scathach/config.json` if preferred for the desktop context).
- The API key entry field in Settings uses the OS keychain via Tauri's `keyring` plugin rather than a plain `.env` file, improving security for non-technical users.

---

## 6. Feature Parity Checklist

| Feature                          | CLI                         | GUI                                              |
| -------------------------------- | --------------------------- | ------------------------------------------------ |
| Ingest file (PDF, DOCX, etc.)    | `scathach ingest <path>`    | File picker button; drag-and-drop support        |
| Ingest pasted text               | `scathach ingest --paste`   | "Paste Text" modal with textarea                 |
| Folder auto-scan (`./docs/`)     | `scathach ingest` (no arg)  | "Scan Docs Folder" button; configurable folder   |
| List topics                      | `scathach topics`           | Dashboard grid of topic cards                    |
| Rename topic                     | `scathach rename`           | Inline edit on topic card                        |
| New session with wizard          | `scathach session <topic>`  | Topic card → wizard modal → session screen       |
| Resume interrupted session       | `scathach session --resume` | "Resume" chip on topic card                      |
| Timed answer with dual-zone      | DualZoneTimer in terminal   | Progress bar UI component                        |
| Multiline answer input           | prompt_toolkit editor       | Resizable `<textarea>`                           |
| Hydra sub-question spawn         | Inline terminal notice      | HydraNotice banner + question transition         |
| Review (levels 1–2)              | `scathach review`           | Review screen, FSRS due count shown on dashboard |
| Super-review (levels 3–6)        | `scathach super-review`     | Super-review screen with Hydra toggle            |
| Stats dashboard                  | `scathach stats`            | Stats screen with charts                         |
| Config / API key                 | `.env` file                 | Settings panel + OS keychain                     |
| Config test                      | `scathach config --test`    | "Test Connection" button in Settings             |
| Open source doc on session start | `--open-doc` flag           | Toggle in session wizard                         |

---

## 7. Development Roadmap

### Phase 1 — Backend API (2–3 weeks)

1. Scaffold `scathach/api/` with FastAPI.
2. Implement all routes; wire them to existing domain logic.
3. Write integration tests (pytest + httpx AsyncClient).
4. Add `scathach-server` CLI entry point for standalone server mode.

### Phase 2 — Frontend SPA (3–4 weeks)

1. Scaffold React + Vite project in `ui/`.
2. Implement Dashboard, Settings, and Topic Detail pages (read-only paths first).
3. Implement SessionScreen with DualZoneTimer and AnswerEditor.
4. Implement Review and Super-Review screens.
5. Connect all pages to the FastAPI backend via `api.ts`.

### Phase 3 — Tauri Integration (1–2 weeks)

1. Scaffold Tauri project in `src-tauri/`.
2. Implement sidecar launch + port injection in `main.rs`.
3. Configure window chrome, app icon, and file associations.
4. Smoke-test end-to-end on all three platforms.

### Phase 4 — Packaging & Release (1–2 weeks)

1. PyInstaller configuration; resolve docling model bundling.
2. GitHub Actions CI/CD matrix workflow.
3. Code signing (macOS notarization, Windows Authenticode).
4. Build installer and test on clean machines with no Python installed.

### Phase 5 — Polish (ongoing)

- Keyboard shortcuts (e.g. Ctrl+Enter to submit answer).
- Onboarding flow for first-time users (API key wizard on first launch).
- Dark/light theme toggle.
- In-app update notifications via Tauri's updater plugin.

---

## 8. Key Technical Decisions & Rationale

### Why Tauri instead of Electron?

Tauri produces dramatically smaller bundles (typically 5–15 MB vs 150–300 MB for Electron) because it uses the OS's native webview (WebKit on macOS/Linux, WebView2 on Windows) rather than bundling Chromium. For a learning app with no browser-compatibility requirements, this is the right tradeoff. Tauri also has a more restrictive security model that is appropriate for a desktop app.

### Why FastAPI instead of Tauri commands calling Python directly?

Tauri supports calling Python via `Command::sidecar`, but IPC over named pipes or stdout is awkward for streaming async operations (scoring, generation). FastAPI gives us standard HTTP + Server-Sent Events, which the React frontend can consume with `fetch` and `EventSource` exactly as it would in a web context. It also means the backend is independently testable and could trivially be exposed as a local web app in the future.

### Why PyInstaller instead of a Docker image?

The target audience is non-technical users. Docker requires separate installation, daemon management, and CLI familiarity. PyInstaller produces a genuine executable that non-technical users can double-click.

### Why keep the CLI?

The CLI is not removed — it continues to exist in `scathach/cli/` and remains installable via `pip install scathach`. Power users who prefer the terminal keep their workflow. The new `scathach/api/` layer is additive.

### Why not rewrite in a different language?

The LLM integration, docling ingestion, and SQLite schema are already mature Python. A rewrite would be high-risk and high-cost for no user-facing benefit. Tauri's sidecar pattern was designed precisely for this scenario.

---

## 9. Repository Structure After Transition

```
scathach/                      # Python package (unchanged domain logic)
├── core/
├── db/
├── llm/
├── ingestion/
├── cli/                       # Kept for pip-install users
└── api/                       # NEW: FastAPI server

ui/                            # NEW: React + Vite frontend
├── src/
├── public/
└── vite.config.ts

src-tauri/                     # NEW: Tauri shell
├── src/main.rs
├── tauri.conf.json
├── icons/
└── binaries/                  # PyInstaller output placed here

.github/
└── workflows/
    └── release.yml            # NEW: Cross-platform CI/CD

pyproject.toml                 # Unchanged (pip install scathach still works)
package.json                   # NEW: root workspace for ui/ tooling
```

---

## 10. Open Questions

1. **docling model weights** — docling downloads ML models on first use. For the desktop build, should models be bundled into the executable (large binary, works offline) or downloaded on first launch (smaller binary, requires internet on first ingest)? Recommendation: download on first use, cache in `~/.scathach/models/`, show a progress modal.

2. **SQLite WAL mode on Windows** — WAL mode works on Windows but requires that the database file and the `-wal`/`-shm` files are on the same volume. This is standard behavior; just worth documenting for users who might move their DB.

3. **Code signing costs** — macOS notarization requires an Apple Developer Program membership ($99/year). Windows Authenticode requires a certificate from a CA ($100–400/year). For an open-source project, consider using a free EV alternative or documenting the "unverified developer" bypass procedure for initial releases.

4. **API key storage** — Tauri's keyring plugin uses the OS keychain (Keychain on macOS, Credential Manager on Windows, libsecret on Linux). This is more secure than a `.env` file but requires the user to grant keychain access on first run. The fallback is a plain config file at `~/.scathach/config.json` with a warning in the UI.
