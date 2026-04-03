"""
Unit tests for the LLM client, with all network calls mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scathach.llm.client import LLMClient, LLMError
from scathach.llm.providers import (
    ARCEE_BLAZE,
    GEMINI_FLASH,
    KIMI_K2,
    get_provider,
    ALL_PROVIDERS,
)


# ---------------------------------------------------------------------------
# Provider config tests
# ---------------------------------------------------------------------------


def test_all_known_providers_present() -> None:
    assert KIMI_K2.model_id in ALL_PROVIDERS
    assert ARCEE_BLAZE.model_id in ALL_PROVIDERS
    assert GEMINI_FLASH.model_id in ALL_PROVIDERS


def test_get_provider_known() -> None:
    p = get_provider("moonshotai/kimi-k2")
    assert p.display_name == "Kimi K2 (Moonshot AI)"
    assert p.is_free is True


def test_get_provider_unknown_returns_generic() -> None:
    p = get_provider("some/unknown-model")
    assert p.model_id == "some/unknown-model"
    assert p.max_tokens == 4096


def test_provider_max_tokens_positive() -> None:
    for p in ALL_PROVIDERS.values():
        assert p.max_tokens > 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(text: str) -> MagicMock:
    """Build a minimal mock of an openai ChatCompletion response."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_client() -> LLMClient:
    return LLMClient(api_key="sk-test", model="moonshotai/kimi-k2")


# ---------------------------------------------------------------------------
# LLMClient.generate — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_returns_text() -> None:
    client = _make_client()
    mock_resp = _make_mock_response("Here is the answer.")

    with patch.object(
        client._client.chat.completions,
        "create",
        new=AsyncMock(return_value=mock_resp),
    ):
        result = await client.generate(
            system_prompt="You are a tutor.",
            user_prompt="Explain gravity.",
        )

    assert result == "Here is the answer."


@pytest.mark.asyncio
async def test_generate_passes_model_to_api() -> None:
    client = _make_client()
    mock_resp = _make_mock_response("ok")
    create_mock = AsyncMock(return_value=mock_resp)

    with patch.object(client._client.chat.completions, "create", new=create_mock):
        await client.generate(system_prompt="sys", user_prompt="usr")

    call_kwargs = create_mock.call_args.kwargs
    assert call_kwargs["model"] == "moonshotai/kimi-k2"


@pytest.mark.asyncio
async def test_generate_overrides_max_tokens() -> None:
    client = _make_client()
    mock_resp = _make_mock_response("ok")
    create_mock = AsyncMock(return_value=mock_resp)

    with patch.object(client._client.chat.completions, "create", new=create_mock):
        await client.generate(system_prompt="s", user_prompt="u", max_tokens=100)

    assert create_mock.call_args.kwargs["max_tokens"] == 100


# ---------------------------------------------------------------------------
# LLMClient.generate — empty response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_raises_on_empty_content() -> None:
    client = _make_client()
    mock_resp = _make_mock_response(None)

    with patch.object(
        client._client.chat.completions, "create", new=AsyncMock(return_value=mock_resp)
    ):
        with pytest.raises(LLMError, match="empty response"):
            await client.generate(system_prompt="s", user_prompt="u")


# ---------------------------------------------------------------------------
# LLMClient.generate — retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_retries_on_429() -> None:
    from openai import APIStatusError

    client = LLMClient(api_key="sk-test", model="moonshotai/kimi-k2", max_retries=2)
    mock_resp = _make_mock_response("success after retry")

    # First two calls raise 429, third succeeds
    error_429 = APIStatusError(
        "rate limit",
        response=MagicMock(status_code=429, headers={}),
        body={"error": "rate limit"},
    )
    create_mock = AsyncMock(side_effect=[error_429, error_429, mock_resp])

    with patch.object(client._client.chat.completions, "create", new=create_mock):
        with patch("scathach.llm.client.asyncio.sleep", new=AsyncMock()):
            result = await client.generate(system_prompt="s", user_prompt="u")

    assert result == "success after retry"
    assert create_mock.call_count == 3


@pytest.mark.asyncio
async def test_generate_raises_after_max_retries() -> None:
    from openai import APIStatusError

    client = LLMClient(api_key="sk-test", model="moonshotai/kimi-k2", max_retries=1)

    error_503 = APIStatusError(
        "service unavailable",
        response=MagicMock(status_code=503, headers={}),
        body={"error": "unavailable"},
    )
    create_mock = AsyncMock(side_effect=[error_503, error_503])

    with patch.object(client._client.chat.completions, "create", new=create_mock):
        with patch("scathach.llm.client.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(LLMError):
                await client.generate(system_prompt="s", user_prompt="u")

    assert create_mock.call_count == 2  # 1 initial + 1 retry


@pytest.mark.asyncio
async def test_generate_raises_immediately_on_non_retryable() -> None:
    from openai import APIStatusError

    client = LLMClient(api_key="sk-test", model="moonshotai/kimi-k2", max_retries=3)
    error_401 = APIStatusError(
        "unauthorized",
        response=MagicMock(status_code=401, headers={}),
        body={"error": "auth"},
    )
    create_mock = AsyncMock(side_effect=error_401)

    with patch.object(client._client.chat.completions, "create", new=create_mock):
        with pytest.raises(LLMError, match="401"):
            await client.generate(system_prompt="s", user_prompt="u")

    # Should not retry for 401
    assert create_mock.call_count == 1


# ---------------------------------------------------------------------------
# LLMClient.model property
# ---------------------------------------------------------------------------


def test_client_model_property() -> None:
    client = LLMClient(api_key="sk-test", model="arcee-ai/arcee-blaze")
    assert client.model == "arcee-ai/arcee-blaze"
