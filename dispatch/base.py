"""Abstract base class for LLM provider clients and shared data types."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerateResult:
    """Result from a single LLM generation call."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str
    cost: float
    latency_ms: float


@dataclass
class CostTracker:
    """Accumulates per-provider API spend for budget monitoring."""

    _records: dict[str, float] = field(default_factory=dict)
    _input_tokens: dict[str, int] = field(default_factory=dict)
    _output_tokens: dict[str, int] = field(default_factory=dict)

    def record(
        self,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """Record token usage and cost for a provider."""
        self._records[provider] = self._records.get(provider, 0.0) + cost
        self._input_tokens[provider] = (
            self._input_tokens.get(provider, 0) + input_tokens
        )
        self._output_tokens[provider] = (
            self._output_tokens.get(provider, 0) + output_tokens
        )

    def total(self) -> float:
        """Return total spend across all providers."""
        return sum(self._records.values())

    def by_provider(self) -> dict[str, float]:
        """Return spend broken down by provider name."""
        return dict(self._records)

    def summary(self) -> str:
        """Return a human-readable cost summary."""
        lines: list[str] = ["Cost Summary:"]
        for provider, cost in sorted(self._records.items()):
            inp = self._input_tokens.get(provider, 0)
            out = self._output_tokens.get(provider, 0)
            lines.append(
                f"  {provider}: ${cost:.4f} "
                f"(input={inp:,} tokens, output={out:,} tokens)"
            )
        lines.append(f"  TOTAL: ${self.total():.4f}")
        return "\n".join(lines)


class ProviderClient(ABC):
    """Abstract base class for LLM API provider clients.

    All provider implementations must subclass this and implement
    the generate method. generate_batch has a default sequential
    implementation that subclasses can override with batch APIs.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider display name (e.g. 'anthropic', 'openai')."""

    @property
    @abstractmethod
    def tier(self) -> int:
        """Provider tier: 1 (low cost bulk) or 2 (high capability)."""

    @property
    @abstractmethod
    def cost_per_input_mtok(self) -> float:
        """Cost in USD per million input tokens."""

    @property
    @abstractmethod
    def cost_per_output_mtok(self) -> float:
        """Cost in USD per million output tokens."""

    @abstractmethod
    async def generate(self, system: str, prompt: str) -> GenerateResult:
        """Generate a completion from the provider.

        Args:
            system: System prompt content.
            prompt: User prompt content.

        Returns:
            GenerateResult with text, token counts, cost, and latency.
        """

    async def generate_batch(
        self, system: str, prompts: list[str]
    ) -> list[GenerateResult]:
        """Generate completions for multiple prompts.

        Default implementation calls generate() sequentially.
        Subclasses should override with batch API support where available.

        Args:
            system: System prompt content (shared across all prompts).
            prompts: List of user prompts.

        Returns:
            List of GenerateResult, one per prompt.
        """
        results: list[GenerateResult] = []
        for prompt in prompts:
            result = await self.generate(system, prompt)
            results.append(result)
        return results

    def _compute_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Compute cost in USD for a given token count."""
        input_cost = (input_tokens / 1_000_000) * self.cost_per_input_mtok
        output_cost = (output_tokens / 1_000_000) * self.cost_per_output_mtok
        return input_cost + output_cost

    @staticmethod
    def _now_ms() -> float:
        """Return current time in milliseconds (for latency tracking)."""
        return time.monotonic() * 1000
