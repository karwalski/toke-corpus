"""Trial scorer and model pool allocator.

Consumes ``TrialResults`` from the trial runner, computes composite scores
per model, drops poor performers, and recommends a workload allocation
across surviving models.

Story 8.1.5 — Model capability trial framework.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from trial.runner import TrialResults, TrialTaskResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring weights (Section 4.3 of cloud-corpus-proposal.md)
# ---------------------------------------------------------------------------

WEIGHT_FIRST_PASS: float = 0.40
WEIGHT_CORRECTION: float = 0.20
WEIGHT_COST: float = 0.10
WEIGHT_DIFF_AGREEMENT: float = 0.30

# Baseline value for diff agreement during trial (no diff test available).
DIFF_AGREEMENT_BASELINE: float = 0.50

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ModelScore:
    """Composite score for a single model after the trial."""

    provider_name: str
    first_pass_compile_rate: float
    correction_success_rate: float
    diff_agreement_rate: float
    cost_per_accepted: float
    composite_score: float
    passed: bool
    per_category: dict[str, float] = field(default_factory=dict)


@dataclass
class ModelScorecard:
    """Full scorecard across all trialled models."""

    scores: list[ModelScore] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    trial_task_count: int = 0


@dataclass
class PoolAllocation:
    """Recommended workload allocation after scoring.

    Each entry is ``(provider_name, fraction)`` where fractions within
    a tier sum to that tier's share.
    """

    tier1: list[tuple[str, float]] = field(default_factory=list)
    tier2: list[tuple[str, float]] = field(default_factory=list)
    tier2_minimum_pct: float = 0.40


# ---------------------------------------------------------------------------
# TrialScorer
# ---------------------------------------------------------------------------


class TrialScorer:
    """Score trial results and produce a model scorecard + allocation."""

    def __init__(
        self,
        results: TrialResults,
        min_compile_rate: float = 0.50,
    ) -> None:
        self._results = results
        self._min_compile_rate = min_compile_rate

    # -- public API ---------------------------------------------------------

    def score(self) -> ModelScorecard:
        """Compute composite scores per model.

        Returns a ``ModelScorecard`` with per-model scores and a list of
        dropped providers that fell below ``min_compile_rate``.
        """
        by_provider = self._group_by_provider()
        task_ids = {r.task_id for r in self._results.results}

        scorecard = ModelScorecard(trial_task_count=len(task_ids))

        # Compute raw cost-efficiency values so we can normalise later.
        raw_scores: list[tuple[str, float, float, float, float, dict[str, float]]] = []

        for provider_name, provider_results in sorted(by_provider.items()):
            total = len(provider_results)
            if total == 0:
                continue

            compiled = sum(1 for r in provider_results if r.compiled)
            first_pass_rate = compiled / total

            # Correction success rate: of the tasks that failed first pass,
            # how many were fixed by the correction attempt?
            failed = [r for r in provider_results if not r.compiled]
            correction_successes = sum(
                1 for r in failed if r.correction_compiled
            )
            correction_rate = (
                correction_successes / len(failed) if failed else 0.0
            )

            # Cost per accepted program (compiled or correction_compiled).
            accepted = sum(
                1
                for r in provider_results
                if r.compiled or r.correction_compiled
            )
            total_cost = sum(r.cost for r in provider_results)
            cost_per_accepted = total_cost / accepted if accepted > 0 else float("inf")

            # Per-category first-pass compile rate.
            per_category = self._per_category_rates(provider_results)

            raw_scores.append((
                provider_name,
                first_pass_rate,
                correction_rate,
                cost_per_accepted,
                total_cost,
                per_category,
            ))

        # Normalise cost efficiency: 1/cost_per_accepted, scaled so the
        # most cost-efficient model gets 1.0 and the least gets close to 0.
        cost_efficiencies = [
            1.0 / cpa if cpa > 0 and cpa != float("inf") else 0.0
            for _, _, _, cpa, _, _ in raw_scores
        ]
        max_eff = max(cost_efficiencies) if cost_efficiencies else 1.0
        if max_eff == 0:
            max_eff = 1.0

        for idx, (
            pname, fp_rate, corr_rate, cpa, _total_cost, per_cat
        ) in enumerate(raw_scores):
            norm_cost = cost_efficiencies[idx] / max_eff

            composite = (
                WEIGHT_FIRST_PASS * fp_rate
                + WEIGHT_CORRECTION * corr_rate
                + WEIGHT_COST * norm_cost
                + WEIGHT_DIFF_AGREEMENT * DIFF_AGREEMENT_BASELINE
            )

            passed = fp_rate >= self._min_compile_rate

            ms = ModelScore(
                provider_name=pname,
                first_pass_compile_rate=fp_rate,
                correction_success_rate=corr_rate,
                diff_agreement_rate=DIFF_AGREEMENT_BASELINE,
                cost_per_accepted=cpa,
                composite_score=round(composite, 4),
                passed=passed,
                per_category=per_cat,
            )
            scorecard.scores.append(ms)
            if not passed:
                scorecard.dropped.append(pname)
                logger.info(
                    "Dropped %s: first-pass compile rate %.1f%% < %.1f%% min",
                    pname,
                    fp_rate * 100,
                    self._min_compile_rate * 100,
                )

        scorecard.scores.sort(key=lambda s: s.composite_score, reverse=True)
        return scorecard

    def recommend(self) -> PoolAllocation:
        """Decide which models to use and at what percentage.

        Tier 2 models always receive at least ``tier2_minimum_pct`` of the
        total workload. Surviving Tier 1 models share the remainder
        proportionally by composite score. Tier 2 models also share their
        allocation proportionally.

        Models that failed the trial (``passed=False``) are excluded.
        """
        scorecard = self.score()
        allocation = PoolAllocation()

        survivors = [s for s in scorecard.scores if s.passed]
        if not survivors:
            logger.warning("No models passed the trial — empty allocation")
            return allocation

        # Split survivors into tiers based on composite score.
        # Top tier: top 50% by score (at least 1). Rest are tier 2.
        # This is a simple heuristic; the orchestrator can override.
        sorted_survivors = sorted(
            survivors, key=lambda s: s.composite_score, reverse=True
        )

        # At least 1 model in tier 1.
        tier1_count = max(1, len(sorted_survivors) // 2)
        tier1_models = sorted_survivors[:tier1_count]
        tier2_models = sorted_survivors[tier1_count:]

        tier1_share = 1.0 - allocation.tier2_minimum_pct
        tier2_share = allocation.tier2_minimum_pct

        # Proportional allocation within tier 1.
        tier1_total_score = sum(m.composite_score for m in tier1_models)
        if tier1_total_score > 0:
            allocation.tier1 = [
                (
                    m.provider_name,
                    round(
                        tier1_share * m.composite_score / tier1_total_score, 4
                    ),
                )
                for m in tier1_models
            ]
        else:
            equal = round(tier1_share / len(tier1_models), 4)
            allocation.tier1 = [(m.provider_name, equal) for m in tier1_models]

        # Proportional allocation within tier 2. If no tier 2 models,
        # give the tier 2 share to tier 1 proportionally.
        if tier2_models:
            tier2_total_score = sum(m.composite_score for m in tier2_models)
            if tier2_total_score > 0:
                allocation.tier2 = [
                    (
                        m.provider_name,
                        round(
                            tier2_share
                            * m.composite_score
                            / tier2_total_score,
                            4,
                        ),
                    )
                    for m in tier2_models
                ]
            else:
                equal = round(tier2_share / len(tier2_models), 4)
                allocation.tier2 = [
                    (m.provider_name, equal) for m in tier2_models
                ]
        else:
            # All survivors are tier 1 — redistribute tier 2 share.
            if tier1_total_score > 0:
                allocation.tier1 = [
                    (
                        m.provider_name,
                        round(m.composite_score / tier1_total_score, 4),
                    )
                    for m in tier1_models
                ]
            allocation.tier2_minimum_pct = 0.0

        return allocation

    # -- persistence ---------------------------------------------------------

    @staticmethod
    def save_scorecard(scorecard: ModelScorecard, path: Path) -> None:
        """Serialise the scorecard to JSON."""
        data = {
            "trial_task_count": scorecard.trial_task_count,
            "scores": [asdict(s) for s in scorecard.scores],
            "dropped": scorecard.dropped,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        logger.info("Scorecard saved to %s", path)

    @staticmethod
    def load_scorecard(path: Path) -> ModelScorecard:
        """Deserialise a scorecard from JSON."""
        data = json.loads(path.read_text())
        scores = [
            ModelScore(**{k: v for k, v in s.items()})
            for s in data["scores"]
        ]
        return ModelScorecard(
            scores=scores,
            dropped=data["dropped"],
            trial_task_count=data["trial_task_count"],
        )

    # -- internals -----------------------------------------------------------

    def _group_by_provider(self) -> dict[str, list[TrialTaskResult]]:
        """Group trial results by provider name."""
        groups: dict[str, list[TrialTaskResult]] = {}
        for r in self._results.results:
            groups.setdefault(r.provider_name, []).append(r)
        return groups

    @staticmethod
    def _per_category_rates(
        results: list[TrialTaskResult],
    ) -> dict[str, float]:
        """Compute first-pass compile rate per category.

        Category is inferred from the task_id prefix (e.g. ``A-MTH`` from
        ``A-MTH-0001``).
        """
        by_cat: dict[str, list[TrialTaskResult]] = {}
        for r in results:
            # task_id format: A-{CAT}-{NNNN} — category is first two parts.
            parts = r.task_id.split("-")
            cat = "-".join(parts[:2]) if len(parts) >= 2 else r.task_id
            by_cat.setdefault(cat, []).append(r)

        rates: dict[str, float] = {}
        for cat, cat_results in sorted(by_cat.items()):
            total = len(cat_results)
            compiled = sum(1 for r in cat_results if r.compiled)
            rates[cat] = round(compiled / total, 4) if total > 0 else 0.0
        return rates
