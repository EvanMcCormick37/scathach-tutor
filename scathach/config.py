"""
Application configuration via pydantic-settings.
Settings are read from environment variables or a .env file.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Re-export so callers can import TimingMode from either config or core.question
from scathach.core.question import TimingMode  # noqa: F401 — re-exported for convenience


class ModelProvider(str, Enum):
    KIMI_K2 = "moonshotai/kimi-k2"
    ARCEE_BLAZE = "arcee-ai/arcee-blaze"
    GEMINI_FLASH = "google/gemini-flash-1.5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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
        default=ModelProvider.KIMI_K2.value,
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
    main_timing: TimingMode = Field(
        default=TimingMode.UNTIMED,
        description="Default timing mode for learning sessions (timed or untimed).",
    )
    review_timing: TimingMode = Field(
        default=TimingMode.UNTIMED,
        description="Default timing mode for review sessions (timed or untimed).",
    )
    hydra_in_super_review: bool = Field(
        default=False,
        description="Whether the Hydra Protocol is enabled during super-review sessions.",
    )
    open_doc_on_session: bool = Field(
        default=False,
        description="Whether to open the source document in the system viewer at session start.",
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
