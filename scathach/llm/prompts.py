"""
All LLM prompt templates for scathach.

Prompts are treated as first-class artifacts and version-pinned.
Changing a prompt version triggers re-testing against fixed example documents.

Prompt versions are stored as module-level constants so changes are traceable
via git history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scathach.core.question import DifficultyLevel

if TYPE_CHECKING:
    from scathach.db.models import Question

# ---------------------------------------------------------------------------
# Prompt versions — bump these when you change the corresponding prompt body
# ---------------------------------------------------------------------------

QUESTION_GENERATION_PROMPT_VERSION = "1.0"
HYDRA_PROMPT_VERSION = "1.0"
SCORING_PROMPT_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Difficulty rubric — embedded into the question generation prompt
# ---------------------------------------------------------------------------

_DIFFICULTY_RUBRIC = "\n".join(
    f"  Level {d.level} ({d.label}): {d.answer_descriptor}. "
    f"Time limit: {d.time_limit_s // 60} min {d.time_limit_s % 60} s "
    f"({d.time_limit_s} seconds)."
    for d in DifficultyLevel
)

# Few-shot examples embedded into the generation prompt to anchor difficulty calibration
_FEW_SHOT_EXAMPLES = """
Examples of well-formed questions at each level:
  Level 1: "What is the SI unit of force?" → ideal_answer: "Newton (N)"
  Level 2: "State Newton's Second Law of Motion in one sentence." → ideal_answer: "Force equals mass times acceleration (F = ma)."
  Level 3: "Explain the difference between static and kinetic friction." → ideal_answer: [one paragraph explaining both types, their relationship, and typical coefficient ranges]
  Level 4: "Describe how Newton's Three Laws of Motion interact to explain the motion of a rocket in space." → ideal_answer: [one to two paragraphs connecting all three laws to the specific context]
  Level 5: "Compare and contrast the Newtonian and Lagrangian formulations of classical mechanics, including their practical applications." → ideal_answer: [multiple paragraphs with mathematical context]
  Level 6: "Synthesise the role of symmetry principles in both classical mechanics and special relativity, tracing how Noether's theorem underpins conservation laws in both frameworks." → ideal_answer: [comprehensive essay-length treatment]
"""

# ---------------------------------------------------------------------------
# Prompt 1: Question Generation
# ---------------------------------------------------------------------------

_QUESTION_GENERATION_SYSTEM = """\
You are an academic tutor. Your task is to generate exactly 6 open-ended questions \
from the provided document — one for each difficulty level 1 through 6 — that test deep \
understanding of the material.

Difficulty rubric:
{rubric}

{few_shot}

For each question you MUST also provide an ideal 10/10 answer that a top student would give. \
The ideal answer length MUST match the difficulty level's expected format as described above.

Respond with ONLY a valid JSON array of exactly 6 objects in the following format:
[
  {{
    "difficulty": <integer 1–6>,
    "body": "<the question text>",
    "ideal_answer": "<the ideal answer text>"
  }},
  ...
]

Do not include any text outside the JSON array. Do not use markdown code fences. \
Questions must be grounded in the provided document content — do not invent facts."""

_QUESTION_GENERATION_USER = """\
Document content:
---
{document_content}
---
{prior_section}
Generate 6 questions (one per difficulty level 1–6) with their ideal answers."""

_PRIOR_QUESTIONS_SECTION = """\

Previously asked questions about this document — do NOT repeat or closely \
paraphrase any of the following:
{per_level_blocks}
"""

_PRIOR_LEVEL_BLOCK = """\
[Level {level} — {label}]
{bodies}"""


def _format_prior_questions(prior_questions: "list[Question]") -> str:
    """Format prior questions into a deduplication block grouped by difficulty."""
    from collections import defaultdict
    by_level: dict[int, list[str]] = defaultdict(list)
    for q in prior_questions:
        by_level[q.difficulty].append(q.body)

    blocks: list[str] = []
    for level in sorted(by_level):
        dl = DifficultyLevel.from_int(level)
        bodies = "\n".join(f"- {body}" for body in by_level[level])
        blocks.append(_PRIOR_LEVEL_BLOCK.format(
            level=level, label=dl.label, bodies=bodies
        ))
    return _PRIOR_QUESTIONS_SECTION.format(per_level_blocks="\n".join(blocks))


def render_question_generation_prompt(
    document_content: str,
    prior_questions: "list[Question] | None" = None,
) -> tuple[str, str]:
    """
    Render the question generation prompts.

    Args:
        document_content:  The document text to generate questions from.
        prior_questions:   Previously asked root questions for this topic; if
                           provided they are embedded in the prompt so the LLM
                           avoids repeating them. Up to 25 per level recommended.

    Returns:
        (system_prompt, user_prompt)
    """
    system = _QUESTION_GENERATION_SYSTEM.format(
        rubric=_DIFFICULTY_RUBRIC,
        few_shot=_FEW_SHOT_EXAMPLES,
    )
    prior_section = _format_prior_questions(prior_questions) if prior_questions else ""
    user = _QUESTION_GENERATION_USER.format(
        document_content=document_content,
        prior_section=prior_section,
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 2: Hydra Sub-question Generation
# ---------------------------------------------------------------------------

_HYDRA_SYSTEM = """\
You are a rigorous academic tutor. A student has failed to adequately answer a question, \
revealing specific conceptual gaps. Your task is to generate exactly {count} targeted sub-questions \
that directly address those gaps, helping the student build the foundational understanding \
needed to answer the original question.

Sub-question difficulty level: {target_difficulty} ({target_label})
Expected answer format at this level: {answer_descriptor}

For each sub-question you MUST also provide an ideal 10/10 answer.

Respond with ONLY a valid JSON array of exactly {count} objects:
[
  {{
    "difficulty": {target_difficulty},
    "body": "<the sub-question text>",
    "ideal_answer": "<the ideal answer text>"
  }},
  ...
]

Do not include any text outside the JSON array. Do not use markdown code fences. \
All sub-questions must directly address the diagnosed conceptual gaps."""

_HYDRA_USER = """\
Original question (difficulty {parent_difficulty}):
{parent_body}

Student's answer:
{student_answer}

Diagnosis of conceptual gaps:
{diagnosis}
{prior_section}
Generate {count} sub-questions at difficulty level {target_difficulty} that address these gaps."""

_HYDRA_PRIOR_SECTION = """\

The following level {target_difficulty} questions already exist for this document — \
DO NOT repeat or closely paraphrase any of them:
{bodies}
"""


def render_hydra_prompt(
    parent_body: str,
    parent_difficulty: int,
    student_answer: str,
    diagnosis: str,
    target_difficulty: int,
    existing_questions: "list[Question] | None" = None,
    count: int = 3,
) -> tuple[str, str]:
    """
    Render the Hydra sub-question generation prompts.

    Args:
        parent_body:        The question the student failed.
        parent_difficulty:  Difficulty of the parent question (1–6).
        student_answer:     The student's failing answer text.
        diagnosis:          LLM-generated diagnosis of conceptual gaps.
        target_difficulty:  Difficulty for sub-questions (max(1, parent - 1)).
        existing_questions: All questions for this topic at `target_difficulty`
                            (root and sub); embedded so the LLM avoids duplicates
                            across the entire document, not just this parent.
        count:              Number of sub-questions to request from the LLM.

    Returns:
        (system_prompt, user_prompt)
    """
    dl = DifficultyLevel.from_int(target_difficulty)
    system = _HYDRA_SYSTEM.format(
        count=count,
        target_difficulty=target_difficulty,
        target_label=dl.label,
        answer_descriptor=dl.answer_descriptor,
    )
    prior_section = ""
    if existing_questions:
        bodies = "\n".join(f"- {q.body}" for q in existing_questions)
        prior_section = _HYDRA_PRIOR_SECTION.format(
            target_difficulty=target_difficulty, bodies=bodies
        )
    user = _HYDRA_USER.format(
        count=count,
        parent_difficulty=parent_difficulty,
        parent_body=parent_body,
        student_answer=student_answer,
        diagnosis=diagnosis,
        target_difficulty=target_difficulty,
        prior_section=prior_section,
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 3: Answer Scoring
# ---------------------------------------------------------------------------

_SCORING_SYSTEM = """\
You are a strict but fair academic evaluator. Your task is to score a student's answer \
to the given question on a scale of 0 to 10.

Scoring criteria:
  - Accuracy: Is the content factually correct?
  - Completeness: Does the answer address all key aspects relative to difficulty level {difficulty} ({difficulty_label})?
  - Clarity: Is the answer clearly expressed?

Expected answer format at this difficulty level: {answer_descriptor}, {document_coverage}.

A score of 0 means completely wrong or no meaningful attempt. E.g. "IDK" or a blank answer.
A score of 10 means an ideal answer which demonstrates deep and thourough understanding of the material.

Most answers will not be worth 0 or 10 points, but will fall somewhere in between. Use your judgment to determine the score for the user's answer.

Respond with ONLY a valid JSON object:
{{
  "score": <integer 0–10>,
  "diagnosis": "<1–2 sentences describing conceptual gaps or strengths — present even for passing answers>"
}}

Do not include any text outside the JSON object. Do not use markdown code fences."""

_SCORING_USER = """\
Question (difficulty {difficulty}):
{question_body}

Student's answer:
{answer_text}

Score this answer."""


def render_scoring_prompt(
    question_body: str,
    difficulty: int,
    answer_text: str,
) -> tuple[str, str]:
    """
    Render the answer scoring prompts.

    NOTE: The ideal_answer is NOT passed to the scorer — it is stored on the
    question row and retrieved by the application only on failure. The scorer
    evaluates purely on the answer's own merits.

    NOTE: Time taken and time penalty are NOT passed to the scorer. Timing is
    a mechanical post-processing step applied in scoring.py after the LLM returns.

    Args:
        question_body:  The question text.
        difficulty:     Difficulty level (1–6).
        answer_text:    The student's answer.

    Returns:
        (system_prompt, user_prompt)
    """
    dl = DifficultyLevel.from_int(difficulty)
    system = _SCORING_SYSTEM.format(
        difficulty=difficulty,
        difficulty_label=dl.label,
        answer_descriptor=dl.answer_descriptor,
        document_coverage=dl.document_coverage,
    )
    user = _SCORING_USER.format(
        difficulty=difficulty,
        question_body=question_body,
        answer_text=answer_text,
    )
    return system, user
