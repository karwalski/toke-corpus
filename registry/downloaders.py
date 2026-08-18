"""Benchmark dataset downloaders — stub fetchers with cache-or-raise semantics.

Story 9.2.6 (Part 3).

Each download method checks the local cache first.  If the data is already
present it returns the path; otherwise it raises ``DatasetNotCached`` with
instructions.  The ``download_all_script`` helper emits a shell script that
performs the actual fetches (git clone / curl).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from registry.source_registry import get_source


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class DatasetNotCached(Exception):
    """Raised when a requested dataset has not been downloaded yet."""


# ---------------------------------------------------------------------------
# Source metadata (URL + expected artefact inside the cache dir)
# ---------------------------------------------------------------------------

_SOURCES: dict[str, dict[str, str]] = {
    "humaneval": {
        "url": "https://github.com/openai/human-eval",
        "clone_url": "https://github.com/openai/human-eval.git",
        "artefact": "human-eval/data/HumanEval.jsonl.gz",
        "method": "git",
    },
    "mbpp": {
        "url": "https://github.com/google-research/google-research/tree/master/mbpp",
        "download_url": "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl",
        "artefact": "mbpp/mbpp.jsonl",
        "method": "curl",
    },
    "apps": {
        "url": "https://github.com/hendrycks/apps",
        "clone_url": "https://github.com/hendrycks/apps.git",
        "artefact": "apps/train",
        "method": "git",
    },
    "codecontests": {
        "url": "https://github.com/google-deepmind/code_contests",
        "clone_url": "https://github.com/google-deepmind/code_contests.git",
        "artefact": "code_contests",
        "method": "git",
    },
    "taco": {
        "url": "https://github.com/FlagOpen/TACO",
        "clone_url": "https://github.com/FlagOpen/TACO.git",
        "artefact": "TACO",
        "method": "git",
    },
    "leetcodedataset": {
        "url": "https://github.com/doocs/leetcode",
        "clone_url": "https://github.com/doocs/leetcode.git",
        "artefact": "leetcode",
        "method": "git",
    },
}


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------


class DatasetDownloader:
    """Downloads and caches benchmark datasets for the registry.

    All datasets are downloaded to ``data/benchmarks/<source_name>/``.
    """

    def __init__(self, cache_dir: Path = Path("data/benchmarks")) -> None:
        self.cache_dir = cache_dir

    # -- internal helpers ---------------------------------------------------

    def _check_cache(self, name: str) -> Path:
        """Return the artefact path if already cached, else raise."""
        meta = _SOURCES[name]
        artefact = self.cache_dir / meta["artefact"]
        if artefact.exists():
            return artefact
        source = get_source(name)
        raise DatasetNotCached(
            f"Dataset {source.display_name!r} is not cached.\n"
            f"Expected artefact at: {artefact}\n"
            f"Source URL: {meta['url']}\n"
            f"Run scripts/setup_benchmarks.sh to download all benchmarks."
        )

    # -- public download stubs ----------------------------------------------

    def download_humaneval(self) -> Path:
        """Download HumanEval dataset from GitHub.

        Source: https://github.com/openai/human-eval
        Returns path to the extracted problems JSONL.
        """
        return self._check_cache("humaneval")

    def download_mbpp(self) -> Path:
        """Download MBPP dataset from GitHub.

        Source: https://github.com/google-research/google-research/tree/master/mbpp
        Returns path to the extracted problems JSONL.
        """
        return self._check_cache("mbpp")

    def download_apps(self) -> Path:
        """Download APPS dataset.

        Source: https://github.com/hendrycks/apps (or HuggingFace)
        Returns path to the extracted problems directory.
        """
        return self._check_cache("apps")

    def download_codecontests(self) -> Path:
        """Download CodeContests dataset.

        Source: https://github.com/google-deepmind/code_contests
        Returns path to the extracted problems.
        """
        return self._check_cache("codecontests")

    def download_taco(self) -> Path:
        """Download TACO dataset.

        Source: https://github.com/FlagOpen/TACO
        Returns path to the extracted problems.
        """
        return self._check_cache("taco")

    def download_leetcode(self) -> Path:
        """Download LeetCode dataset.

        Source: available on HuggingFace / GitHub
        Returns path to extracted problems.
        """
        return self._check_cache("leetcodedataset")

    # -- script generator ---------------------------------------------------

    def download_all_script(self) -> str:
        """Generate a bash script that downloads every benchmark dataset.

        The script is idempotent — it skips sources whose artefact already
        exists under *cache_dir*.
        """
        lines = [
            "#!/usr/bin/env bash",
            "# Auto-generated benchmark download script",
            "# Run from the repository root.",
            "set -euo pipefail",
            "",
            f'CACHE_DIR="{self.cache_dir}"',
            'mkdir -p "$CACHE_DIR"',
            "",
        ]

        for name, meta in _SOURCES.items():
            artefact = meta["artefact"]
            lines.append(f"# --- {name} ---")
            lines.append(f'if [ -e "$CACHE_DIR/{artefact}" ]; then')
            lines.append(f'  echo "SKIP {name} (already cached)"')
            lines.append("else")
            if meta["method"] == "git":
                clone_url = meta["clone_url"]
                repo_dir = artefact.split("/")[0]
                lines.append(
                    f'  git clone --depth 1 {clone_url} "$CACHE_DIR/{repo_dir}"'
                )
            else:
                download_url = meta["download_url"]
                dest = artefact
                parent = str(Path(artefact).parent)
                lines.append(f'  mkdir -p "$CACHE_DIR/{parent}"')
                lines.append(
                    f'  curl -fSL -o "$CACHE_DIR/{dest}" "{download_url}"'
                )
            lines.append(f'  echo "DONE {name}"')
            lines.append("fi")
            lines.append("")

        lines.append('echo "All benchmarks ready."')
        return "\n".join(lines) + "\n"
