"""Tests for dispatch.pool — model pool manager and task router.

Story 8.1.6.
"""
from __future__ import annotations

import asyncio
import collections
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from dispatch.base import CostTracker, GenerateResult, ProviderClient
from dispatch.pool import PoolConfig, PoolManager, _REBALANCE_WINDOW
from generator.curriculum import TaskSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    category: str = "A-MTH",
    task_id: str = "A-MTH-0001",
    difficulty: int = 1,
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        category=category,
        description="test task",
        expected_signature="F=f():i64",
        difficulty=difficulty,
    )


def _make_result(provider: str = "mock", cost: float = 0.001) -> GenerateResult:
    return GenerateResult(
        text="M=test;F=f():i64{<1};",
        input_tokens=100,
        output_tokens=50,
        model=provider,
        cost=cost,
        latency_ms=50.0,
    )


class MockProvider(ProviderClient):
    """Fake provider for testing."""

    def __init__(
        self,
        name: str,
        tier: int,
        *,
        should_fail: bool = False,
    ) -> None:
        self._name = name
        self._tier = tier
        self._should_fail = should_fail
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def tier(self) -> int:
        return self._tier

    @property
    def cost_per_input_mtok(self) -> float:
        return 0.15

    @property
    def cost_per_output_mtok(self) -> float:
        return 0.60

    async def generate(self, system: str, prompt: str) -> GenerateResult:
        self.call_count += 1
        if self._should_fail:
            raise RuntimeError(f"{self._name} failed")
        return _make_result(self._name)


def _default_config() -> PoolConfig:
    return PoolConfig(
        tier1_providers=[("gemini", 0.5), ("deepseek", 0.5)],
        tier2_providers=[("haiku", 0.6), ("gpt4", 0.4)],
        tier2_minimum_pct=0.40,
        category_tier2_overrides={"A-ERR": 0.60, "A-SRT": 0.50},
    )


def _default_providers() -> dict[str, ProviderClient]:
    return {
        "gemini": MockProvider("gemini", tier=1),
        "deepseek": MockProvider("deepseek", tier=1),
        "haiku": MockProvider("haiku", tier=2),
        "gpt4": MockProvider("gpt4", tier=2),
    }


def _make_pool(
    config: PoolConfig | None = None,
    providers: dict[str, ProviderClient] | None = None,
    seed: int = 42,
) -> PoolManager:
    return PoolManager(
        providers=providers or _default_providers(),
        config=config or _default_config(),
        cost_tracker=CostTracker(),
        seed=seed,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAssignTierAllocation:
    """Assignment respects tier allocation over many tasks."""

    def test_tier_split_within_tolerance(self) -> None:
        """Over 1000 tasks, the Tier 2 share should be close to the
        configured 40% default."""
        pool = _make_pool()
        task = _make_task()

        tier2_names = {"haiku", "gpt4"}
        tier2_count = 0
        total = 1000

        for _ in range(total):
            provider = pool.assign(task)
            if provider.name in tier2_names:
                tier2_count += 1

        tier2_pct = tier2_count / total
        # Allow 5% tolerance around the 40% target.
        assert 0.35 <= tier2_pct <= 0.45, (
            f"Tier 2 share {tier2_pct:.2%} outside expected range [35%, 45%]"
        )

    def test_within_tier_proportional(self) -> None:
        """Within Tier 1, gemini (50%) and deepseek (50%) should each get
        roughly half of the Tier 1 assignments."""
        pool = _make_pool()
        task = _make_task()

        counts: dict[str, int] = collections.defaultdict(int)
        total = 2000

        for _ in range(total):
            provider = pool.assign(task)
            counts[provider.name] += 1

        tier1_total = counts["gemini"] + counts["deepseek"]
        if tier1_total > 0:
            gemini_share = counts["gemini"] / tier1_total
            # 50% target with 10% tolerance.
            assert 0.40 <= gemini_share <= 0.60, (
                f"gemini share {gemini_share:.2%} outside [40%, 60%]"
            )


class TestCategoryOverrides:
    """Category-specific Tier 2 overrides shift the tier boundary."""

    def test_err_category_gets_more_tier2(self) -> None:
        """A-ERR has a 60% Tier 2 override."""
        pool = _make_pool()
        task = _make_task(category="A-ERR", task_id="A-ERR-0001")

        tier2_names = {"haiku", "gpt4"}
        tier2_count = 0
        total = 1000

        for _ in range(total):
            provider = pool.assign(task)
            if provider.name in tier2_names:
                tier2_count += 1

        tier2_pct = tier2_count / total
        # 60% target with 5% tolerance.
        assert 0.55 <= tier2_pct <= 0.65, (
            f"A-ERR tier 2 share {tier2_pct:.2%} outside [55%, 65%]"
        )

    def test_mth_category_uses_default(self) -> None:
        """A-MTH has no override, should use 40% default."""
        pool = _make_pool()
        task = _make_task(category="A-MTH")

        tier2_names = {"haiku", "gpt4"}
        tier2_count = 0
        total = 1000

        for _ in range(total):
            provider = pool.assign(task)
            if provider.name in tier2_names:
                tier2_count += 1

        tier2_pct = tier2_count / total
        assert 0.35 <= tier2_pct <= 0.45


class TestEscalationChain:
    """Escalation promotes failures through the tier chain."""

    @pytest.mark.asyncio
    async def test_escalation_tries_tier2_providers(self) -> None:
        """After a Tier 1 failure, escalation should try a Tier 2 provider."""
        pool = _make_pool()
        task = _make_task()

        result = await pool.escalate(
            task, "system", "prompt", failed_provider="gemini"
        )
        assert result is not None
        # Should have been handled by a Tier 2 provider.
        assert result.model in {"haiku", "gpt4"}

    @pytest.mark.asyncio
    async def test_escalation_skips_failed_provider(self) -> None:
        """The failed provider should not be retried in escalation."""
        providers = _default_providers()
        pool = _make_pool(providers=providers)
        task = _make_task()

        result = await pool.escalate(
            task, "system", "prompt", failed_provider="haiku"
        )
        assert result is not None
        # haiku was the failed provider, so gpt4 should handle it.
        assert result.model == "gpt4"

    @pytest.mark.asyncio
    async def test_escalation_returns_none_when_all_fail(self) -> None:
        """When all Tier 2 providers fail, escalation returns None."""
        providers: dict[str, ProviderClient] = {
            "gemini": MockProvider("gemini", tier=1),
            "deepseek": MockProvider("deepseek", tier=1),
            "haiku": MockProvider("haiku", tier=2, should_fail=True),
            "gpt4": MockProvider("gpt4", tier=2, should_fail=True),
        }
        pool = _make_pool(providers=providers)
        task = _make_task()

        result = await pool.escalate(
            task, "system", "prompt", failed_provider="gemini"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_escalation_records_failure_and_escalation_counts(self) -> None:
        """Escalation should update the failed and escalated counters."""
        pool = _make_pool()
        task = _make_task()

        await pool.escalate(task, "system", "prompt", failed_provider="gemini")

        prog = pool.progress()
        assert prog.failed.get("gemini", 0) == 1
        assert prog.escalated.get("gemini", 0) == 1


class TestAutoRebalance:
    """Auto-rebalance triggers when a provider's failure rate is too high."""

    @pytest.mark.asyncio
    async def test_high_failure_rate_halves_allocation(self) -> None:
        """If a Tier 1 provider fails >50% over the rebalance window,
        its allocation should be halved."""
        providers = _default_providers()
        pool = _make_pool(providers=providers, seed=1)

        # Manually inject failures into the rolling window to trigger
        # auto-rebalance.  We need _REBALANCE_WINDOW entries with >50% False.
        for _ in range(_REBALANCE_WINDOW):
            pool._recent["gemini"].append(False)

        # The next record_failure call should trigger auto-rebalance.
        pool._record_failure("gemini")

        # gemini started at 0.50, should now be 0.25.
        assert pool._tier1_alloc["gemini"] == pytest.approx(0.25, abs=0.01)
        # deepseek should have absorbed the freed share.
        assert pool._tier1_alloc["deepseek"] == pytest.approx(0.75, abs=0.01)

    @pytest.mark.asyncio
    async def test_no_rebalance_below_window_size(self) -> None:
        """Auto-rebalance should not trigger if the window is not full."""
        pool = _make_pool()

        # Add fewer than _REBALANCE_WINDOW failures.
        for _ in range(_REBALANCE_WINDOW - 10):
            pool._recent["gemini"].append(False)

        pool._record_failure("gemini")

        # Allocation should be unchanged.
        assert pool._tier1_alloc["gemini"] == pytest.approx(0.50, abs=0.01)


class TestProgressTracking:
    """Progress snapshot accurately reflects dispatched state."""

    @pytest.mark.asyncio
    async def test_generate_updates_counters(self) -> None:
        """A successful generate call increments dispatched and accepted."""
        pool = _make_pool()
        task = _make_task()

        await pool.generate(task, "system", "prompt")

        prog = pool.progress()
        total_dispatched = sum(prog.dispatched.values())
        total_accepted = sum(prog.accepted.values())
        assert total_dispatched == 1
        assert total_accepted == 1

    @pytest.mark.asyncio
    async def test_cost_recorded_after_generate(self) -> None:
        """Cost tracker should have a non-zero total after generation."""
        pool = _make_pool()
        task = _make_task()

        await pool.generate(task, "system", "prompt")

        prog = pool.progress()
        assert prog.cost.total() > 0

    def test_progress_starts_empty(self) -> None:
        """Fresh pool should have all-zero progress."""
        pool = _make_pool()
        prog = pool.progress()
        assert sum(prog.dispatched.values()) == 0
        assert sum(prog.accepted.values()) == 0
        assert sum(prog.failed.values()) == 0
        assert sum(prog.escalated.values()) == 0


class TestManualRebalance:
    """Manual rebalance adjusts allocation and redistributes."""

    def test_rebalance_redistributes(self) -> None:
        """Halving gemini should give the freed share to deepseek."""
        pool = _make_pool()
        pool.rebalance("gemini", 0.25)

        assert pool._tier1_alloc["gemini"] == pytest.approx(0.25, abs=0.01)
        assert pool._tier1_alloc["deepseek"] == pytest.approx(0.75, abs=0.01)

    def test_rebalance_tier2(self) -> None:
        """Rebalance works on Tier 2 providers too."""
        pool = _make_pool()
        pool.rebalance("haiku", 0.30)

        assert pool._tier2_alloc["haiku"] == pytest.approx(0.30, abs=0.01)
        assert pool._tier2_alloc["gpt4"] == pytest.approx(0.70, abs=0.01)

    def test_rebalance_unknown_provider_raises(self) -> None:
        """Rebalancing an unknown provider should raise ValueError."""
        pool = _make_pool()
        with pytest.raises(ValueError, match="Unknown provider"):
            pool.rebalance("nonexistent", 0.50)


class TestReproducibility:
    """Assignment is reproducible with the same seed."""

    def test_same_seed_same_assignments(self) -> None:
        """Two pools with the same seed produce identical assignment
        sequences."""
        task = _make_task()

        providers_a = _default_providers()
        providers_b = _default_providers()

        pool_a = _make_pool(providers=providers_a, seed=99)
        pool_b = _make_pool(providers=providers_b, seed=99)

        seq_a = [pool_a.assign(task).name for _ in range(200)]
        seq_b = [pool_b.assign(task).name for _ in range(200)]

        assert seq_a == seq_b

    def test_different_seed_different_assignments(self) -> None:
        """Two pools with different seeds produce different sequences."""
        task = _make_task()

        pool_a = _make_pool(seed=1)
        pool_b = _make_pool(seed=2)

        seq_a = [pool_a.assign(task).name for _ in range(200)]
        seq_b = [pool_b.assign(task).name for _ in range(200)]

        assert seq_a != seq_b
