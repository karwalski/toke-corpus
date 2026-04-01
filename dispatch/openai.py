"""OpenAI API client using the Batch API for bulk generation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from dispatch.base import GenerateResult, ProviderClient

logger = logging.getLogger(__name__)

_OPENAI_API_BASE = "https://api.openai.com"
_BATCH_POLL_INTERVAL_S = 30.0
_MAX_RETRIES = 5
_BASE_DELAY_S = 1.0


class OpenAIClient(ProviderClient):
    """Client for the OpenAI API with Batch API support.

    Uses the Batch API (file upload, batch create, poll, download) for
    bulk generation. Reads OPENAI_API_KEY from the environment.
    """

    def __init__(
        self,
        model: str = "gpt-4.1-mini",
        tier: int = 1,
        cost_per_input_mtok: float = 0.40,
        cost_per_output_mtok: float = 1.60,
        max_tokens: int = 1024,
    ) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            msg = "OPENAI_API_KEY environment variable is not set"
            raise ValueError(msg)
        self._api_key = api_key
        self._model = model
        self._tier = tier
        self._cost_per_input_mtok = cost_per_input_mtok
        self._cost_per_output_mtok = cost_per_output_mtok
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "openai"

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
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff on rate limits."""
        delay = _BASE_DELAY_S
        headers = {**self._headers(), **(extra_headers or {})}

        for attempt in range(_MAX_RETRIES):
            if method == "POST":
                if content is not None:
                    response = await client.post(
                        url, headers=headers, content=content
                    )
                else:
                    response = await client.post(
                        url, headers=headers, json=json_data
                    )
            else:
                response = await client.get(url, headers=headers)

            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                wait = float(retry_after) if retry_after else delay
                logger.warning(
                    "OpenAI rate limited (attempt %d/%d), waiting %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                )
                await asyncio.sleep(wait)
                delay *= 2
                continue

            response.raise_for_status()
            return response

        msg = f"OpenAI request failed after {_MAX_RETRIES} retries"
        raise httpx.HTTPStatusError(
            msg,
            request=response.request,  # type: ignore[possibly-undefined]
            response=response,  # type: ignore[possibly-undefined]
        )

    def _build_chat_body(
        self, system: str, prompt: str
    ) -> dict[str, Any]:
        """Build the Chat Completions request body."""
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }

    async def generate(self, system: str, prompt: str) -> GenerateResult:
        """Generate a single completion via the Chat Completions API."""
        start = self._now_ms()
        async with httpx.AsyncClient(
            base_url=_OPENAI_API_BASE, timeout=120.0
        ) as client:
            body = self._build_chat_body(system, prompt)
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

    async def generate_batch(
        self, system: str, prompts: list[str]
    ) -> list[GenerateResult]:
        """Submit prompts via the OpenAI Batch API.

        Workflow: upload JSONL file -> create batch -> poll -> download results.
        """
        start = self._now_ms()

        # Build JSONL for batch input
        lines: list[str] = []
        for idx, prompt in enumerate(prompts):
            request_obj = {
                "custom_id": f"req-{idx}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": self._build_chat_body(system, prompt),
            }
            lines.append(json.dumps(request_obj))
        jsonl_content = "\n".join(lines).encode()

        async with httpx.AsyncClient(
            base_url=_OPENAI_API_BASE, timeout=120.0
        ) as client:
            # Step 1: Upload the JSONL file
            upload_headers = {
                "Authorization": f"Bearer {self._api_key}",
            }
            # Use multipart form upload for the files endpoint
            import io

            files_payload = {
                "file": ("batch_input.jsonl", io.BytesIO(jsonl_content), "application/jsonl"),
                "purpose": (None, "batch"),
            }
            upload_response = await client.post(
                "/v1/files",
                headers=upload_headers,
                files=files_payload,
            )
            upload_response.raise_for_status()
            file_id = upload_response.json()["id"]
            logger.info("OpenAI batch file uploaded: %s", file_id)

            # Step 2: Create the batch
            batch_response = await self._request_with_retry(
                client,
                "POST",
                "/v1/batches",
                json_data={
                    "input_file_id": file_id,
                    "endpoint": "/v1/chat/completions",
                    "completion_window": "24h",
                },
            )
            batch_data = batch_response.json()
            batch_id = batch_data["id"]
            logger.info(
                "OpenAI batch created: %s (%d requests)", batch_id, len(prompts)
            )

            # Step 3: Poll until complete
            while True:
                status_response = await self._request_with_retry(
                    client, "GET", f"/v1/batches/{batch_id}"
                )
                status_data = status_response.json()
                status = status_data.get("status")

                if status == "completed":
                    break
                if status in ("failed", "expired", "cancelled"):
                    msg = f"OpenAI batch {batch_id} ended with status: {status}"
                    raise RuntimeError(msg)

                logger.debug("Batch %s status: %s", batch_id, status)
                await asyncio.sleep(_BATCH_POLL_INTERVAL_S)

            # Step 4: Download the output file
            output_file_id = status_data.get("output_file_id", "")
            output_response = await self._request_with_retry(
                client, "GET", f"/v1/files/{output_file_id}/content"
            )

        # Parse JSONL results
        elapsed = self._now_ms() - start
        per_request_latency = elapsed / max(len(prompts), 1)

        result_map: dict[str, dict[str, Any]] = {}
        for line in output_response.text.strip().split("\n"):
            if not line.strip():
                continue
            entry = json.loads(line)
            result_map[entry["custom_id"]] = entry

        results: list[GenerateResult] = []
        for idx in range(len(prompts)):
            cid = f"req-{idx}"
            entry = result_map.get(cid, {})
            response_body = entry.get("response", {}).get("body", {})

            choices = response_body.get("choices", [])
            text = choices[0]["message"]["content"] if choices else ""

            usage = response_body.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            cost = self._compute_cost(input_tokens, output_tokens)

            results.append(
                GenerateResult(
                    text=text,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    model=response_body.get("model", self._model),
                    cost=cost,
                    latency_ms=per_request_latency,
                )
            )

        return results
