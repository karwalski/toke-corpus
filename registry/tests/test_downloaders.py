"""Tests for registry.downloaders — benchmark dataset download stubs.

Story 9.2.6 (Part 3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from registry.downloaders import DatasetDownloader, DatasetNotCached


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def dl(tmp_path: Path) -> DatasetDownloader:
    """A downloader whose cache_dir is an isolated tmp dir."""
    return DatasetDownloader(cache_dir=tmp_path)


def _seed_artefact(tmp_path: Path, rel: str) -> Path:
    """Create a fake artefact file/dir so the cache check passes."""
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if "." in p.name:
        p.write_text("stub")
    else:
        p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# DatasetNotCached raised when data is missing
# ---------------------------------------------------------------------------


class TestNotCachedErrors:
    def test_humaneval_not_cached(self, dl: DatasetDownloader) -> None:
        with pytest.raises(DatasetNotCached, match="HumanEval"):
            dl.download_humaneval()

    def test_mbpp_not_cached(self, dl: DatasetDownloader) -> None:
        with pytest.raises(DatasetNotCached, match="MBPP"):
            dl.download_mbpp()

    def test_apps_not_cached(self, dl: DatasetDownloader) -> None:
        with pytest.raises(DatasetNotCached, match="APPS"):
            dl.download_apps()

    def test_codecontests_not_cached(self, dl: DatasetDownloader) -> None:
        with pytest.raises(DatasetNotCached, match="CodeContests"):
            dl.download_codecontests()

    def test_taco_not_cached(self, dl: DatasetDownloader) -> None:
        with pytest.raises(DatasetNotCached, match="TACO"):
            dl.download_taco()

    def test_leetcode_not_cached(self, dl: DatasetDownloader) -> None:
        with pytest.raises(DatasetNotCached, match="LeetCodeDataset"):
            dl.download_leetcode()


# ---------------------------------------------------------------------------
# Cache check returns path when artefact exists
# ---------------------------------------------------------------------------


class TestCacheHit:
    def test_humaneval_cached(self, dl: DatasetDownloader, tmp_path: Path) -> None:
        _seed_artefact(tmp_path, "human-eval/data/HumanEval.jsonl.gz")
        result = dl.download_humaneval()
        assert result.exists()

    def test_mbpp_cached(self, dl: DatasetDownloader, tmp_path: Path) -> None:
        _seed_artefact(tmp_path, "mbpp/mbpp.jsonl")
        result = dl.download_mbpp()
        assert result.exists()


# ---------------------------------------------------------------------------
# download_all_script generates valid shell commands
# ---------------------------------------------------------------------------


class TestDownloadScript:
    def test_script_starts_with_shebang(self, dl: DatasetDownloader) -> None:
        script = dl.download_all_script()
        assert script.startswith("#!/usr/bin/env bash")

    def test_script_contains_git_clone(self, dl: DatasetDownloader) -> None:
        script = dl.download_all_script()
        assert "git clone --depth 1" in script

    def test_script_contains_curl(self, dl: DatasetDownloader) -> None:
        script = dl.download_all_script()
        assert "curl -fSL" in script

    def test_script_contains_all_sources(self, dl: DatasetDownloader) -> None:
        script = dl.download_all_script()
        for name in ("humaneval", "mbpp", "apps", "codecontests", "taco", "leetcodedataset"):
            assert name in script

    def test_script_idempotent_guards(self, dl: DatasetDownloader) -> None:
        script = dl.download_all_script()
        # Each source should have an if-exists guard.
        assert script.count("SKIP") >= 6

    def test_script_uses_cache_dir(self, tmp_path: Path) -> None:
        dl = DatasetDownloader(cache_dir=tmp_path)
        script = dl.download_all_script()
        assert str(tmp_path) in script


# ---------------------------------------------------------------------------
# Error message includes download instructions
# ---------------------------------------------------------------------------


class TestErrorMessages:
    def test_error_includes_url(self, dl: DatasetDownloader) -> None:
        with pytest.raises(DatasetNotCached) as exc_info:
            dl.download_humaneval()
        assert "github.com/openai/human-eval" in str(exc_info.value)

    def test_error_includes_script_hint(self, dl: DatasetDownloader) -> None:
        with pytest.raises(DatasetNotCached) as exc_info:
            dl.download_mbpp()
        assert "setup_benchmarks.sh" in str(exc_info.value)
