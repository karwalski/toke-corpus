"""Tests for the task source registry."""

import pytest

from registry.source_registry import (
    REGISTRY,
    TaskSource,
    get_evaluation_sources,
    get_source,
    get_sources_by_category,
    get_split_sources,
    get_training_sources,
)

VALID_USAGES = {"evaluation", "training", "split"}
VALID_CATEGORIES = {"benchmark", "exercise", "algorithm", "domain"}


# -------------------------------------------------------------------
# Field validation
# -------------------------------------------------------------------

class TestRegistryFieldValidation:
    """Every source entry must have well-formed fields."""

    def test_all_sources_have_nonempty_name(self):
        for s in REGISTRY:
            assert s.name, f"Source has empty name: {s}"

    def test_all_sources_have_nonempty_display_name(self):
        for s in REGISTRY:
            assert s.display_name, f"Source {s.name} has empty display_name"

    def test_all_sources_have_nonnegative_size(self):
        for s in REGISTRY:
            assert s.size >= 0, f"Source {s.name} has negative size"

    def test_all_sources_have_nonempty_license(self):
        for s in REGISTRY:
            assert s.license, f"Source {s.name} has empty license"

    def test_all_sources_have_valid_usage(self):
        for s in REGISTRY:
            assert s.usage in VALID_USAGES, (
                f"Source {s.name} has invalid usage={s.usage!r}"
            )

    def test_all_sources_have_valid_category(self):
        for s in REGISTRY:
            assert s.category in VALID_CATEGORIES, (
                f"Source {s.name} has invalid category={s.category!r}"
            )

    def test_all_sources_are_frozen_dataclass(self):
        for s in REGISTRY:
            assert isinstance(s, TaskSource)
            with pytest.raises(AttributeError):
                s.name = "mutated"  # type: ignore[misc]


# -------------------------------------------------------------------
# Uniqueness
# -------------------------------------------------------------------

class TestRegistryUniqueness:
    def test_no_duplicate_names(self):
        names = [s.name for s in REGISTRY]
        assert len(names) == len(set(names)), "Duplicate source names found"


# -------------------------------------------------------------------
# Usage / split_ratio consistency
# -------------------------------------------------------------------

class TestUsageConsistency:
    def test_evaluation_sources_have_no_split_ratio(self):
        for s in REGISTRY:
            if s.usage == "evaluation":
                assert s.split_ratio is None, (
                    f"Evaluation source {s.name} should have split_ratio=None"
                )

    def test_split_sources_have_split_ratio_080(self):
        for s in REGISTRY:
            if s.usage == "split":
                assert s.split_ratio == 0.8, (
                    f"Split source {s.name} should have split_ratio=0.8"
                )

    def test_training_sources_have_no_split_ratio(self):
        for s in REGISTRY:
            if s.usage == "training":
                assert s.split_ratio is None, (
                    f"Training source {s.name} should have split_ratio=None"
                )


# -------------------------------------------------------------------
# Lookup helpers
# -------------------------------------------------------------------

class TestGetSource:
    def test_lookup_existing_source(self):
        src = get_source("humaneval")
        assert src.display_name == "HumanEval"

    def test_lookup_missing_source_raises(self):
        with pytest.raises(KeyError, match="Unknown source"):
            get_source("nonexistent-source")

    def test_lookup_returns_same_object_as_registry(self):
        src = get_source("apps")
        assert src in REGISTRY


class TestGetTrainingSources:
    def test_returns_only_training(self):
        for s in get_training_sources():
            assert s.usage == "training"

    def test_excludes_evaluation(self):
        names = {s.name for s in get_training_sources()}
        for s in REGISTRY:
            if s.usage == "evaluation":
                assert s.name not in names

    def test_excludes_split(self):
        names = {s.name for s in get_training_sources()}
        for s in REGISTRY:
            if s.usage == "split":
                assert s.name not in names


class TestGetEvaluationSources:
    def test_returns_only_evaluation(self):
        for s in get_evaluation_sources():
            assert s.usage == "evaluation"

    def test_includes_holdout_sources(self):
        names = {s.name for s in get_evaluation_sources()}
        assert "gate1-holdout" in names
        assert "gate2-hidden" in names

    def test_excludes_training(self):
        names = {s.name for s in get_evaluation_sources()}
        for s in REGISTRY:
            if s.usage == "training":
                assert s.name not in names


class TestGetSplitSources:
    def test_returns_only_split(self):
        for s in get_split_sources():
            assert s.usage == "split"

    def test_all_have_ratio(self):
        for s in get_split_sources():
            assert s.split_ratio is not None

    def test_known_split_sources(self):
        names = {s.name for s in get_split_sources()}
        assert "apps" in names
        assert "codecontests" in names
        assert "taco" in names
        assert "leetcodedataset" in names


class TestGetSourcesByCategory:
    def test_benchmark_category(self):
        benchmarks = get_sources_by_category("benchmark")
        assert len(benchmarks) > 0
        for s in benchmarks:
            assert s.category == "benchmark"

    def test_exercise_category(self):
        exercises = get_sources_by_category("exercise")
        assert len(exercises) > 0
        for s in exercises:
            assert s.category == "exercise"

    def test_empty_category(self):
        assert get_sources_by_category("nonexistent") == []
