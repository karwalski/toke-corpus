"""Tests for store.writer, store.metrics, and store.checkpoint.

All file operations use temporary directories to avoid polluting the
real corpus or metrics directories.

Story 8.1.9 — Corpus writer and metrics dashboard.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import jsonschema
import pytest

from generator.curriculum import TaskSpec
from store.checkpoint import Checkpoint
from store.metrics import (
    CategoryMetrics,
    CostSummary,
    MetricsCollector,
    ModelMetrics,
    PipelineMetrics,
)
from store.writer import (
    CorpusEntry,
    CorpusWriter,
    DiffInfo,
    JudgeInfo,
    ValidationInfo,
    count_tokens,
)
from validate.compiler import CompileResult
from validate.diff_test import DiffResult
from validate.quality import QualityScore

# Path to the normative schema in the repository.
SCHEMA_PATH = str(Path(__file__).resolve().parent.parent.parent / "corpus" / "schema.json")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entry(
    entry_id: str = "A-A-MTH-0001-abcd1234",
    task_id: str = "A-MTH-0001",
    tk_source: str = "M=test;\nF=add(a:i64;b:i64):i64{<a+b};",
) -> CorpusEntry:
    """Create a minimal valid CorpusEntry for testing."""
    return CorpusEntry(
        id=entry_id,
        version=1,
        phase="A",
        task_id=task_id,
        tk_source=tk_source,
        tk_tokens=count_tokens(tk_source),
        attempts=1,
        model="test-model",
        validation=ValidationInfo(compiler_exit_code=0, error_codes=[]),
        differential=DiffInfo(
            languages_agreed=["toke", "python", "c", "java"],
            majority_output="42",
        ),
        judge=JudgeInfo(accepted=True, score=0.85),
    )


def _make_task_spec(task_id: str = "A-MTH-0001") -> TaskSpec:
    """Create a minimal TaskSpec for testing."""
    return TaskSpec(
        task_id=task_id,
        category="A-MTH",
        description="Add two integers.",
        expected_signature="F=add(a:i64;b:i64):i64",
        difficulty=1,
    )


def _make_compile_result(success: bool = True) -> CompileResult:
    """Create a minimal CompileResult for testing."""
    return CompileResult(
        language="toke",
        success=success,
        exit_code=0 if success else 1,
        stdout="",
        stderr="" if success else "error: E4021 type mismatch",
        duration_ms=10.0,
    )


def _make_diff_result(passed: bool = True) -> DiffResult:
    """Create a minimal DiffResult for testing."""
    return DiffResult(
        passed=passed,
        languages_agreed=["toke", "python", "c", "java"] if passed else [],
        majority_output="42",
        outputs={"toke": "42", "python": "42", "c": "42", "java": "42"},
        toke_agrees=passed,
    )


def _make_quality(accepted: bool = True) -> QualityScore:
    """Create a minimal QualityScore for testing."""
    return QualityScore(accepted=accepted, score=0.85 if accepted else 0.3)


# ===================================================================
# CorpusWriter tests
# ===================================================================


class TestCorpusWriter:
    """Tests for CorpusWriter."""

    def test_write_and_load_roundtrip(self) -> None:
        """Write an entry, then load it back and verify fields match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            entry = _make_entry()

            path = writer.write(entry)
            assert Path(path).exists()
            assert writer.count() == 1

            loaded = writer.load(entry.id)
            assert loaded is not None
            assert loaded.id == entry.id
            assert loaded.tk_source == entry.tk_source
            assert loaded.tk_tokens == entry.tk_tokens
            assert loaded.phase == entry.phase
            assert loaded.task_id == entry.task_id
            assert loaded.model == entry.model
            assert loaded.validation.compiler_exit_code == 0
            assert loaded.differential.languages_agreed == [
                "toke", "python", "c", "java"
            ]
            assert loaded.judge.accepted is True
            assert loaded.judge.score == 0.85

    def test_directory_structure(self) -> None:
        """Entry files are written to phase_a/{category}/ subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            entry = _make_entry()
            path = writer.write(entry)

            rel = Path(path).relative_to(tmpdir)
            parts = rel.parts
            assert parts[0] == "phase_a"
            assert parts[1] == "A-MTH"
            assert parts[2].endswith(".json")

    def test_schema_validation_rejects_bad_entry(self) -> None:
        """An entry with an invalid phase value is rejected by schema validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            entry = _make_entry()
            # Phase must be one of A, B, C per the schema enum.
            entry.phase = "Z"

            with pytest.raises(jsonschema.ValidationError):
                writer.write(entry)

    def test_duplicate_rejection(self) -> None:
        """Writing the same entry ID twice raises FileExistsError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            entry = _make_entry()
            writer.write(entry)

            with pytest.raises(FileExistsError):
                writer.write(entry)

    def test_count_increments(self) -> None:
        """count() reflects the number of successful writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            assert writer.count() == 0

            writer.write(_make_entry(entry_id="A-A-MTH-0001-aaaa1111"))
            assert writer.count() == 1

            writer.write(_make_entry(entry_id="A-A-MTH-0002-bbbb2222", task_id="A-MTH-0002"))
            assert writer.count() == 2

    def test_load_nonexistent_returns_none(self) -> None:
        """Loading a non-existent entry ID returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            assert writer.load("no-such-entry") is None

    def test_written_json_is_valid_against_schema(self) -> None:
        """The JSON file on disk passes independent schema validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            entry = _make_entry()
            path = writer.write(entry)

            with open(SCHEMA_PATH, encoding="utf-8") as fh:
                schema = json.load(fh)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)

            # Should not raise.
            jsonschema.validate(data, schema)

    def test_missing_holdout_raises(self) -> None:
        """CorpusWriter refuses to initialise without holdout_task_ids."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="holdout_task_ids"):
                CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH)

    def test_holdout_violation_rejects_write(self) -> None:
        """Writing an entry whose task_id is in the holdout set raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(
                corpus_dir=tmpdir,
                schema_path=SCHEMA_PATH,
                holdout_task_ids={"A-MTH-0001"},
            )
            entry = _make_entry(task_id="A-MTH-0001")
            with pytest.raises(ValueError, match="HOLDOUT VIOLATION"):
                writer.write(entry)


# ===================================================================
# build_entry tests
# ===================================================================


class TestBuildEntry:
    """Tests for CorpusWriter.build_entry()."""

    def test_build_entry_from_components(self) -> None:
        """build_entry assembles a valid CorpusEntry from pipeline results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            task = _make_task_spec()
            toke_src = "M=test;\nF=add(a:i64;b:i64):i64{<a+b};"
            tk_tokens = count_tokens(toke_src)

            entry = writer.build_entry(
                task=task,
                toke_source=toke_src,
                model_name="claude-3-haiku",
                attempts=2,
                compile_result=_make_compile_result(success=True),
                diff_result=_make_diff_result(passed=True),
                quality=_make_quality(accepted=True),
                tk_tokens=tk_tokens,
            )

            assert entry.phase == "A"
            assert entry.task_id == "A-MTH-0001"
            assert entry.model == "claude-3-haiku"
            assert entry.attempts == 2
            assert entry.tk_tokens == tk_tokens
            assert entry.validation.compiler_exit_code == 0
            assert entry.differential.languages_agreed == [
                "toke", "python", "c", "java"
            ]
            assert entry.judge.accepted is True
            assert entry.judge.score == 0.85
            # Entry ID follows the pattern: {phase}-{task_id}-{hash}.
            assert entry.id.startswith("A-A-MTH-0001-")

    def test_build_entry_extracts_error_codes(self) -> None:
        """build_entry extracts E-codes from compiler stderr."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            task = _make_task_spec()
            compile_result = _make_compile_result(success=False)

            entry = writer.build_entry(
                task=task,
                toke_source="M=test;\nF=bad(x:u64):Str{<x};",
                model_name="test",
                attempts=1,
                compile_result=compile_result,
                diff_result=_make_diff_result(passed=False),
                quality=_make_quality(accepted=False),
                tk_tokens=10,
            )

            assert "E4021" in entry.validation.error_codes

    def test_build_entry_is_writable(self) -> None:
        """An entry from build_entry can be written to disk without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH, holdout_task_ids={"HOLDOUT-TEST-001"})
            task = _make_task_spec()
            toke_src = "M=test;\nF=add(a:i64;b:i64):i64{<a+b};"

            entry = writer.build_entry(
                task=task,
                toke_source=toke_src,
                model_name="test-model",
                attempts=1,
                compile_result=_make_compile_result(),
                diff_result=_make_diff_result(),
                quality=_make_quality(),
                tk_tokens=count_tokens(toke_src),
            )

            path = writer.write(entry)
            assert Path(path).exists()


# ===================================================================
# MetricsCollector tests
# ===================================================================


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    def test_record_and_snapshot(self) -> None:
        """Recording events updates snapshot counters correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mc = MetricsCollector(total_tasks=100, metrics_dir=tmpdir)

            mc.record_dispatch("t1", "anthropic", "A-MTH")
            mc.record_dispatch("t2", "openai", "A-STR")
            mc.record_accepted("t1", "anthropic", "A-MTH", cost=0.01)
            mc.record_failed("t2", "openai", "A-STR", cost=0.005)
            mc.record_escalated("t2", "openai", "anthropic")

            snap = mc.snapshot()

            assert snap.total_tasks == 100
            assert snap.dispatched == 2
            assert snap.accepted == 1
            assert snap.failed == 1
            assert snap.escalated == 1
            assert snap.pending == 98

            # Per-model.
            assert "anthropic" in snap.per_model
            assert snap.per_model["anthropic"].dispatched == 1
            assert snap.per_model["anthropic"].accepted == 1
            assert snap.per_model["openai"].failed == 1

            # Per-category.
            assert snap.per_category["A-MTH"].accepted == 1
            assert snap.per_category["A-STR"].failed == 1

            # Cost.
            assert snap.cost.api_total == pytest.approx(0.015, abs=1e-6)
            assert snap.cost.by_provider["anthropic"] == pytest.approx(0.01, abs=1e-6)

    def test_save_and_load_json(self) -> None:
        """save() writes valid JSON that can be read back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mc = MetricsCollector(total_tasks=50, metrics_dir=tmpdir)
            mc.record_dispatch("t1", "anthropic", "A-MTH")
            mc.record_accepted("t1", "anthropic", "A-MTH", cost=0.02)
            mc.save()

            progress_path = Path(tmpdir) / "progress.json"
            assert progress_path.exists()

            with open(progress_path, encoding="utf-8") as fh:
                data = json.load(fh)

            assert data["total_tasks"] == 50
            assert data["dispatched"] == 1
            assert data["accepted"] == 1
            assert data["cost"]["api_total"] == pytest.approx(0.02, abs=1e-6)

    def test_auto_save(self) -> None:
        """Auto-save triggers after the configured interval."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mc = MetricsCollector(
                total_tasks=1000, metrics_dir=tmpdir, auto_save_interval=5
            )
            progress_path = Path(tmpdir) / "progress.json"

            # Record fewer than the interval — no auto-save yet.
            for i in range(4):
                mc.record_dispatch(f"t{i}", "anthropic", "A-MTH")
            assert not progress_path.exists()

            # One more should trigger auto-save.
            mc.record_dispatch("t4", "anthropic", "A-MTH")
            assert progress_path.exists()

    def test_acceptance_rate_calculation(self) -> None:
        """Acceptance rate is correctly calculated per model."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mc = MetricsCollector(total_tasks=100, metrics_dir=tmpdir)
            for i in range(10):
                mc.record_dispatch(f"t{i}", "model-a", "A-MTH")
            for i in range(7):
                mc.record_accepted(f"t{i}", "model-a", "A-MTH", cost=0.01)
            for i in range(7, 10):
                mc.record_failed(f"t{i}", "model-a", "A-MTH", cost=0.005)

            snap = mc.snapshot()
            assert snap.per_model["model-a"].acceptance_rate == pytest.approx(
                0.7, abs=0.01
            )

    def test_pipeline_metrics_from_dict_roundtrip(self) -> None:
        """PipelineMetrics can round-trip through to_dict/from_dict."""
        original = PipelineMetrics(
            started_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T01:00:00+00:00",
            total_tasks=100,
            dispatched=50,
            pending=50,
            accepted=30,
            failed=20,
            escalated=5,
            per_model={"m1": ModelMetrics(dispatched=50, accepted=30, failed=20, acceptance_rate=0.6, cost=1.5)},
            per_category={"A-MTH": CategoryMetrics(dispatched=50, accepted=30, failed=20, acceptance_rate=0.6)},
            cost=CostSummary(api_total=1.5, by_provider={"anthropic": 1.5}, compute_estimate=0.0),
            estimated_remaining_hours=2.5,
        )

        data = original.to_dict()
        restored = PipelineMetrics.from_dict(data)

        assert restored.total_tasks == original.total_tasks
        assert restored.dispatched == original.dispatched
        assert restored.accepted == original.accepted
        assert restored.per_model["m1"].acceptance_rate == 0.6
        assert restored.cost.api_total == 1.5


# ===================================================================
# Checkpoint tests
# ===================================================================


class TestCheckpoint:
    """Tests for Checkpoint."""

    def test_save_and_load_roundtrip(self) -> None:
        """Checkpoint save/load preserves completed set and metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cp_path = str(Path(tmpdir) / "checkpoint.json")
            cp = Checkpoint(cp_path)

            completed = {"task-1", "task-2", "task-3"}
            metrics = PipelineMetrics(
                started_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T01:00:00+00:00",
                total_tasks=100,
                dispatched=10,
                pending=90,
                accepted=3,
                failed=7,
                escalated=0,
                cost=CostSummary(api_total=0.5),
            )

            cp.save(completed, metrics)
            assert cp.exists()

            loaded_completed, loaded_metrics = cp.load()
            assert loaded_completed == completed
            assert loaded_metrics is not None
            assert loaded_metrics.total_tasks == 100
            assert loaded_metrics.accepted == 3
            assert loaded_metrics.cost.api_total == 0.5

    def test_load_nonexistent_returns_empty(self) -> None:
        """Loading a non-existent checkpoint returns empty defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cp_path = str(Path(tmpdir) / "no_such_file.json")
            cp = Checkpoint(cp_path)

            assert not cp.exists()
            completed, metrics = cp.load()
            assert completed == set()
            assert metrics is None

    def test_resume_with_completed_set(self) -> None:
        """Simulates an orchestrator resuming from a checkpoint."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cp_path = str(Path(tmpdir) / "checkpoint.json")
            cp = Checkpoint(cp_path)

            # First run: complete some tasks.
            first_run_completed = {"A-MTH-0001", "A-MTH-0002", "A-STR-0001"}
            metrics = PipelineMetrics(
                total_tasks=50000,
                dispatched=3,
                accepted=3,
                pending=49997,
            )
            cp.save(first_run_completed, metrics)

            # Second run: load checkpoint and verify we can skip completed.
            loaded_completed, loaded_metrics = cp.load()
            all_tasks = [f"A-MTH-{i:04d}" for i in range(1, 6)]
            remaining = [t for t in all_tasks if t not in loaded_completed]

            assert len(remaining) == 3
            assert "A-MTH-0001" not in remaining
            assert "A-MTH-0002" not in remaining
            assert "A-MTH-0003" in remaining

    def test_checkpoint_json_format(self) -> None:
        """Checkpoint file contains expected top-level keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cp_path = str(Path(tmpdir) / "checkpoint.json")
            cp = Checkpoint(cp_path)

            cp.save({"t1"}, PipelineMetrics(total_tasks=10))

            with open(cp_path, encoding="utf-8") as fh:
                data = json.load(fh)

            assert "completed" in data
            assert "metrics" in data
            assert "saved_at" in data
            assert data["completed"] == ["t1"]
