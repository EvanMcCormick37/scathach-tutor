"""
Tests for prompt rendering — verify all required fields appear in rendered output.
"""

from __future__ import annotations

import pytest

from scathach.llm.prompts import (
    HYDRA_PROMPT_VERSION,
    QUESTION_GENERATION_PROMPT_VERSION,
    SCORING_PROMPT_VERSION,
    render_hydra_prompt,
    render_question_generation_prompt,
    render_scoring_prompt,
)


# ---------------------------------------------------------------------------
# Prompt version pins
# ---------------------------------------------------------------------------


def test_prompt_versions_are_strings() -> None:
    assert isinstance(QUESTION_GENERATION_PROMPT_VERSION, str)
    assert isinstance(HYDRA_PROMPT_VERSION, str)
    assert isinstance(SCORING_PROMPT_VERSION, str)


# ---------------------------------------------------------------------------
# Question generation prompt
# ---------------------------------------------------------------------------


def test_question_generation_prompt_structure() -> None:
    sys_p, user_p = render_question_generation_prompt("Gravity pulls objects together.")
    # System prompt should include the rubric and JSON format instructions
    assert "Level 1" in sys_p
    assert "Level 6" in sys_p
    assert "JSON" in sys_p
    assert "ideal_answer" in sys_p
    assert "difficulty" in sys_p
    # User prompt should include the document content
    assert "Gravity pulls objects together." in user_p


def test_question_generation_prompt_no_prior_questions_by_default() -> None:
    _, user_p = render_question_generation_prompt("Some content.")
    assert "Previously asked" not in user_p


def test_question_generation_prompt_embeds_prior_questions() -> None:
    from scathach.db.models import Question
    prior = [
        Question(topic_id=1, difficulty=1, body="What is gravity?", ideal_answer="A force.", is_root=True),
        Question(topic_id=1, difficulty=2, body="State Newton's first law.", ideal_answer="...", is_root=True),
    ]
    _, user_p = render_question_generation_prompt("Some content.", prior_questions=prior)
    assert "Previously asked" in user_p
    assert "What is gravity?" in user_p
    assert "State Newton's first law." in user_p
    assert "Level 1" in user_p
    assert "Level 2" in user_p


def test_question_generation_prompt_all_levels_in_rubric() -> None:
    sys_p, _ = render_question_generation_prompt("content")
    for level in range(1, 7):
        assert f"Level {level}" in sys_p


def test_question_generation_prompt_no_code_fence() -> None:
    """Prompt must instruct model NOT to use markdown code fences."""
    sys_p, _ = render_question_generation_prompt("content")
    assert "code fence" in sys_p.lower() or "markdown" in sys_p.lower()


def test_question_generation_prompt_no_hallucination_instruction() -> None:
    """Prompt must ground questions in the document."""
    sys_p, _ = render_question_generation_prompt("content")
    assert "document" in sys_p.lower()


# ---------------------------------------------------------------------------
# Hydra sub-question prompt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parent_difficulty,expected_target", [
    (3, 2),
    (2, 1),
    (1, 1),  # clamped at 1
])
def test_hydra_prompt_target_difficulty(parent_difficulty: int, expected_target: int) -> None:
    target = max(1, parent_difficulty - 1)
    assert target == expected_target
    sys_p, user_p = render_hydra_prompt(
        parent_body="Explain photosynthesis.",
        parent_difficulty=parent_difficulty,
        student_answer="I don't know.",
        diagnosis="Student lacks basic understanding of plant metabolism.",
        target_difficulty=target,
    )
    assert str(target) in sys_p
    assert str(target) in user_p


def test_hydra_prompt_includes_diagnosis() -> None:
    _, user_p = render_hydra_prompt(
        parent_body="Q",
        parent_difficulty=3,
        student_answer="A",
        diagnosis="Gap in understanding of osmosis.",
        target_difficulty=2,
    )
    assert "osmosis" in user_p


def test_hydra_prompt_includes_parent_question() -> None:
    _, user_p = render_hydra_prompt(
        parent_body="What is cellular respiration?",
        parent_difficulty=3,
        student_answer="It's breathing.",
        diagnosis="Confused respiration with breathing.",
        target_difficulty=2,
    )
    assert "cellular respiration" in user_p


def test_hydra_prompt_json_format_specified() -> None:
    sys_p, _ = render_hydra_prompt(
        parent_body="Q",
        parent_difficulty=2,
        student_answer="A",
        diagnosis="D",
        target_difficulty=1,
    )
    assert "JSON" in sys_p
    assert "ideal_answer" in sys_p


def test_hydra_prompt_exactly_3_questions_specified() -> None:
    sys_p, _ = render_hydra_prompt(
        parent_body="Q",
        parent_difficulty=2,
        student_answer="A",
        diagnosis="D",
        target_difficulty=1,
    )
    assert "3" in sys_p


# ---------------------------------------------------------------------------
# Scoring prompt
# ---------------------------------------------------------------------------


def test_scoring_prompt_structure() -> None:
    sys_p, user_p = render_scoring_prompt(
        question_body="What is Newton's second law?",
        difficulty=2,
        answer_text="Force equals mass times acceleration.",
    )
    assert "score" in sys_p.lower()
    assert "0" in sys_p and "10" in sys_p
    assert "Newton's second law" in user_p
    assert "Force equals mass times acceleration" in user_p


def test_scoring_prompt_no_time_info() -> None:
    """Scoring prompt must NOT mention time taken or time penalties."""
    sys_p, user_p = render_scoring_prompt(
        question_body="Q", difficulty=1, answer_text="A"
    )
    combined = (sys_p + user_p).lower()
    assert "time_taken" not in combined
    assert "time penalty" not in combined


def test_scoring_prompt_no_ideal_answer() -> None:
    """Ideal answer must NOT be passed to the scorer."""
    sys_p, user_p = render_scoring_prompt(
        question_body="Q", difficulty=1, answer_text="A"
    )
    combined = sys_p + user_p
    # The ideal answer isn't an input to this function at all
    assert "ideal_answer" not in user_p


def test_scoring_prompt_includes_difficulty_label() -> None:
    from scathach.core.question import DifficultyLevel
    sys_p, _ = render_scoring_prompt(
        question_body="Q", difficulty=1, answer_text="A"
    )
    assert DifficultyLevel.from_int(1).label in sys_p


def test_scoring_prompt_json_format_specified() -> None:
    sys_p, _ = render_scoring_prompt(
        question_body="Q", difficulty=3, answer_text="A"
    )
    assert "JSON" in sys_p
    assert "diagnosis" in sys_p


@pytest.mark.parametrize("difficulty", range(1, 7))
def test_scoring_prompt_all_difficulties(difficulty: int) -> None:
    sys_p, user_p = render_scoring_prompt(
        question_body="Test question?",
        difficulty=difficulty,
        answer_text="Test answer.",
    )
    assert str(difficulty) in user_p
    assert len(sys_p) > 100
