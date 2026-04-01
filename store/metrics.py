"""Pipeline metrics collector and dashboard.

Tracks dispatched/accepted/failed/escalated counts per model and
category, API cost by provider, and estimated remaining time. Writes
periodic snapshots to ``metrics_dir/progress.json``.

Story 8.1.9 — Corpus writer and metrics dashboard.
"""
from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ModelMetrics:
    """Per-model generation metrics."""

    dispatched: int = 0
    accepted: int = 0
    failed: int = 0
    acceptance_rate: float = 0.0
    cost: float = 0.0


@dataclass
class CategoryMetrics:
    """Per-category generation metrics."""

    dispatched: int = 0
    accepted: int = 0
    failed: int = 0
    acceptance_rate: float = 0.0


@dataclass
class CostSummary:
    """Aggregated cost information."""

    api_total: float = 0.0
    by_provider: dict[str, float] = field(default_factory=dict)
    compute_estimate: float = 0.0


@dataclass
class PipelineMetrics:
    """Full pipeline metrics snapshot."""

    started_at: str = ""
    updated_at: str = ""
    total_tasks: int = 0
    dispatched: int = 0
    pending: int = 0
    accepted: int = 0
    failed: int = 0
    escalated: int = 0
    per_model: dict[str, ModelMetrics] = field(default_factory=dict)
    per_category: dict[str, CategoryMetrics] = field(default_factory=dict)
    cost: CostSummary = field(default_factory=CostSummary)
    estimated_remaining_hours: float = 0.0

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON encoding."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PipelineMetrics:
        """Deserialise from a plain dict."""
        per_model = {
            k: ModelMetrics(**v) for k, v in data.get("per_model", {}).items()
        }
        per_category = {
            k: CategoryMetrics(**v) for k, v in data.get("per_category", {}).items()
        }
        cost_data = data.get("cost", {})
        cost = CostSummary(
            api_total=cost_data.get("api_total", 0.0),
            by_provider=cost_data.get("by_provider", {}),
            compute_estimate=cost_data.get("compute_estimate", 0.0),
        )
        return cls(
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
            total_tasks=data.get("total_tasks", 0),
            dispatched=data.get("dispatched", 0),
            pending=data.get("pending", 0),
            accepted=data.get("accepted", 0),
            failed=data.get("failed", 0),
            escalated=data.get("escalated", 0),
            per_model=per_model,
            per_category=per_category,
            cost=cost,
            estimated_remaining_hours=data.get("estimated_remaining_hours", 0.0),
        )


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Collect and persist pipeline execution metrics.

    Auto-saves to ``metrics_dir/progress.json`` every *auto_save_interval*
    records (default 100).
    """

    def __init__(
        self,
        total_tasks: int,
        metrics_dir: str,
        auto_save_interval: int = 100,
    ) -> None:
        self._total_tasks = total_tasks
        self._metrics_dir = Path(metrics_dir)
        self._metrics_dir.mkdir(parents=True, exist_ok=True)
        self._auto_save_interval = auto_save_interval
        self._records_since_save = 0

        now = datetime.now(timezone.utc).isoformat()
        self._started_at = now
        self._start_monotonic = time.monotonic()

        # Counters.
        self._dispatched = 0
        self._accepted = 0
        self._failed = 0
        self._escalated = 0

        # Per-model tracking.
        self._model_dispatched: dict[str, int] = {}
        self._model_accepted: dict[str, int] = {}
        self._model_failed: dict[str, int] = {}
        self._model_cost: dict[str, float] = {}

        # Per-category tracking.
        self._cat_dispatched: dict[str, int] = {}
        self._cat_accepted: dict[str, int] = {}
        self._cat_failed: dict[str, int] = {}

        # Cost tracking.
        self._cost_by_provider: dict[str, float] = {}

        logger.debug(
            "MetricsCollector initialised: total_tasks=%d, metrics_dir=%s",
            total_tasks,
            self._metrics_dir,
        )

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    def record_dispatch(
        self, task_id: str, provider: str, category: str
    ) -> None:
        """Record that a task was dispatched to a provider."""
        self._dispatched += 1
        self._model_dispatched[provider] = (
            self._model_dispatched.get(provider, 0) + 1
        )
        self._cat_dispatched[category] = (
            self._cat_dispatched.get(category, 0) + 1
        )
        self._maybe_auto_save()

    def record_accepted(
        self, task_id: str, provider: str, category: str, cost: float
    ) -> None:
        """Record that a task was accepted after validation."""
        self._accepted += 1
        self._model_accepted[provider] = (
            self._model_accepted.get(provider, 0) + 1
        )
        self._model_cost[provider] = self._model_cost.get(provider, 0.0) + cost
        self._cat_accepted[category] = (
            self._cat_accepted.get(category, 0) + 1
        )
        self._cost_by_provider[provider] = (
            self._cost_by_provider.get(provider, 0.0) + cost
        )
        self._maybe_auto_save()

    def record_failed(
        self, task_id: str, provider: str, category: str, cost: float
    ) -> None:
        """Record that a task failed validation."""
        self._failed += 1
        self._model_failed[provider] = (
            self._model_failed.get(provider, 0) + 1
        )
        self._model_cost[provider] = self._model_cost.get(provider, 0.0) + cost
        self._cat_failed[category] = self._cat_failed.get(category, 0) + 1
        self._cost_by_provider[provider] = (
            self._cost_by_provider.get(provider, 0.0) + cost
        )
        self._maybe_auto_save()

    def record_escalated(
        self, task_id: str, from_provider: str, to_provider: str
    ) -> None:
        """Record that a task was escalated from one provider to another."""
        self._escalated += 1
        self._maybe_auto_save()

    # ------------------------------------------------------------------
    # Snapshot and persistence
    # ------------------------------------------------------------------

    def snapshot(self) -> PipelineMetrics:
        """Return the current metrics as a PipelineMetrics snapshot."""
        now = datetime.now(timezone.utc).isoformat()

        # Build per-model metrics.
        all_models = set(self._model_dispatched) | set(self._model_accepted) | set(self._model_failed)
        per_model: dict[str, ModelMetrics] = {}
        for model in sorted(all_models):
            dispatched = self._model_dispatched.get(model, 0)
            accepted = self._model_accepted.get(model, 0)
            failed = self._model_failed.get(model, 0)
            rate = accepted / dispatched if dispatched > 0 else 0.0
            per_model[model] = ModelMetrics(
                dispatched=dispatched,
                accepted=accepted,
                failed=failed,
                acceptance_rate=round(rate, 4),
                cost=round(self._model_cost.get(model, 0.0), 6),
            )

        # Build per-category metrics.
        all_cats = set(self._cat_dispatched) | set(self._cat_accepted) | set(self._cat_failed)
        per_category: dict[str, CategoryMetrics] = {}
        for cat in sorted(all_cats):
            dispatched = self._cat_dispatched.get(cat, 0)
            accepted = self._cat_accepted.get(cat, 0)
            failed = self._cat_failed.get(cat, 0)
            rate = accepted / dispatched if dispatched > 0 else 0.0
            per_category[cat] = CategoryMetrics(
                dispatched=dispatched,
                accepted=accepted,
                failed=failed,
                acceptance_rate=round(rate, 4),
            )

        # Cost summary.
        api_total = sum(self._cost_by_provider.values())
        cost = CostSummary(
            api_total=round(api_total, 6),
            by_provider={k: round(v, 6) for k, v in sorted(self._cost_by_provider.items())},
            compute_estimate=0.0,
        )

        # Estimated remaining hours based on acceptance rate and elapsed time.
        pending = self._total_tasks - self._accepted - self._failed
        estimated_remaining = self._estimate_remaining_hours()

        return PipelineMetrics(
            started_at=self._started_at,
            updated_at=now,
            total_tasks=self._total_tasks,
            dispatched=self._dispatched,
            pending=max(0, pending),
            accepted=self._accepted,
            failed=self._failed,
            escalated=self._escalated,
            per_model=per_model,
            per_category=per_category,
            cost=cost,
            estimated_remaining_hours=round(estimated_remaining, 2),
        )

    def save(self) -> None:
        """Write current metrics to ``metrics_dir/progress.json``."""
        metrics = self.snapshot()
        out_path = self._metrics_dir / "progress.json"

        # Atomic write.
        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(self._metrics_dir), suffix=".tmp", prefix=".metrics_"
        )
        tmp_path = Path(tmp_path_str)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(metrics.to_dict(), fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            tmp_path.rename(out_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._records_since_save = 0
        logger.debug("Saved metrics to %s", out_path)

    def print_summary(self) -> None:
        """Log a one-line progress summary."""
        rate = (
            self._accepted / self._dispatched
            if self._dispatched > 0
            else 0.0
        )
        api_total = sum(self._cost_by_provider.values())
        logger.info(
            "Progress: %d/%d dispatched, %d accepted (%.1f%%), "
            "%d failed, %d escalated, cost=$%.2f",
            self._dispatched,
            self._total_tasks,
            self._accepted,
            rate * 100,
            self._failed,
            self._escalated,
            api_total,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _maybe_auto_save(self) -> None:
        """Auto-save every N records."""
        self._records_since_save += 1
        if self._records_since_save >= self._auto_save_interval:
            self.save()

    def _estimate_remaining_hours(self) -> float:
        """Estimate hours remaining based on current throughput.

        Uses accepted + failed as "completed" to compute throughput,
        then estimates how long the remaining tasks will take.
        """
        completed = self._accepted + self._failed
        if completed == 0:
            return 0.0

        elapsed_s = time.monotonic() - self._start_monotonic
        if elapsed_s <= 0:
            return 0.0

        tasks_per_second = completed / elapsed_s
        remaining = self._total_tasks - completed
        if remaining <= 0:
            return 0.0

        return (remaining / tasks_per_second) / 3600.0
