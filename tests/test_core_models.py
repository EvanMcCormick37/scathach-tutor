"""
Tests for core data model enums: DifficultyLevel, TimingMode, TimerZone.
"""

from __future__ import annotations

import pytest

from scathach.core.question import DifficultyLevel, TimerZone, TimingMode


# ---------------------------------------------------------------------------
# TimingMode
# ---------------------------------------------------------------------------


def test_timing_mode_values() -> None:
    assert TimingMode.TIMED.value == "timed"
    assert TimingMode.UNTIMED.value == "untimed"


# ---------------------------------------------------------------------------
# DifficultyLevel metadata
# ---------------------------------------------------------------------------


def test_all_six_levels_exist() -> None:
    levels = [d.level for d in DifficultyLevel]
    assert sorted(levels) == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize(
    "level, expected_t, expected_2t",
    [
        (1, 30, 60),
        (2, 60, 120),
        (3, 300, 600),
        (4, 600, 1200),
        (5, 900, 1800),
        (6, 1800, 3600),
    ],
)
def test_time_limits(level: int, expected_t: int, expected_2t: int) -> None:
    d = DifficultyLevel.from_int(level)
    assert d.time_limit_s == expected_t
    assert d.penalty_limit_s == expected_2t


def test_from_int_valid() -> None:
    d = DifficultyLevel.from_int(3)
    assert d.level == 3
    assert "paragraph" in d.label.lower()


def test_from_int_invalid() -> None:
    with pytest.raises(ValueError):
        DifficultyLevel.from_int(0)
    with pytest.raises(ValueError):
        DifficultyLevel.from_int(7)


def test_levels_up_to() -> None:
    subset = DifficultyLevel.levels_up_to(3)
    assert [d.level for d in subset] == [1, 2, 3]


def test_levels_up_to_all() -> None:
    assert len(DifficultyLevel.levels_up_to(6)) == 6


def test_answer_descriptors_non_empty() -> None:
    for d in DifficultyLevel:
        assert d.answer_descriptor != ""


# ---------------------------------------------------------------------------
# TimerZone via DifficultyLevel.timer_zone()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level, elapsed, expected_zone",
    [
        (1, 0.0,   TimerZone.NORMAL),   # 0s < 30s
        (1, 29.9,  TimerZone.NORMAL),   # just under t
        (1, 30.0,  TimerZone.NORMAL),   # exactly at t is still NORMAL (not yet over)
        (1, 30.1,  TimerZone.PENALTY),  # just over t
        (1, 59.9,  TimerZone.PENALTY),  # just under 2t
        (1, 60.0,  TimerZone.PENALTY),  # exactly at 2t → still PENALTY
        (1, 60.01, TimerZone.EXPIRED),  # just over 2t
        (3, 299.0, TimerZone.NORMAL),   # level 3, under 300s
        (3, 301.0, TimerZone.PENALTY),  # level 3, over 300s
        (3, 601.0, TimerZone.EXPIRED),  # level 3, over 600s
    ],
)
def test_timer_zone(level: int, elapsed: float, expected_zone: TimerZone) -> None:
    d = DifficultyLevel.from_int(level)
    assert d.timer_zone(elapsed) == expected_zone


# ---------------------------------------------------------------------------
# TimerZone enum
# ---------------------------------------------------------------------------


def test_timer_zone_values() -> None:
    assert TimerZone.NORMAL.value == "normal"
    assert TimerZone.PENALTY.value == "penalty"
    assert TimerZone.EXPIRED.value == "expired"
