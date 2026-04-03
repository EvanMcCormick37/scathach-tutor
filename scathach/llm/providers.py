"""
LLM provider configurations for OpenRouter.

All providers use the OpenAI-compatible API format.
The active provider is selected via config; the client is provider-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for a single LLM provider available on OpenRouter."""
    model_id: str        # OpenRouter model identifier
    display_name: str    # Human-readable label
    max_tokens: int      # Recommended max output tokens for this model
    temperature: float   # Default temperature for this model
    is_free: bool        # Whether this model is on the free tier


# Primary provider — Kimi-K2 (strong reasoning, generous free tier)
KIMI_K2 = ProviderConfig(
    model_id="moonshotai/kimi-k2",
    display_name="Kimi K2 (Moonshot AI)",
    max_tokens=8192,
    temperature=0.3,
    is_free=True,
)

# Secondary — Arcee Blaze
ARCEE_BLAZE = ProviderConfig(
    model_id="arcee-ai/arcee-blaze",
    display_name="Arcee Blaze",
    max_tokens=4096,
    temperature=0.3,
    is_free=True,
)

# Fallback — Gemini Flash 1.5 (very generous free tier)
GEMINI_FLASH = ProviderConfig(
    model_id="google/gemini-flash-1.5",
    display_name="Gemini Flash 1.5 (Google)",
    max_tokens=8192,
    temperature=0.3,
    is_free=True,
)

# All providers indexed by model_id for quick lookup
ALL_PROVIDERS: dict[str, ProviderConfig] = {
    p.model_id: p for p in [KIMI_K2, ARCEE_BLAZE, GEMINI_FLASH]
}

DEFAULT_PROVIDER = KIMI_K2


def get_provider(model_id: str) -> ProviderConfig:
    """
    Look up a provider by model_id.
    Falls back to a generic config if the model is not in ALL_PROVIDERS,
    so users can supply arbitrary OpenRouter model strings.
    """
    if model_id in ALL_PROVIDERS:
        return ALL_PROVIDERS[model_id]
    # Generic fallback for unknown model strings
    return ProviderConfig(
        model_id=model_id,
        display_name=model_id,
        max_tokens=4096,
        temperature=0.3,
        is_free=False,
    )
