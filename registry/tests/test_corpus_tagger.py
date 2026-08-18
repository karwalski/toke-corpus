"""Tests for the batch corpus tagger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from registry.corpus_tagger import CorpusTagger, TaggingReport
from registry.firewall import ContaminationFirewall
from registry.source_registry import REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _firewall(**kwargs) -> ContaminationFirewall:
    return ContaminationFirewall(registry=REGISTRY, **kwargs)


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCorpusTagger:
    def test_full_pipeline_mixed_corpus(self, tmp_path: Path):
        """Mixed corpus: training entries pass, evaluation entries rejected."""
        corpus = [
            {"source": {"origin": "exercism-python"}, "id": "ex-1"},
            {"source": {"origin": "humaneval"}, "id": "he-1"},
            {"source": {"origin": "rosettacode"}, "id": "rc-1"},
            {"source": {"origin": "mbpp"}, "id": "mb-1"},
        ]
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "output.jsonl"
        rejected_path = tmp_path / "rejected.jsonl"
        _write_jsonl(input_path, corpus)

        tagger = CorpusTagger(_firewall())
        report = tagger.process_corpus(input_path, output_path, rejected_path)

        assert report.total_entries == 4
        assert report.passed == 2
        assert report.rejected_evaluation_source == 2

    def test_output_file_contains_only_passed(self, tmp_path: Path):
        corpus = [
            {"source": {"origin": "exercism-python"}, "id": "ex-1"},
            {"source": {"origin": "humaneval"}, "id": "he-1"},
        ]
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "output.jsonl"
        rejected_path = tmp_path / "rejected.jsonl"
        _write_jsonl(input_path, corpus)

        tagger = CorpusTagger(_firewall())
        tagger.process_corpus(input_path, output_path, rejected_path)

        passed = _read_jsonl(output_path)
        assert len(passed) == 1
        assert passed[0]["id"] == "ex-1"
        assert passed[0]["usage"] == "training"
        assert passed[0]["firewall_check"] == "pass"

    def test_rejected_file_contains_rejected(self, tmp_path: Path):
        corpus = [
            {"source": {"origin": "humaneval"}, "id": "he-1"},
        ]
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "output.jsonl"
        rejected_path = tmp_path / "rejected.jsonl"
        _write_jsonl(input_path, corpus)

        tagger = CorpusTagger(_firewall())
        tagger.process_corpus(input_path, output_path, rejected_path)

        rejected = _read_jsonl(rejected_path)
        assert len(rejected) == 1
        assert rejected[0]["id"] == "he-1"
        assert rejected[0]["firewall_check"] == "fail"
        assert rejected[0]["firewall_reason"] == "evaluation_source"

    def test_report_counts_match(self, tmp_path: Path):
        corpus = [
            {"source": {"origin": "exercism-python"}, "id": "1"},
            {"source": {"origin": "humaneval"}, "id": "2"},
            {"source": {"origin": "mbpp"}, "id": "3"},
            {"source": {"origin": "rosettacode"}, "id": "4"},
            {"source": {"origin": "exercism-go"}, "id": "5"},
        ]
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "output.jsonl"
        rejected_path = tmp_path / "rejected.jsonl"
        _write_jsonl(input_path, corpus)

        tagger = CorpusTagger(_firewall())
        report = tagger.process_corpus(input_path, output_path, rejected_path)

        assert report.total_entries == 5
        assert report.passed == 3
        assert report.total_rejected == 2
        assert report.passed + report.total_rejected == report.total_entries

    def test_source_provenance_preserved(self, tmp_path: Path):
        """Original source metadata is preserved in output."""
        corpus = [
            {
                "source": {"origin": "exercism-python", "url": "https://example.com"},
                "id": "ex-1",
                "tk_source": "f=hello():str{<\"world\"};",
            },
        ]
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "output.jsonl"
        rejected_path = tmp_path / "rejected.jsonl"
        _write_jsonl(input_path, corpus)

        tagger = CorpusTagger(_firewall())
        tagger.process_corpus(input_path, output_path, rejected_path)

        passed = _read_jsonl(output_path)
        assert len(passed) == 1
        assert passed[0]["source"]["origin"] == "exercism-python"
        assert passed[0]["source"]["url"] == "https://example.com"
        assert passed[0]["tk_source"] == "f=hello():str{<\"world\"};"

    def test_empty_corpus(self, tmp_path: Path):
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "output.jsonl"
        rejected_path = tmp_path / "rejected.jsonl"
        input_path.write_text("")

        tagger = CorpusTagger(_firewall())
        report = tagger.process_corpus(input_path, output_path, rejected_path)

        assert report.total_entries == 0
        assert report.passed == 0
        assert report.total_rejected == 0

    def test_similarity_rejection_counted(self, tmp_path: Path):
        fw = _firewall()
        fw.add_evaluation_descriptions([
            "Write a function that computes the factorial of a number",
        ])
        corpus = [
            {
                "source": {"origin": "exercism-python"},
                "id": "sim-1",
                "description": "Write a function that computes the factorial of a number",
            },
        ]
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "output.jsonl"
        rejected_path = tmp_path / "rejected.jsonl"
        _write_jsonl(input_path, corpus)

        tagger = CorpusTagger(fw)
        report = tagger.process_corpus(input_path, output_path, rejected_path)

        assert report.rejected_similarity == 1
        assert report.passed == 0

    def test_holdout_rejection_counted(self, tmp_path: Path):
        from registry.split_protocol import HoldoutManifest

        manifest = HoldoutManifest(
            source_name="apps",
            created_at="2025-01-01T00:00:00Z",
            total_tasks=2,
            training_ids=["t-001"],
            evaluation_ids=["e-001"],
            manifest_hash="abc",
            split_ratio=0.8,
        )
        fw = _firewall(holdout_manifest=manifest)
        corpus = [
            {"source": {"origin": "apps"}, "task_id": "e-001", "id": "h-1"},
        ]
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "output.jsonl"
        rejected_path = tmp_path / "rejected.jsonl"
        _write_jsonl(input_path, corpus)

        tagger = CorpusTagger(fw)
        report = tagger.process_corpus(input_path, output_path, rejected_path)

        assert report.rejected_holdout_partition == 1

    def test_tagging_report_summary(self):
        report = TaggingReport(
            total_entries=100,
            passed=80,
            rejected_evaluation_source=10,
            rejected_holdout_partition=5,
            rejected_similarity=3,
            rejected_cross_source=2,
        )
        summary = report.summary()
        assert "100" in summary
        assert "80" in summary
        assert "20" in summary  # total rejected

    def test_output_dirs_created(self, tmp_path: Path):
        """Output and rejected dirs are created if they don't exist."""
        input_path = tmp_path / "corpus.jsonl"
        output_path = tmp_path / "sub" / "deep" / "output.jsonl"
        rejected_path = tmp_path / "sub2" / "rejected.jsonl"
        input_path.write_text("")

        tagger = CorpusTagger(_firewall())
        report = tagger.process_corpus(input_path, output_path, rejected_path)

        assert output_path.exists()
        assert rejected_path.exists()
        assert report.total_entries == 0
