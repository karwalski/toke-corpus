"""Checkpoint persistence for pipeline resumption.

Saves completed task IDs and metrics state to a JSON file so the
orchestrator can resume after interruption without re-processing
already-accepted entries.

Story 8.1.9 — Corpus writer and metrics dashboard.
"""
from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from store.metrics import PipelineMetrics

logger = logging.getLogger(__name__)


class Checkpoint:
    """Save and restore pipeline checkpoint state.

    Format::

        {
            "completed": ["task-id-1", "task-id-2", ...],
            "metrics": { ... },
            "saved_at": "2026-03-29T12:00:00+00:00"
        }
    """

    def __init__(self, checkpoint_path: str) -> None:
        self._path = Path(checkpoint_path)

    def save(
        self,
        completed_task_ids: set[str],
        metrics: PipelineMetrics,
    ) -> None:
        """Persist completed task IDs and metrics to disk.

        Write is atomic: temp file then rename.
        """
        data = {
            "completed": sorted(completed_task_ids),
            "metrics": metrics.to_dict(),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        # Ensure parent directory exists.
        self._path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(self._path.parent),
            suffix=".tmp",
            prefix=".checkpoint_",
        )
        tmp_path = Path(tmp_path_str)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            tmp_path.rename(self._path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        logger.debug(
            "Checkpoint saved: %d completed tasks -> %s",
            len(completed_task_ids),
            self._path,
        )

    def load(self) -> tuple[set[str], PipelineMetrics | None]:
        """Load checkpoint state from disk.

        Returns:
            A tuple of (completed_task_ids, metrics). If the checkpoint
            file does not exist, returns ``(set(), None)``.
        """
        if not self._path.is_file():
            return set(), None

        with open(self._path, encoding="utf-8") as fh:
            data = json.load(fh)

        completed = set(data.get("completed", []))
        metrics_data = data.get("metrics")
        metrics = (
            PipelineMetrics.from_dict(metrics_data) if metrics_data else None
        )

        logger.debug(
            "Checkpoint loaded: %d completed tasks from %s",
            len(completed),
            self._path,
        )
        return completed, metrics

    def exists(self) -> bool:
        """Return True if the checkpoint file exists on disk."""
        return self._path.is_file()
