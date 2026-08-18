"""Deterministic 80/20 split protocol for evaluation holdout isolation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from registry.source_registry import get_source


@dataclass
class HoldoutManifest:
    source_name: str
    created_at: str                          # ISO 8601
    total_tasks: int
    training_ids: list[str]
    evaluation_ids: list[str]
    manifest_hash: str                       # SHA-256 of sorted evaluation IDs
    split_ratio: float                       # actual ratio achieved


class SplitProtocol:
    """Deterministic SHA-256-based 80/20 split."""

    # ------------------------------------------------------------------
    # Core partition logic
    # ------------------------------------------------------------------

    def compute_partition(self, source_name: str, task_id: str) -> str:
        """Return 'evaluation' or 'training' using SHA-256 deterministic split.

        hash = SHA-256(source_name + task_id)
        if hash_int mod 5 == 0 -> evaluation (20%)
        else -> training (80%)
        """
        digest = hashlib.sha256(
            (source_name + task_id).encode("utf-8")
        ).hexdigest()
        hash_int = int(digest, 16)
        return "evaluation" if hash_int % 5 == 0 else "training"

    # ------------------------------------------------------------------
    # Manifest generation
    # ------------------------------------------------------------------

    def generate_manifest(
        self,
        source_name: str,
        task_ids: list[str],
        *,
        strata: dict[str, list[str]] | None = None,
    ) -> HoldoutManifest:
        """Generate the full holdout manifest for a source.

        Parameters
        ----------
        source_name:
            Registry name of the source (must have usage='split').
        task_ids:
            Complete list of task IDs from this source.
        strata:
            Optional mapping of stratum label -> task IDs for stratified
            splitting.  When provided, the 80/20 split is applied within
            each stratum independently.  All task IDs across strata must
            be a subset of *task_ids*.
        """
        # Validate source exists and is a split source
        source = get_source(source_name)
        if source.usage != "split":
            raise ValueError(
                f"Source {source_name!r} has usage={source.usage!r}, "
                "expected 'split'"
            )

        if strata is not None:
            training, evaluation = self._stratified_split(
                source_name, task_ids, strata
            )
        else:
            training: list[str] = []
            evaluation: list[str] = []
            for tid in task_ids:
                bucket = self.compute_partition(source_name, tid)
                if bucket == "evaluation":
                    evaluation.append(tid)
                else:
                    training.append(tid)

        total = len(training) + len(evaluation)
        actual_ratio = len(training) / total if total > 0 else 0.0

        return HoldoutManifest(
            source_name=source_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            total_tasks=total,
            training_ids=sorted(training),
            evaluation_ids=sorted(evaluation),
            manifest_hash=self._hash_ids(evaluation),
            split_ratio=round(actual_ratio, 6),
        )

    # ------------------------------------------------------------------
    # Manifest verification
    # ------------------------------------------------------------------

    def verify_manifest(self, manifest: HoldoutManifest) -> bool:
        """Verify manifest integrity via SHA-256 hash of evaluation IDs."""
        expected = self._hash_ids(manifest.evaluation_ids)
        return expected == manifest.manifest_hash

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def write_manifest(
        self, manifest: HoldoutManifest, path: str = "data/holdout_manifest.json"
    ) -> None:
        """Write manifest to JSON. Raises FileExistsError if path exists."""
        if os.path.exists(path):
            raise FileExistsError(
                f"Manifest already exists at {path!r} — manifests are immutable"
            )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(manifest), f, indent=2, sort_keys=True)

    @staticmethod
    def load_manifest(path: str = "data/holdout_manifest.json") -> HoldoutManifest:
        """Load a manifest from JSON."""
        with open(path) as f:
            data = json.load(f)
        return HoldoutManifest(**data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_ids(ids: list[str]) -> str:
        """SHA-256 hex digest of newline-joined sorted IDs."""
        payload = "\n".join(sorted(ids)).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _stratified_split(
        self,
        source_name: str,
        task_ids: list[str],
        strata: dict[str, list[str]],
    ) -> tuple[list[str], list[str]]:
        """Split within each stratum, then collect unstratified leftovers."""
        stratified_ids: set[str] = set()
        for ids in strata.values():
            stratified_ids.update(ids)

        training: list[str] = []
        evaluation: list[str] = []

        # Split within each stratum
        for _label, ids in strata.items():
            for tid in ids:
                bucket = self.compute_partition(source_name, tid)
                if bucket == "evaluation":
                    evaluation.append(tid)
                else:
                    training.append(tid)

        # Handle any task_ids not covered by strata
        for tid in task_ids:
            if tid not in stratified_ids:
                bucket = self.compute_partition(source_name, tid)
                if bucket == "evaluation":
                    evaluation.append(tid)
                else:
                    training.append(tid)

        return training, evaluation
