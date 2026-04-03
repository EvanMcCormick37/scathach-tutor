"""
Unit tests for the ingestion pipeline.
Docling is mocked so these tests have no external dependencies.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scathach.db.repository import get_topic_by_name, list_topics
from scathach.db.schema import apply_schema, get_connection
from scathach.ingestion.chunker import chunk
from scathach.ingestion.ingestor import IngestionError, ingest_file, ingest_text


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = get_connection(":memory:")
    apply_schema(c)
    return c


# ---------------------------------------------------------------------------
# ingest_text
# ---------------------------------------------------------------------------


def test_ingest_text_basic(conn: sqlite3.Connection) -> None:
    topic = ingest_text(conn, "Newton's laws of motion.", topic_name="Physics")
    assert topic.id is not None
    assert topic.name == "Physics"
    assert "Newton" in topic.content


def test_ingest_text_strips_whitespace(conn: sqlite3.Connection) -> None:
    topic = ingest_text(conn, "  \n  some content  \n  ", topic_name="Test")
    assert topic.content == "some content"


def test_ingest_text_empty_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(IngestionError):
        ingest_text(conn, "   ", topic_name="Empty")


def test_ingest_text_upsert(conn: sqlite3.Connection) -> None:
    ingest_text(conn, "first version", topic_name="MyTopic")
    ingest_text(conn, "second version", topic_name="MyTopic")
    topics = list_topics(conn)
    assert len(topics) == 1
    assert topics[0].content == "second version"


# ---------------------------------------------------------------------------
# ingest_file — TXT / MD (plain-text fallback, no docling needed)
# ---------------------------------------------------------------------------


def test_ingest_txt_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("Thermodynamics notes.", encoding="utf-8")
    topic = ingest_file(conn, txt)
    assert topic.name == "notes"
    assert "Thermodynamics" in topic.content
    assert str(txt) in topic.source_path


def test_ingest_md_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    md = tmp_path / "lecture.md"
    md.write_text("# Lecture 1\n\nContent here.", encoding="utf-8")
    topic = ingest_file(conn, md, topic_name="Lecture 1")
    assert topic.name == "Lecture 1"
    assert "Content here" in topic.content


def test_ingest_file_custom_name(conn: sqlite3.Connection, tmp_path: Path) -> None:
    f = tmp_path / "random_filename.txt"
    f.write_text("data", encoding="utf-8")
    topic = ingest_file(conn, f, topic_name="Custom Name")
    assert topic.name == "Custom Name"


def test_ingest_file_not_found(conn: sqlite3.Connection) -> None:
    with pytest.raises(IngestionError, match="File not found"):
        ingest_file(conn, "/nonexistent/path/file.txt")


# ---------------------------------------------------------------------------
# ingest_file — PDF / DOCX (docling path, mocked)
# ---------------------------------------------------------------------------


def _make_docling_mock(markdown_output: str) -> MagicMock:
    """Build a minimal mock of the docling DocumentConverter return chain."""
    mock_doc = MagicMock()
    mock_doc.export_to_markdown.return_value = markdown_output
    mock_result = MagicMock()
    mock_result.document = mock_doc
    mock_converter = MagicMock()
    mock_converter.convert.return_value = mock_result
    return mock_converter


def test_ingest_pdf_with_docling(conn: sqlite3.Connection, tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")  # fake file, docling is mocked

    mock_converter = _make_docling_mock("# Paper Title\n\nAbstract content.")

    with patch("scathach.ingestion.ingestor._extract_with_docling") as mock_extract:
        mock_extract.return_value = "# Paper Title\n\nAbstract content."
        topic = ingest_file(conn, pdf)

    assert topic.name == "paper"
    assert "Abstract content" in topic.content


def test_ingest_docling_failure_raises(conn: sqlite3.Connection, tmp_path: Path) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a pdf")

    with patch(
        "scathach.ingestion.ingestor._extract_with_docling",
        side_effect=IngestionError("docling failed"),
    ):
        with pytest.raises(IngestionError):
            ingest_file(conn, pdf)


def test_ingest_unknown_extension_falls_back_to_plaintext(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Unknown extension: docling fails → falls back to plain read."""
    f = tmp_path / "data.xyz"
    f.write_text("plain text content", encoding="utf-8")

    with patch(
        "scathach.ingestion.ingestor._extract_with_docling",
        side_effect=IngestionError("unsupported"),
    ):
        topic = ingest_file(conn, f)

    assert "plain text content" in topic.content


# ---------------------------------------------------------------------------
# chunker (MVP no-op)
# ---------------------------------------------------------------------------


def test_chunk_returns_single_element() -> None:
    result = chunk("hello world")
    assert result == ["hello world"]


def test_chunk_empty_string() -> None:
    result = chunk("")
    assert result == [""]
