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


QWEN_36_PLUS = ProviderConfig(
    model_id="qwen/qwen3.6-plus:free",
    display_name="Qwen 3.6 Plus (Free)",
    max_tokens=8192,
    temperature=0.3,
    is_free=True,
)

KIMI_K2 = ProviderConfig(
    model_id="moonshotai/kimi-k2",
    display_name="Kimi K2 (Moonshot AI)",
    max_tokens=8192,
    temperature=0.3,
    is_free=True,
)

ARCEE_BLAZE = ProviderConfig(
    model_id="arcee-ai/arcee-blaze",
    display_name="Arcee Blaze",
    max_tokens=4096,
    temperature=0.3,
    is_free=True,
)

GEMINI_31_FLASH_LITE = ProviderConfig(
    model_id="google/gemini-3.1-flash-lite-preview",
    display_name="Gemini 3.1 Flash Lite Preview (Google)",
    max_tokens=8192,
    temperature=0.3,
    is_free=True,
)

GEMINI_31_PRO_PREVIEW = ProviderConfig(
    model_id="google/gemini-3.1-pro-preview",
    display_name="Gemini 3.1 Pro Preview (Google)",
    max_tokens=8192,
    temperature=0.3,
    is_free=False,
)

ALL_PROVIDERS: dict[str, ProviderConfig] = {
    p.model_id: p
    for p in [QWEN_36_PLUS, KIMI_K2, ARCEE_BLAZE, GEMINI_31_FLASH_LITE, GEMINI_31_PRO_PREVIEW]
}


def get_provider(model_id: str) -> ProviderConfig:
    """
    Look up a provider by model_id.
    Falls back to a generic config if the model is not in ALL_PROVIDERS,
    so users can supply arbitrary OpenRouter model strings.
    """
    if model_id in ALL_PROVIDERS:
        return ALL_PROVIDERS[model_id]
    return ProviderConfig(
        model_id=model_id,
        display_name=model_id,
        max_tokens=4096,
        temperature=0.3,
        is_free=False,
    )


def get_default_provider() -> ProviderConfig:
    """Return the provider config for the model set in settings."""
    from scathach.config import settings  # local import avoids circular dependency
    return get_provider(settings.model)
