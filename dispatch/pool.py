"""Model pool manager and task router.

Routes generation tasks to the right provider based on trial scores,
category difficulty, and tier allocation rules.  At least 40% of tasks
go to Tier 2 models by default.

Story 8.1.6 -- Model pool manager and task router.
"""
from __future__ import annotations

import collections
import logging
import random
from dataclasses import dataclass, field

from dispatch.base import CostTracker, GenerateResult, ProviderClient
from generator.curriculum import TaskSpec

logger = logging.getLogger(__name__)

# Number of recent outcomes to consider when checking a provider's
# failure rate for auto-rebalance.
_REBALANCE_WINDOW: int = 100

# Failure rate threshold that triggers automatic allocation reduction.
_FAILURE_RATE_THRESHOLD: float = 0.50


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class PoolConfig:
    """Configuration for the model pool manager.

    Attributes:
        tier1_providers: List of ``(provider_name, allocation_fraction)``
            pairs for Tier 1.  Fractions should sum to 1.0 within the tier.
        tier2_providers: Same for Tier 2.
        tier2_minimum_pct: Minimum fraction of total tasks routed to Tier 2.
        category_tier2_overrides: Per-category Tier 2 fraction.  Categories
            not present here use ``tier2_minimum_pct``.
    """

    tier1_providers: list[tuple[str, float]]
    tier2_providers: list[tuple[str, float]]
    tier2_minimum_pct: float = 0.40
    category_tier2_overrides: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------


@dataclass
class PoolProgress:
    """Snapshot of pool dispatch progress."""

    dispatched: dict[str, int] = field(default_factory=dict)
    accepted: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    escalated: dict[str, int] = field(default_factory=dict)
    cost: CostTracker = field(default_factory=CostTracker)


# ---------------------------------------------------------------------------
# Pool manager
# ---------------------------------------------------------------------------


class PoolManager:
    """Assigns tasks to providers and manages generation, escalation, and
    auto-rebalance.

    The manager is stateful: it tracks per-provider dispatch counts and
    recent outcomes so that it can keep actual allocation close to target
    and degrade gracefully when a provider starts failing.
    """

    def __init__(
        self,
        providers: dict[str, ProviderClient],
        config: PoolConfig,
        cost_tracker: CostTracker,
        *,
        seed: int = 42,
    ) -> None:
        self._providers = providers
        self._config = config
        self._cost = cost_tracker
        self._rng = random.Random(seed)

        # Live allocation fractions (mutated by rebalance).
        self._tier1_alloc: dict[str, float] = dict(config.tier1_providers)
        self._tier2_alloc: dict[str, float] = dict(config.tier2_providers)

        # Counters.
        self._dispatched: dict[str, int] = collections.defaultdict(int)
        self._accepted: dict[str, int] = collections.defaultdict(int)
        self._failed: dict[str, int] = collections.defaultdict(int)
        self._escalated: dict[str, int] = collections.defaultdict(int)

        # Rolling window of recent outcomes per provider for auto-rebalance.
        # Each entry is True (success) or False (failure).
        self._recent: dict[str, collections.deque[bool]] = {
            name: collections.deque(maxlen=_REBALANCE_WINDOW)
            for name in providers
        }

    # -- public API ---------------------------------------------------------

    def assign(self, task: TaskSpec) -> ProviderClient:
        """Decide which provider handles *task*.

        The algorithm:
        1. Look up the effective Tier 2 percentage for the task's category.
        2. Roll against the tier boundary to pick Tier 1 or Tier 2.
        3. Within the chosen tier, select a provider proportional to its
           current allocation fraction, with a correction toward the target
           based on how many tasks have been dispatched so far.
        """
        tier2_pct = self._effective_tier2_pct(task.category)
        tier = 2 if self._rng.random() < tier2_pct else 1
        alloc = self._tier2_alloc if tier == 2 else self._tier1_alloc

        provider_name = self._pick_provider(alloc)
        self._dispatched[provider_name] += 1
        return self._providers[provider_name]

    async def generate(
        self,
        task: TaskSpec,
        system_prompt: str,
        prompt: str,
    ) -> GenerateResult:
        """Generate using the assigned provider and record the outcome."""
        provider = self.assign(task)
        result = await provider.generate(system_prompt, prompt)
        self._record_success(provider.name, result)
        return result

    async def escalate(
        self,
        task: TaskSpec,
        system_prompt: str,
        prompt: str,
        failed_provider: str,
    ) -> GenerateResult | None:
        """Try the next tier up after a failure.

        Escalation chain:
          Tier 1 failure -> Tier 2 primary -> Tier 2 alternate -> None

        Returns ``None`` if all providers in the escalation chain are
        exhausted.
        """
        self._record_failure(failed_provider)
        self._escalated[failed_provider] += 1

        # Build the ordered escalation list from Tier 2 providers, excluding
        # any that have already been tried for this task (represented by
        # ``failed_provider``).
        chain = [
            name
            for name, _ in sorted(
                self._tier2_alloc.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            if name != failed_provider
        ]

        for candidate_name in chain:
            if candidate_name not in self._providers:
                continue
            candidate = self._providers[candidate_name]
            try:
                result = await candidate.generate(system_prompt, prompt)
                self._dispatched[candidate_name] += 1
                self._record_success(candidate_name, result)
                logger.info(
                    "Escalation succeeded: %s -> %s for task %s",
                    failed_provider,
                    candidate_name,
                    task.task_id,
                )
                return result
            except Exception:
                logger.warning(
                    "Escalation attempt failed for %s on task %s",
                    candidate_name,
                    task.task_id,
                    exc_info=True,
                )
                self._record_failure(candidate_name)
                self._escalated[candidate_name] += 1

        logger.warning(
            "All escalation providers exhausted for task %s", task.task_id
        )
        return None

    def progress(self) -> PoolProgress:
        """Return a snapshot of current dispatch progress."""
        return PoolProgress(
            dispatched=dict(self._dispatched),
            accepted=dict(self._accepted),
            failed=dict(self._failed),
            escalated=dict(self._escalated),
            cost=self._cost,
        )

    def rebalance(self, provider_name: str, new_allocation: float) -> None:
        """Manually adjust a provider's allocation and redistribute the
        remainder proportionally among the other providers in the same tier.
        """
        if provider_name in self._tier1_alloc:
            self._rebalance_tier(self._tier1_alloc, provider_name, new_allocation)
        elif provider_name in self._tier2_alloc:
            self._rebalance_tier(self._tier2_alloc, provider_name, new_allocation)
        else:
            raise ValueError(f"Unknown provider: {provider_name}")
        logger.info(
            "Rebalanced %s to %.2f allocation", provider_name, new_allocation
        )

    # -- internals ----------------------------------------------------------

    def _effective_tier2_pct(self, category: str) -> float:
        """Return the Tier 2 fraction for *category*."""
        return self._config.category_tier2_overrides.get(
            category, self._config.tier2_minimum_pct
        )

    def _pick_provider(self, alloc: dict[str, float]) -> str:
        """Weighted random selection with drift correction.

        Computes the gap between target and actual allocation for each
        provider, adds the gap to the base weight, then performs a weighted
        random draw.  This keeps the actual distribution close to target
        over many calls without being perfectly deterministic.
        """
        total_dispatched = sum(self._dispatched.values()) or 1
        weights: dict[str, float] = {}
        for name, target_frac in alloc.items():
            actual_frac = self._dispatched[name] / total_dispatched
            gap = target_frac - actual_frac
            # Weight is the base allocation plus half the gap (damped correction).
            weight = max(target_frac + 0.5 * gap, 0.01)
            weights[name] = weight

        names = list(weights.keys())
        values = [weights[n] for n in names]
        chosen = self._rng.choices(names, weights=values, k=1)[0]
        return chosen

    def _record_success(self, provider_name: str, result: GenerateResult) -> None:
        """Record a successful generation."""
        self._accepted[provider_name] += 1
        self._recent[provider_name].append(True)
        self._cost.record(
            provider_name,
            result.input_tokens,
            result.output_tokens,
            result.cost,
        )
        self._check_auto_rebalance(provider_name)

    def _record_failure(self, provider_name: str) -> None:
        """Record a failed generation and check if auto-rebalance is needed."""
        self._failed[provider_name] += 1
        if provider_name in self._recent:
            self._recent[provider_name].append(False)
        self._check_auto_rebalance(provider_name)

    def _check_auto_rebalance(self, provider_name: str) -> None:
        """If *provider_name*'s recent failure rate exceeds the threshold,
        halve its allocation and redistribute to peers in the same tier.
        """
        window = self._recent.get(provider_name)
        if window is None or len(window) < _REBALANCE_WINDOW:
            return

        failure_rate = 1.0 - (sum(window) / len(window))
        if failure_rate <= _FAILURE_RATE_THRESHOLD:
            return

        logger.warning(
            "Auto-rebalance: %s failure rate %.1f%% over last %d tasks — "
            "halving allocation",
            provider_name,
            failure_rate * 100,
            _REBALANCE_WINDOW,
        )

        if provider_name in self._tier1_alloc:
            old = self._tier1_alloc[provider_name]
            self._rebalance_tier(self._tier1_alloc, provider_name, old / 2)
        elif provider_name in self._tier2_alloc:
            old = self._tier2_alloc[provider_name]
            self._rebalance_tier(self._tier2_alloc, provider_name, old / 2)

    @staticmethod
    def _rebalance_tier(
        alloc: dict[str, float],
        target: str,
        new_value: float,
    ) -> None:
        """Set *target*'s allocation to *new_value* and redistribute the
        freed share proportionally among the other providers in the tier.
        """
        old_value = alloc.get(target, 0.0)
        freed = old_value - new_value
        alloc[target] = new_value

        # Redistribute freed share proportionally among others.
        others_total = sum(v for k, v in alloc.items() if k != target)
        if others_total == 0:
            # Only one provider in the tier — nothing to redistribute.
            return
        for name in alloc:
            if name != target:
                alloc[name] += freed * (alloc[name] / others_total)
