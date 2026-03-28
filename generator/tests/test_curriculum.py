"""
Tests for generator.curriculum — Story 2.11.1.

Covers: determinism, phase coverage, ID uniqueness, signature format, task counts.
"""
from __future__ import annotations

import random
import re

from generator.curriculum import (
    Curriculum,
    Task,
    TaskPhase,
    generate_curriculum_batch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIG_RE = re.compile(
    r"^F=\w+\(.*\):"   # F=name(params):returnType
)


def _all_tasks() -> list[Task]:
    """Return every task from a default Curriculum."""
    return Curriculum(random.Random(42)).all_tasks()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Same seed must produce identical output."""

    def test_same_seed_same_output(self) -> None:
        a = generate_curriculum_batch(TaskPhase.A, 50, seed=99)
        b = generate_curriculum_batch(TaskPhase.A, 50, seed=99)
        assert [t.id for t in a] == [t.id for t in b]

    def test_different_seed_different_order(self) -> None:
        a = generate_curriculum_batch(TaskPhase.A, 50, seed=1)
        b = generate_curriculum_batch(TaskPhase.A, 50, seed=2)
        # Same task IDs but (very likely) different ordering
        assert set(t.id for t in a) == set(t.id for t in b)
        # Not guaranteed to differ but extremely likely for 50 items
        assert [t.id for t in a] != [t.id for t in b]

    def test_determinism_across_phases(self) -> None:
        for phase in TaskPhase:
            a = generate_curriculum_batch(phase, 30, seed=7)
            b = generate_curriculum_batch(phase, 30, seed=7)
            assert [t.id for t in a] == [t.id for t in b]


# ---------------------------------------------------------------------------
# Phase coverage
# ---------------------------------------------------------------------------

class TestPhaseCoverage:
    """Each phase produces tasks and all tasks belong to the right phase."""

    def test_phase_a_tasks_exist(self) -> None:
        tasks = generate_curriculum_batch(TaskPhase.A, 10, seed=42)
        assert len(tasks) == 10
        assert all(t.phase == TaskPhase.A for t in tasks)

    def test_phase_b_tasks_exist(self) -> None:
        tasks = generate_curriculum_batch(TaskPhase.B, 10, seed=42)
        assert len(tasks) == 10
        assert all(t.phase == TaskPhase.B for t in tasks)

    def test_phase_c_tasks_exist(self) -> None:
        tasks = generate_curriculum_batch(TaskPhase.C, 10, seed=42)
        assert len(tasks) == 10
        assert all(t.phase == TaskPhase.C for t in tasks)

    def test_phase_a_categories(self) -> None:
        """Phase A must include sorting, string, math, array, and parsing tasks."""
        tasks = Curriculum(random.Random(42)).generate(TaskPhase.A, 200)
        cats = {t.category for t in tasks}
        for expected in [
            "sorting-and-searching",
            "string-manipulation",
            "mathematical-computation",
            "array-sequence-operations",
            "parsing",
        ]:
            assert expected in cats, f"Missing category {expected!r} in Phase A"

    def test_phase_b_categories(self) -> None:
        tasks = Curriculum(random.Random(42)).generate(TaskPhase.B, 200)
        cats = {t.category for t in tasks}
        for expected in [
            "linked-list",
            "binary-tree",
            "stack-and-queue",
            "graph-operations",
            "hash-table",
        ]:
            assert expected in cats, f"Missing category {expected!r} in Phase B"

    def test_phase_c_categories(self) -> None:
        tasks = Curriculum(random.Random(42)).generate(TaskPhase.C, 200)
        cats = {t.category for t in tasks}
        for expected in [
            "http-server",
            "database-crud",
            "file-io",
            "json-api",
            "process-spawn",
        ]:
            assert expected in cats, f"Missing category {expected!r} in Phase C"


# ---------------------------------------------------------------------------
# Task ID uniqueness
# ---------------------------------------------------------------------------

class TestIDUniqueness:
    """Task IDs must be unique within and across phases."""

    def test_ids_unique_within_pool(self) -> None:
        """The *pool* of unique tasks (before generate repeats) has no dupes."""
        all_tasks = _all_tasks()
        ids = [t.id for t in all_tasks]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_ids_unique_across_phases(self) -> None:
        c = Curriculum(random.Random(42))
        ids: list[str] = []
        for phase in TaskPhase:
            pool = c.generate(phase, 200)
            ids.extend(t.id for t in pool)
        # Only check *base* IDs (before generate duplicates via wrapping)
        base_ids = list(dict.fromkeys(ids))  # preserves order, drops dupes
        # Every unique base ID should only appear in one phase
        seen_phase: dict[str, str] = {}
        for t in _all_tasks():
            if t.id in seen_phase:
                assert seen_phase[t.id] == t.phase.value, (
                    f"ID {t.id} appears in both phase {seen_phase[t.id]} and {t.phase.value}"
                )
            seen_phase[t.id] = t.phase.value

    def test_id_format(self) -> None:
        """IDs follow the pattern PHASE-CAT-NNNN."""
        for t in _all_tasks():
            assert re.match(r"^[ABC]-[A-Z]{3}-\d{4}$", t.id), f"Bad ID format: {t.id!r}"


# ---------------------------------------------------------------------------
# Function signature validity
# ---------------------------------------------------------------------------

class TestSignatures:
    """Toke function signatures must follow F=name(params):retType."""

    def test_all_signatures_valid(self) -> None:
        for t in _all_tasks():
            assert _SIG_RE.match(t.function_signature), (
                f"Invalid signature for {t.id}: {t.function_signature!r}"
            )

    def test_signatures_have_return_type(self) -> None:
        for t in _all_tasks():
            # After the last ')' there should be ':' then a type
            assert "):" in t.function_signature, (
                f"Missing return type in {t.id}: {t.function_signature!r}"
            )

    def test_signatures_non_empty_name(self) -> None:
        for t in _all_tasks():
            # Extract function name between F= and (
            match = re.match(r"^F=(\w+)\(", t.function_signature)
            assert match, f"Cannot parse name from {t.function_signature!r}"
            assert len(match.group(1)) > 0


# ---------------------------------------------------------------------------
# Task count per phase
# ---------------------------------------------------------------------------

class TestTaskCounts:
    """generate_curriculum_batch must return the requested count."""

    def test_at_least_100_phase_a(self) -> None:
        tasks = generate_curriculum_batch(TaskPhase.A, 100, seed=42)
        assert len(tasks) == 100

    def test_at_least_100_phase_b(self) -> None:
        tasks = generate_curriculum_batch(TaskPhase.B, 100, seed=42)
        assert len(tasks) == 100

    def test_at_least_100_phase_c(self) -> None:
        tasks = generate_curriculum_batch(TaskPhase.C, 100, seed=42)
        assert len(tasks) == 100

    def test_exact_count(self) -> None:
        for count in [1, 7, 25, 50, 150]:
            tasks = generate_curriculum_batch(TaskPhase.A, count, seed=42)
            assert len(tasks) == count

    def test_each_task_has_test_cases(self) -> None:
        for t in _all_tasks():
            assert len(t.test_cases) > 0, f"Task {t.id} has no test cases"

    def test_each_task_has_languages(self) -> None:
        for t in _all_tasks():
            assert len(t.languages) > 0, f"Task {t.id} has no languages"


# ---------------------------------------------------------------------------
# Curriculum class
# ---------------------------------------------------------------------------

class TestCurriculumClass:
    """Test the Curriculum wrapper itself."""

    def test_all_tasks_non_empty(self) -> None:
        assert len(_all_tasks()) > 0

    def test_categories_list(self) -> None:
        c = Curriculum()
        cats = c.categories
        assert len(cats) > 0
        # Categories span all three phases
        phases = {cat.phase for cat in cats}
        assert phases == {TaskPhase.A, TaskPhase.B, TaskPhase.C}

    def test_generate_zero(self) -> None:
        c = Curriculum(random.Random(0))
        assert c.generate(TaskPhase.A, 0) == []
