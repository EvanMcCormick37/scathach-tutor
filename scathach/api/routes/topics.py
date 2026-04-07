"""
Topics routes.

GET  /topics                 — list all topics
POST /topics/ingest          — upload a file and ingest it
POST /topics/paste           — ingest raw text
PATCH /topics/{topic_id}     — rename a topic
DELETE /topics/{topic_id}    — delete a topic (not in CLI, bonus)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from scathach.api.models import (
    IngestPasteRequest,
    TopicListResponse,
    TopicRenameRequest,
    TopicResponse,
)
from scathach.db.repository import (
    get_topic_by_id,
    list_topics,
    rename_topic,
)
from scathach.ingestion.ingestor import IngestionError, ingest_file, ingest_text

router = APIRouter()


def _topic_to_response(topic) -> TopicResponse:
    return TopicResponse(
        id=topic.id,
        name=topic.name,
        source_path=str(topic.source_path) if topic.source_path else None,
        created_at=str(topic.created_at),
    )


@router.get("", response_model=TopicListResponse)
async def list_all_topics(request: Request):
    conn = request.app.state.conn
    topics = list_topics(conn)
    return TopicListResponse(topics=[_topic_to_response(t) for t in topics])


@router.post("/ingest", response_model=TopicResponse)
async def ingest_file_upload(request: Request, file: UploadFile):
    """Upload a document file (PDF, DOCX, PPTX, HTML, TXT, MD …) and ingest it."""
    conn = request.app.state.conn
    suffix = Path(file.filename or "upload.txt").suffix or ".txt"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)
        topic_name = Path(file.filename or "Untitled").stem
        topic = ingest_file(conn, tmp_path, topic_name=topic_name)
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return _topic_to_response(topic)


@router.post("/paste", response_model=TopicResponse)
async def ingest_paste(request: Request, body: IngestPasteRequest):
    conn = request.app.state.conn
    try:
        topic = ingest_text(conn, body.text, body.topic_name)
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _topic_to_response(topic)


@router.patch("/{topic_id}", response_model=TopicResponse)
async def rename_topic_endpoint(
    request: Request, topic_id: int, body: TopicRenameRequest
):
    conn = request.app.state.conn
    topic = get_topic_by_id(conn, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    updated = rename_topic(conn, topic.name, body.new_name)
    if updated is None:
        raise HTTPException(status_code=409, detail="Name already taken or topic not found")
    return _topic_to_response(updated)
