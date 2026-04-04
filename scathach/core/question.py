"""
Core domain types for scathach.

DifficultyLevel — enum for the 6 question difficulty tiers, carrying time limits and metadata.
TimingMode      — whether a session or review is timed or untimed.
TimerZone       — runtime state of the dual-zone countdown timer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TimingMode(str, Enum):
    """Whether a session or review attempt is run under a timer."""
    TIMED = "timed"
    UNTIMED = "untimed"


class TimerZone(str, Enum):
    """
    Runtime state of the dual-zone countdown timer.

    NORMAL  — user is within the base time limit t.
    PENALTY — user has exceeded t but not 2t; score will be halved.
    EXPIRED — user has exceeded 2t; auto-fail triggered.
    """
    NORMAL = "normal"
    PENALTY = "penalty"
    EXPIRED = "expired"


@dataclass(frozen=True)
class DifficultyMeta:
    """Metadata associated with a single difficulty level."""
    level: int           # 1–6
    label: str           # human-readable label
    time_limit_s: int    # base time limit t in seconds (0 = untimed/no limit)
    answer_descriptor: str  # expected answer format
    document_coverage: str # How much of the document's meaning the question covers


class DifficultyLevel(Enum):
    """
    The six difficulty tiers used for scathach questions.

    Each member carries a DifficultyMeta payload with timing and format info.
    The integer value (1–6) is used for DB storage.
    """

    EASY_SHORT   = DifficultyMeta(1, "short answer",  30,   "Single word or phrase",    "A narrow fact or definition")
    HARD_SHORT   = DifficultyMeta(2, "short answer",  60,   "One to two sentences",     "A key concept or relationship")
    EASY_PARA    = DifficultyMeta(3, "paragraph",     300,  "One paragraph",            "A single section or theme")
    HARD_PARA    = DifficultyMeta(4, "paragraph",     600,  "One to two paragraphs",    "Multiple related concepts. May require general outside knowledge.")
    EASY_LONG    = DifficultyMeta(5, "long answer",   900,  "Multiple paragraphs",      "A major argument or framework. Requires showing some degree of outside domain knowledge.")
    HARD_LONG    = DifficultyMeta(6, "long answer",   1800, "Comprehensive essay",      "Integrates the whole document. Requires deep domain expertise.")

    # Convenience accessors so call-sites don't have to dig into .value
    @property
    def level(self) -> int:
        return self.value.level

    @property
    def label(self) -> str:
        return self.value.label

    @property
    def time_limit_s(self) -> int:
        return self.value.time_limit_s

    @property
    def answer_descriptor(self) -> str:
        return self.value.answer_descriptor

    @property
    def penalty_limit_s(self) -> int:
        """The auto-fail threshold: 2 × base time limit."""
        return self.time_limit_s * 2

    def timer_zone(self, elapsed_s: float) -> TimerZone:
        """Return which timing zone the elapsed time falls into."""
        t = self.time_limit_s
        if elapsed_s <= t:
            return TimerZone.NORMAL
        if elapsed_s <= self.penalty_limit_s:
            return TimerZone.PENALTY
        return TimerZone.EXPIRED

    @classmethod
    def from_int(cls, level: int) -> "DifficultyLevel":
        """Look up a DifficultyLevel by its integer value (1–6)."""
        for member in cls:
            if member.level == level:
                return member
        raise ValueError(f"No DifficultyLevel with level={level!r}. Must be 1–6.")

    @classmethod
    def levels_up_to(cls, max_level: int) -> list["DifficultyLevel"]:
        """Return all difficulty levels from 1 to max_level, inclusive."""
        return [d for d in cls if d.level <= max_level]
