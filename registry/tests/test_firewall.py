"""Tests for the contamination firewall."""

from __future__ import annotations

import pytest

from registry.firewall import ContaminationFirewall, FirewallResult
from registry.source_registry import REGISTRY, TaskSource, get_source
from registry.split_protocol import HoldoutManifest, SplitProtocol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(origin: str, task_id: str = "", description: str = "") -> dict:
    """Build a minimal corpus entry for testing."""
    entry: dict = {"source": {"origin": origin}}
    if task_id:
        entry["task_id"] = task_id
    if description:
        entry["description"] = description
    return entry


def _firewall(**kwargs) -> ContaminationFirewall:
    """Create a firewall with the real registry."""
    return ContaminationFirewall(registry=REGISTRY, **kwargs)


# ---------------------------------------------------------------------------
# 1. Source-level rejection
# ---------------------------------------------------------------------------

class TestSourceLevelRejection:
    def test_evaluation_only_source_rejected(self):
        fw = _firewall()
        entry = _make_entry("humaneval")
        result = fw.check_entry(entry)
        assert not result.passed
        assert result.reason == "evaluation_source"

    def test_mbpp_evaluation_source_rejected(self):
        fw = _firewall()
        entry = _make_entry("mbpp")
        result = fw.check_entry(entry)
        assert not result.passed
        assert result.reason == "evaluation_source"

    def test_training_source_passes(self):
        fw = _firewall()
        entry = _make_entry("exercism-python")
        result = fw.check_entry(entry)
        assert result.passed
        assert result.reason == "pass"

    def test_rosettacode_training_passes(self):
        fw = _firewall()
        entry = _make_entry("rosettacode")
        result = fw.check_entry(entry)
        assert result.passed

    def test_unknown_source_rejected_fail_safe(self):
        fw = _firewall()
        entry = _make_entry("totally-unknown-dataset")
        result = fw.check_entry(entry)
        assert not result.passed
        assert result.reason == "evaluation_source"
        assert "Unknown source" in result.details

    def test_missing_source_origin_rejected(self):
        fw = _firewall()
        entry = {"source": {}}
        result = fw.check_entry(entry)
        assert not result.passed

    def test_missing_source_dict_rejected(self):
        fw = _firewall()
        entry = {"id": "something"}
        result = fw.check_entry(entry)
        assert not result.passed


# ---------------------------------------------------------------------------
# 2. Holdout partition checks (split sources)
# ---------------------------------------------------------------------------

class TestHoldoutPartition:
    def test_split_source_eval_partition_rejected_via_protocol(self):
        """Split source entry hashing to evaluation is rejected."""
        fw = _firewall()
        sp = SplitProtocol()
        # Find a task_id that hashes to evaluation for 'apps'
        eval_id = None
        for i in range(200):
            tid = f"task-{i:04d}"
            if sp.compute_partition("apps", tid) == "evaluation":
                eval_id = tid
                break
        assert eval_id is not None, "Could not find an eval-partition task ID"
        entry = _make_entry("apps", task_id=eval_id)
        result = fw.check_entry(entry)
        assert not result.passed
        assert result.reason == "holdout_partition"

    def test_split_source_training_partition_passes_via_protocol(self):
        """Split source entry hashing to training passes."""
        fw = _firewall()
        sp = SplitProtocol()
        train_id = None
        for i in range(200):
            tid = f"task-{i:04d}"
            if sp.compute_partition("apps", tid) == "training":
                train_id = tid
                break
        assert train_id is not None
        entry = _make_entry("apps", task_id=train_id)
        result = fw.check_entry(entry)
        assert result.passed

    def test_split_source_eval_partition_rejected_via_manifest(self):
        """When a manifest is provided, its evaluation IDs are used."""
        manifest = HoldoutManifest(
            source_name="apps",
            created_at="2025-01-01T00:00:00Z",
            total_tasks=10,
            training_ids=["t-001", "t-002"],
            evaluation_ids=["e-001", "e-002"],
            manifest_hash="abc",
            split_ratio=0.8,
        )
        fw = _firewall(holdout_manifest=manifest)
        entry = _make_entry("apps", task_id="e-001")
        result = fw.check_entry(entry)
        assert not result.passed
        assert result.reason == "holdout_partition"

    def test_split_source_training_partition_passes_via_manifest(self):
        manifest = HoldoutManifest(
            source_name="apps",
            created_at="2025-01-01T00:00:00Z",
            total_tasks=10,
            training_ids=["t-001"],
            evaluation_ids=["e-001"],
            manifest_hash="abc",
            split_ratio=0.8,
        )
        fw = _firewall(holdout_manifest=manifest)
        entry = _make_entry("apps", task_id="t-001")
        result = fw.check_entry(entry)
        assert result.passed


# ---------------------------------------------------------------------------
# 3. Similarity checks
# ---------------------------------------------------------------------------

class TestSimilarity:
    def test_identical_description_rejected(self):
        fw = _firewall()
        eval_descs = [
            "Write a function that computes the factorial of a number",
            "Implement binary search on a sorted array",
        ]
        fw.add_evaluation_descriptions(eval_descs)

        is_safe, score, matched = fw.check_similarity(
            "Write a function that computes the factorial of a number"
        )
        assert not is_safe
        assert score > 0.99

    def test_very_different_description_passes(self):
        fw = _firewall()
        eval_descs = [
            "Write a function that computes the factorial of a number",
        ]
        fw.add_evaluation_descriptions(eval_descs)

        is_safe, score, _ = fw.check_similarity(
            "Deploy a containerised web server with TLS termination"
        )
        assert is_safe
        assert score < 0.5

    def test_paraphrased_high_similarity_rejected(self):
        fw = _firewall()
        eval_descs = [
            "Write a function that computes the factorial of a number recursively",
        ]
        fw.add_evaluation_descriptions(eval_descs)

        # Very close paraphrase — only one word different
        is_safe, score, _ = fw.check_similarity(
            "Write a function that computes the factorial of a number recursively please"
        )
        # Almost identical; cosine should be high
        assert score > 0.80

    def test_no_eval_descriptions_always_safe(self):
        fw = _firewall()
        is_safe, score, matched = fw.check_similarity("anything at all")
        assert is_safe
        assert score == 0.0
        assert matched is None

    def test_similarity_threshold_boundary(self):
        """Entries exactly at threshold should pass (> threshold triggers rejection)."""
        fw = _firewall(similarity_threshold=1.0)
        eval_descs = ["hello world program"]
        fw.add_evaluation_descriptions(eval_descs)
        is_safe, score, _ = fw.check_similarity("hello world program")
        # With threshold=1.0, even identical should pass (score ~= 1.0, but <= 1.0)
        assert is_safe

    def test_custom_threshold_lower(self):
        fw = _firewall(similarity_threshold=0.3)
        eval_descs = [
            "Write a function that sorts an array using bubble sort",
        ]
        fw.add_evaluation_descriptions(eval_descs)
        is_safe, score, _ = fw.check_similarity(
            "Write a function that sorts an array using insertion sort"
        )
        # High overlap, should exceed 0.3
        assert not is_safe


# ---------------------------------------------------------------------------
# 4. Cross-source dedup
# ---------------------------------------------------------------------------

class TestCrossSourceDedup:
    def test_training_copy_of_eval_task_rejected(self):
        fw = _firewall()
        eval_descs = [
            "Implement a function that reverses a linked list",
        ]
        fw.add_evaluation_descriptions(eval_descs)

        is_safe, match = fw.check_cross_source_dedup(
            "Implement a function that reverses a linked list",
            "exercism-python",
        )
        assert not is_safe
        assert match is not None

    def test_no_duplicate_passes(self):
        fw = _firewall()
        eval_descs = [
            "Implement a function that reverses a linked list",
        ]
        fw.add_evaluation_descriptions(eval_descs)

        is_safe, match = fw.check_cross_source_dedup(
            "Build a web scraper that extracts product prices",
            "rosettacode",
        )
        assert is_safe
        assert match is None

    def test_cross_source_dedup_no_eval_descriptions(self):
        fw = _firewall()
        is_safe, match = fw.check_cross_source_dedup(
            "anything", "exercism-python"
        )
        assert is_safe
        assert match is None


# ---------------------------------------------------------------------------
# 5. Tagging
# ---------------------------------------------------------------------------

class TestTagging:
    def test_tag_adds_correct_fields(self):
        fw = _firewall()
        entry = _make_entry("exercism-python")
        tagged = fw.tag_entry(entry)
        assert tagged["usage"] == "training"
        assert tagged["firewall_check"] == "pass"
        # Original entry should not be mutated
        assert "usage" not in entry

    def test_tag_preserves_existing_fields(self):
        fw = _firewall()
        entry = _make_entry("exercism-python")
        entry["id"] = "test-123"
        entry["tk_source"] = "f=main():void{...}"
        tagged = fw.tag_entry(entry)
        assert tagged["id"] == "test-123"
        assert tagged["tk_source"] == "f=main():void{...}"
        assert tagged["usage"] == "training"


# ---------------------------------------------------------------------------
# 6. FirewallResult dataclass
# ---------------------------------------------------------------------------

class TestFirewallResult:
    def test_pass_result_fields(self):
        r = FirewallResult(
            passed=True,
            reason="pass",
            details="OK",
            similarity_score=None,
            matched_source=None,
        )
        assert r.passed
        assert r.reason == "pass"

    def test_rejection_result_has_similarity(self):
        r = FirewallResult(
            passed=False,
            reason="similarity_threshold",
            details="Too similar",
            similarity_score=0.92,
            matched_source="eval-0",
        )
        assert not r.passed
        assert r.similarity_score == 0.92
        assert r.matched_source == "eval-0"

    def test_rejection_result_cross_source(self):
        r = FirewallResult(
            passed=False,
            reason="cross_source_duplicate",
            details="Duplicate",
            similarity_score=None,
            matched_source="humaneval",
        )
        assert r.reason == "cross_source_duplicate"


# ---------------------------------------------------------------------------
# 7. Integration: check_entry with similarity
# ---------------------------------------------------------------------------

class TestCheckEntryIntegration:
    def test_entry_with_eval_description_rejected(self):
        fw = _firewall()
        fw.add_evaluation_descriptions([
            "Write a function that computes the sum of a list of integers",
        ])
        entry = _make_entry("exercism-python")
        entry["description"] = "Write a function that computes the sum of a list of integers"
        result = fw.check_entry(entry)
        assert not result.passed
        assert result.reason == "similarity_threshold"

    def test_entry_with_different_description_passes(self):
        fw = _firewall()
        fw.add_evaluation_descriptions([
            "Write a function that computes the sum of a list of integers",
        ])
        entry = _make_entry("exercism-python")
        entry["description"] = "Deploy kubernetes cluster with helm charts"
        result = fw.check_entry(entry)
        assert result.passed

    def test_empty_entry_description_passes_similarity(self):
        """Entry without description field skips similarity check."""
        fw = _firewall()
        fw.add_evaluation_descriptions([
            "Write a function that computes the sum of a list of integers",
        ])
        entry = _make_entry("exercism-python")
        result = fw.check_entry(entry)
        assert result.passed
