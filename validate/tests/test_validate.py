"""Tests for the validation pipeline: compiler, diff_test, quality, schema, dedup."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from validate.compiler import CompileResult, CompilerValidator
from validate import diff_test
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
    """Phase A contract of validate/diff_test.py since 131.87 (owner decision
    docs/owner-decisions-2026-10-01.md §4.16): on every declared test input the
    reference outputs must reach majority agreement (>= min_refs, no tie), and
    the toke program is built, run on the same stdin, and must print the
    majority output. Before 131.87 the module never compared outputs, ignored
    test_inputs and always set toke_agrees=True; the negative controls below
    are the cases that used to pass.
    """

    @pytest.fixture
    def tester(self) -> DifferentialTester:
        return DifferentialTester(CompilerValidator(tkc_path="/usr/local/bin/tkc"))

    @staticmethod
    def _wire(tester: DifferentialTester, by_stdin: dict[str, dict[str, CompileResult]],
              calls: list[tuple[str, str]] | None = None) -> None:
        """Fake every runner: by_stdin[stdin][lang] -> result."""
        def make(lang: str):
            async def run(src: str, stdin: str = "") -> CompileResult:
                if calls is not None:
                    calls.append((lang, stdin))
                return by_stdin[stdin][lang]
            return run
        for lang in ("python", "c", "java", "toke"):
            setattr(tester.compiler, f"run_{lang}", make(lang))

    @staticmethod
    def _ok(out: str) -> CompileResult:
        return _make_compile_result(stdout=out + "\n", success=True)

    _BAD = _make_compile_result(success=False, exit_code=1, stdout="partial")

    def _same(self, out: str, toke: str | None = None) -> dict[str, CompileResult]:
        return {"python": self._ok(out), "c": self._ok(out), "java": self._ok(out),
                "toke": self._ok(out if toke is None else toke)}

    @pytest.mark.asyncio
    async def test_all_agree_and_toke_matches(self, tester: DifferentialTester) -> None:
        self._wire(tester, {"": self._same("42")})
        result = await tester.test("tk", "py", "c", "java", [{"stdin": ""}])
        assert result.passed is True and result.toke_agrees is True
        assert result.languages_agreed == ["c", "java", "python"]
        assert result.majority_output == "42"
        assert result.outputs["toke"] == "42"
        assert result.discarded_reason is None

    @pytest.mark.asyncio
    async def test_two_of_three_agree_dissenter_excluded(self, tester: DifferentialTester) -> None:
        runs = self._same("42")
        runs["java"] = self._ok("41")
        self._wire(tester, {"": runs})
        result = await tester.test("tk", "py", "c", "java", [])
        assert result.passed is True
        assert result.languages_agreed == ["c", "python"]

    @pytest.mark.asyncio
    async def test_two_of_three_with_one_failing(self, tester: DifferentialTester) -> None:
        runs = self._same("42")
        runs["java"] = self._BAD
        self._wire(tester, {"": runs})
        result = await tester.test("tk", "py", "c", "java", [{"stdin": ""}])
        assert result.passed is True
        assert result.languages_agreed == ["c", "python"]
        assert result.outputs["java"] == ""

    @pytest.mark.asyncio
    async def test_three_different_answers_fail(self, tester: DifferentialTester) -> None:
        """Negative control: this passed before 131.87 (first output won)."""
        runs = {"python": self._ok("1"), "c": self._ok("2"), "java": self._ok("3"),
                "toke": self._ok("1")}
        self._wire(tester, {"": runs})
        result = await tester.test("tk", "py", "c", "java", [])
        assert result.passed is False and result.toke_agrees is False
        assert result.discarded_reason == diff_test.REASON_REFS_DISAGREE

    @pytest.mark.asyncio
    async def test_two_producing_refs_that_disagree_fail(self, tester: DifferentialTester) -> None:
        runs = {"python": self._ok("1"), "c": self._ok("2"), "java": self._BAD,
                "toke": self._ok("1")}
        self._wire(tester, {"": runs})
        result = await tester.test("tk", "py", "c", "java", [])
        assert result.passed is False
        assert result.discarded_reason == diff_test.REASON_REFS_DISAGREE

    @pytest.mark.asyncio
    async def test_min_refs_one_tie_fails_single_passes(self, tester: DifferentialTester) -> None:
        tie = {"python": self._ok("1"), "c": self._ok("2"), "java": self._BAD,
               "toke": self._ok("1")}
        self._wire(tester, {"": tie})
        assert (await tester.test("tk", "py", "c", "java", [], min_refs=1)).passed is False
        single = {"python": self._ok("1"), "c": self._BAD, "java": self._BAD,
                  "toke": self._ok("1")}
        self._wire(tester, {"": single})
        assert (await tester.test("tk", "py", "c", "java", [], min_refs=1)).passed is True

    @pytest.mark.asyncio
    async def test_one_of_three_is_insufficient(self, tester: DifferentialTester) -> None:
        runs = {"python": self._ok("42"), "c": self._BAD,
                "java": _make_compile_result(stdout="  \n", success=True),
                "toke": self._ok("42")}
        calls: list[tuple[str, str]] = []
        self._wire(tester, {"": runs}, calls)
        result = await tester.test("tk", "py", "c", "java", [{"stdin": ""}])
        assert result.passed is False and result.toke_agrees is False
        assert result.languages_agreed == ["python"]
        assert result.discarded_reason == diff_test.REASON_INSUFFICIENT
        assert ("toke", "") not in calls          # toke is not built when refs fail

    @pytest.mark.asyncio
    async def test_min_refs_three(self, tester: DifferentialTester) -> None:
        runs = self._same("42")
        runs["java"] = self._BAD
        self._wire(tester, {"": runs})
        result = await tester.test("tk", "py", "c", "java", [], min_refs=3)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_toke_output_must_match(self, tester: DifferentialTester) -> None:
        """Negative control: toke_agrees used to be True unconditionally."""
        self._wire(tester, {"": self._same("42", toke="43")})
        result = await tester.test("tk", "py", "c", "java", [])
        assert result.passed is False and result.toke_agrees is False
        assert result.discarded_reason == diff_test.REASON_TOKE_DISAGREES
        assert result.majority_output == "42" and result.outputs["toke"] == "43"

    @pytest.mark.asyncio
    async def test_toke_build_or_run_failure_fails(self, tester: DifferentialTester) -> None:
        runs = self._same("42")
        runs["toke"] = _make_compile_result(success=False, exit_code=1, stdout="42\n")
        self._wire(tester, {"": runs})
        result = await tester.test("tk", "py", "c", "java", [])
        assert result.passed is False
        assert result.discarded_reason == diff_test.REASON_TOKE_FAILED

    @pytest.mark.asyncio
    async def test_every_test_input_is_run_with_its_stdin(self, tester: DifferentialTester) -> None:
        calls: list[tuple[str, str]] = []
        self._wire(tester, {"a": self._same("A"), "b": self._same("B")}, calls)
        result = await tester.test("tk", "py", "c", "java", [{"stdin": "a"}, {"stdin": "b"}])
        assert result.passed is True
        assert [r.majority_output for r in result.per_input] == ["A", "B"]
        assert result.majority_output == "A"
        for lang in ("python", "c", "java", "toke"):
            assert (lang, "a") in calls and (lang, "b") in calls

    @pytest.mark.asyncio
    async def test_a_later_input_can_fail_the_task(self, tester: DifferentialTester) -> None:
        """Negative control: test_inputs used to be ignored entirely."""
        self._wire(tester, {"a": self._same("A"), "b": self._same("B", toke="A")})
        result = await tester.test("tk", "py", "c", "java", [{"stdin": "a"}, {"stdin": "b"}])
        assert result.passed is False
        assert result.discarded_reason == diff_test.REASON_TOKE_DISAGREES
        assert len(result.per_input) == 2

    @pytest.mark.asyncio
    async def test_no_inputs_means_one_empty_stdin_run(self, tester: DifferentialTester) -> None:
        calls: list[tuple[str, str]] = []
        self._wire(tester, {"": self._same("42")}, calls)
        result = await tester.test("tk", "py", "c", "java", [])
        assert result.passed is True
        assert sorted(calls) == [("c", ""), ("java", ""), ("python", ""), ("toke", "")]

    def test_reference_majority_is_pure(self) -> None:
        rm = diff_test.reference_majority
        assert rm({"python": "1", "c": "1", "java": "2"}, 2) == ("1", ["c", "python"], None)
        assert rm({"python": "1", "c": "2", "java": "3"}, 2)[2] == diff_test.REASON_REFS_DISAGREE
        assert rm({"python": "1", "c": "", "java": ""}, 2)[2] == diff_test.REASON_INSUFFICIENT
        assert rm({"python": "1", "c": "", "java": ""}, 1) == ("1", ["python"], None)


class TestRunTokeCommand:
    """131.87: run_toke builds with `tkc <src> --out <bin>` and runs that binary."""

    @pytest.mark.asyncio
    async def test_build_argv_names_the_binary(self) -> None:
        cv = CompilerValidator(tkc_path="tkc", toke_build_flags=["--allow-all"])
        seen: list[list[str]] = []

        async def fake_run(args, cwd=None, env=None):
            seen.append(list(args))
            return (1, "", "boom", 1.0)

        cv._run = fake_run
        r = await cv.run_toke("m=x;")
        assert r.success is False
        argv = seen[0]
        assert argv[0] == "tkc" and argv[1].endswith("program.tk")
        assert argv[2] == "--out" and argv[3].endswith("program")
        assert argv[4:] == ["--allow-all"]
        assert "-o" not in argv


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


class TestCanonicalOutput:
    """131.87: only cross-language spelling differences are folded."""

    @pytest.mark.parametrize("a,b", [
        ("True\nFalse", "true\nfalse"),
        ("2.500000", "2.5"),
        ("3.0", "3"),
        ("x \n", "x"),
        ("[1.0, 2.0]", "[1, 2]"),
    ])
    def test_equal(self, a: str, b: str) -> None:
        assert diff_test.canonical_output(a) == diff_test.canonical_output(b)

    @pytest.mark.parametrize("a,b", [
        ("1", "true"),              # 0/1 are not booleans
        ("0.333333", "0.3333333333333333"),
        ("a b", "a  b"),
        ("x1.0", "x1"),             # part of an identifier, not a number
        ("1\n2", "2\n1"),
    ])
    def test_not_equal(self, a: str, b: str) -> None:
        assert diff_test.canonical_output(a) != diff_test.canonical_output(b)

    @pytest.mark.asyncio
    async def test_boolean_spelling_is_not_disagreement(self) -> None:
        t = DifferentialTester(CompilerValidator(tkc_path="tkc"))
        outs = {"python": "True", "c": "1", "java": "true", "toke": "true"}

        def mk(lang: str):
            async def run(src: str, stdin: str = "") -> CompileResult:
                return _make_compile_result(stdout=outs[lang] + "\n", success=True)
            return run
        for lang in outs:
            setattr(t.compiler, f"run_{lang}", mk(lang))
        r = await t.test("tk", "py", "c", "java", [])
        assert r.passed is True and r.languages_agreed == ["java", "python"]
        assert r.majority_output == "True"
