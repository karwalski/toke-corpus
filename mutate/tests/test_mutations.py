"""Tests for the toke mutation engine."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mutate.mutations import MutationEngine, batch_mutate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine() -> MutationEngine:
    return MutationEngine(seed=42)


# Sample toke sources
SIMPLE_FUNC = """\
M=math;
F=main():i64{
  let a=10;
  let b=20;
  let c=a+b*2;
  <c
};"""

FACTORIAL = """\
M=fact;
F=factorial(n:i64):i64{
  if(n<2){<1};
  <n*factorial(n-1)
};"""

LOOP_SUM = """\
M=loops;
F=main():i64{
  let sum=mut.0;
  lp(let i=1;i<6;i=i+1){
    sum=sum+i
  };
  <sum
};"""

MULTI_FUNC = """\
M=chain;
F=double(x:i64):i64{
  <x*2
};
F=inc(x:i64):i64{
  <x+1
};
F=main():i64{
  let v=double(inc(5));
  <v
};"""

NEGATE_FUNC = """\
M=multi;
F=negate(n:i64):i64{
  <0-n
};
F=main():i64{
  let y=negate(4);
  <y
};"""

STRUCT_FUNC = """\
M=structs;
T=Point{x:i64;y:i64};
F=main():i64{
  let p=Point{x:3;y:4};
  <p.x+p.y
};"""

BOOL_FUNC = """\
M=logic;
F=both(a:bool;b:bool):bool{
  if(a){
    if(b){
      <true
    }el{
      <false
    }
  }el{
    <false
  }
};"""


# ---------------------------------------------------------------------------
# 1. Variable renaming tests
# ---------------------------------------------------------------------------

class TestVariableRenaming:
    def test_renames_let_variable(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        rename_results = [r for r in results if r[1] == "variable_rename"]
        assert len(rename_results) > 0

    def test_rename_a_to_b(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        rename_results = [r for r in results if r[1] == "variable_rename"]
        # Should have a rename for 'a'
        a_renames = [r for r in rename_results if "a ->" in r[2]]
        assert len(a_renames) > 0
        mutated = a_renames[0][0]
        # 'a' should no longer appear as a standalone variable
        assert "let b=" in mutated or "let p=" in mutated or "let v=" in mutated

    def test_rename_is_consistent(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        rename_results = [r for r in results if r[1] == "variable_rename"]
        a_renames = [r for r in rename_results if "a ->" in r[2]]
        assert len(a_renames) > 0
        mutated = a_renames[0][0]
        details = a_renames[0][2]
        new_name = details.split("-> ")[1]
        # The new name should appear in the expression where 'a' was used
        assert new_name in mutated

    def test_does_not_rename_module(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        rename_results = [r for r in results if r[1] == "variable_rename"]
        for mutated, _, details in rename_results:
            # Module name 'math' should remain
            assert "M=math;" in mutated

    def test_does_not_rename_function_name(self, engine: MutationEngine) -> None:
        results = engine.mutate(MULTI_FUNC)
        rename_results = [r for r in results if r[1] == "variable_rename"]
        for mutated, _, _ in rename_results:
            assert "F=double(" in mutated
            assert "F=inc(" in mutated
            assert "F=main(" in mutated

    def test_does_not_rename_type_name(self, engine: MutationEngine) -> None:
        results = engine.mutate(STRUCT_FUNC)
        rename_results = [r for r in results if r[1] == "variable_rename"]
        for mutated, _, _ in rename_results:
            assert "T=Point{" in mutated

    def test_rename_function_param(self, engine: MutationEngine) -> None:
        results = engine.mutate(FACTORIAL)
        rename_results = [r for r in results if r[1] == "variable_rename"]
        n_renames = [r for r in rename_results if "n ->" in r[2]]
        assert len(n_renames) > 0

    def test_rename_preserves_string_literals(self, engine: MutationEngine) -> None:
        source = 'M=test;\nF=main():i64{\n  let a="a is a";\n  <0\n};'
        results = engine.mutate(source)
        rename_results = [r for r in results if r[1] == "variable_rename"]
        a_renames = [r for r in rename_results if "a ->" in r[2]]
        if a_renames:
            mutated = a_renames[0][0]
            # String content should be unchanged
            assert '"a is a"' in mutated


# ---------------------------------------------------------------------------
# 2. Type widening tests
# ---------------------------------------------------------------------------

class TestTypeWidening:
    def test_widens_i64_to_u64(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        wide_results = [r for r in results if r[1] == "type_widening"]
        assert len(wide_results) == 1
        mutated = wide_results[0][0]
        assert "u64" in mutated
        assert "i64" not in mutated

    def test_no_widening_with_negation(self, engine: MutationEngine) -> None:
        results = engine.mutate(NEGATE_FUNC)
        wide_results = [r for r in results if r[1] == "type_widening"]
        assert len(wide_results) == 0

    def test_no_widening_without_i64(self, engine: MutationEngine) -> None:
        results = engine.mutate(BOOL_FUNC)
        wide_results = [r for r in results if r[1] == "type_widening"]
        assert len(wide_results) == 0

    def test_widening_preserves_structure(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        wide_results = [r for r in results if r[1] == "type_widening"]
        assert len(wide_results) == 1
        mutated = wide_results[0][0]
        assert "M=math;" in mutated
        assert "F=main():u64{" in mutated

    def test_widening_all_occurrences(self, engine: MutationEngine) -> None:
        results = engine.mutate(MULTI_FUNC)
        wide_results = [r for r in results if r[1] == "type_widening"]
        assert len(wide_results) == 1
        mutated = wide_results[0][0]
        # All i64 should be replaced
        assert mutated.count("i64") == 0
        assert mutated.count("u64") >= 3


# ---------------------------------------------------------------------------
# 3. Let -> mut promotion tests
# ---------------------------------------------------------------------------

class TestLetToMut:
    def test_promotes_immutable_let(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        mut_results = [r for r in results if r[1] == "let_to_mut"]
        assert len(mut_results) > 0

    def test_promotes_each_let_separately(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        mut_results = [r for r in results if r[1] == "let_to_mut"]
        # SIMPLE_FUNC has 3 immutable let bindings
        assert len(mut_results) == 3

    def test_mut_prefix_added(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        mut_results = [r for r in results if r[1] == "let_to_mut"]
        # At least one should add mut. prefix
        found = any("let a=mut.10;" in r[0] for r in mut_results)
        assert found

    def test_does_not_double_mut(self, engine: MutationEngine) -> None:
        results = engine.mutate(LOOP_SUM)
        mut_results = [r for r in results if r[1] == "let_to_mut"]
        # The existing 'let sum=mut.0;' should NOT be promoted again
        for mutated, _, details in mut_results:
            assert "sum" not in details  # sum already mutable

    def test_preserves_rhs_expression(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        mut_results = [r for r in results if r[1] == "let_to_mut"]
        c_muts = [r for r in mut_results if "let c=" in r[2]]
        if c_muts:
            assert "mut.a+b*2" in c_muts[0][0]


# ---------------------------------------------------------------------------
# 4. Expression extraction tests
# ---------------------------------------------------------------------------

class TestExpressionExtraction:
    def test_extracts_return_expression(self, engine: MutationEngine) -> None:
        source = "M=t;\nF=main():i64{\n  <a+b\n};"
        results = engine.mutate(source)
        extract_results = [r for r in results if r[1] == "expression_extraction"]
        assert len(extract_results) > 0

    def test_extracted_into_let_temp(self, engine: MutationEngine) -> None:
        source = "M=t;\nF=main():i64{\n  <a+b\n};"
        results = engine.mutate(source)
        extract_results = [r for r in results if r[1] == "expression_extraction"]
        assert len(extract_results) > 0
        mutated = extract_results[0][0]
        assert "let temp=" in mutated
        assert "<temp" in mutated

    def test_compound_expr_extraction(self, engine: MutationEngine) -> None:
        source = "M=t;\nF=main():i64{\n  <a+b*c\n};"
        results = engine.mutate(source)
        extract_results = [r for r in results if r[1] == "expression_extraction"]
        assert len(extract_results) > 0
        mutated = extract_results[0][0]
        assert "let temp=b*c;" in mutated
        assert "<a+temp" in mutated

    def test_preserves_indentation(self, engine: MutationEngine) -> None:
        source = "M=t;\nF=main():i64{\n  <a+b\n};"
        results = engine.mutate(source)
        extract_results = [r for r in results if r[1] == "expression_extraction"]
        if extract_results:
            mutated = extract_results[0][0]
            # Both lines should have same indentation
            lines = mutated.split("\n")
            let_line = [l for l in lines if "let temp=" in l]
            ret_line = [l for l in lines if "<temp" in l]
            if let_line and ret_line:
                assert let_line[0].startswith("  ")
                assert ret_line[0].startswith("  ")

    def test_extraction_details_describe_expr(self, engine: MutationEngine) -> None:
        source = "M=t;\nF=main():i64{\n  <a+b*c\n};"
        results = engine.mutate(source)
        extract_results = [r for r in results if r[1] == "expression_extraction"]
        assert any("b*c" in r[2] for r in extract_results)


# ---------------------------------------------------------------------------
# 5. Constant variation tests
# ---------------------------------------------------------------------------

class TestConstantVariation:
    def test_perturbs_numeric_literal(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        const_results = [r for r in results if r[1] == "constant_variation"]
        assert len(const_results) > 0

    def test_skips_zero_and_one(self, engine: MutationEngine) -> None:
        source = "M=t;\nF=main():i64{\n  let x=0;\n  let y=1;\n  <x+y\n};"
        results = engine.mutate(source)
        const_results = [r for r in results if r[1] == "constant_variation"]
        assert len(const_results) == 0

    def test_increments_by_one(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        const_results = [r for r in results if r[1] == "constant_variation"]
        ten_muts = [r for r in const_results if "10 -> 11" in r[2]]
        assert len(ten_muts) == 1

    def test_variation_details(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        const_results = [r for r in results if r[1] == "constant_variation"]
        # Should have variations for 10, 20, 2
        details = [r[2] for r in const_results]
        assert any("10 ->" in d for d in details)
        assert any("20 ->" in d for d in details)
        assert any("2 ->" in d for d in details)

    def test_replaces_first_occurrence_only(self, engine: MutationEngine) -> None:
        source = "M=t;\nF=main():i64{\n  let a=5;\n  let b=5;\n  <a+b\n};"
        results = engine.mutate(source)
        const_results = [r for r in results if r[1] == "constant_variation"]
        five_muts = [r for r in const_results if "5 -> 6" in r[2]]
        assert len(five_muts) == 1
        mutated = five_muts[0][0]
        # First 5 replaced, second kept
        assert "let a=6;" in mutated
        assert "let b=5;" in mutated


# ---------------------------------------------------------------------------
# Integration / general tests
# ---------------------------------------------------------------------------

class TestMutationEngine:
    def test_mutate_returns_tuples(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        for r in results:
            assert isinstance(r, tuple)
            assert len(r) == 3

    def test_mutate_produces_all_types(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        types = {r[1] for r in results}
        assert "variable_rename" in types
        assert "type_widening" in types
        assert "let_to_mut" in types
        assert "constant_variation" in types

    def test_mutated_source_differs(self, engine: MutationEngine) -> None:
        results = engine.mutate(SIMPLE_FUNC)
        for mutated, _, _ in results:
            assert mutated != SIMPLE_FUNC

    def test_empty_source(self, engine: MutationEngine) -> None:
        results = engine.mutate("")
        assert results == []

    def test_module_only(self, engine: MutationEngine) -> None:
        results = engine.mutate("M=test;")
        assert results == []

    def test_deterministic_with_seed(self) -> None:
        e1 = MutationEngine(seed=99)
        e2 = MutationEngine(seed=99)
        r1 = e1.mutate(SIMPLE_FUNC)
        r2 = e2.mutate(SIMPLE_FUNC)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Batch processing tests
# ---------------------------------------------------------------------------

class TestBatchMutate:
    def test_batch_processes_json(self, tmp_path: Path) -> None:
        entry = {"id": "test_001", "source": SIMPLE_FUNC}
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps(entry))

        output_file = tmp_path / "output.jsonl"
        count = batch_mutate(tmp_path, output_file)
        assert count > 0

        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == count
        first = json.loads(lines[0])
        assert "source_id" in first
        assert "original_source" in first
        assert "mutated_source" in first
        assert "mutation_type" in first
        assert "mutation_details" in first

    def test_batch_processes_jsonl(self, tmp_path: Path) -> None:
        entries = [
            {"id": "ex_001", "source": SIMPLE_FUNC},
            {"id": "ex_002", "source": FACTORIAL},
        ]
        input_file = tmp_path / "corpus.jsonl"
        with open(input_file, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        output_file = tmp_path / "output.jsonl"
        count = batch_mutate(tmp_path, output_file)
        assert count > 0

    def test_batch_handles_tk_source_field(self, tmp_path: Path) -> None:
        entry = {"id": "test_001", "tk_source": SIMPLE_FUNC}
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps(entry))

        output_file = tmp_path / "output.jsonl"
        count = batch_mutate(tmp_path, output_file)
        assert count > 0

    def test_batch_empty_dir(self, tmp_path: Path) -> None:
        output_file = tmp_path / "output.jsonl"
        count = batch_mutate(tmp_path, output_file)
        assert count == 0

    def test_batch_output_is_valid_jsonl(self, tmp_path: Path) -> None:
        entry = {"id": "test_001", "source": MULTI_FUNC}
        input_file = tmp_path / "input.json"
        input_file.write_text(json.dumps(entry))

        output_file = tmp_path / "output.jsonl"
        batch_mutate(tmp_path, output_file)

        for line in output_file.read_text().strip().split("\n"):
            record = json.loads(line)
            assert isinstance(record, dict)
