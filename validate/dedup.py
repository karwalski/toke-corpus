"""Corpus deduplication using exact hashing and character n-gram Jaccard similarity.

Avoids external embedding API dependencies — all dedup runs locally
using deterministic string operations.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Default n-gram size for near-dedup Jaccard computation.
DEFAULT_NGRAM_SIZE: int = 3


class Deduplicator:
    """Detect exact and near-duplicate toke source entries."""

    def __init__(
        self,
        threshold: float = 0.95,
        ngram_size: int = DEFAULT_NGRAM_SIZE,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold must be in (0, 1], got {threshold}")
        self.threshold = threshold
        self.ngram_size = ngram_size

        # entry_id -> sha256 hex digest of normalised source.
        self._hashes: dict[str, str] = {}
        # entry_id -> set of character n-grams (for Jaccard).
        self._ngram_sets: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, entry_id: str, toke_src: str) -> bool:
        """Register an entry. Returns False if it duplicates an existing one."""
        is_unique, similar_id = self.check(toke_src)
        if not is_unique:
            logger.info(
                "Duplicate detected: '%s' too similar to '%s'",
                entry_id,
                similar_id,
            )
            return False

        norm = self._normalise(toke_src)
        digest = self._hash(norm)
        ngrams = self._char_ngrams(norm)

        self._hashes[entry_id] = digest
        self._ngram_sets[entry_id] = ngrams
        return True

    def check(self, toke_src: str) -> tuple[bool, str | None]:
        """Check whether *toke_src* is unique relative to stored entries.

        Returns (is_unique, similar_entry_id). When unique, the second
        element is None.
        """
        norm = self._normalise(toke_src)
        digest = self._hash(norm)

        # Exact duplicate check (fast path).
        for entry_id, stored_hash in self._hashes.items():
            if stored_hash == digest:
                return False, entry_id

        # Near-duplicate check via Jaccard similarity.
        candidate_ngrams = self._char_ngrams(norm)
        for entry_id, stored_ngrams in self._ngram_sets.items():
            similarity = self._jaccard(candidate_ngrams, stored_ngrams)
            if similarity >= self.threshold:
                return False, entry_id

        return True, None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(source: str) -> str:
        """Normalise source for comparison: lowercase, collapse whitespace."""
        text = source.lower()
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _hash(normalised: str) -> str:
        """SHA-256 hex digest of a normalised string."""
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    def _char_ngrams(self, text: str) -> set[str]:
        """Extract character-level n-grams from *text*."""
        n = self.ngram_size
        if len(text) < n:
            return {text} if text else set()
        return {text[i : i + n] for i in range(len(text) - n + 1)}

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        """Jaccard similarity between two sets."""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        intersection = len(a & b)
        union = len(a | b)
        return intersection / union
