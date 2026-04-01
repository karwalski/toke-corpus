"""Google Gemini API client with context caching and rate limiting."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from dispatch.base import GenerateResult, ProviderClient

logger = logging.getLogger(__name__)

_GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
_MAX_RETRIES = 5
_BASE_DELAY_S = 1.0
_DEFAULT_RPM = 1000


class GeminiClient(ProviderClient):
    """Client for the Google Gemini API (real-time, not batch).

    Supports context caching for repeated system prompts and uses an
    asyncio.Semaphore for RPM-based rate limiting. Reads GEMINI_API_KEY
    from the environment.
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        tier: int = 1,
        cost_per_input_mtok: float = 0.15,
        cost_per_output_mtok: float = 0.60,
        max_output_tokens: int = 1024,
        requests_per_minute: int = _DEFAULT_RPM,
    ) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            msg = "GEMINI_API_KEY environment variable is not set"
            raise ValueError(msg)
        self._api_key = api_key
        self._model = model
        self._tier = tier
        self._cost_per_input_mtok = cost_per_input_mtok
        self._cost_per_output_mtok = cost_per_output_mtok
        self._max_output_tokens = max_output_tokens
        self._semaphore = asyncio.Semaphore(requests_per_minute)
        self._cached_content_name: str | None = None

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def tier(self) -> int:
        return self._tier

    @property
    def cost_per_input_mtok(self) -> float:
        return self._cost_per_input_mtok

    @property
    def cost_per_output_mtok(self) -> float:
        return self._cost_per_output_mtok

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff on 429."""
        delay = _BASE_DELAY_S
        for attempt in range(_MAX_RETRIES):
            if method == "POST":
                response = await client.post(url, json=json_data)
            else:
                response = await client.get(url)

            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                wait = float(retry_after) if retry_after else delay
                logger.warning(
                    "Gemini rate limited (attempt %d/%d), waiting %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
                delay *= 2
                continue

            response.raise_for_status()
            return response

        msg = f"Gemini request failed after {_MAX_RETRIES} retries"
        raise httpx.HTTPStatusError(
            msg,
            request=response.request,  # type: ignore[possibly-undefined]
            response=response,  # type: ignore[possibly-undefined]
        )

    async def _ensure_cached_content(
        self, client: httpx.AsyncClient, system: str
    ) -> str:
        """Create or reuse a cached content resource for the system prompt.

        Returns the cached content resource name for use in generateContent.
        """
        if self._cached_content_name is not None:
            return self._cached_content_name

        url = (
            f"{_GEMINI_API_BASE}/v1beta/cachedContents"
            f"?key={self._api_key}"
        )
        body: dict[str, Any] = {
            "model": f"models/{self._model}",
            "contents": [
                {
                    "parts": [{"text": system}],
                    "role": "user",
                }
            ],
            "ttl": "3600s",
        }
        response = await self._request_with_retry(
            client, "POST", url, json_data=body
        )
        data = response.json()
        cached_name = data.get("name", "")
        self._cached_content_name = cached_name
        logger.info("Gemini cached content created: %s", cached_name)
        return cached_name

    async def generate(self, system: str, prompt: str) -> GenerateResult:
        """Generate a single completion via the Gemini generateContent API."""
        start = self._now_ms()

        async with self._semaphore:
            async with httpx.AsyncClient(timeout=120.0) as client:
                # Try to use context caching for system prompt
                try:
                    cached_name = await self._ensure_cached_content(
                        client, system
                    )
                    use_cache = True
                except (httpx.HTTPStatusError, KeyError):
                    logger.debug(
                        "Gemini context caching unavailable, "
                        "falling back to inline system instruction"
                    )
                    use_cache = False

                url = (
                    f"{_GEMINI_API_BASE}/v1beta/"
                    f"models/{self._model}:generateContent"
                    f"?key={self._api_key}"
                )

                body: dict[str, Any] = {
                    "contents": [
                        {
                            "parts": [{"text": prompt}],
                            "role": "user",
                        }
                    ],
                    "generationConfig": {
                        "maxOutputTokens": self._max_output_tokens,
                    },
                }

                if use_cache:
                    body["cachedContent"] = cached_name
                else:
                    body["systemInstruction"] = {
                        "parts": [{"text": system}]
                    }

                response = await self._request_with_retry(
                    client, "POST", url, json_data=body
                )
                data = response.json()

        latency = self._now_ms() - start

        # Extract text from response
        candidates = data.get("candidates", [])
        text = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                text += part.get("text", "")

        # Extract token counts from usageMetadata
        usage = data.get("usageMetadata", {})
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)
        cached_tokens = usage.get("cachedContentTokenCount", 0)

        # Cached tokens are typically free or heavily discounted
        billable_input = input_tokens - cached_tokens
        cost = self._compute_cost(max(billable_input, 0), output_tokens)

        return GenerateResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._model,
            cost=cost,
            latency_ms=latency,
        )

    async def generate_batch(
        self, system: str, prompts: list[str]
    ) -> list[GenerateResult]:
        """Generate completions concurrently with semaphore-based rate limiting.

        Gemini does not have a batch API, so this sends concurrent real-time
        requests throttled by the RPM semaphore.
        """
        tasks = [self.generate(system, prompt) for prompt in prompts]
        return list(await asyncio.gather(*tasks))
