"""xAI (Grok) API client using the OpenAI-compatible chat completions endpoint."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from dispatch.base import GenerateResult, ProviderClient

logger = logging.getLogger(__name__)

_XAI_API_BASE = "https://api.x.ai"
_MAX_RETRIES = 5
_BASE_DELAY_S = 1.0


class XAIClient(ProviderClient):
    """Client for the xAI API (OpenAI-compatible).

    Reads XAI_API_KEY from the environment.
    """

    def __init__(
        self,
        model: str = "grok-3-mini",
        tier: int = 1,
        cost_per_input_mtok: float = 0.30,
        cost_per_output_mtok: float = 0.50,
        max_tokens: int = 1024,
    ) -> None:
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            msg = "XAI_API_KEY environment variable is not set"
            raise ValueError(msg)
        self._api_key = api_key
        self._model = model
        self._tier = tier
        self._cost_per_input_mtok = cost_per_input_mtok
        self._cost_per_output_mtok = cost_per_output_mtok
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "xai"

    @property
    def tier(self) -> int:
        return self._tier

    @property
    def cost_per_input_mtok(self) -> float:
        return self._cost_per_input_mtok

    @property
    def cost_per_output_mtok(self) -> float:
        return self._cost_per_output_mtok

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff on rate limits."""
        delay = _BASE_DELAY_S
        for attempt in range(_MAX_RETRIES):
            if method == "POST":
                response = await client.post(
                    url, headers=self._headers(), json=json_data
                )
            else:
                response = await client.get(url, headers=self._headers())

            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                wait = float(retry_after) if retry_after else delay
                logger.warning(
                    "xAI rate limited (attempt %d/%d), waiting %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
                delay *= 2
                continue

            response.raise_for_status()
            return response

        msg = f"xAI request failed after {_MAX_RETRIES} retries"
        raise httpx.HTTPStatusError(
            msg,
            request=response.request,  # type: ignore[possibly-undefined]
            response=response,  # type: ignore[possibly-undefined]
        )

    async def generate(self, system: str, prompt: str) -> GenerateResult:
        """Generate a single completion via the chat completions endpoint."""
        start = self._now_ms()
        async with httpx.AsyncClient(
            base_url=_XAI_API_BASE, timeout=120.0
        ) as client:
            body: dict[str, Any] = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }
            response = await self._request_with_retry(
                client, "POST", "/v1/chat/completions", json_data=body
            )
            data = response.json()

        latency = self._now_ms() - start
        choices = data.get("choices", [])
        text = choices[0]["message"]["content"] if choices else ""

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost = self._compute_cost(input_tokens, output_tokens)

        return GenerateResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=data.get("model", self._model),
            cost=cost,
            latency_ms=latency,
        )
