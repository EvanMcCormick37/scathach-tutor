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
HYDRA_PROMPT_VERSION = "2.0"
SCORING_PROMPT_VERSION = "2.0"
DRILL_PROMPT_VERSION = "1.0"

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
revealing specific conceptual gaps. Your task is to generate a targeted set of 1–5 \
sub-questions that are collectively necessary and sufficient for the student to build the \
understanding required to answer the original question.

You have full latitude to choose:
  - How many sub-questions to generate (between 1 and 5, inclusive).
  - What difficulty level to assign each sub-question (any level strictly below the \
original question's level).

Design your sub-question set as a minimal diagnostic scaffold: ask precisely what the student \
needs to understand, nothing more. If the diagnosis reveals that the student is missing a \
single basic definition that underlies the whole answer, one level-1 question may be \
sufficient. If the gaps are broader or span multiple concepts, include questions at \
multiple levels.

Difficulty rubric for the levels available to you:
{rubric}

For each sub-question you MUST also provide an ideal 10/10 answer whose length and depth \
match the assigned difficulty level.

Respond with ONLY a valid JSON array of 1–5 objects:
[
  {{
    "difficulty": <integer strictly less than {parent_difficulty}>,
    "body": "<the sub-question text>",
    "ideal_answer": "<the ideal answer text>"
  }},
  ...
]

Do not include any text outside the JSON array. Do not use markdown code fences. \
Every sub-question must have difficulty strictly less than {parent_difficulty}."""

_HYDRA_USER = """\
Original question (difficulty {parent_difficulty} — {parent_label}):
{parent_body}

Student's answer:
{student_answer}

Diagnosis of conceptual gaps:
{diagnosis}
{prior_section}
Generate 1–5 sub-questions at difficulty levels 1–{max_target} that are collectively \
necessary and sufficient to give the student what they need to answer the original question."""

_HYDRA_PRIOR_SECTION = """\

The following questions already exist for this document at levels 1–{max_target} — \
DO NOT repeat or closely paraphrase any of them:
{per_level_blocks}
"""

_HYDRA_PRIOR_LEVEL_BLOCK = """\
[Level {level} — {label}]
{bodies}"""


def _format_hydra_prior_questions(existing: "list[Question]") -> str:
    """Format existing sub-level questions into a grouped deduplication block."""
    from collections import defaultdict
    by_level: dict[int, list[str]] = defaultdict(list)
    for q in existing:
        by_level[q.difficulty].append(q.body)

    blocks: list[str] = []
    for level in sorted(by_level):
        dl = DifficultyLevel.from_int(level)
        bodies = "\n".join(f"- {body}" for body in by_level[level])
        blocks.append(_HYDRA_PRIOR_LEVEL_BLOCK.format(
            level=level, label=dl.label, bodies=bodies
        ))
    return blocks


def render_hydra_prompt(
    parent_body: str,
    parent_difficulty: int,
    student_answer: str,
    diagnosis: str,
    existing_questions: "list[Question] | None" = None,
) -> tuple[str, str]:
    """
    Render the Hydra sub-question generation prompts.

    The LLM selects both the number (1–5) and difficulty levels of sub-questions.
    All sub-questions must have difficulty strictly less than parent_difficulty.

    Args:
        parent_body:        The question the student failed.
        parent_difficulty:  Difficulty of the parent question (1–6).
        student_answer:     The student's failing answer text.
        diagnosis:          LLM-generated diagnosis of conceptual gaps.
        existing_questions: All questions for this topic with difficulty <
                            parent_difficulty; embedded for deduplication.

    Returns:
        (system_prompt, user_prompt)
    """
    max_target = parent_difficulty - 1
    rubric = "\n".join(
        f"  Level {d.level} ({d.label}): {d.answer_descriptor}. "
        f"Time limit: {d.time_limit_s // 60} min {d.time_limit_s % 60} s."
        for d in DifficultyLevel
        if d.level < parent_difficulty
    )
    parent_label = DifficultyLevel.from_int(parent_difficulty).label

    system = _HYDRA_SYSTEM.format(
        rubric=rubric,
        parent_difficulty=parent_difficulty,
    )

    prior_section = ""
    if existing_questions:
        level_blocks = _format_hydra_prior_questions(existing_questions)
        if level_blocks:
            prior_section = _HYDRA_PRIOR_SECTION.format(
                max_target=max_target,
                per_level_blocks="\n".join(level_blocks),
            )

    user = _HYDRA_USER.format(
        parent_difficulty=parent_difficulty,
        parent_label=parent_label,
        parent_body=parent_body,
        student_answer=student_answer,
        diagnosis=diagnosis,
        max_target=max_target,
        prior_section=prior_section,
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 3: Answer Scoring
# ---------------------------------------------------------------------------

_SCORING_SYSTEM = """\
You are a harsh but fair academic evaluator. Your task is to score a student's answer \
to the given question on a scale of 0 to 10.

Scoring criteria:
  - Accuracy: Is the content factually correct?
  - Completeness: Does the answer address all key aspects relative to difficulty level {difficulty} ({difficulty_label})?
  - Clarity: Is the answer clearly expressed?

Expected answer format at this difficulty level: {answer_descriptor}, {document_coverage}.

A score of 0 means completely wrong or no meaningful attempt. E.g. "IDK" or a blank answer.
A score of 10 means an ideal answer which demonstrates deep and thourough understanding of the material.

Most answers will not be worth 0 or 10 points, but will fall somewhere in between. Use your judgment to determine the appropriate score for the user's answer.

Respond with ONLY a valid JSON object:
{{
  "score": <integer 0–10>,
  "diagnosis": "<1–2 sentences describing conceptual gaps or strengths — present even for passing answers>"
}}

Do not include any text outside the JSON object. Do not use markdown code fences."""

_SCORING_USER = """\
{context_section}Question (difficulty {difficulty}):
{question_body}

Student's answer:
{answer_text}

Score this answer."""

_SCORING_DOCUMENT_SECTION = """\
Reference document:
---
{document_content}
---

"""

_SCORING_IDEAL_ANSWER_SECTION = """\
Reference answer:
{ideal_answer}

"""


def render_scoring_prompt(
    question_body: str,
    difficulty: int,
    answer_text: str,
    document_content: "str | None" = None,
    ideal_answer: "str | None" = None,
) -> tuple[str, str]:
    """
    Render the answer scoring prompts.

    Exactly one of `document_content` or `ideal_answer` should be supplied:
    - Sessions and drills pass `document_content` so the scorer can verify
      factual accuracy against the source material.
    - Reviews pass `ideal_answer` so the scorer has a reference to compare
      against without needing the full document in context.

    NOTE: Time taken and time penalty are NOT passed to the scorer. Timing is
    a mechanical post-processing step applied in scoring.py after the LLM returns.

    Args:
        question_body:     The question text.
        difficulty:        Difficulty level (1–6).
        answer_text:       The student's answer.
        document_content:  Full source document (session / drill only).
        ideal_answer:      Ideal answer text (review only).

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

    if document_content is not None:
        context_section = _SCORING_DOCUMENT_SECTION.format(
            document_content=document_content
        )
    elif ideal_answer is not None:
        context_section = _SCORING_IDEAL_ANSWER_SECTION.format(
            ideal_answer=ideal_answer
        )
    else:
        context_section = ""

    user = _SCORING_USER.format(
        context_section=context_section,
        difficulty=difficulty,
        question_body=question_body,
        answer_text=answer_text,
    )
    return system, user


# ---------------------------------------------------------------------------
# Prompt 4: Drill Question Generation
# ---------------------------------------------------------------------------

_DRILL_SYSTEM = """\
You are an expert academic tutor. Your task is to generate exactly {count} open-ended questions \
from the provided document, all at difficulty level {level} ({label}): {answer_descriptor}.

The {count} questions must cover distinct aspects of the material — do not repeat or closely \
paraphrase each other. Each question must be independently answerable.

For each question you MUST also provide an ideal 10/10 answer whose length and depth \
match difficulty level {level}.

Respond with ONLY a valid JSON array of exactly {count} objects:
[
  {{
    "difficulty": {level},
    "body": "<the question text>",
    "ideal_answer": "<the ideal answer text>"
  }},
  ...
]

Do not include any text outside the JSON array. Do not use markdown code fences. \
All questions must be grounded in the document content. \
Every object must have "difficulty": {level}."""

_DRILL_USER = """\
Document content:
---
{document_content}
---
{prior_section}
Generate {count} level-{level} questions with their ideal answers."""


def render_drill_prompt(
    document_content: str,
    level: int,
    count: int,
    prior_questions: "list[Question] | None" = None,
) -> tuple[str, str]:
    """
    Render drill question generation prompts: `count` questions all at `level`.

    Args:
        document_content: The document text to generate questions from.
        level:            Difficulty level (1–6) for all questions.
        count:            Number of questions to generate.
        prior_questions:  Existing questions at this level; embedded for deduplication.

    Returns:
        (system_prompt, user_prompt)
    """
    dl = DifficultyLevel.from_int(level)
    system = _DRILL_SYSTEM.format(
        count=count,
        level=level,
        label=dl.label,
        answer_descriptor=dl.answer_descriptor,
    )
    prior_section = _format_prior_questions(prior_questions) if prior_questions else ""
    user = _DRILL_USER.format(
        document_content=document_content,
        count=count,
        level=level,
        prior_section=prior_section,
    )
    return system, user
