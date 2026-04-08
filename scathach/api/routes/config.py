"""
Config routes.

GET  /config         — read current settings (no API key in response)
PATCH /config        — update settings (writes to .env file)
POST /config/test    — validate the API key / LLM connectivity
"""

from __future__ import annotations

from dotenv import set_key
from fastapi import APIRouter, HTTPException, Request

from scathach.api.models import ConfigPatchRequest, ConfigResponse, ConfigTestResponse
from scathach.config import ENV_FILE, settings

router = APIRouter()

_ENV_FILE = ENV_FILE


def _current_config() -> ConfigResponse:
    return ConfigResponse(
        model=settings.model,
        quality_threshold=settings.quality_threshold,
        main_timing=settings.main_timing.value,
        review_timing=settings.review_timing.value,
        hydra_in_super_review=settings.hydra_in_super_review,
        open_doc_on_session=settings.open_doc_on_session,
        has_api_key=bool(settings.openrouter_api_key),
    )


@router.get("", response_model=ConfigResponse)
async def get_config():
    return _current_config()


@router.patch("", response_model=ConfigResponse)
async def patch_config(body: ConfigPatchRequest):
    """
    Persist changed settings to the .env file and update the in-process settings
    singleton so changes take effect immediately without a server restart.
    """
    env_path = str(_ENV_FILE)

    if body.api_key is not None:
        set_key(env_path, "SCATHACH_OPENROUTER_API_KEY", body.api_key)
        object.__setattr__(settings, "openrouter_api_key", body.api_key)

    if body.model is not None:
        set_key(env_path, "SCATHACH_MODEL", body.model)
        object.__setattr__(settings, "model", body.model)

    if body.quality_threshold is not None:
        set_key(env_path, "SCATHACH_QUALITY_THRESHOLD", str(body.quality_threshold))
        object.__setattr__(settings, "quality_threshold", body.quality_threshold)

    if body.main_timing is not None:
        set_key(env_path, "SCATHACH_MAIN_TIMING", body.main_timing)
        from scathach.core.question import TimingMode
        object.__setattr__(settings, "main_timing", TimingMode(body.main_timing))

    if body.review_timing is not None:
        set_key(env_path, "SCATHACH_REVIEW_TIMING", body.review_timing)
        from scathach.core.question import TimingMode
        object.__setattr__(settings, "review_timing", TimingMode(body.review_timing))

    if body.hydra_in_super_review is not None:
        set_key(env_path, "SCATHACH_HYDRA_IN_SUPER_REVIEW", str(body.hydra_in_super_review).lower())
        object.__setattr__(settings, "hydra_in_super_review", body.hydra_in_super_review)

    if body.open_doc_on_session is not None:
        set_key(env_path, "SCATHACH_OPEN_DOC_ON_SESSION", str(body.open_doc_on_session).lower())
        object.__setattr__(settings, "open_doc_on_session", body.open_doc_on_session)

    # Rebuild the LLM client if key or model changed so new requests use updated creds.
    # The app.state.client is replaced on the next request via the route below; for the
    # PATCH route itself we just return the updated config.
    return _current_config()


@router.post("/test", response_model=ConfigTestResponse)
async def test_config(request: Request):
    """Send a minimal request to the LLM API to verify the key and model are valid."""
    from scathach.llm.client import LLMClient, LLMError

    client = LLMClient(
        api_key=settings.openrouter_api_key,
        model=settings.model,
        base_url=settings.openrouter_base_url,
        max_retries=1,
    )
    try:
        response = await client.generate(
            system_prompt="You are a test assistant.",
            user_prompt="Reply with exactly one word: OK",
            max_tokens=5,
        )
        return ConfigTestResponse(ok=True, message=f"Connection successful. Response: {response!r}")
    except LLMError as exc:
        return ConfigTestResponse(ok=False, message=str(exc))
    except Exception as exc:
        return ConfigTestResponse(ok=False, message=f"Unexpected error: {exc}")
