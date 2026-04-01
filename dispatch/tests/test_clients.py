"""Unit tests for dispatch provider clients with mocked HTTP responses."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from dispatch.anthropic import AnthropicClient
from dispatch.base import CostTracker, GenerateResult, ProviderClient
from dispatch.deepseek import DeepSeekClient
from dispatch.gemini import GeminiClient
from dispatch.openai import OpenAIClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure all API key env vars are set for tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")


# ---------------------------------------------------------------------------
# Helper: mock httpx response
# ---------------------------------------------------------------------------


def _mock_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
    text: str = "",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a mock httpx.Response."""
    if json_data is not None:
        content = json.dumps(json_data).encode()
    else:
        content = text.encode()
    resp = httpx.Response(
        status_code=status_code,
        content=content,
        headers={
            "content-type": "application/json" if json_data else "text/plain",
            **(headers or {}),
        },
        request=httpx.Request("POST", "https://example.com"),
    )
    return resp


# ---------------------------------------------------------------------------
# Anthropic tests
# ---------------------------------------------------------------------------


class TestAnthropicClient:
    """Tests for AnthropicClient."""

    def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            AnthropicClient()

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        response_data = {
            "content": [{"type": "text", "text": "fn main() {}"}],
            "model": "claude-haiku-4-5-20250315",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        mock_resp = _mock_response(json_data=response_data)

        client = AnthropicClient()
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = await client.generate("system prompt", "write code")

        assert result.text == "fn main() {}"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.model == "claude-haiku-4-5-20250315"
        assert result.cost > 0
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_generate_rate_limit_retry(self) -> None:
        rate_limit_resp = _mock_response(status_code=429, headers={"retry-after": "0.01"})
        success_data = {
            "content": [{"type": "text", "text": "ok"}],
            "model": "claude-haiku-4-5-20250315",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        success_resp = _mock_response(json_data=success_data)

        client = AnthropicClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=[rate_limit_resp, success_resp],
        ):
            result = await client.generate("sys", "prompt")

        assert result.text == "ok"

    @pytest.mark.asyncio
    async def test_generate_overloaded_retry(self) -> None:
        """Test retry on 529 (overloaded) status."""
        overloaded_resp = _mock_response(status_code=529)
        success_data = {
            "content": [{"type": "text", "text": "done"}],
            "model": "claude-haiku-4-5-20250315",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
        success_resp = _mock_response(json_data=success_data)

        client = AnthropicClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=[overloaded_resp, success_resp],
        ):
            result = await client.generate("sys", "prompt")

        assert result.text == "done"

    @pytest.mark.asyncio
    async def test_generate_http_error(self) -> None:
        error_resp = _mock_response(status_code=500, json_data={"error": "internal"})

        client = AnthropicClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=error_resp,
        ), pytest.raises(httpx.HTTPStatusError):
            await client.generate("sys", "prompt")

    def test_cost_calculation(self) -> None:
        client = AnthropicClient(
            cost_per_input_mtok=0.80, cost_per_output_mtok=4.00
        )
        cost = client._compute_cost(1_000_000, 1_000_000)
        assert abs(cost - 4.80) < 0.001

    def test_properties(self) -> None:
        client = AnthropicClient()
        assert client.name == "anthropic"
        assert client.tier == 2
        assert isinstance(client, ProviderClient)


# ---------------------------------------------------------------------------
# OpenAI tests
# ---------------------------------------------------------------------------


class TestOpenAIClient:
    """Tests for OpenAIClient."""

    def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            OpenAIClient()

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        response_data = {
            "choices": [{"message": {"content": "print('hello')"}}],
            "model": "gpt-4.1-mini",
            "usage": {"prompt_tokens": 80, "completion_tokens": 30},
        }
        mock_resp = _mock_response(json_data=response_data)

        client = OpenAIClient()
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = await client.generate("system", "write python")

        assert result.text == "print('hello')"
        assert result.input_tokens == 80
        assert result.output_tokens == 30
        assert result.cost > 0

    @pytest.mark.asyncio
    async def test_generate_rate_limit_retry(self) -> None:
        rate_limit_resp = _mock_response(status_code=429, headers={"retry-after": "0.01"})
        success_data = {
            "choices": [{"message": {"content": "ok"}}],
            "model": "gpt-4.1-mini",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        success_resp = _mock_response(json_data=success_data)

        client = OpenAIClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=[rate_limit_resp, success_resp],
        ):
            result = await client.generate("sys", "prompt")

        assert result.text == "ok"

    @pytest.mark.asyncio
    async def test_generate_http_error(self) -> None:
        error_resp = _mock_response(status_code=500, json_data={"error": "bad"})

        client = OpenAIClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=error_resp,
        ), pytest.raises(httpx.HTTPStatusError):
            await client.generate("sys", "prompt")

    def test_properties(self) -> None:
        client = OpenAIClient()
        assert client.name == "openai"
        assert client.tier == 1
        assert isinstance(client, ProviderClient)


# ---------------------------------------------------------------------------
# Gemini tests
# ---------------------------------------------------------------------------


class TestGeminiClient:
    """Tests for GeminiClient."""

    def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiClient()

    @pytest.mark.asyncio
    async def test_generate_success_no_cache(self) -> None:
        """Test generate falls back to inline system instruction when caching fails."""
        cache_error = _mock_response(status_code=400, json_data={"error": "no cache"})
        gen_data = {
            "candidates": [
                {"content": {"parts": [{"text": "result text"}]}}
            ],
            "usageMetadata": {
                "promptTokenCount": 200,
                "candidatesTokenCount": 40,
                "cachedContentTokenCount": 0,
            },
        }
        gen_resp = _mock_response(json_data=gen_data)

        client = GeminiClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=[cache_error, gen_resp],
        ):
            result = await client.generate("system", "user prompt")

        assert result.text == "result text"
        assert result.input_tokens == 200
        assert result.output_tokens == 40

    @pytest.mark.asyncio
    async def test_generate_with_cache(self) -> None:
        """Test generate using context caching."""
        cache_resp = _mock_response(
            json_data={"name": "cachedContents/abc123"}
        )
        gen_data = {
            "candidates": [
                {"content": {"parts": [{"text": "cached result"}]}}
            ],
            "usageMetadata": {
                "promptTokenCount": 200,
                "candidatesTokenCount": 30,
                "cachedContentTokenCount": 150,
            },
        }
        gen_resp = _mock_response(json_data=gen_data)

        client = GeminiClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=[cache_resp, gen_resp],
        ):
            result = await client.generate("system", "prompt")

        assert result.text == "cached result"
        # Cost should reflect cached tokens discount
        assert result.cost >= 0

    @pytest.mark.asyncio
    async def test_generate_rate_limit_retry(self) -> None:
        rate_limit_resp = _mock_response(status_code=429, headers={"retry-after": "0.01"})
        cache_error = _mock_response(status_code=400, json_data={"error": "no"})
        gen_data = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "cachedContentTokenCount": 0,
            },
        }
        gen_resp = _mock_response(json_data=gen_data)

        client = GeminiClient()
        # Cache POST gets 429, then 400 (no cache), then generate succeeds
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=[rate_limit_resp, cache_error, gen_resp],
        ):
            result = await client.generate("sys", "prompt")

        assert result.text == "ok"

    def test_properties(self) -> None:
        client = GeminiClient()
        assert client.name == "gemini"
        assert client.tier == 1
        assert isinstance(client, ProviderClient)


# ---------------------------------------------------------------------------
# DeepSeek tests
# ---------------------------------------------------------------------------


class TestDeepSeekClient:
    """Tests for DeepSeekClient."""

    def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            DeepSeekClient()

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        response_data = {
            "choices": [{"message": {"content": "fn add(a, b) { a + b }"}}],
            "model": "deepseek-chat",
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 40,
                "prompt_cache_hit_tokens": 0,
            },
        }
        mock_resp = _mock_response(json_data=response_data)

        client = DeepSeekClient()
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = await client.generate("system", "write toke")

        assert result.text == "fn add(a, b) { a + b }"
        assert result.input_tokens == 150
        assert result.output_tokens == 40

    @pytest.mark.asyncio
    async def test_generate_with_cache_hit(self) -> None:
        """Test that cache hit tokens reduce cost."""
        response_data = {
            "choices": [{"message": {"content": "cached result"}}],
            "model": "deepseek-chat",
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 30,
                "prompt_cache_hit_tokens": 150,
            },
        }
        mock_resp = _mock_response(json_data=response_data)

        client = DeepSeekClient(
            cost_per_input_mtok=0.27,
            cost_per_output_mtok=1.10,
            cost_per_cached_input_mtok=0.07,
        )
        with patch.object(
            httpx.AsyncClient, "post", new_callable=AsyncMock, return_value=mock_resp
        ):
            result = await client.generate("system", "prompt")

        # 50 non-cached input tokens at 0.27/MTok + 150 cached at 0.07/MTok
        # + 30 output at 1.10/MTok
        expected_input_cost = (50 / 1e6) * 0.27 + (150 / 1e6) * 0.07
        expected_output_cost = (30 / 1e6) * 1.10
        expected_cost = expected_input_cost + expected_output_cost
        assert abs(result.cost - expected_cost) < 1e-8

    @pytest.mark.asyncio
    async def test_generate_rate_limit_retry(self) -> None:
        rate_limit_resp = _mock_response(status_code=429, headers={"retry-after": "0.01"})
        success_data = {
            "choices": [{"message": {"content": "ok"}}],
            "model": "deepseek-chat",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_cache_hit_tokens": 0,
            },
        }
        success_resp = _mock_response(json_data=success_data)

        client = DeepSeekClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            side_effect=[rate_limit_resp, success_resp],
        ):
            result = await client.generate("sys", "prompt")

        assert result.text == "ok"

    @pytest.mark.asyncio
    async def test_generate_http_error(self) -> None:
        error_resp = _mock_response(status_code=500, json_data={"error": "fail"})

        client = DeepSeekClient()
        with patch.object(
            httpx.AsyncClient,
            "post",
            new_callable=AsyncMock,
            return_value=error_resp,
        ), pytest.raises(httpx.HTTPStatusError):
            await client.generate("sys", "prompt")

    def test_properties(self) -> None:
        client = DeepSeekClient()
        assert client.name == "deepseek"
        assert client.tier == 1
        assert isinstance(client, ProviderClient)


# ---------------------------------------------------------------------------
# CostTracker tests
# ---------------------------------------------------------------------------


class TestCostTracker:
    """Tests for the CostTracker dataclass."""

    def test_empty_tracker(self) -> None:
        tracker = CostTracker()
        assert tracker.total() == 0.0
        assert tracker.by_provider() == {}
        assert "TOTAL: $0.0000" in tracker.summary()

    def test_record_single_provider(self) -> None:
        tracker = CostTracker()
        tracker.record("anthropic", 1000, 500, 0.0052)
        assert abs(tracker.total() - 0.0052) < 1e-8
        assert "anthropic" in tracker.by_provider()

    def test_record_multiple_providers(self) -> None:
        tracker = CostTracker()
        tracker.record("anthropic", 1000, 500, 0.005)
        tracker.record("openai", 2000, 800, 0.003)
        tracker.record("anthropic", 500, 200, 0.002)

        assert abs(tracker.total() - 0.010) < 1e-8
        by_prov = tracker.by_provider()
        assert abs(by_prov["anthropic"] - 0.007) < 1e-8
        assert abs(by_prov["openai"] - 0.003) < 1e-8

    def test_summary_format(self) -> None:
        tracker = CostTracker()
        tracker.record("gemini", 5000, 1000, 0.0015)
        tracker.record("deepseek", 3000, 800, 0.0012)

        summary = tracker.summary()
        assert "Cost Summary:" in summary
        assert "gemini" in summary
        assert "deepseek" in summary
        assert "TOTAL:" in summary
        # Check token counts appear
        assert "5,000" in summary
        assert "1,000" in summary


# ---------------------------------------------------------------------------
# GenerateResult tests
# ---------------------------------------------------------------------------


class TestGenerateResult:
    """Tests for the GenerateResult dataclass."""

    def test_frozen(self) -> None:
        result = GenerateResult(
            text="hello",
            input_tokens=10,
            output_tokens=5,
            model="test",
            cost=0.001,
            latency_ms=50.0,
        )
        with pytest.raises(AttributeError):
            result.text = "modified"  # type: ignore[misc]

    def test_fields(self) -> None:
        result = GenerateResult(
            text="output",
            input_tokens=100,
            output_tokens=50,
            model="model-v1",
            cost=0.005,
            latency_ms=123.4,
        )
        assert result.text == "output"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.model == "model-v1"
        assert result.cost == 0.005
        assert result.latency_ms == 123.4
