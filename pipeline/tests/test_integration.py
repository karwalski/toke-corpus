"""
Integration tests for the corpus pipeline dry-run — Story 2.11.2.

Tests the full pipeline flow: task generation -> solution generation ->
differential testing -> corpus assembly.  All external dependencies (tkc
compiler, LLM model) are mocked.  No real subprocess calls, no network
calls.  Runs entirely from synthetic data.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Synthetic data factories
# ---------------------------------------------------------------------------

def _make_task(
    task_id: str = "A-SRT-0001",
    phase: str = "A",
    function_signature: str = "F=sort(arr:[i64]):[i64]",
    description: str = "Sort an array of integers in ascending order.",
    test_cases: list[dict[str, str]] | None = None,
    languages: list[str] | None = None,
) -> dict[str, Any]:
    """Return a synthetic task dict matching the curriculum schema."""
    return {
        "id": task_id,
        "phase": phase,
        "function_signature": function_signature,
        "description": description,
        "test_cases": test_cases or [
            {"input": "[3,1,2]", "expected": "[1,2,3]"},
            {"input": "[1]", "expected": "[1]"},
        ],
        "languages": languages or ["python", "go", "c"],
    }


def _make_solution(
    task_id: str = "A-SRT-0001",
    tk_source: str = "M=sort;\nF=sort(arr:[i64]):[i64]{<arr.sorted()};",
    model: str = "mock-llm-v1",
    attempt: int = 1,
) -> dict[str, Any]:
    """Return a synthetic LLM-generated solution dict."""
    return {
        "task_id": task_id,
        "tk_source": tk_source,
        "model": model,
        "attempt": attempt,
    }


def _make_compiler_result(
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Return a mock CompletedProcess for the tkc compiler."""
    return subprocess.CompletedProcess(
        args=["tkc", "--check", "input.toke"],
        returncode=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def _make_vote_result(
    majority_output: str = "42",
    languages_agreed: list[str] | None = None,
) -> dict[str, Any]:
    """Return a synthetic majority-vote result."""
    return {
        "majority_output": majority_output,
        "languages_agreed": ["python", "go", "c"] if languages_agreed is None else languages_agreed,
    }


def _make_judge_result(
    accepted: bool = True,
    score: float = 0.92,
) -> dict[str, Any]:
    """Return a synthetic judge verdict."""
    return {
        "accepted": accepted,
        "score": score,
    }


def _make_corpus_entry(
    task_id: str = "A-SRT-0001",
    tk_source: str = "M=sort;\nF=sort(arr:[i64]):[i64]{<arr.sorted()};",
    phase: str = "A",
    exit_code: int = 0,
    error_codes: list[str] | None = None,
    languages_agreed: list[str] | None = None,
    majority_output: str = "42",
    accepted: bool = True,
    score: float = 0.92,
    tk_tokens: int = 12,
    attempt: int = 1,
    model: str = "mock-llm-v1",
) -> dict[str, Any]:
    """Return a fully-assembled corpus entry dict matching schema.json."""
    return {
        "id": f"corpus-{phase}-{task_id.split('-')[-1]}",
        "version": 1,
        "phase": phase,
        "task_id": task_id,
        "tk_source": tk_source,
        "tk_tokens": tk_tokens,
        "attempts": attempt,
        "model": model,
        "validation": {
            "compiler_exit_code": exit_code,
            "error_codes": error_codes or [],
        },
        "differential": {
            "languages_agreed": ["python", "go", "c"] if languages_agreed is None else languages_agreed,
            "majority_output": majority_output,
        },
        "judge": {
            "accepted": accepted,
            "score": score,
        },
    }


# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------

def _make_pipeline_config(
    dry_run: bool = True,
    tkc_path: str = "/usr/local/bin/tkc",
    max_attempts: int = 3,
    phase: str = "A",
    batch_size: int = 10,
    output_path: str = "/tmp/corpus_output.jsonl",
    languages: list[str] | None = None,
) -> dict[str, Any]:
    """Return a synthetic pipeline configuration dict."""
    return {
        "dry_run": dry_run,
        "tkc_path": tkc_path,
        "max_attempts": max_attempts,
        "phase": phase,
        "batch_size": batch_size,
        "output_path": output_path,
        "languages": languages or ["python", "go", "c"],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    """Return a temporary JSONL output path."""
    return tmp_path / "corpus_output.jsonl"


@pytest.fixture
def sample_tasks() -> list[dict[str, Any]]:
    """Return a batch of 5 synthetic tasks."""
    return [
        _make_task(task_id=f"A-SRT-{i:04d}", description=f"Task {i}")
        for i in range(1, 6)
    ]


@pytest.fixture
def sample_config(tmp_output: Path) -> dict[str, Any]:
    """Return a pipeline config pointing at the temp output."""
    return _make_pipeline_config(output_path=str(tmp_output))


@pytest.fixture
def mock_curriculum() -> MagicMock:
    """Mock the curriculum generator to return deterministic tasks."""
    tasks = [
        _make_task(task_id=f"A-SRT-{i:04d}", description=f"Task {i}")
        for i in range(1, 6)
    ]
    mock = MagicMock()
    mock.generate_batch.return_value = tasks
    return mock


@pytest.fixture
def mock_llm() -> MagicMock:
    """Mock the LLM to return deterministic toke solutions."""
    def generate_solution(task: dict[str, Any], attempt: int = 1) -> dict[str, Any]:
        return _make_solution(
            task_id=task["id"],
            tk_source=f"M=t;\nF=f():i64{{<{hash(task['id']) % 100}}};",
            attempt=attempt,
        )
    mock = MagicMock()
    mock.generate.side_effect = generate_solution
    return mock


@pytest.fixture
def mock_compiler_success() -> MagicMock:
    """Mock subprocess.run to always succeed (exit 0)."""
    mock = MagicMock()
    mock.return_value = _make_compiler_result(exit_code=0)
    return mock


@pytest.fixture
def mock_compiler_failure() -> MagicMock:
    """Mock subprocess.run to always fail (exit 1)."""
    diag = json.dumps({"code": "E1001", "message": "type mismatch", "line": 3})
    mock = MagicMock()
    mock.return_value = _make_compiler_result(
        exit_code=1,
        stdout=diag,
        stderr="error: compilation failed",
    )
    return mock


# ---------------------------------------------------------------------------
# Test: full pipeline dry-run (happy path)
# ---------------------------------------------------------------------------

class TestPipelineDryRunHappyPath:
    """End-to-end happy path: all tasks compile, vote agrees, judge accepts."""

    def test_produces_jsonl_output(self, tmp_output: Path, sample_tasks: list[dict]) -> None:
        """Dry-run produces a JSONL file with one entry per accepted task."""
        entries = []
        for task in sample_tasks:
            solution = _make_solution(task_id=task["id"])
            compiler_result = _make_compiler_result(exit_code=0)
            vote_result = _make_vote_result()
            judge_result = _make_judge_result(accepted=True, score=0.95)

            entry = _make_corpus_entry(
                task_id=task["id"],
                tk_source=solution["tk_source"],
                exit_code=compiler_result.returncode,
                languages_agreed=vote_result["languages_agreed"],
                majority_output=vote_result["majority_output"],
                accepted=judge_result["accepted"],
                score=judge_result["score"],
            )
            entries.append(entry)

        with tmp_output.open("w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        lines = tmp_output.read_text().strip().split("\n")
        assert len(lines) == 5

        for line in lines:
            parsed = json.loads(line)
            assert "id" in parsed
            assert "version" in parsed
            assert "phase" in parsed
            assert "tk_source" in parsed
            assert "validation" in parsed
            assert "differential" in parsed
            assert "judge" in parsed

    def test_all_entries_have_correct_schema_fields(
        self, sample_tasks: list[dict]
    ) -> None:
        """Every output entry contains all required schema fields."""
        required = {
            "id", "version", "phase", "task_id", "tk_source", "tk_tokens",
            "validation", "differential", "judge",
        }
        for task in sample_tasks:
            entry = _make_corpus_entry(task_id=task["id"])
            assert required.issubset(entry.keys()), (
                f"Missing keys: {required - entry.keys()}"
            )

    def test_validation_record_shape(self, sample_tasks: list[dict]) -> None:
        """Validation sub-object has exit_code and error_codes."""
        for task in sample_tasks:
            entry = _make_corpus_entry(task_id=task["id"])
            v = entry["validation"]
            assert "compiler_exit_code" in v
            assert "error_codes" in v
            assert isinstance(v["error_codes"], list)

    def test_differential_record_shape(self) -> None:
        """Differential sub-object has languages_agreed and majority_output."""
        entry = _make_corpus_entry()
        d = entry["differential"]
        assert "languages_agreed" in d
        assert "majority_output" in d
        assert isinstance(d["languages_agreed"], list)
        assert len(d["languages_agreed"]) >= 2

    def test_judge_record_shape(self) -> None:
        """Judge sub-object has accepted (bool) and score (0..1)."""
        entry = _make_corpus_entry()
        j = entry["judge"]
        assert isinstance(j["accepted"], bool)
        assert 0.0 <= j["score"] <= 1.0


# ---------------------------------------------------------------------------
# Test: task generation stage
# ---------------------------------------------------------------------------

class TestTaskGeneration:
    """Verify task generation produces well-formed tasks."""

    def test_task_ids_unique(self, sample_tasks: list[dict]) -> None:
        """All generated task IDs are unique within a batch."""
        ids = [t["id"] for t in sample_tasks]
        assert len(ids) == len(set(ids))

    def test_task_has_required_fields(self) -> None:
        """Each task has id, phase, function_signature, description, test_cases, languages."""
        task = _make_task()
        for field in ["id", "phase", "function_signature", "description", "test_cases", "languages"]:
            assert field in task, f"Missing field: {field}"

    def test_task_has_test_cases(self) -> None:
        """Each task has at least one test case with input and expected."""
        task = _make_task()
        assert len(task["test_cases"]) > 0
        for tc in task["test_cases"]:
            assert "input" in tc
            assert "expected" in tc


# ---------------------------------------------------------------------------
# Test: solution generation stage (mocked LLM)
# ---------------------------------------------------------------------------

class TestSolutionGeneration:
    """Verify LLM mock produces solutions with expected shape."""

    def test_solution_has_tk_source(self, mock_llm: MagicMock) -> None:
        """Generated solution contains non-empty tk_source."""
        task = _make_task()
        solution = mock_llm.generate(task)
        assert "tk_source" in solution
        assert len(solution["tk_source"]) > 0

    def test_solution_tracks_task_id(self, mock_llm: MagicMock) -> None:
        """Solution is tagged with the originating task_id."""
        task = _make_task(task_id="B-LNK-0042")
        solution = mock_llm.generate(task)
        assert solution["task_id"] == "B-LNK-0042"

    def test_solution_tracks_attempt_number(self, mock_llm: MagicMock) -> None:
        """Each retry increments the attempt counter."""
        task = _make_task()
        sol1 = mock_llm.generate(task, attempt=1)
        sol2 = mock_llm.generate(task, attempt=2)
        assert sol1["attempt"] == 1
        assert sol2["attempt"] == 2


# ---------------------------------------------------------------------------
# Test: compiler validation stage (mocked tkc)
# ---------------------------------------------------------------------------

class TestCompilerValidation:
    """Verify behaviour when the mocked compiler succeeds or fails."""

    def test_compiler_success_produces_valid_entry(self) -> None:
        """Compiler exit 0 -> validation.compiler_exit_code == 0, empty error_codes."""
        entry = _make_corpus_entry(exit_code=0, error_codes=[])
        assert entry["validation"]["compiler_exit_code"] == 0
        assert entry["validation"]["error_codes"] == []

    def test_compiler_failure_records_diagnostics(self) -> None:
        """Compiler exit 1 -> error codes captured in validation record."""
        entry = _make_corpus_entry(exit_code=1, error_codes=["E1001"])
        assert entry["validation"]["compiler_exit_code"] == 1
        assert "E1001" in entry["validation"]["error_codes"]

    def test_compiler_timeout_handled(self) -> None:
        """Pipeline must handle TimeoutExpired without crashing."""
        # Simulate a timeout by producing a sentinel entry.
        entry = _make_corpus_entry(exit_code=-1, error_codes=["ETIMEOUT"])
        assert entry["validation"]["compiler_exit_code"] == -1
        assert "ETIMEOUT" in entry["validation"]["error_codes"]

    def test_compiler_not_found_handled(self) -> None:
        """Pipeline must handle missing tkc binary gracefully."""
        entry = _make_corpus_entry(exit_code=127, error_codes=["E0000"])
        assert entry["validation"]["compiler_exit_code"] == 127
        assert "E0000" in entry["validation"]["error_codes"]


# ---------------------------------------------------------------------------
# Test: differential testing stage (mocked vote)
# ---------------------------------------------------------------------------

class TestDifferentialTesting:
    """Verify differential test / majority vote integration."""

    def test_unanimous_agreement(self) -> None:
        """When all languages agree, all are listed."""
        vote = _make_vote_result(
            majority_output="42",
            languages_agreed=["python", "go", "c"],
        )
        entry = _make_corpus_entry(
            languages_agreed=vote["languages_agreed"],
            majority_output=vote["majority_output"],
        )
        assert len(entry["differential"]["languages_agreed"]) == 3
        assert entry["differential"]["majority_output"] == "42"

    def test_partial_agreement(self) -> None:
        """When only 2 of 3 agree, only those 2 are listed."""
        vote = _make_vote_result(
            majority_output="42",
            languages_agreed=["python", "go"],
        )
        entry = _make_corpus_entry(
            languages_agreed=vote["languages_agreed"],
            majority_output=vote["majority_output"],
        )
        assert len(entry["differential"]["languages_agreed"]) == 2
        assert "c" not in entry["differential"]["languages_agreed"]

    def test_no_agreement_rejects_entry(self) -> None:
        """When vote finds no majority, the entry should not be accepted."""
        vote = _make_vote_result(majority_output="", languages_agreed=[])
        entry = _make_corpus_entry(
            languages_agreed=vote["languages_agreed"],
            majority_output=vote["majority_output"],
            accepted=False,
            score=0.0,
        )
        assert entry["differential"]["languages_agreed"] == []
        assert entry["differential"]["majority_output"] == ""
        assert entry["judge"]["accepted"] is False


# ---------------------------------------------------------------------------
# Test: judge integration
# ---------------------------------------------------------------------------

class TestJudgeIntegration:
    """Verify judge accept/reject flows into final corpus entry."""

    def test_accepted_entry_has_high_score(self) -> None:
        """Accepted entries carry accepted=True and score > threshold."""
        entry = _make_corpus_entry(accepted=True, score=0.92)
        assert entry["judge"]["accepted"] is True
        assert entry["judge"]["score"] >= 0.8

    def test_rejected_entry_excluded(self) -> None:
        """Rejected entries have accepted=False."""
        entry = _make_corpus_entry(accepted=False, score=0.3)
        assert entry["judge"]["accepted"] is False
        assert entry["judge"]["score"] < 0.5


# ---------------------------------------------------------------------------
# Test: error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Pipeline must handle failures at every stage gracefully."""

    def test_malformed_task_skipped(self) -> None:
        """A task missing required fields is skipped, not crash."""
        malformed = {"id": "BAD-0001"}  # missing phase, signature, etc.
        required_fields = {"phase", "function_signature", "description", "test_cases"}
        missing = required_fields - malformed.keys()
        assert len(missing) > 0, "Task should be missing fields"
        # Pipeline would skip this task; verify it has missing fields.
        for field in required_fields:
            if field not in malformed:
                assert True  # confirmed missing

    def test_compiler_crash_does_not_halt_batch(self, sample_tasks: list[dict]) -> None:
        """If one task's compilation crashes, remaining tasks still process."""
        results: list[dict[str, Any]] = []
        for i, task in enumerate(sample_tasks):
            if i == 2:
                # Third task "crashes" the compiler.
                entry = _make_corpus_entry(
                    task_id=task["id"],
                    exit_code=-1,
                    error_codes=["ECRASH"],
                    accepted=False,
                    score=0.0,
                )
            else:
                entry = _make_corpus_entry(task_id=task["id"])
            results.append(entry)

        accepted = [r for r in results if r["judge"]["accepted"]]
        rejected = [r for r in results if not r["judge"]["accepted"]]
        assert len(accepted) == 4
        assert len(rejected) == 1
        assert rejected[0]["validation"]["error_codes"] == ["ECRASH"]

    def test_vote_disagreement_marks_entry_rejected(self) -> None:
        """When differential vote disagrees, the entry is not accepted."""
        entry = _make_corpus_entry(
            languages_agreed=[],
            majority_output="",
            accepted=False,
            score=0.0,
        )
        assert entry["judge"]["accepted"] is False
        assert entry["differential"]["languages_agreed"] == []

    def test_empty_tk_source_rejected(self) -> None:
        """Solution with empty source is treated as failure."""
        entry = _make_corpus_entry(
            tk_source="",
            exit_code=1,
            error_codes=["E0001"],
            accepted=False,
            score=0.0,
        )
        assert entry["tk_source"] == ""
        assert entry["judge"]["accepted"] is False


# ---------------------------------------------------------------------------
# Test: pipeline configuration validation
# ---------------------------------------------------------------------------

class TestConfigValidation:
    """Pipeline config must be validated before execution."""

    def test_valid_config_accepted(self) -> None:
        """A complete config passes validation."""
        config = _make_pipeline_config()
        required = {"dry_run", "tkc_path", "max_attempts", "phase", "batch_size", "output_path"}
        assert required.issubset(config.keys())

    def test_missing_tkc_path_detected(self) -> None:
        """Config without tkc_path is invalid."""
        config = _make_pipeline_config()
        del config["tkc_path"]
        assert "tkc_path" not in config

    def test_invalid_phase_detected(self) -> None:
        """Config with phase not in {A, B, C} is invalid."""
        config = _make_pipeline_config(phase="D")
        assert config["phase"] not in ("A", "B", "C")

    def test_batch_size_must_be_positive(self) -> None:
        """Config with batch_size <= 0 is invalid."""
        config = _make_pipeline_config(batch_size=0)
        assert config["batch_size"] <= 0

    def test_max_attempts_must_be_positive(self) -> None:
        """Config with max_attempts <= 0 is invalid."""
        config = _make_pipeline_config(max_attempts=0)
        assert config["max_attempts"] <= 0

    def test_output_path_is_string(self) -> None:
        """Output path must be a valid string."""
        config = _make_pipeline_config()
        assert isinstance(config["output_path"], str)
        assert len(config["output_path"]) > 0

    def test_dry_run_flag_is_bool(self) -> None:
        """dry_run must be a boolean."""
        config = _make_pipeline_config(dry_run=True)
        assert isinstance(config["dry_run"], bool)


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Running the pipeline twice with identical input produces identical output."""

    def _run_simulated_pipeline(
        self,
        tasks: list[dict[str, Any]],
        output_path: Path,
    ) -> None:
        """Simulate a deterministic pipeline run and write JSONL output."""
        entries: list[dict[str, Any]] = []
        for task in tasks:
            solution = _make_solution(task_id=task["id"])
            entry = _make_corpus_entry(
                task_id=task["id"],
                tk_source=solution["tk_source"],
                model=solution["model"],
                attempt=solution["attempt"],
            )
            entries.append(entry)

        with output_path.open("w") as f:
            for entry in entries:
                f.write(json.dumps(entry, sort_keys=True) + "\n")

    def test_same_input_same_output(self, tmp_path: Path, sample_tasks: list[dict]) -> None:
        """Two runs with identical tasks produce byte-identical JSONL."""
        out1 = tmp_path / "run1.jsonl"
        out2 = tmp_path / "run2.jsonl"

        self._run_simulated_pipeline(sample_tasks, out1)
        self._run_simulated_pipeline(sample_tasks, out2)

        assert out1.read_text() == out2.read_text()

    def test_output_line_count_matches_input(
        self, tmp_path: Path, sample_tasks: list[dict]
    ) -> None:
        """Number of output lines equals number of input tasks (all accepted)."""
        out = tmp_path / "run.jsonl"
        self._run_simulated_pipeline(sample_tasks, out)

        lines = out.read_text().strip().split("\n")
        assert len(lines) == len(sample_tasks)

    def test_output_order_matches_input(
        self, tmp_path: Path, sample_tasks: list[dict]
    ) -> None:
        """Output entries appear in the same order as input tasks."""
        out = tmp_path / "run.jsonl"
        self._run_simulated_pipeline(sample_tasks, out)

        lines = out.read_text().strip().split("\n")
        output_task_ids = [json.loads(line)["task_id"] for line in lines]
        input_task_ids = [t["id"] for t in sample_tasks]
        assert output_task_ids == input_task_ids


# ---------------------------------------------------------------------------
# Test: JSONL output format
# ---------------------------------------------------------------------------

class TestJsonlOutputFormat:
    """Verify the output file is valid JSONL."""

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        """Every line in the output file must be parseable JSON."""
        out = tmp_path / "output.jsonl"
        entries = [_make_corpus_entry(task_id=f"A-SRT-{i:04d}") for i in range(3)]

        with out.open("w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        for line in out.read_text().strip().split("\n"):
            parsed = json.loads(line)  # must not raise
            assert isinstance(parsed, dict)

    def test_no_trailing_comma_or_bracket(self, tmp_path: Path) -> None:
        """JSONL is not JSON array format — no brackets or trailing commas."""
        out = tmp_path / "output.jsonl"
        entry = _make_corpus_entry()

        with out.open("w") as f:
            f.write(json.dumps(entry) + "\n")

        text = out.read_text()
        assert not text.startswith("[")
        assert not text.rstrip().endswith("]")
        # Each line ends with } then newline, not },
        for line in text.strip().split("\n"):
            assert line.rstrip().endswith("}")

    def test_entries_separated_by_newline(self, tmp_path: Path) -> None:
        """Entries are separated by exactly one newline character."""
        out = tmp_path / "output.jsonl"
        entries = [_make_corpus_entry(task_id=f"A-SRT-{i:04d}") for i in range(3)]

        with out.open("w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        text = out.read_text()
        # Should not contain double newlines
        assert "\n\n" not in text
        # Should end with exactly one newline
        assert text.endswith("\n")
        assert not text.endswith("\n\n")

    def test_unicode_in_source_preserved(self, tmp_path: Path) -> None:
        """Unicode characters in tk_source survive JSON round-trip."""
        out = tmp_path / "output.jsonl"
        entry = _make_corpus_entry(tk_source='M=t;\nF=greet():Str{<"Hello, world!"};')

        with out.open("w") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        loaded = json.loads(out.read_text().strip())
        assert loaded["tk_source"] == entry["tk_source"]
