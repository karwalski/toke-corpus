"""
Tests for generator.curriculum — Story 8.1.3.

Covers: determinism, category distribution, ID uniqueness, schema validity,
difficulty distribution, total count, and toke syntax references.
"""
from __future__ import annotations

import re

from generator.curriculum import (
    CATEGORIES,
    CurriculumGenerator,
    TaskSpec,
)

# ---------------------------------------------------------------------------
# Regex patterns for validation
# ---------------------------------------------------------------------------

_TASK_ID_RE = re.compile(r"^A-[A-Z]{3}-\d{4}(v\d+)?$")
_SIG_RE = re.compile(r"^F=\w+\(.*\):.+$")

# Valid toke primitive and composite type patterns
_TOKE_TYPES = {
    "i64", "u64", "f64", "bool", "Str", "void",
}


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed must produce identical output."""

    def test_same_seed_same_output(self) -> None:
        a = CurriculumGenerator(seed=99, total_tasks=300).generate()
        b = CurriculumGenerator(seed=99, total_tasks=300).generate()
        assert [t.task_id for t in a] == [t.task_id for t in b]
        assert [t.description for t in a] == [t.description for t in b]

    def test_different_seed_different_order(self) -> None:
        a = CurriculumGenerator(seed=1, total_tasks=300).generate()
        b = CurriculumGenerator(seed=2, total_tasks=300).generate()
        ids_a = [t.task_id for t in a]
        ids_b = [t.task_id for t in b]
        # Same underlying pool, but shuffled differently within categories
        assert ids_a != ids_b

    def test_determinism_per_category(self) -> None:
        for cat in CATEGORIES:
            a = CurriculumGenerator(seed=7).generate_category(cat, 50)
            b = CurriculumGenerator(seed=7).generate_category(cat, 50)
            assert [t.task_id for t in a] == [t.task_id for t in b]


# ---------------------------------------------------------------------------
# Category distribution
# ---------------------------------------------------------------------------


class TestCategoryDistribution:
    """All 6 categories must be present with reasonable counts."""

    def test_all_categories_present(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=600).generate()
        cats = {t.category for t in tasks}
        assert cats == set(CATEGORIES)

    def test_category_counts_within_tolerance(self) -> None:
        total = 600
        gen = CurriculumGenerator(seed=42, total_tasks=total)
        tasks = gen.generate()
        per_cat = total // len(CATEGORIES)
        for cat in CATEGORIES:
            count = sum(1 for t in tasks if t.category == cat)
            assert count >= per_cat, (
                f"Category {cat} has {count} tasks, expected >= {per_cat}"
            )

    def test_generate_category_returns_correct_category(self) -> None:
        gen = CurriculumGenerator(seed=42)
        for cat in CATEGORIES:
            tasks = gen.generate_category(cat, 10)
            for t in tasks:
                assert t.category == cat

    def test_invalid_category_raises(self) -> None:
        gen = CurriculumGenerator(seed=42)
        try:
            gen.generate_category("A-XXX", 10)
            assert False, "Expected ValueError"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Task ID uniqueness
# ---------------------------------------------------------------------------


class TestIDUniqueness:
    """Task IDs must be unique within a generation run."""

    def test_ids_unique_within_category(self) -> None:
        gen = CurriculumGenerator(seed=42)
        for cat in CATEGORIES:
            tasks = gen.generate_category(cat, 100)
            ids = [t.task_id for t in tasks]
            assert len(ids) == len(set(ids)), (
                f"Duplicate IDs in {cat}: "
                f"{[x for x in ids if ids.count(x) > 1]}"
            )

    def test_ids_unique_across_full_generation(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=600).generate()
        ids = [t.task_id for t in tasks]
        assert len(ids) == len(set(ids)), "Duplicate IDs across categories"

    def test_id_format(self) -> None:
        gen = CurriculumGenerator(seed=42)
        for cat in CATEGORIES:
            tasks = gen.generate_category(cat, 30)
            for t in tasks:
                assert _TASK_ID_RE.match(t.task_id), (
                    f"Bad ID format: {t.task_id!r}"
                )


# ---------------------------------------------------------------------------
# Schema: all fields populated
# ---------------------------------------------------------------------------


class TestSchema:
    """Every TaskSpec field must be populated correctly."""

    def test_all_fields_populated(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=300).generate()
        for t in tasks:
            assert t.task_id, f"Empty task_id"
            assert t.category in CATEGORIES, (
                f"Invalid category: {t.category}"
            )
            assert len(t.description) > 20, (
                f"Description too short for {t.task_id}: {t.description!r}"
            )
            assert t.expected_signature, (
                f"Empty signature for {t.task_id}"
            )
            assert t.difficulty in (1, 2, 3), (
                f"Invalid difficulty {t.difficulty} for {t.task_id}"
            )
            assert len(t.type_hints) > 0, (
                f"Empty type_hints for {t.task_id}"
            )
            assert t.test_input_hint, (
                f"Empty test_input_hint for {t.task_id}"
            )

    def test_signature_format(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=300).generate()
        for t in tasks:
            assert _SIG_RE.match(t.expected_signature), (
                f"Invalid signature for {t.task_id}: "
                f"{t.expected_signature!r}"
            )

    def test_signature_has_return_type(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=300).generate()
        for t in tasks:
            assert "):" in t.expected_signature, (
                f"Missing return type in {t.task_id}: "
                f"{t.expected_signature!r}"
            )

    def test_description_contains_function_signature(self) -> None:
        """The description should reference the expected function signature."""
        tasks = CurriculumGenerator(seed=42, total_tasks=300).generate()
        for t in tasks:
            # Extract function name from signature
            match = re.match(r"^F=(\w+)\(", t.expected_signature)
            assert match, (
                f"Cannot parse name from {t.expected_signature!r}"
            )
            fn_name = match.group(1)
            assert fn_name in t.description, (
                f"Function name {fn_name!r} not in description "
                f"for {t.task_id}"
            )

    def test_task_spec_is_frozen(self) -> None:
        """TaskSpec instances should be immutable."""
        gen = CurriculumGenerator(seed=42)
        tasks = gen.generate_category("A-MTH", 5)
        t = tasks[0]
        try:
            t.task_id = "A-MTH-9999"  # type: ignore[misc]
            assert False, "Expected FrozenInstanceError"
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Difficulty distribution
# ---------------------------------------------------------------------------


class TestDifficultyDistribution:
    """All 3 difficulty tiers must be present per category."""

    def test_all_tiers_present_per_category(self) -> None:
        gen = CurriculumGenerator(seed=42)
        for cat in CATEGORIES:
            # Use a large enough sample to include all tiers
            tasks = gen.generate_category(cat, 200)
            tiers = {t.difficulty for t in tasks}
            assert tiers == {1, 2, 3}, (
                f"Category {cat} missing difficulty tiers: "
                f"has {tiers}, expected {{1, 2, 3}}"
            )

    def test_difficulty_values_valid(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=300).generate()
        for t in tasks:
            assert t.difficulty in (1, 2, 3), (
                f"Invalid difficulty {t.difficulty} for {t.task_id}"
            )


# ---------------------------------------------------------------------------
# Total count
# ---------------------------------------------------------------------------


class TestTotalCount:
    """Generator must return the exact requested count."""

    def test_default_total(self) -> None:
        gen = CurriculumGenerator(seed=42, total_tasks=600)
        tasks = gen.generate()
        assert len(tasks) == 600

    def test_custom_total(self) -> None:
        for total in [6, 12, 60, 120, 300]:
            tasks = CurriculumGenerator(
                seed=42, total_tasks=total
            ).generate()
            assert len(tasks) == total, (
                f"Expected {total}, got {len(tasks)}"
            )

    def test_category_count_exact(self) -> None:
        gen = CurriculumGenerator(seed=42)
        for count in [1, 5, 10, 50]:
            tasks = gen.generate_category("A-MTH", count)
            assert len(tasks) == count

    def test_large_count_uses_variants(self) -> None:
        """When count exceeds the pool, variant suffixes are appended."""
        gen = CurriculumGenerator(seed=42)
        tasks = gen.generate_category("A-MTH", 500)
        assert len(tasks) == 500
        # Some IDs should have variant suffixes
        variant_ids = [t.task_id for t in tasks if "v" in t.task_id]
        assert len(variant_ids) > 0, (
            "Expected some variant IDs for count exceeding pool size"
        )


# ---------------------------------------------------------------------------
# Toke type references
# ---------------------------------------------------------------------------


class TestTokeTypes:
    """Type hints should reference valid toke types."""

    def _is_valid_toke_type(self, ty: str) -> bool:
        """Check if a type string is a valid toke type expression."""
        # Primitives
        if ty in _TOKE_TYPES:
            return True
        # Array type [T]
        if ty.startswith("[") and ty.endswith("]") and ":" not in ty:
            return self._is_valid_toke_type(ty[1:-1])
        # Map type [K:V]
        if ty.startswith("[") and ty.endswith("]") and ":" in ty:
            inner = ty[1:-1]
            colon = inner.index(":")
            return (
                self._is_valid_toke_type(inner[:colon])
                and self._is_valid_toke_type(inner[colon + 1:])
            )
        # Error union T!Err
        if "!" in ty:
            parts = ty.split("!", 1)
            return self._is_valid_toke_type(parts[0])
        # Nested arrays [[T]]
        if ty.startswith("[[") and ty.endswith("]]"):
            return self._is_valid_toke_type(ty[1:-1])
        return False

    def test_type_hints_valid(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=300).generate()
        for t in tasks:
            for ty in t.type_hints:
                assert self._is_valid_toke_type(ty), (
                    f"Invalid toke type {ty!r} in type_hints "
                    f"for {t.task_id}"
                )


# ---------------------------------------------------------------------------
# Content quality: descriptions are self-contained
# ---------------------------------------------------------------------------


class TestContentQuality:
    """Task descriptions should be self-contained and varied."""

    def test_descriptions_mention_write_function(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=300).generate()
        for t in tasks:
            assert "Write a function" in t.description, (
                f"Description for {t.task_id} does not start with "
                f"'Write a function': {t.description[:80]!r}"
            )

    def test_descriptions_are_unique_within_base_pool(self) -> None:
        """Base pool tasks (before variant cycling) have unique descriptions."""
        gen = CurriculumGenerator(seed=42)
        for cat in CATEGORIES:
            # Generate just the base pool (small count)
            tasks = gen.generate_category(cat, 10)
            descs = [t.description for t in tasks]
            assert len(descs) == len(set(descs)), (
                f"Duplicate descriptions in {cat}"
            )

    def test_test_input_hints_non_trivial(self) -> None:
        tasks = CurriculumGenerator(seed=42, total_tasks=300).generate()
        for t in tasks:
            assert len(t.test_input_hint) >= 5, (
                f"Trivial test_input_hint for {t.task_id}: "
                f"{t.test_input_hint!r}"
            )
