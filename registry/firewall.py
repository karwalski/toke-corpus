"""Contamination firewall — prevents evaluation data from leaking into training."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from registry.source_registry import TaskSource, get_source
from registry.split_protocol import HoldoutManifest, SplitProtocol


@dataclass
class FirewallResult:
    """Result of a firewall check on a single corpus entry."""

    passed: bool
    reason: str  # "pass" | "evaluation_source" | "holdout_partition" | "similarity_threshold" | "cross_source_duplicate"
    details: str  # human-readable explanation
    similarity_score: float | None  # if similarity check was performed
    matched_source: str | None  # if cross-source dedup triggered


class ContaminationFirewall:
    """Prevents evaluation data from contaminating the training corpus.

    Three enforcement mechanisms:
    1. Source-level rejection: entries from evaluation-only sources are rejected
    2. Semantic similarity: entries too similar to holdout tasks are rejected
    3. Cross-source dedup: if same problem appears in both training and eval
       source, training copy removed
    """

    DEFAULT_THRESHOLD = 0.85

    def __init__(
        self,
        registry: list[TaskSource],
        holdout_manifest: HoldoutManifest | None = None,
        similarity_threshold: float = DEFAULT_THRESHOLD,
    ):
        self._registry = {src.name: src for src in registry}
        self._manifest = holdout_manifest
        self._split_protocol = SplitProtocol()
        self._threshold = similarity_threshold

        # Similarity state
        self._eval_descriptions: list[tuple[str, str]] = []  # (id_or_index, text)
        self._eval_tfidf: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}
        self._doc_count = 0

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def check_entry(self, entry: dict) -> FirewallResult:
        """Check if a corpus entry is safe for training.

        Entry must have:
        - "source" dict with "origin" field matching a registry source name
        - Optionally "task_id" for split source partition checking

        Returns FirewallResult with pass/fail and reason.
        """
        source_info = entry.get("source")
        if source_info is None or "origin" not in source_info:
            return FirewallResult(
                passed=False,
                reason="evaluation_source",
                details="Entry missing source.origin — rejected as unsafe.",
                similarity_score=None,
                matched_source=None,
            )

        origin = source_info["origin"]
        src = self._registry.get(origin)

        # Unknown source: fail safe
        if src is None:
            return FirewallResult(
                passed=False,
                reason="evaluation_source",
                details=f"Unknown source {origin!r} — rejected (fail safe).",
                similarity_score=None,
                matched_source=None,
            )

        # 1. Source-level rejection
        if src.usage == "evaluation":
            return FirewallResult(
                passed=False,
                reason="evaluation_source",
                details=f"Source {origin!r} is evaluation-only.",
                similarity_score=None,
                matched_source=None,
            )

        # 2. Holdout partition check for split sources
        if src.usage == "split":
            task_id = entry.get("task_id", source_info.get("task_id", ""))
            if task_id:
                # Check manifest first
                if self._manifest is not None:
                    if task_id in self._manifest.evaluation_ids:
                        return FirewallResult(
                            passed=False,
                            reason="holdout_partition",
                            details=f"Task {task_id!r} is in evaluation partition for {origin!r}.",
                            similarity_score=None,
                            matched_source=None,
                        )
                else:
                    # Fallback to protocol computation
                    partition = self._split_protocol.compute_partition(origin, task_id)
                    if partition == "evaluation":
                        return FirewallResult(
                            passed=False,
                            reason="holdout_partition",
                            details=f"Task {task_id!r} hashes to evaluation partition for {origin!r}.",
                            similarity_score=None,
                            matched_source=None,
                        )

        # 3. Similarity check (if eval descriptions loaded)
        description = self._extract_description(entry)
        if description and self._eval_descriptions:
            is_safe, score, matched_id = self.check_similarity(description)
            if not is_safe:
                return FirewallResult(
                    passed=False,
                    reason="similarity_threshold",
                    details=f"Description too similar to eval task {matched_id!r} (cosine={score:.4f}).",
                    similarity_score=score,
                    matched_source=matched_id,
                )

            # 4. Cross-source dedup
            cs_safe, cs_match = self.check_cross_source_dedup(description, origin)
            if not cs_safe:
                return FirewallResult(
                    passed=False,
                    reason="cross_source_duplicate",
                    details=f"Training entry from {origin!r} duplicates eval source {cs_match!r}.",
                    similarity_score=None,
                    matched_source=cs_match,
                )

        return FirewallResult(
            passed=True,
            reason="pass",
            details="Entry passed all firewall checks.",
            similarity_score=None,
            matched_source=None,
        )

    # ------------------------------------------------------------------
    # Similarity engine (bag-of-words TF-IDF, no external deps)
    # ------------------------------------------------------------------

    def add_evaluation_descriptions(self, descriptions: list[str]) -> None:
        """Load evaluation task descriptions for similarity checking.

        Uses simple bag-of-words TF-IDF cosine similarity (no external ML deps).
        """
        self._eval_descriptions = [
            (f"eval-{i}", desc) for i, desc in enumerate(descriptions)
        ]
        self._doc_count = len(descriptions)

        # Build IDF from evaluation corpus
        df: Counter[str] = Counter()
        tokenised: list[list[str]] = []
        for desc in descriptions:
            tokens = self._tokenize(desc)
            tokenised.append(tokens)
            unique = set(tokens)
            for tok in unique:
                df[tok] += 1

        self._idf = {}
        for term, count in df.items():
            self._idf[term] = math.log((self._doc_count + 1) / (count + 1)) + 1.0

        # Pre-compute TF-IDF vectors for eval descriptions
        self._eval_tfidf = []
        for tokens in tokenised:
            self._eval_tfidf.append(self._compute_tfidf(tokens))

    def check_similarity(
        self, description: str
    ) -> tuple[bool, float, str | None]:
        """Check if description is too similar to any evaluation task.

        Returns (is_safe, max_similarity, matched_eval_task_id_or_None).
        Threshold: cosine > threshold means contaminated.
        """
        if not self._eval_descriptions:
            return (True, 0.0, None)

        tokens = self._tokenize(description)
        query_vec = self._compute_tfidf(tokens)

        max_sim = 0.0
        best_match: str | None = None
        for i, eval_vec in enumerate(self._eval_tfidf):
            sim = self._cosine_similarity(query_vec, eval_vec)
            if sim > max_sim:
                max_sim = sim
                best_match = self._eval_descriptions[i][0]

        is_safe = max_sim <= self._threshold
        return (is_safe, max_sim, best_match if max_sim > 0 else None)

    def check_cross_source_dedup(
        self, task_description: str, source_name: str
    ) -> tuple[bool, str | None]:
        """Check if this task from a training source duplicates an eval source task.

        Returns (is_safe, matching_eval_source_or_None).
        """
        src = self._registry.get(source_name)
        if src is None or src.usage == "evaluation":
            # Not a training source — no cross-source dedup needed
            return (True, None)

        if not self._eval_descriptions:
            return (True, None)

        tokens = self._tokenize(task_description)
        query_vec = self._compute_tfidf(tokens)

        for i, eval_vec in enumerate(self._eval_tfidf):
            sim = self._cosine_similarity(query_vec, eval_vec)
            if sim > self._threshold:
                return (False, self._eval_descriptions[i][0])

        return (True, None)

    # ------------------------------------------------------------------
    # Tagging
    # ------------------------------------------------------------------

    def tag_entry(self, entry: dict) -> dict:
        """Add usage tag to a corpus entry after it passes the firewall.

        Adds: "usage": "training", "firewall_check": "pass"
        """
        tagged = dict(entry)
        tagged["usage"] = "training"
        tagged["firewall_check"] = "pass"
        return tagged

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize by splitting on whitespace and lowercasing."""
        return text.lower().split()

    def _compute_tfidf(self, tokens: list[str]) -> dict[str, float]:
        """Compute TF-IDF vector for a list of tokens."""
        tf = Counter(tokens)
        total = len(tokens) if tokens else 1
        vec: dict[str, float] = {}
        for term, count in tf.items():
            tf_val = count / total
            idf_val = self._idf.get(term, math.log(self._doc_count + 1) + 1.0)
            vec[term] = tf_val * idf_val
        return vec

    @staticmethod
    def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
        """Cosine similarity between two sparse TF-IDF vectors."""
        if not a or not b:
            return 0.0
        # Dot product over intersection
        dot = 0.0
        for term, val in a.items():
            if term in b:
                dot += val * b[term]
        if dot == 0.0:
            return 0.0
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _extract_description(entry: dict) -> str:
        """Extract a textual description from a corpus entry for similarity matching."""
        # Try common fields
        for key in ("description", "prompt", "task_description", "tk_source"):
            if key in entry and isinstance(entry[key], str):
                return entry[key]
        # Try nested source dict
        source = entry.get("source", {})
        if isinstance(source, dict):
            for key in ("description", "prompt", "task_description"):
                if key in source and isinstance(source[key], str):
                    return source[key]
        return ""
