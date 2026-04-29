"""
Application configuration via pydantic-settings.
Settings are read from environment variables or a .env file located at
~/.scathach/.env so they persist correctly when running as a packaged binary.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Re-export so callers can import TimingMode from either config or core.question
from scathach.core.question import TimingMode  # noqa: F401 — re-exported for convenience

# User-writable config directory — same location as the database.
CONFIG_DIR = Path("~/.scathach").expanduser()
ENV_FILE = CONFIG_DIR / ".env"

class OnFailedReview(str, Enum):
    REPEAT = "repeat"   # always repeat the question immediately
    SKIP = "skip"       # never repeat, let FSRS reschedule
    CHOOSE = "choose"   # ask the user each time


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        env_prefix="SCATHACH_",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM API
    openrouter_api_key: str = Field(
        default="",
        description="Your OpenRouter API key. Get one free at https://openrouter.ai",
    )
    model: str = Field(
        default="google/gemini-3.1-flash-lite-preview",
        description="The LLM model identifier to use via OpenRouter.",
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter API base URL.",
    )

    # Session defaults
    quality_threshold: int = Field(
        default=7,
        ge=5,
        le=10,
        description="Minimum score (0–10) for a question to be considered passed.",
    )
    timing: TimingMode = Field(
        default=TimingMode.UNTIMED,
        description="Default timing mode for all sessions and reviews (timed or untimed).",
    )
    hydra_in_review: bool = Field(
        default=False,
        description="Whether the Hydra Protocol is enabled during long-answer and topic reviews.",
    )
    hydra_in_drill: bool = Field(
        default=True,
        description="Whether the Hydra Protocol is enabled during drill sessions.",
    )
    on_failed_review: OnFailedReview = Field(
        default=OnFailedReview.CHOOSE,
        description=(
            "What to do when a review question is failed: "
            "'repeat' always re-queues it immediately, "
            "'skip' leaves rescheduling to FSRS, "
            "'choose' prompts you each time."
        ),
    )
    max_practice_support: float = Field(
        default=14.0,
        description="Maximum days of review interval contributed by practice (open-book) sessions.",
    )

    # Database
    db_path: Path = Field(
        default=Path("~/.scathach/scathach.db"),
        description="Path to the SQLite database file.",
    )

    @field_validator("db_path", mode="before")
    @classmethod
    def expand_db_path(cls, v: object) -> Path:
        return Path(str(v)).expanduser()

    @field_validator("quality_threshold", mode="before")
    @classmethod
    def validate_threshold(cls, v: object) -> int:
        val = int(str(v))
        if not 5 <= val <= 10:
            raise ValueError("quality_threshold must be between 5 and 10")
        return val


# Singleton — import this throughout the app
settings = Settings()
