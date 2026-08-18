"""
Tests for generator.curriculum_v2 — Story 9.2.1.

Covers: all categories, ID uniqueness, determinism, type annotations,
test case structure, domain context, difficulty range, total count.
"""
from __future__ import annotations

import re

from generator.curriculum_v2 import (
    ALL_CATEGORIES,
    DOMAIN_CATEGORIES,
    VALID_TOKE_TYPES,
    CurriculumGeneratorV2,
    TaskSpecV2,
    TestCase,
)
from generator.curriculum import CATEGORIES as PHASE_A_CATEGORIES

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_TASK_ID_RE = re.compile(r"^[AD]-[A-Z]{3}-\d{4}(v\d+)?$")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _small_gen(seed: int = 42, pa: int = 60, dom: int = 80) -> CurriculumGeneratorV2:
    """Build a small generator for fast tests."""
    return CurriculumGeneratorV2(seed=seed, phase_a_tasks=pa, domain_tasks=dom)


def _domain_pool(seed: int = 42) -> list[TaskSpecV2]:
    """Generate just the domain tasks (small count)."""
    gen = _small_gen(seed=seed, pa=0, dom=80)
    # Only domain tasks since pa=0
    return gen.generate()


# ---------------------------------------------------------------------------
# 1. All categories generate valid specs
# ---------------------------------------------------------------------------


class TestAllCategoriesValid:
    """Every category produces TaskSpecV2 objects with required fields."""

    def test_phase_a_categories_present(self) -> None:
        tasks = _small_gen(pa=60, dom=80).generate()
        cats = {t.category for t in tasks}
        for cat in PHASE_A_CATEGORIES:
            assert cat in cats, f"Phase A category {cat} missing"

    def test_domain_categories_present(self) -> None:
        tasks = _small_gen(pa=60, dom=80).generate()
        cats = {t.category for t in tasks}
        for cat in DOMAIN_CATEGORIES:
            assert cat in cats, f"Domain category {cat} missing"

    def test_each_domain_category_generates_specs(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            specs = gen.generate_domain_category(cat, 5)
            assert len(specs) == 5, f"{cat} did not generate 5 specs"
            for s in specs:
                assert isinstance(s, TaskSpecV2)

    def test_all_14_categories_present(self) -> None:
        tasks = _small_gen(pa=120, dom=160).generate()
        cats = {t.category for t in tasks}
        assert len(cats) >= 14, f"Expected 14+ categories, got {len(cats)}: {cats}"


# ---------------------------------------------------------------------------
# 2. Task IDs are unique
# ---------------------------------------------------------------------------


class TestTaskIDUniqueness:
    """No two tasks may share the same task_id."""

    def test_ids_unique_small(self) -> None:
        tasks = _small_gen(pa=120, dom=160).generate()
        ids = [t.task_id for t in tasks]
        assert len(ids) == len(set(ids)), "Duplicate task IDs found"

    def test_domain_ids_unique(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        all_ids: list[str] = []
        for cat in DOMAIN_CATEGORIES:
            specs = gen.generate_domain_category(cat, 30)
            all_ids.extend(s.task_id for s in specs)
        assert len(all_ids) == len(set(all_ids)), "Duplicate domain IDs"

    def test_ids_match_pattern(self) -> None:
        tasks = _small_gen(pa=60, dom=80).generate()
        for t in tasks:
            assert _TASK_ID_RE.match(t.task_id), (
                f"Task ID {t.task_id!r} does not match pattern"
            )


# ---------------------------------------------------------------------------
# 3. Deterministic output with same seed
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed must produce identical output."""

    def test_same_seed_same_output(self) -> None:
        a = _small_gen(seed=99, pa=60, dom=80).generate()
        b = _small_gen(seed=99, pa=60, dom=80).generate()
        assert [t.task_id for t in a] == [t.task_id for t in b]
        assert [t.description for t in a] == [t.description for t in b]

    def test_different_seed_different_order(self) -> None:
        a = _small_gen(seed=1, pa=60, dom=80).generate()
        b = _small_gen(seed=2, pa=60, dom=80).generate()
        ids_a = [t.task_id for t in a]
        ids_b = [t.task_id for t in b]
        assert ids_a != ids_b

    def test_determinism_per_domain_category(self) -> None:
        for cat in DOMAIN_CATEGORIES:
            a = CurriculumGeneratorV2(seed=7).generate_domain_category(cat, 10)
            b = CurriculumGeneratorV2(seed=7).generate_domain_category(cat, 10)
            assert [t.task_id for t in a] == [t.task_id for t in b]


# ---------------------------------------------------------------------------
# 4. Type annotations use valid toke types
# ---------------------------------------------------------------------------


class TestTypeAnnotations:
    """input_types and output_type must be valid toke types."""

    def test_domain_input_types_valid(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            specs = gen.generate_domain_category(cat, 10)
            for s in specs:
                for ty in s.input_types:
                    assert ty in VALID_TOKE_TYPES, (
                        f"Invalid input type {ty!r} in {s.task_id}"
                    )

    def test_domain_output_types_valid(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            specs = gen.generate_domain_category(cat, 10)
            for s in specs:
                assert s.output_type in VALID_TOKE_TYPES, (
                    f"Invalid output type {s.output_type!r} in {s.task_id}"
                )

    def test_input_types_non_empty_for_domain(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            pool = gen._expand_domain_category(cat)
            for s in pool:
                assert len(s.input_types) > 0, (
                    f"Empty input_types in {s.task_id}"
                )


# ---------------------------------------------------------------------------
# 5. Test cases have matching types
# ---------------------------------------------------------------------------


class TestTestCases:
    """Domain tasks must have 3 test cases each."""

    def test_domain_tasks_have_3_test_cases(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            pool = gen._expand_domain_category(cat)
            for s in pool:
                assert len(s.test_cases) == 3, (
                    f"{s.task_id} has {len(s.test_cases)} test cases, expected 3"
                )

    def test_test_cases_are_testcase_objects(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        specs = gen.generate_domain_category("D-WEB", 5)
        for s in specs:
            for tc in s.test_cases:
                assert isinstance(tc, TestCase)

    def test_test_case_inputs_are_lists(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            pool = gen._expand_domain_category(cat)
            for s in pool:
                for tc in s.test_cases:
                    assert isinstance(tc.inputs, list), (
                        f"Test case inputs for {s.task_id} not a list"
                    )

    def test_test_case_input_count_matches_input_types(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            pool = gen._expand_domain_category(cat)
            for s in pool:
                for tc in s.test_cases:
                    assert len(tc.inputs) == len(s.input_types), (
                        f"{s.task_id}: test case has {len(tc.inputs)} inputs "
                        f"but spec has {len(s.input_types)} input_types"
                    )


# ---------------------------------------------------------------------------
# 6. Domain context is non-empty for new categories
# ---------------------------------------------------------------------------


class TestDomainContext:
    """All domain tasks must have non-empty domain_context."""

    def test_domain_context_non_empty(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            pool = gen._expand_domain_category(cat)
            for s in pool:
                assert s.domain_context, (
                    f"{s.task_id} has empty domain_context"
                )

    def test_domain_context_contains_function_sigs(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            pool = gen._expand_domain_category(cat)
            for s in pool:
                assert "F=" in s.domain_context, (
                    f"{s.task_id} domain_context missing function signatures"
                )


# ---------------------------------------------------------------------------
# 7. Difficulty range
# ---------------------------------------------------------------------------


class TestDifficulty:
    def test_difficulty_in_range(self) -> None:
        tasks = _small_gen(pa=60, dom=80).generate()
        for t in tasks:
            assert 1 <= t.difficulty <= 5, (
                f"{t.task_id} difficulty {t.difficulty} out of range 1-5"
            )


# ---------------------------------------------------------------------------
# 8. Total count >= 100K
# ---------------------------------------------------------------------------


class TestTotalCount:
    def test_default_generates_100k_plus(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        tasks = gen.generate()
        assert len(tasks) >= 100_000, (
            f"Expected >= 100,000 tasks, got {len(tasks)}"
        )

    def test_category_distribution(self) -> None:
        gen = CurriculumGeneratorV2(seed=42, phase_a_tasks=120, domain_tasks=160)
        tasks = gen.generate()
        from collections import Counter
        counts = Counter(t.category for t in tasks)
        # Every category should have at least 1 task
        for cat in ALL_CATEGORIES:
            assert counts[cat] > 0, f"Category {cat} has 0 tasks"


# ---------------------------------------------------------------------------
# 9. Description quality
# ---------------------------------------------------------------------------


class TestDescriptionQuality:
    def test_descriptions_non_empty(self) -> None:
        tasks = _small_gen(pa=60, dom=80).generate()
        for t in tasks:
            assert t.description, f"{t.task_id} has empty description"

    def test_descriptions_have_reasonable_length(self) -> None:
        gen = CurriculumGeneratorV2(seed=42)
        for cat in DOMAIN_CATEGORIES:
            pool = gen._expand_domain_category(cat)
            for s in pool:
                assert len(s.description) >= 20, (
                    f"{s.task_id} description too short: {s.description!r}"
                )
