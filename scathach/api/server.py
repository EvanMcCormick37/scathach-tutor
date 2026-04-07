"""
FastAPI application factory for the scathach API server.

Usage (development):
    uvicorn scathach.api.server:app --reload --port 8765

Usage (production / sidecar):
    python -m scathach.api.server_entry --port <port>
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from scathach.api.routes import config, review, sessions, topics
from scathach.config import settings
from scathach.db.schema import open_db
from scathach.llm.client import LLMClient, make_client


# ---------------------------------------------------------------------------
# App-level shared state (set in lifespan, injected via request.app.state)
# ---------------------------------------------------------------------------


class AppState:
    conn: sqlite3.Connection
    client: LLMClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.conn = open_db(settings.db_path)
    app.state.client = make_client()
    yield
    # Shutdown
    app.state.conn.close()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="scathach API",
        description="LLM-powered spaced-repetition tutor — REST API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Allow the Tauri webview (and local dev server) to reach the API.
    # In production the webview origin is tauri://localhost or http://localhost:<vite-port>.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(topics.router, prefix="/topics", tags=["topics"])
    app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
    app.include_router(review.router, prefix="/review", tags=["review"])
    app.include_router(config.router, prefix="/config", tags=["config"])

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
