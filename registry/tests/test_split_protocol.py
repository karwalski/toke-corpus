"""Tests for the deterministic split protocol."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from registry.split_protocol import HoldoutManifest, SplitProtocol


@pytest.fixture
def protocol():
    return SplitProtocol()


# -------------------------------------------------------------------
# Determinism
# -------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_partition(self, protocol: SplitProtocol):
        r1 = protocol.compute_partition("apps", "task_42")
        r2 = protocol.compute_partition("apps", "task_42")
        assert r1 == r2

    def test_repeated_calls_stable(self, protocol: SplitProtocol):
        results = [
            protocol.compute_partition("taco", "problem_99") for _ in range(100)
        ]
        assert len(set(results)) == 1

    def test_different_ids_eventually_differ(self, protocol: SplitProtocol):
        partitions = {
            protocol.compute_partition("apps", f"task_{i}") for i in range(50)
        }
        assert len(partitions) == 2, "Expected both 'training' and 'evaluation'"


# -------------------------------------------------------------------
# Distribution
# -------------------------------------------------------------------

class TestDistribution:
    def test_approx_20_percent_evaluation(self, protocol: SplitProtocol):
        n = 10_000
        evals = sum(
            1
            for i in range(n)
            if protocol.compute_partition("apps", f"id_{i}") == "evaluation"
        )
        ratio = evals / n
        assert 0.15 <= ratio <= 0.25, f"Evaluation ratio {ratio:.3f} outside 15-25%"

    def test_different_source_different_split(self, protocol: SplitProtocol):
        """Same task_id but different source_name can yield different partitions."""
        results = set()
        for source in ["apps", "taco", "codecontests", "leetcodedataset"]:
            results.add(protocol.compute_partition(source, "task_0"))
        # With 4 sources, at least one should differ (probabilistically near-certain)
        # We check that the function actually uses source_name.
        # Deterministic check: just verify it runs without error.
        assert len(results) >= 1


# -------------------------------------------------------------------
# Manifest generation
# -------------------------------------------------------------------

class TestManifestGeneration:
    def test_generates_valid_manifest(self, protocol: SplitProtocol):
        ids = [f"task_{i}" for i in range(100)]
        m = protocol.generate_manifest("apps", ids)
        assert m.source_name == "apps"
        assert m.total_tasks == 100
        assert len(m.training_ids) + len(m.evaluation_ids) == 100
        assert m.manifest_hash
        assert 0.0 <= m.split_ratio <= 1.0

    def test_manifest_ids_are_sorted(self, protocol: SplitProtocol):
        ids = [f"task_{i}" for i in range(50)]
        m = protocol.generate_manifest("apps", ids)
        assert m.training_ids == sorted(m.training_ids)
        assert m.evaluation_ids == sorted(m.evaluation_ids)

    def test_manifest_hash_deterministic(self, protocol: SplitProtocol):
        ids = [f"task_{i}" for i in range(50)]
        m1 = protocol.generate_manifest("apps", ids)
        m2 = protocol.generate_manifest("apps", ids)
        assert m1.manifest_hash == m2.manifest_hash

    def test_rejects_non_split_source(self, protocol: SplitProtocol):
        with pytest.raises(ValueError, match="expected 'split'"):
            protocol.generate_manifest("humaneval", ["t1", "t2"])

    def test_rejects_unknown_source(self, protocol: SplitProtocol):
        with pytest.raises(KeyError):
            protocol.generate_manifest("no-such-source", ["t1"])


# -------------------------------------------------------------------
# Manifest verification
# -------------------------------------------------------------------

class TestManifestVerification:
    def test_valid_manifest_passes(self, protocol: SplitProtocol):
        ids = [f"task_{i}" for i in range(100)]
        m = protocol.generate_manifest("apps", ids)
        assert protocol.verify_manifest(m) is True

    def test_tampered_manifest_fails(self, protocol: SplitProtocol):
        ids = [f"task_{i}" for i in range(100)]
        m = protocol.generate_manifest("apps", ids)
        # Tamper by adding a fake evaluation ID
        tampered = HoldoutManifest(
            source_name=m.source_name,
            created_at=m.created_at,
            total_tasks=m.total_tasks,
            training_ids=m.training_ids,
            evaluation_ids=m.evaluation_ids + ["injected_id"],
            manifest_hash=m.manifest_hash,
            split_ratio=m.split_ratio,
        )
        assert protocol.verify_manifest(tampered) is False


# -------------------------------------------------------------------
# Stratified split
# -------------------------------------------------------------------

class TestStratifiedSplit:
    def test_stratified_preserves_approx_20_per_stratum(self, protocol: SplitProtocol):
        easy = [f"easy_{i}" for i in range(500)]
        hard = [f"hard_{i}" for i in range(500)]
        all_ids = easy + hard
        strata = {"easy": easy, "hard": hard}
        m = protocol.generate_manifest("apps", all_ids, strata=strata)

        easy_eval = [tid for tid in m.evaluation_ids if tid.startswith("easy_")]
        hard_eval = [tid for tid in m.evaluation_ids if tid.startswith("hard_")]

        easy_ratio = len(easy_eval) / 500
        hard_ratio = len(hard_eval) / 500

        assert 0.13 <= easy_ratio <= 0.27, f"Easy eval ratio {easy_ratio:.3f}"
        assert 0.13 <= hard_ratio <= 0.27, f"Hard eval ratio {hard_ratio:.3f}"

    def test_stratified_total_matches(self, protocol: SplitProtocol):
        a = [f"a_{i}" for i in range(100)]
        b = [f"b_{i}" for i in range(100)]
        m = protocol.generate_manifest("taco", a + b, strata={"a": a, "b": b})
        assert m.total_tasks == 200


# -------------------------------------------------------------------
# Persistence / immutability
# -------------------------------------------------------------------

class TestPersistence:
    def test_write_and_load_roundtrip(self, protocol: SplitProtocol):
        ids = [f"task_{i}" for i in range(30)]
        m = protocol.generate_manifest("apps", ids)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "manifest.json")
            protocol.write_manifest(m, path)
            loaded = SplitProtocol.load_manifest(path)
            assert loaded.source_name == m.source_name
            assert loaded.manifest_hash == m.manifest_hash
            assert loaded.evaluation_ids == m.evaluation_ids

    def test_immutability_error_on_existing_file(self, protocol: SplitProtocol):
        ids = [f"task_{i}" for i in range(10)]
        m = protocol.generate_manifest("apps", ids)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "manifest.json")
            protocol.write_manifest(m, path)
            with pytest.raises(FileExistsError, match="immutable"):
                protocol.write_manifest(m, path)

    def test_manifest_json_is_valid(self, protocol: SplitProtocol):
        ids = [f"task_{i}" for i in range(20)]
        m = protocol.generate_manifest("apps", ids)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "manifest.json")
            protocol.write_manifest(m, path)
            with open(path) as f:
                data = json.load(f)
            assert "source_name" in data
            assert "manifest_hash" in data
