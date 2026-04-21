"""
JSON extraction, schema definitions, and response validators for LLM outputs.

Because not all OpenRouter providers support structured output enforcement,
responses are returned as raw text and parsed client-side via extract_json().
The schema constants below document the expected shape but are no longer sent
to the API.
"""

from __future__ import annotations

import json
import re
from typing import Any


class ParseError(Exception):
    """Raised when a structured response fails validation."""


def extract_json(text: str) -> Any:
    """
    Extract and parse a JSON value from raw LLM text.

    Strategies (in order):
      1. Direct json.loads() on the stripped text.
      2. Regex extraction of the first [...] block (for array responses).
      3. Regex extraction of the first {...} block (for object responses).

    Raises:
        ParseError: If all strategies fail.
    """
    text = text.strip()

    # Strip markdown code fences if present
    fenced = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    fenced = re.sub(r"\s*```$", "", fenced).strip()

    for candidate in (fenced, text):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass

    # Try extracting the first [...] block
    array_match = re.search(r"\[.*\]", text, re.DOTALL)
    if array_match:
        try:
            return json.loads(array_match.group())
        except (json.JSONDecodeError, ValueError):
            pass

    # Try extracting the first {...} block
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except (json.JSONDecodeError, ValueError):
            pass

    raise ParseError(f"Could not extract valid JSON from LLM response: {text[:200]!r}")


# ---------------------------------------------------------------------------
# JSON schemas — document expected shapes (not sent to the API)
# ---------------------------------------------------------------------------

QUESTIONS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "difficulty":    {"type": "integer", "minimum": 1, "maximum": 6},
            "body":          {"type": "string"},
            "ideal_answer":  {"type": "string"},
        },
        "required": ["difficulty", "body", "ideal_answer"],
        "additionalProperties": False,
    },
}

SCORE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score":     {"type": "integer", "minimum": 0, "maximum": 10},
        "diagnosis": {"type": "string"},
    },
    "required": ["score", "diagnosis"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Validators — accept already-parsed Python objects
# ---------------------------------------------------------------------------

def validate_questions_response(data: Any) -> list[dict[str, Any]]:
    """
    Validate a parsed question-generation response.

    Expected shape: [{"difficulty": int, "body": str, "ideal_answer": str}, ...]

    Raises:
        ParseError: If the structure is invalid.
    """
    if not isinstance(data, list):
        raise ParseError(f"Expected a list, got {type(data).__name__}.")

    required = {"difficulty", "body", "ideal_answer"}
    result: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ParseError(f"Item {i} is not an object.")
        missing = required - item.keys()
        if missing:
            raise ParseError(f"Item {i} missing fields: {missing}.")
        try:
            item["difficulty"] = int(item["difficulty"])
        except (ValueError, TypeError) as exc:
            raise ParseError(f"Item {i} invalid 'difficulty': {item['difficulty']!r}") from exc
        if not 1 <= item["difficulty"] <= 6:
            raise ParseError(f"Item {i} difficulty {item['difficulty']} out of range 1–6.")
        result.append(item)

    return result


def validate_score_response(data: Any) -> dict[str, Any]:
    """
    Validate a parsed answer-scoring response.

    Expected shape: {"score": int, "diagnosis": str}

    Raises:
        ParseError: If the structure is invalid.
    """
    if not isinstance(data, dict):
        raise ParseError(f"Expected an object, got {type(data).__name__}.")
    if "score" not in data:
        raise ParseError("Score response missing 'score'.")
    if "diagnosis" not in data:
        raise ParseError("Score response missing 'diagnosis'.")

    try:
        score = int(data["score"])
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Invalid 'score': {data['score']!r}") from exc

    if not 0 <= score <= 10:
        raise ParseError(f"Score {score} out of range 0–10.")

    return {"score": score, "diagnosis": str(data["diagnosis"])}
