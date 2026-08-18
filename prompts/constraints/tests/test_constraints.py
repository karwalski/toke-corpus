"""Tests for constraint-driven generation prompt templates."""

from __future__ import annotations

import random
import re

import pytest

from prompts.constraints.constraints import Constraint, ConstraintGenerator


@pytest.fixture
def gen() -> ConstraintGenerator:
    return ConstraintGenerator()


# -- Basic invariants -------------------------------------------------------

def test_list_constraints_returns_all(gen: ConstraintGenerator) -> None:
    names = gen.list_constraints()
    assert len(names) >= 8


def test_constraint_names_unique(gen: ConstraintGenerator) -> None:
    names = gen.list_constraints()
    assert len(names) == len(set(names))


def test_all_constraints_have_nonempty_fields(gen: ConstraintGenerator) -> None:
    for name in gen.list_constraints():
        c = gen.get_constraint(name)
        assert c.name, "name must not be empty"
        assert c.description, "description must not be empty"
        assert c.required_features, "required_features must not be empty"
        assert c.prompt_template, "prompt_template must not be empty"
        assert c.example_output, "example_output must not be empty"
        assert 1 <= c.difficulty <= 5, f"difficulty out of range: {c.difficulty}"
        assert c.validation_criteria, "validation_criteria must not be empty"


def test_all_constraints_have_task_placeholder(gen: ConstraintGenerator) -> None:
    for name in gen.list_constraints():
        c = gen.get_constraint(name)
        assert "{task_description}" in c.prompt_template


# -- Prompt generation ------------------------------------------------------

def test_generate_prompt_fills_task(gen: ConstraintGenerator) -> None:
    prompt = gen.generate_prompt("Write a factorial function", "sum_type")
    assert "Write a factorial function" in prompt
    assert "{task_description}" not in prompt


def test_generate_prompt_includes_spec_reference(gen: ConstraintGenerator) -> None:
    prompt = gen.generate_prompt("any task", "error_propagation")
    assert "toke Phase 2 Specification Reference" in prompt


def test_generate_prompt_unknown_constraint_raises(gen: ConstraintGenerator) -> None:
    with pytest.raises(KeyError, match="nonexistent"):
        gen.generate_prompt("task", "nonexistent")


def test_generate_prompt_contains_constraint_text(gen: ConstraintGenerator) -> None:
    prompt = gen.generate_prompt("any task", "arena_block")
    assert "arena" in prompt.lower()


# -- Batch generation -------------------------------------------------------

def test_batch_correct_count(gen: ConstraintGenerator) -> None:
    tasks = ["task a", "task b", "task c"]
    results = gen.generate_batch(tasks, constraints_per_task=2, rng=random.Random(42))
    assert len(results) == 6  # 3 tasks x 2 constraints


def test_batch_single_constraint_per_task(gen: ConstraintGenerator) -> None:
    tasks = ["task a", "task b"]
    results = gen.generate_batch(tasks, constraints_per_task=1, rng=random.Random(99))
    assert len(results) == 2


def test_batch_returns_tuples_of_prompt_and_name(gen: ConstraintGenerator) -> None:
    tasks = ["task x"]
    results = gen.generate_batch(tasks, constraints_per_task=1, rng=random.Random(7))
    assert len(results) == 1
    prompt, cname = results[0]
    assert isinstance(prompt, str) and len(prompt) > 0
    assert cname in gen.list_constraints()


def test_batch_all_names_are_valid(gen: ConstraintGenerator) -> None:
    tasks = ["t1", "t2", "t3", "t4"]
    results = gen.generate_batch(tasks, constraints_per_task=3, rng=random.Random(0))
    valid = set(gen.list_constraints())
    for _, cname in results:
        assert cname in valid


def test_batch_empty_tasks(gen: ConstraintGenerator) -> None:
    results = gen.generate_batch([], constraints_per_task=2)
    assert results == []


# -- Example output validation (string checks) -----------------------------

def test_sum_type_example_has_sum_type(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("sum_type").example_output
    assert "t=$" in ex
    assert "|{" in ex


def test_error_propagation_example_has_bang(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("error_propagation").example_output
    assert "!$" in ex  # error union in signature or propagation


def test_match_3arms_example_has_3_arms(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("match_3arms").example_output
    # A match with 3 arms has the pattern: |{arm1;arm2;arm3}
    match_block = re.search(r"\|\{([^}]+)\}", ex)
    assert match_block is not None
    arms = match_block.group(1).split(";")
    assert len(arms) >= 3


def test_map_type_example_has_map_syntax(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("map_type").example_output
    assert "@($" in ex or "@(i" in ex  # map type like @($str:i64)
    assert ".get(" in ex


def test_arena_block_example_has_arena(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("arena_block").example_output
    assert "{arena" in ex


def test_multi_param_example_has_4_params(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("multi_param").example_output
    # Find the parameter list between ( and ) after f=
    match = re.search(r"f=\w+\(([^)]+)\)", ex)
    assert match is not None
    params = match.group(1).split(";")
    assert len(params) >= 4


def test_nested_struct_example_has_nested_access(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("nested_struct").example_output
    # Nested field access: identifier.field.field
    assert re.search(r"\w+\.\w+\.\w+", ex) is not None
    # At least two type declarations
    assert ex.count("t=$") >= 2


def test_error_3variant_example_has_3_variants(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("error_3variant").example_output
    # Count $-prefixed variant names inside the type decl
    type_match = re.search(r"t=\$\w+\{([^}]+)\}", ex)
    assert type_match is not None
    variants = [s.strip() for s in type_match.group(1).split(";") if "$" in s]
    assert len(variants) >= 3


def test_error_return_union_example(gen: ConstraintGenerator) -> None:
    ex = gen.get_constraint("error_return_union").example_output
    assert "!$" in ex
    assert "|{" in ex
    assert "$ok:" in ex


# -- Example outputs start with module declaration -------------------------

def test_all_examples_start_with_module(gen: ConstraintGenerator) -> None:
    for name in gen.list_constraints():
        ex = gen.get_constraint(name).example_output
        assert ex.lstrip().startswith("m="), (
            f"Example for {name!r} must start with m= module declaration"
        )
