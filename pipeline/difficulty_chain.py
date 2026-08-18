"""Difficulty chain builder for toke programs (Story 9.3.4).

Progressively scales a seed program through difficulty levels, validating
each step with tkc --check.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from pipeline.difficulty_scaler import DifficultyScaler

TKC_BINARY = Path("/Users/matthew.watt/tk/toke/tkc")

MAX_LEVEL = 5


class DifficultyChain:
    """Build a chain of progressively harder toke programs from a seed."""

    def __init__(self, tkc_path: Path | None = None) -> None:
        self.tkc_path = tkc_path or TKC_BINARY
        self.scaler = DifficultyScaler()

    def build_chain(self, seed_source: str) -> list[tuple[int, str]]:
        """Progressively scale *seed_source* from level 1 upward.

        Each step is validated with ``tkc --check``. The chain stops at
        the first level that fails validation or where the scaler returns
        None.

        Args:
            seed_source: A valid toke program at difficulty level 1.

        Returns:
            List of ``(level, source)`` pairs that all pass validation.
            The list always starts with ``(1, seed_source)`` if the seed
            itself validates.
        """
        chain: list[tuple[int, str]] = []

        # Validate the seed
        if not self._tkc_check(seed_source):
            return chain

        chain.append((1, seed_source))
        current = seed_source

        for level in range(1, MAX_LEVEL):
            scaled = self.scaler.scale_up(current, level)
            if scaled is None:
                break
            if not self._tkc_check(scaled):
                break
            chain.append((level + 1, scaled))
            current = scaled

        return chain

    def _tkc_check(self, source: str) -> bool:
        """Validate toke source with ``tkc --check``.

        Returns True if the source passes, False otherwise.
        """
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".toke", delete=True, encoding="utf-8"
            ) as tmp:
                tmp.write(source)
                tmp.flush()
                result = subprocess.run(
                    [str(self.tkc_path), "--check", tmp.name],
                    capture_output=True,
                    timeout=10,
                )
                return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False
