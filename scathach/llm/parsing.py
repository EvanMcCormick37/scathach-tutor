"""
Robust JSON parsing helpers for LLM responses.

LLMs frequently produce slightly malformed JSON (trailing commas, extra text,
markdown fences). This module provides parse functions that try strict JSON
first, then fall back to regex extraction.
"""

from __future__ import annotations

import json
import re
from typing import Any


class ParseError(Exception):
    """Raised when a response cannot be parsed by any strategy."""


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def parse_json(text: str) -> Any:
    """
    Parse JSON from an LLM response.

    Tries:
      1. Direct json.loads after stripping markdown fences.
      2. Extract the first JSON array or object via regex.

    Raises:
        ParseError: If all strategies fail.
    """
    cleaned = _strip_fences(text)

    # Strategy 1: direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract first [...] block
    array_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if array_match:
        try:
            return json.loads(array_match.group(0))
        except json.JSONDecodeError:
            pass

    # Strategy 3: extract first {...} block
    obj_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ParseError(f"Could not parse JSON from LLM response. Raw text:\n{text[:500]}")


def parse_questions_response(text: str) -> list[dict[str, Any]]:
    """
    Parse a question-generation LLM response into a list of question dicts.

    Expected shape: [{"difficulty": int, "body": str, "ideal_answer": str}, ...]

    Raises:
        ParseError: If the response cannot be parsed or is missing required fields.
    """
    data = parse_json(text)

    if not isinstance(data, list):
        raise ParseError(f"Expected a JSON array, got {type(data).__name__}.")

    required = {"difficulty", "body", "ideal_answer"}
    result = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ParseError(f"Item {i} is not a JSON object.")
        missing = required - item.keys()
        if missing:
            raise ParseError(f"Item {i} is missing fields: {missing}.")
        # Coerce difficulty to int
        try:
            item["difficulty"] = int(item["difficulty"])
        except (ValueError, TypeError) as exc:
            raise ParseError(f"Item {i} has invalid 'difficulty': {item['difficulty']!r}") from exc
        if not 1 <= item["difficulty"] <= 6:
            raise ParseError(f"Item {i} difficulty {item['difficulty']} is not in range 1–6.")
        result.append(item)

    return result


def parse_score_response(text: str) -> dict[str, Any]:
    """
    Parse an answer-scoring LLM response.

    Expected shape: {"score": int, "diagnosis": str}

    Raises:
        ParseError: If the response cannot be parsed or is missing required fields.
    """
    data = parse_json(text)

    if not isinstance(data, dict):
        raise ParseError(f"Expected a JSON object, got {type(data).__name__}.")

    if "score" not in data:
        raise ParseError("Score response missing 'score' field.")
    if "diagnosis" not in data:
        raise ParseError("Score response missing 'diagnosis' field.")

    try:
        score = int(data["score"])
    except (ValueError, TypeError) as exc:
        raise ParseError(f"Invalid 'score' value: {data['score']!r}") from exc

    if not 0 <= score <= 10:
        raise ParseError(f"Score {score} is out of range 0–10.")

    return {"score": score, "diagnosis": str(data["diagnosis"])}
