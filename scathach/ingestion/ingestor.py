"""
Document ingestion pipeline.

Accepts a file path (PDF, DOCX, PPTX, TXT, MD) or raw pasted text.
Uses docling's DocumentConverter to extract clean markdown text.
Falls back to a plain file read for plain-text formats if docling fails.
Stores extracted text into the topics table via the repository layer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from scathach.db.models import Topic
from scathach.db.repository import upsert_topic

# Formats that docling can handle natively
_DOCLING_FORMATS = {".pdf", ".docx", ".pptx", ".html", ".htm"}

# Formats we fall back to plain-text read for
_PLAINTEXT_FORMATS = {".txt", ".md", ".markdown", ".rst"}


class IngestionError(Exception):
    """Raised when a document cannot be ingested."""


def _extract_with_docling(path: Path) -> str:
    """Use docling to extract text from a binary document. Returns markdown string."""
    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import]
    except ImportError as exc:
        raise IngestionError(
            "docling is not installed. Run: pip install docling"
        ) from exc

    try:
        converter = DocumentConverter()
        result = converter.convert(str(path))
        return result.document.export_to_markdown()
    except Exception as exc:
        raise IngestionError(f"docling failed to convert {path.name!r}: {exc}") from exc


def _extract_plaintext(path: Path) -> str:
    """Read a plain-text file and return its contents."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise IngestionError(f"Cannot read file {path.name!r}: {exc}") from exc


def ingest_file(
    conn: sqlite3.Connection,
    file_path: str | Path,
    topic_name: Optional[str] = None,
) -> Topic:
    """
    Ingest a document file into the topics table.

    Args:
        conn:        Open SQLite connection (schema already applied).
        file_path:   Path to the file to ingest.
        topic_name:  Override for the topic name; defaults to the file stem.

    Returns:
        The upserted Topic with id set.

    Raises:
        IngestionError: If the file cannot be read or converted.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise IngestionError(f"File not found: {path}")

    suffix = path.suffix.lower()
    name = topic_name or path.stem

    if suffix in _DOCLING_FORMATS:
        content = _extract_with_docling(path)
    elif suffix in _PLAINTEXT_FORMATS:
        content = _extract_plaintext(path)
    else:
        # Try docling for unknown formats; fall back to plain read
        try:
            content = _extract_with_docling(path)
        except IngestionError:
            content = _extract_plaintext(path)

    topic = Topic(name=name, content=content, source_path=str(path))
    return upsert_topic(conn, topic)


def ingest_text(
    conn: sqlite3.Connection,
    text: str,
    topic_name: str,
) -> Topic:
    """
    Ingest raw pasted text as a topic.

    Args:
        conn:        Open SQLite connection.
        text:        The raw text content.
        topic_name:  Name for the topic.

    Returns:
        The upserted Topic with id set.
    """
    if not text.strip():
        raise IngestionError("Cannot ingest empty text.")
    topic = Topic(name=topic_name, content=text.strip(), source_path=None)
    return upsert_topic(conn, topic)
