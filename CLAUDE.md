# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

See `scathach/ARCHITECTURE.md` for the full architecture reference. The short version:

The codebase is a CLI-based spaced-repetition tutor with a strict layered design:

```
CLI (Typer + Rich + prompt_toolkit)
  └─ Core (pure business logic, no I/O — communicates via async callbacks)
       ├─ DB (SQLite via repository.py)
       ├─ LLM (OpenRouter/OpenAI SDK via llm/client.py)
       └─ Ingestion (docling pipeline)
```

`SessionRunner` in `scathach/core/session.py` is the central state machine. It is I/O-agnostic: the CLI wires async callbacks into it; `SessionRunner` itself never touches the terminal. This makes it independently testable.

## Commands

Install (editable):
```bash
pip install -e .
```

Run the CLI:
```bash
scathach [command]
```

Run all tests:
```bash
pytest
```

Run a single test file or function:
```bash
pytest tests/test_scoring.py
pytest tests/test_scoring.py::test_function_name
```

Configuration lives in `~/.scathach/.env`; see `.env.example` for available `SCATHACH_*` variables.

## Key design decisions

- **Callbacks, not inheritance**: `SessionRunner` accepts async callables for every I/O event (display question, receive answer, show result). Avoid adding terminal/Rich calls inside core/.
- **Two independent FSRS queues**: timed and untimed reviews are tracked separately per question; don't conflate them.
- **Hydra protocol**: when a question is failed it is split into sub-questions; the parent is always re-asked after all sub-questions clear.
- **Scoring is client-side**: time penalties are applied after the LLM scores the answer so the LLM sees only content.
- **Prompt version pins**: every prompt in `llm/prompts.py` carries a version string — bump it when changing prompts that affect output shape.
