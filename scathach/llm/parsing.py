"""
JSON schema definitions and response validators for LLM structured outputs.

The schemas are passed to LLMClient.generate() as `response_schema`; the API
enforces them so responses arrive as already-valid Python objects. The
validators below just check structural invariants and normalise types.
"""

from __future__ import annotations

from typing import Any


class ParseError(Exception):
    """Raised when a structured response fails validation."""


# ---------------------------------------------------------------------------
# JSON schemas — passed to LLMClient as response_schema
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
