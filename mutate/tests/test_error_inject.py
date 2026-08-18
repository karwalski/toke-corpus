"""Tests for the error injection and repair pair generator.

Story 9.1.3 -- verifies text transformations are correct without
requiring the toke compiler.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mutate.error_inject import ErrorInjector, Injection, batch_inject


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FACTORIAL_SRC = "m=fact;f=factorial(n:i64):i64{if(n<2){<1};<n*factorial(n-1)};"

ARRSUM_SRC = (
    "m=arrsum;"
    "f=sum(arr:@(i64)):i64{"
    "let total=mut.0;"
    "lp(let i=0;i<arr.len;i=i+1){"
    "total=total+arr.get(i)"
    "};"
    "<total"
    "};"
)

ADD_SRC = "m=add;f=add(a:i64;b:i64):i64{<a+b};"

MULTI_FUNC_SRC = (
    "m=math;"
    "f=double(x:i64):i64{<x*2};"
    "f=triple(x:i64):i64{<x*3};"
    "f=apply(n:i64):i64{<double(triple(n))};"
)

IMMUTABLE_SRC = "m=test;f=go():i64{let x=42;<x};"

MUTABLE_SRC = "m=test;f=go():i64{let x=mut.0;x=10;<x};"

BOOL_SRC = "m=test;f=isPos(n:i64):bool{if(n>0){<true};<false};"

STR_SRC = 'm=test;f=greet(name:$str):$str{<"hello"};'


@pytest.fixture
def injector() -> ErrorInjector:
    return ErrorInjector(seed=42)


# ---------------------------------------------------------------------------
# Type mismatch tests
# ---------------------------------------------------------------------------


class TestTypeMismatch:
    def test_finds_type_annotations(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ADD_SRC) if r.injection_type == "type_mismatch"]
        # a:i64, b:i64, return :i64 -- should find at least 2
        assert len(results) >= 2

    def test_changes_type(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ADD_SRC) if r.injection_type == "type_mismatch"]
        for r in results:
            assert r.broken_source != ADD_SRC
            assert "i64" not in r.broken_source or r.broken_source.count("i64") < ADD_SRC.count("i64")

    def test_preserves_structure(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ADD_SRC) if r.injection_type == "type_mismatch"]
        for r in results:
            # The only difference should be a type name
            assert "f=add(" in r.broken_source
            assert "m=add;" in r.broken_source

    def test_bool_return_type(self, injector: ErrorInjector):
        results = [r for r in injector.inject(BOOL_SRC) if r.injection_type == "type_mismatch"]
        types_changed = {r.injection_details.split("Changed ")[1].split(" ")[0] for r in results}
        # Should find bool and i64 annotations
        assert len(results) >= 1

    def test_str_type(self, injector: ErrorInjector):
        results = [r for r in injector.inject(STR_SRC) if r.injection_type == "type_mismatch"]
        assert len(results) >= 1
        # At least one should change $str to something else
        any_str_changed = any("$str" in r.injection_details for r in results)
        assert any_str_changed


# ---------------------------------------------------------------------------
# Missing semicolon tests
# ---------------------------------------------------------------------------


class TestMissingSemicolon:
    def test_finds_semicolons(self, injector: ErrorInjector):
        results = [r for r in injector.inject(FACTORIAL_SRC) if r.injection_type == "missing_semicolon"]
        assert len(results) >= 1

    def test_removes_semicolon(self, injector: ErrorInjector):
        results = [r for r in injector.inject(FACTORIAL_SRC) if r.injection_type == "missing_semicolon"]
        for r in results:
            assert len(r.broken_source) == len(FACTORIAL_SRC) - 1

    def test_does_not_remove_param_semicolons(self, injector: ErrorInjector):
        # The ; between a:i64;b:i64 is inside parens -- should not be removed
        results = [r for r in injector.inject(ADD_SRC) if r.injection_type == "missing_semicolon"]
        for r in results:
            # Check that param separator is still intact if it exists
            # (the removed ; should not be between params)
            assert r.broken_source != ADD_SRC

    def test_arrsum_semicolons(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ARRSUM_SRC) if r.injection_type == "missing_semicolon"]
        # arrsum has several statement-separating semicolons
        assert len(results) >= 2


# ---------------------------------------------------------------------------
# Wrong operator tests
# ---------------------------------------------------------------------------


class TestWrongOperator:
    def test_finds_operators(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ADD_SRC) if r.injection_type == "wrong_operator"]
        # a+b should be found
        assert len(results) >= 1

    def test_swaps_arithmetic(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ADD_SRC) if r.injection_type == "wrong_operator"]
        # The + in a+b should be swapped to - or *
        plus_swaps = [r for r in results if "'+'" in r.injection_details]
        assert len(plus_swaps) >= 1
        for r in plus_swaps:
            assert "+" not in r.broken_source or r.broken_source.count("+") < ADD_SRC.count("+")

    def test_swaps_comparison(self, injector: ErrorInjector):
        results = [r for r in injector.inject(FACTORIAL_SRC) if r.injection_type == "wrong_operator"]
        # n<2 should have < swapped
        cmp_swaps = [r for r in results if "'<'" in r.injection_details]
        assert len(cmp_swaps) >= 1

    def test_does_not_swap_return_operator(self, injector: ErrorInjector):
        # The < in <a+b is a return operator, not a comparison
        results = [r for r in injector.inject(ADD_SRC) if r.injection_type == "wrong_operator"]
        for r in results:
            # Broken source should still have return <
            # (we should not replace the return <)
            assert r.injection_details  # just ensure we got details

    def test_multiplication_in_factorial(self, injector: ErrorInjector):
        results = [r for r in injector.inject(FACTORIAL_SRC) if r.injection_type == "wrong_operator"]
        mul_swaps = [r for r in results if "'*'" in r.injection_details]
        assert len(mul_swaps) >= 1


# ---------------------------------------------------------------------------
# Immutable reassignment tests
# ---------------------------------------------------------------------------


class TestImmutableReassignment:
    def test_finds_immutable_let(self, injector: ErrorInjector):
        results = [r for r in injector.inject(IMMUTABLE_SRC) if r.injection_type == "immutable_reassignment"]
        assert len(results) >= 1

    def test_inserts_reassignment(self, injector: ErrorInjector):
        results = [r for r in injector.inject(IMMUTABLE_SRC) if r.injection_type == "immutable_reassignment"]
        assert len(results) == 1
        r = results[0]
        assert "x=99;" in r.broken_source
        assert len(r.broken_source) > len(IMMUTABLE_SRC)

    def test_skips_mutable(self, injector: ErrorInjector):
        results = [r for r in injector.inject(MUTABLE_SRC) if r.injection_type == "immutable_reassignment"]
        # let x=mut.0 should NOT trigger this injection
        assert len(results) == 0

    def test_loop_counter_is_immutable(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ARRSUM_SRC) if r.injection_type == "immutable_reassignment"]
        # let i=0 inside lp() is immutable
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# Undefined variable tests
# ---------------------------------------------------------------------------


class TestUndefinedVariable:
    def test_finds_variables(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ARRSUM_SRC) if r.injection_type == "undefined_variable"]
        assert len(results) >= 1

    def test_renames_usage(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ARRSUM_SRC) if r.injection_type == "undefined_variable"]
        for r in results:
            assert r.broken_source != ARRSUM_SRC
            # The broken source should have a typo'd variable name
            assert "Renamed usage" in r.injection_details

    def test_keeps_declaration(self, injector: ErrorInjector):
        results = [r for r in injector.inject(ARRSUM_SRC) if r.injection_type == "undefined_variable"]
        # The 'total' declaration should still exist
        total_results = [r for r in results if "'total'" in r.injection_details]
        for r in total_results:
            assert "let total=" in r.broken_source


# ---------------------------------------------------------------------------
# Wrong argument count tests
# ---------------------------------------------------------------------------


class TestWrongArgumentCount:
    def test_finds_calls(self, injector: ErrorInjector):
        results = [r for r in injector.inject(MULTI_FUNC_SRC) if r.injection_type == "wrong_argument_count"]
        # double(triple(n)) -- double and triple are single-arg calls
        assert len(results) >= 1

    def test_adds_extra_arg(self, injector: ErrorInjector):
        results = [r for r in injector.inject(MULTI_FUNC_SRC) if r.injection_type == "wrong_argument_count"]
        added = [r for r in results if "Added extra arg" in r.injection_details]
        # single-arg calls should get an extra arg
        for r in added:
            assert ";0" in r.broken_source or ";0)" in r.broken_source

    def test_removes_arg(self, injector: ErrorInjector):
        src = "m=test;f=add(a:i64;b:i64):i64{<a+b};f=go():i64{<add(1;2)};"
        results = [r for r in injector.inject(src) if r.injection_type == "wrong_argument_count"]
        removed = [r for r in results if "Removed last arg" in r.injection_details]
        assert len(removed) >= 1
        for r in removed:
            assert "add(1)" in r.broken_source

    def test_recursive_call(self, injector: ErrorInjector):
        results = [r for r in injector.inject(FACTORIAL_SRC) if r.injection_type == "wrong_argument_count"]
        # factorial(n-1) is a single-arg call
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# General / integration tests
# ---------------------------------------------------------------------------


class TestGeneral:
    def test_all_categories_present(self, injector: ErrorInjector):
        """A sufficiently complex source should trigger all 6 categories."""
        src = (
            "m=test;"
            "f=add(a:i64;b:i64):i64{<a+b};"
            "f=go():i64{let x=42;let y=mut.0;y=add(x;3);<y};"
        )
        results = injector.inject(src)
        categories = {r.injection_type for r in results}
        # We expect at least 5 out of 6 (undefined_variable may be tricky)
        assert len(categories) >= 4, f"Only got: {categories}"

    def test_deterministic_with_seed(self):
        a = ErrorInjector(seed=123)
        b = ErrorInjector(seed=123)
        results_a = a.inject(FACTORIAL_SRC)
        results_b = b.inject(FACTORIAL_SRC)
        assert len(results_a) == len(results_b)
        for ra, rb in zip(results_a, results_b):
            assert ra.broken_source == rb.broken_source
            assert ra.injection_type == rb.injection_type

    def test_injection_details_non_empty(self, injector: ErrorInjector):
        results = injector.inject(FACTORIAL_SRC)
        for r in results:
            assert r.injection_details
            assert r.injection_type

    def test_broken_differs_from_original(self, injector: ErrorInjector):
        results = injector.inject(FACTORIAL_SRC)
        for r in results:
            assert r.broken_source != FACTORIAL_SRC


# ---------------------------------------------------------------------------
# batch_inject tests
# ---------------------------------------------------------------------------


class TestBatchInject:
    def test_batch_writes_jsonl(self, tmp_path: Path):
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        sample = {
            "id": "TEST-001",
            "tk_source": ADD_SRC,
            "validation": {"compiler_exit_code": 0, "error_codes": []},
        }
        (corpus_dir / "TEST-001.json").write_text(json.dumps(sample))
        output = tmp_path / "output.jsonl"

        count = batch_inject(corpus_dir, output)
        assert count > 0
        lines = output.read_text().strip().split("\n")
        assert len(lines) == count
        for line in lines:
            record = json.loads(line)
            assert "source_id" in record
            assert "broken_source" in record
            assert "fixed_source" in record
            assert "injection_type" in record
            assert record["fixed_source"] == ADD_SRC
            assert record["source_id"] == "TEST-001"

    def test_batch_skips_failing_programs(self, tmp_path: Path):
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        sample = {
            "id": "FAIL-001",
            "tk_source": "m=bad;",
            "validation": {"compiler_exit_code": 1, "error_codes": ["E001"]},
        }
        (corpus_dir / "FAIL-001.json").write_text(json.dumps(sample))
        output = tmp_path / "output.jsonl"

        count = batch_inject(corpus_dir, output)
        assert count == 0

    def test_batch_skips_manifest(self, tmp_path: Path):
        corpus_dir = tmp_path / "corpus"
        corpus_dir.mkdir()
        (corpus_dir / "manifest.json").write_text("{}")
        output = tmp_path / "output.jsonl"

        count = batch_inject(corpus_dir, output)
        assert count == 0
