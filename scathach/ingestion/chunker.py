"""
Document chunker — MVP stub.

For MVP, the full document text is passed as context to the LLM directly.
This module is a pass-through placeholder for future RAG / chunking support.

Future: split content into overlapping chunks for embedding-based retrieval.
"""

from __future__ import annotations


def chunk(text: str, max_chars: int = 0) -> list[str]:
    """
    Return the document as a single chunk (no-op for MVP).

    Args:
        text:      The full document text.
        max_chars: Ignored in MVP; future chunk size parameter.

    Returns:
        A list with one element: the full text.
    """
    return [text]
