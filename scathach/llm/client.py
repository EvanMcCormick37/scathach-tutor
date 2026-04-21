"""
Async LLM client wrapper for OpenRouter (OpenAI-compatible API).

Uses the openai SDK pointed at the OpenRouter base URL.
Provides exponential-backoff retry on rate-limit (429) and server errors (503).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from openai import AsyncOpenAI, APIStatusError, APIConnectionError

from scathach.llm.parsing import ParseError, extract_json
from scathach.llm.providers import ProviderConfig, get_provider

logger = logging.getLogger(__name__)

_BACKOFF_BASE_S = 2.0
_MAX_RETRIES = 3
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """Raised when the LLM call fails after all retries."""


class LLMClient:
    """
    Async wrapper around the OpenAI SDK, configured for OpenRouter.

    Pass `response_schema` to `generate()` to enforce structured JSON output
    via the API's response_format parameter. The response is then returned as
    an already-parsed Python object (dict or list) rather than a raw string.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._provider: ProviderConfig = get_provider(model)
        self._max_retries = max_retries
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,  # Retries handled here for finer control
        )

    @property
    def model(self) -> str:
        return self._provider.model_id

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Any:
        """
        Send a chat completion request and return the assistant's response.

        Args:
            system_prompt:   The system role message.
            user_prompt:     The user role message.
            response_schema: When provided, the raw text response is parsed as
                             JSON via extract_json() and returned as a Python
                             object (dict or list). Omit to return raw str.
                             NOTE: the schema is NOT sent to the API — it is used
                             only as a signal to trigger client-side extraction.
            max_tokens:      Override the provider default.
            temperature:     Override the provider default.

        Returns:
            Parsed Python object (dict | list) when response_schema is given,
            otherwise the raw assistant text as str.

        Raises:
            LLMError: If all retries are exhausted or a non-retryable error occurs.
        """
        kwargs: dict[str, Any] = dict(
            model=self._provider.model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            max_tokens=max_tokens or self._provider.max_tokens,
            temperature=temperature if temperature is not None else self._provider.temperature,
        )

        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                if content is None:
                    raise LLMError("LLM returned an empty response.")

                if response_schema is not None:
                    try:
                        return extract_json(content)
                    except ParseError as exc:
                        raise LLMError(f"Failed to parse JSON from response: {exc}") from exc
                return content

            except APIStatusError as exc:
                if exc.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                    wait = _BACKOFF_BASE_S * (2 ** attempt)
                    logger.warning(
                        "LLM API returned %s on attempt %d/%d — retrying in %.1fs",
                        exc.status_code, attempt + 1, self._max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    last_exc = exc
                    continue
                raise LLMError(
                    f"LLM API error (status {exc.status_code}): {exc.message}"
                ) from exc

            except APIConnectionError as exc:
                if attempt < self._max_retries:
                    wait = _BACKOFF_BASE_S * (2 ** attempt)
                    logger.warning(
                        "LLM connection error on attempt %d/%d — retrying in %.1fs",
                        attempt + 1, self._max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    last_exc = exc
                    continue
                raise LLMError(f"LLM connection failed: {exc}") from exc

        raise LLMError("LLM call failed after all retries.") from last_exc


def make_client(api_key: str, model: str, base_url: str) -> LLMClient:
    """Factory function — construct an LLMClient from config values."""
    return LLMClient(api_key=api_key, model=model, base_url=base_url)
