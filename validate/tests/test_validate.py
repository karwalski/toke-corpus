"""Tests for the validation pipeline: compiler, diff_test, quality, schema, dedup."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from validate.compiler import CompileResult, CompilerValidator
from validate.diff_test import DiffResult, DifferentialTester
from validate.quality import QualityScore, QualityScorer
from validate.schema import validate_entry
from validate.dedup import Deduplicator


# ======================================================================
# Helpers
# ======================================================================

def _make_compile_result(
    language: str = "toke",
    success: bool = True,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    duration_ms: float = 5.0,
    binary_path: str | None = None,
) -> CompileResult:
    return CompileResult(
        language=language,
        success=success,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        binary_path=binary_path,
    )


def _make_diff_result(
    passed: bool = True,
    languages_agreed: list[str] | None = None,
    majority_output: str = "42",
    toke_agrees: bool = True,
    discarded_reason: str | None = None,
) -> DiffResult:
    return DiffResult(
        passed=passed,
        languages_agreed=languages_agreed or ["c", "java", "python", "toke"],
        majority_output=majority_output,
        outputs={"toke": "42", "python": "42", "c": "42", "java": "42"},
        toke_agrees=toke_agrees,
        discarded_reason=discarded_reason,
    )


def _make_process_mock(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> AsyncMock:
    """Create a mock async process that returns the given values."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


# ======================================================================
# CompilerValidator tests
# ======================================================================

class TestCompilerValidator:
    """Tests for CompilerValidator using mocked subprocess calls."""

    @pytest.fixture
    def validator(self) -> CompilerValidator:
        return CompilerValidator(tkc_path="/usr/local/bin/tkc")

    @pytest.mark.asyncio
    async def test_validate_toke_success(self, validator: CompilerValidator) -> None:
        proc = _make_process_mock(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await validator.validate_toke("M=test;")
        assert result.success is True
        assert result.language == "toke"
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_validate_toke_failure(self, validator: CompilerValidator) -> None:
        proc = _make_process_mock(returncode=1, stderr=b"E1001: bad token")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await validator.validate_toke("bad code")
        assert result.success is False
        assert result.exit_code == 1
        assert "E1001" in result.stderr

    @pytest.mark.asyncio
    async def test_validate_python_success(self, validator: CompilerValidator) -> None:
        proc = _make_process_mock(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await validator.validate_python("print(42)")
        assert result.success is True
        assert result.language == "python"

    @pytest.mark.asyncio
    async def test_validate_c_success(self, validator: CompilerValidator) -> None:
        proc = _make_process_mock(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            # Patch os.path.isfile to pretend the binary exists.
            with patch("os.path.isfile", return_value=True):
                result = await validator.validate_c('#include <stdio.h>\nint main(){return 0;}')
        assert result.success is True
        assert result.language == "c"

    @pytest.mark.asyncio
    async def test_validate_java_success(self, validator: CompilerValidator) -> None:
        proc = _make_process_mock(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch("os.path.isfile", return_value=True):
                result = await validator.validate_java(
                    "public class Solution { public static void main(String[] args) {} }"
                )
        assert result.success is True
        assert result.language == "java"

    @pytest.mark.asyncio
    async def test_validate_all_runs_four_languages(
        self, validator: CompilerValidator,
    ) -> None:
        proc = _make_process_mock(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch("os.path.isfile", return_value=True):
                results = await validator.validate_all(
                    toke_src="M=test;",
                    python_src="print(42)",
                    c_src="int main(){return 0;}",
                    java_src="public class Solution { public static void main(String[] args) {} }",
                )
        assert set(results.keys()) == {"toke", "python", "c", "java"}
        for r in results.values():
            assert r.success is True

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self, validator: CompilerValidator) -> None:
        async def hanging_communicate(*args, **kwargs):
            await asyncio.sleep(999)

        proc = AsyncMock()
        proc.communicate = hanging_communicate
        proc.kill = MagicMock()
        proc.returncode = None

        validator.timeout_s = 0.1  # Very short timeout for testing.
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await validator.validate_toke("M=test;")
        assert result.success is False
        assert result.exit_code == -1
        assert "timeout" in result.stderr

    def test_extract_java_class_found(self) -> None:
        src = "public class MyApp { public static void main(String[] args) {} }"
        assert CompilerValidator._extract_java_class(src) == "MyApp"

    def test_extract_java_class_fallback(self) -> None:
        src = "class Foo {}"
        assert CompilerValidator._extract_java_class(src) == "Program"


# ======================================================================
# DifferentialTester tests
# ======================================================================

class TestDifferentialTester:
    """Phase A contract of validate/diff_test.py (as it has been since 32c0eae).

    toke is compile-only (never executed); the three reference languages run,
    and the task passes when at least ``min_refs`` of them exit cleanly with
    non-empty stdout. Reference outputs are NOT compared with each other and
    ``test_inputs`` is not used. The earlier tests here described a 4-language
    majority vote that this module has never implemented (131.84c).
    """

    @pytest.fixture
    def tester(self) -> DifferentialTester:
        compiler = CompilerValidator(tkc_path="/usr/local/bin/tkc")

        async def no_toke(src: str, stdin: str = "") -> CompileResult:
            raise AssertionError("Phase A must not execute toke")

        compiler.run_toke = no_toke
        return DifferentialTester(compiler)

    @staticmethod
    def _refs(tester: DifferentialTester, py: CompileResult, c: CompileResult,
              java: CompileResult) -> None:
        async def rp(src: str, stdin: str = "") -> CompileResult:
            return py

        async def rc(src: str, stdin: str = "") -> CompileResult:
            return c

        async def rj(src: str, stdin: str = "") -> CompileResult:
            return java

        tester.compiler.run_python = rp
        tester.compiler.run_c = rc
        tester.compiler.run_java = rj

    @pytest.mark.asyncio
    async def test_all_refs_produce_output(self, tester: DifferentialTester) -> None:
        ok = _make_compile_result(stdout="42\n", success=True)
        self._refs(tester, ok, ok, ok)
        result = await tester.test("tk", "py", "c", "java", [{"stdin": ""}])
        assert result.passed is True
        assert result.toke_agrees is True
        assert result.languages_agreed == ["c", "java", "python"]
        assert result.majority_output == "42"
        assert result.outputs["toke"] == "(compile-only)"

    @pytest.mark.asyncio
    async def test_two_of_three_is_enough(self, tester: DifferentialTester) -> None:
        ok = _make_compile_result(stdout="42\n", success=True)
        bad = _make_compile_result(success=False, exit_code=1)
        self._refs(tester, ok, ok, bad)
        result = await tester.test("tk", "py", "c", "java", [{"stdin": ""}])
        assert result.passed is True
        assert result.languages_agreed == ["c", "python"]
        assert result.outputs["java"] == ""

    @pytest.mark.asyncio
    async def test_one_of_three_is_insufficient(self, tester: DifferentialTester) -> None:
        ok = _make_compile_result(stdout="42\n", success=True)
        bad = _make_compile_result(success=False, exit_code=1)
        empty = _make_compile_result(stdout="  \n", success=True)
        self._refs(tester, ok, bad, empty)
        result = await tester.test("tk", "py", "c", "java", [{"stdin": ""}])
        assert result.passed is False
        assert result.toke_agrees is False
        assert result.languages_agreed == ["python"]
        assert result.discarded_reason == "insufficient reference implementations"

    @pytest.mark.asyncio
    async def test_min_refs_three(self, tester: DifferentialTester) -> None:
        ok = _make_compile_result(stdout="42\n", success=True)
        bad = _make_compile_result(success=False, exit_code=1)
        self._refs(tester, ok, ok, bad)
        result = await tester.test("tk", "py", "c", "java", [], min_refs=3)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_reference_outputs_are_not_compared(
        self, tester: DifferentialTester,
    ) -> None:
        """Pins a known Phase A limitation (reported under 131.84): three
        disagreeing reference outputs still pass, and the first one wins."""
        self._refs(tester,
                   _make_compile_result(stdout="1\n", success=True),
                   _make_compile_result(stdout="2\n", success=True),
                   _make_compile_result(stdout="3\n", success=True))
        result = await tester.test("tk", "py", "c", "java", [{"stdin": "a"}, {"stdin": "b"}])
        assert result.passed is True
        assert result.majority_output == "1"

    @pytest.mark.asyncio
    async def test_test_inputs_are_not_used(self, tester: DifferentialTester) -> None:
        ok = _make_compile_result(stdout="42\n", success=True)
        self._refs(tester, ok, ok, ok)
        empty = await tester.test("tk", "py", "c", "java", [])
        many = await tester.test("tk", "py", "c", "java", [{"stdin": "a"}, {"stdin": "b"}])
        assert empty == many


# ======================================================================
# QualityScorer tests
# ======================================================================

class TestQualityScorer:
    """Tests for quality scoring, holdout checks, and token efficiency."""

    # Sentinel holdout set used by tests that don't exercise holdout logic.
    _DEFAULT_HOLDOUT: set[str] = {"HOLDOUT-TEST-001"}

    def test_missing_holdout_raises(self) -> None:
        with pytest.raises(ValueError, match="holdout_task_ids is required"):
            QualityScorer(holdout_task_ids=set())

    _PROGRAM = "m=t;\nf=add(a:i64;b:i64):i64{<a+b};"
    _PYTHON = "def add(a, b):\n    return a + b\n" * 3

    def test_perfect_score(self) -> None:
        scorer = QualityScorer(holdout_task_ids=self._DEFAULT_HOLDOUT)
        qs = scorer.score(
            task_id="A-MTH-001",
            toke_src=self._PROGRAM,
            python_src=self._PYTHON,
            compile_result=_make_compile_result(success=True),
            diff_result=_make_diff_result(passed=True, toke_agrees=True),
        )
        assert qs.accepted is True
        assert qs.score > 0.6
        assert len(qs.reasons) == 0

    # 128.28: `tkc --check` passes a declaration-only module and the diff
    # test never runs toke, so the scorer is where a body-less "program" is
    # stopped. The first source is one of the 7 records that reached
    # toke-model/training-data (A-A-ERR-0078v11-b155556f).
    def test_declaration_only_source_rejects(self) -> None:
        scorer = QualityScorer(holdout_task_ids=self._DEFAULT_HOLDOUT)
        for src in (
            ("M=safetrim;T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str};"
             "F=safeTrim(s:Str):Str"),
            "m=t;\nt=$e{$z:bool};\nf=div(a:f64;b:f64):f64!$e;",
            "m=t;\nt=$e{$z:bool};",
        ):
            qs = scorer.score(
                task_id="A-ERR-0001",
                toke_src=src,
                python_src=self._PYTHON,
                compile_result=_make_compile_result(success=True),
                diff_result=_make_diff_result(passed=True, toke_agrees=True),
            )
            assert qs.accepted is False, src
            assert any("not a program" in r for r in qs.reasons), src

    def test_extern_next_to_a_definition_is_accepted(self) -> None:
        # Negative control: a body-less extern is legal when the module
        # defines a function; only "nothing defined" is rejected.
        scorer = QualityScorer(holdout_task_ids=self._DEFAULT_HOLDOUT)
        qs = scorer.score(
            task_id="A-MTH-006",
            toke_src="m=t;\nf=ext(a:i64):i64;\nf=add(a:i64;b:i64):i64{<a+b};",
            python_src=self._PYTHON,
            compile_result=_make_compile_result(success=True),
            diff_result=_make_diff_result(passed=True, toke_agrees=True),
        )
        assert qs.accepted is True
        assert not any("not a program" in r for r in qs.reasons)

    def test_interface_task_is_exempt(self) -> None:
        scorer = QualityScorer(holdout_task_ids=self._DEFAULT_HOLDOUT)
        qs = scorer.score(
            task_id="A-MTH-007",
            toke_src="m=t;\nf=add(a:i64;b:i64):i64;",
            python_src=self._PYTHON,
            compile_result=_make_compile_result(success=True),
            diff_result=_make_diff_result(passed=True, toke_agrees=True),
            task_type="interface",
        )
        assert not any("not a program" in r for r in qs.reasons)

    def test_compiler_failure_rejects(self) -> None:
        scorer = QualityScorer(holdout_task_ids=self._DEFAULT_HOLDOUT)
        qs = scorer.score(
            task_id="A-MTH-002",
            toke_src="bad",
            python_src="x = 1",
            compile_result=_make_compile_result(success=False, exit_code=1),
            diff_result=_make_diff_result(passed=True, toke_agrees=True),
        )
        assert qs.accepted is False
        assert any("compiler failed" in r for r in qs.reasons)

    def test_diff_failure_rejects(self) -> None:
        scorer = QualityScorer(holdout_task_ids=self._DEFAULT_HOLDOUT)
        qs = scorer.score(
            task_id="A-MTH-003",
            toke_src="x=1;",
            python_src="x = 1",
            compile_result=_make_compile_result(success=True),
            diff_result=_make_diff_result(passed=False, toke_agrees=False),
        )
        assert qs.accepted is False
        assert any("differential test failed" in r for r in qs.reasons)

    def test_holdout_rejection(self) -> None:
        scorer = QualityScorer(holdout_task_ids={"HIDDEN-001"})
        qs = scorer.score(
            task_id="HIDDEN-001",
            toke_src="x=1;",
            python_src="x = 1",
            compile_result=_make_compile_result(success=True),
            diff_result=_make_diff_result(passed=True, toke_agrees=True),
        )
        assert qs.accepted is False
        assert any("holdout" in r for r in qs.reasons)

    def test_token_inefficiency_flagged(self) -> None:
        scorer = QualityScorer(holdout_task_ids=self._DEFAULT_HOLDOUT)
        # Make toke source much longer than python.
        toke_src = "x = 1;\n" * 200
        python_src = "x = 1"
        qs = scorer.score(
            task_id="A-MTH-004",
            toke_src=toke_src,
            python_src=python_src,
            compile_result=_make_compile_result(success=True),
            diff_result=_make_diff_result(passed=True, toke_agrees=True),
        )
        # Should still be accepted (token efficiency is scored, not required),
        # but the reason list should mention inefficiency.
        assert any("token inefficient" in r for r in qs.reasons)

    def test_score_below_threshold_rejects(self) -> None:
        scorer = QualityScorer(holdout_task_ids=self._DEFAULT_HOLDOUT)
        qs = scorer.score(
            task_id="A-MTH-005",
            toke_src="x=1;",
            python_src="x = 1",
            compile_result=_make_compile_result(success=False, exit_code=1),
            diff_result=_make_diff_result(passed=False, toke_agrees=False),
        )
        assert qs.accepted is False
        assert qs.score < 0.6


# ======================================================================
# Schema validation tests
# ======================================================================

class TestSchemaValidation:
    """Tests for validate_entry against the real corpus/schema.json."""

    def _minimal_entry(self) -> dict:
        """Return a minimal valid corpus entry."""
        return {
            "id": "corpus-001",
            "version": 1,
            "phase": "A",
            "task_id": "A-MTH-001",
            "tk_source": "M=test; F=add(a:i64;b:i64):i64{<a+b};",
            "tk_tokens": 25,
            "validation": {
                "compiler_exit_code": 0,
                "error_codes": [],
            },
            "differential": {
                "languages_agreed": ["toke", "python", "c", "java"],
                "majority_output": "42",
            },
            "judge": {
                "accepted": True,
                "score": 0.95,
            },
        }

    def test_valid_entry_passes(self) -> None:
        entry = self._minimal_entry()
        valid, errors = validate_entry(entry)
        assert valid is True
        assert errors == []

    def test_missing_required_field(self) -> None:
        entry = self._minimal_entry()
        del entry["task_id"]
        valid, errors = validate_entry(entry)
        assert valid is False
        assert any("task_id" in e for e in errors)

    def test_invalid_phase(self) -> None:
        entry = self._minimal_entry()
        entry["phase"] = "Z"
        valid, errors = validate_entry(entry)
        assert valid is False

    def test_invalid_version_type(self) -> None:
        entry = self._minimal_entry()
        entry["version"] = "one"
        valid, errors = validate_entry(entry)
        assert valid is False

    def test_missing_nested_required(self) -> None:
        entry = self._minimal_entry()
        del entry["validation"]["compiler_exit_code"]
        valid, errors = validate_entry(entry)
        assert valid is False

    def test_judge_score_out_of_range(self) -> None:
        entry = self._minimal_entry()
        entry["judge"]["score"] = 1.5
        valid, errors = validate_entry(entry)
        assert valid is False

    def test_empty_dict_fails(self) -> None:
        valid, errors = validate_entry({})
        assert valid is False
        assert len(errors) > 0


# ======================================================================
# Deduplicator tests
# ======================================================================

class TestDeduplicator:
    """Tests for exact and near-duplicate detection."""

    def test_exact_duplicate_rejected(self) -> None:
        dd = Deduplicator(threshold=0.95)
        assert dd.add("e1", "M=test; F=add(a:i64):i64{<a+1};") is True
        assert dd.add("e2", "M=test; F=add(a:i64):i64{<a+1};") is False

    def test_near_duplicate_rejected(self) -> None:
        dd = Deduplicator(threshold=0.80)
        src1 = "M=test; F=add(a:i64;b:i64):i64{<a+b};"
        src2 = "M=test; F=add(a:i64;b:i64):i64{<a+b+0};"
        assert dd.add("e1", src1) is True
        # Very similar — should be caught at 0.80 threshold.
        assert dd.add("e2", src2) is False

    def test_different_programs_accepted(self) -> None:
        dd = Deduplicator(threshold=0.95)
        src1 = "M=math; F=square(n:i64):i64{<n*n};"
        src2 = "M=strings; F=greet(name:Str):Str{<\"Hello \"+name};"
        assert dd.add("e1", src1) is True
        assert dd.add("e2", src2) is True

    def test_check_without_add(self) -> None:
        dd = Deduplicator(threshold=0.95)
        is_unique, similar = dd.check("M=test;")
        assert is_unique is True
        assert similar is None

    def test_check_finds_similar(self) -> None:
        dd = Deduplicator(threshold=0.95)
        dd.add("e1", "M=test; F=foo():i64{<42};")
        is_unique, similar = dd.check("M=test; F=foo():i64{<42};")
        assert is_unique is False
        assert similar == "e1"

    def test_whitespace_normalisation(self) -> None:
        """Whitespace differences should be treated as exact duplicates."""
        dd = Deduplicator(threshold=0.95)
        assert dd.add("e1", "M=test;  F=foo():i64{<42};") is True
        assert dd.add("e2", "M=test; F=foo():i64{<42};") is False

    def test_threshold_boundary(self) -> None:
        """Programs right at the threshold boundary."""
        dd = Deduplicator(threshold=1.0)
        src1 = "M=test; F=add(a:i64;b:i64):i64{<a+b};"
        src2 = "M=test; F=add(a:i64;b:i64):i64{<a+b+0};"
        assert dd.add("e1", src1) is True
        # With threshold=1.0, near-duplicates should pass (only exact blocked).
        assert dd.add("e2", src2) is True

    def test_invalid_threshold_raises(self) -> None:
        with pytest.raises(ValueError):
            Deduplicator(threshold=0.0)
        with pytest.raises(ValueError):
            Deduplicator(threshold=1.5)
