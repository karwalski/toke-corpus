"""Anthropic Claude API client using the Messages Batches API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

import httpx

from dispatch.base import GenerateResult, ProviderClient

logger = logging.getLogger(__name__)

_ANTHROPIC_API_BASE = "https://api.anthropic.com"
_ANTHROPIC_API_VERSION = "2023-06-01"
_BATCH_POLL_INTERVAL_S = 30.0
_MAX_RETRIES = 5
_BASE_DELAY_S = 1.0


class AnthropicClient(ProviderClient):
    """Client for the Anthropic Claude API with batch support.

    Uses the Messages Batches API for bulk generation and prompt caching
    to reduce cost on repeated system prompts. Reads ANTHROPIC_API_KEY
    from the environment.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20250315",
        tier: int = 2,
        cost_per_input_mtok: float = 0.80,
        cost_per_output_mtok: float = 4.00,
        max_tokens: int = 1024,
    ) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            msg = "ANTHROPIC_API_KEY environment variable is not set"
            raise ValueError(msg)
        self._api_key = api_key
        self._model = model
        self._tier = tier
        self._cost_per_input_mtok = cost_per_input_mtok
        self._cost_per_output_mtok = cost_per_output_mtok
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "anthropic"

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
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_API_VERSION,
            "anthropic-beta": "prompt-caching-2024-07-31",
            "content-type": "application/json",
        }

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff on 429/529."""
        delay = _BASE_DELAY_S
        for attempt in range(_MAX_RETRIES):
            if method == "POST":
                response = await client.post(
                    url, headers=self._headers(), json=json_data
                )
            else:
                response = await client.get(url, headers=self._headers())

            if response.status_code in (429, 529):
                retry_after = response.headers.get("retry-after")
                wait = float(retry_after) if retry_after else delay
                logger.warning(
                    "Anthropic rate limited (attempt %d/%d), "
                    "waiting %.1fs: status=%d",
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                    response.status_code,
                )
                await asyncio.sleep(wait)
                delay *= 2
                continue

            response.raise_for_status()
            return response

        msg = f"Anthropic request failed after {_MAX_RETRIES} retries"
        raise httpx.HTTPStatusError(
            msg,
            request=response.request,  # type: ignore[possibly-undefined]
            response=response,  # type: ignore[possibly-undefined]
        )

    def _build_messages_body(
        self, system: str, prompt: str
    ) -> dict[str, Any]:
        """Build the request body for a single Messages API call.

        Marks the system prompt with cache_control for prompt caching.
        """
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": prompt}],
        }

    async def generate(self, system: str, prompt: str) -> GenerateResult:
        """Generate a single completion via the Messages API."""
        start = self._now_ms()
        async with httpx.AsyncClient(
            base_url=_ANTHROPIC_API_BASE, timeout=120.0
        ) as client:
            body = self._build_messages_body(system, prompt)
            response = await self._request_with_retry(
                client, "POST", "/v1/messages", json_data=body
            )
            data = response.json()

        latency = self._now_ms() - start
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")

        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cost = self._compute_cost(input_tokens, output_tokens)

        return GenerateResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=data.get("model", self._model),
            cost=cost,
            latency_ms=latency,
        )

    async def generate_batch(
        self, system: str, prompts: list[str]
    ) -> list[GenerateResult]:
        """Submit prompts via the Messages Batches API and poll for results.

        Creates a batch of message requests, polls until completion,
        then returns results in the same order as the input prompts.
        """
        start = self._now_ms()

        # Build batch requests with deterministic custom_ids to preserve order
        requests: list[dict[str, Any]] = []
        for idx, prompt in enumerate(prompts):
            custom_id = f"req-{idx}-{uuid.uuid4().hex[:8]}"
            requests.append(
                {
                    "custom_id": custom_id,
                    "params": self._build_messages_body(system, prompt),
                }
            )

        async with httpx.AsyncClient(
            base_url=_ANTHROPIC_API_BASE, timeout=120.0
        ) as client:
            # Submit the batch
            create_response = await self._request_with_retry(
                client,
                "POST",
                "/v1/messages/batches",
                json_data={"requests": requests},
            )
            batch = create_response.json()
            batch_id = batch["id"]
            logger.info(
                "Anthropic batch created: %s (%d requests)",
                batch_id,
                len(prompts),
            )

            # Poll until the batch completes
            while True:
                status_response = await self._request_with_retry(
                    client, "GET", f"/v1/messages/batches/{batch_id}"
                )
                status_data = status_response.json()
                processing_status = status_data.get("processing_status")

                if processing_status == "ended":
                    break

                logger.debug(
                    "Batch %s status: %s", batch_id, processing_status
                )
                await asyncio.sleep(_BATCH_POLL_INTERVAL_S)

            # Retrieve results via the results URL
            results_url = status_data.get("results_url", "")
            results_response = await self._request_with_retry(
                client, "GET", results_url
            )

        # Parse JSONL results and map back by custom_id
        elapsed = self._now_ms() - start
        per_request_latency = elapsed / max(len(prompts), 1)

        result_map: dict[str, dict[str, Any]] = {}
        for line in results_response.text.strip().split("\n"):
            if not line.strip():
                continue
            entry = json.loads(line)
            result_map[entry["custom_id"]] = entry

        results: list[GenerateResult] = []
        for req in requests:
            cid = req["custom_id"]
            entry = result_map.get(cid, {})
            result_body = entry.get("result", {})
            message = result_body.get("message", {})

            text = ""
            for block in message.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")

            usage = message.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cost = self._compute_cost(input_tokens, output_tokens)

            results.append(
                GenerateResult(
                    text=text,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    model=message.get("model", self._model),
                    cost=cost,
                    latency_ms=per_request_latency,
                )
            )

        return results
