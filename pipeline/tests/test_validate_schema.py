"""
Tests for pipeline.validate_schema — Story 2.11.1.

Tests schema validation, JSONL I/O, and CorpusEntry helpers.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from pipeline.validate_schema import (
    CorpusEntry,
    DifferentialRecord,
    JudgeRecord,
    ValidationRecord,
    load_corpus,
    save_entry,
    validate_entry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_entry() -> dict:
    """Return a minimal valid corpus entry dict."""
    return {
        "id": "corpus-A-abc123",
        "version": 1,
        "phase": "A",
        "task_id": "A-SRT-0001",
        "tk_source": "fn main() {}",
        "tk_tokens": 4,
        "validation": {
            "compiler_exit_code": 0,
            "error_codes": [],
        },
        "differential": {
            "languages_agreed": ["python", "go"],
            "majority_output": "42",
        },
        "judge": {
            "accepted": True,
            "score": 0.95,
        },
    }


def _schema() -> dict:
    """Load the actual schema.json."""
    schema_path = Path(__file__).parent.parent.parent / "corpus" / "schema.json"
    with schema_path.open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Valid entry tests
# ---------------------------------------------------------------------------

class TestValidEntry:
    def test_valid_entry_passes(self) -> None:
        ok, errors = validate_entry(_valid_entry(), schema=_schema())
        assert ok is True
        assert errors == []

    def test_valid_entry_all_phases(self) -> None:
        for phase in ("A", "B", "C"):
            entry = _valid_entry()
            entry["phase"] = phase
            ok, errors = validate_entry(entry, schema=_schema())
            assert ok is True, f"Phase {phase} failed: {errors}"

    def test_optional_fields_accepted(self) -> None:
        entry = _valid_entry()
        entry["attempts"] = 3
        entry["model"] = "claude-sonnet-4-20250514"
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is True


# ---------------------------------------------------------------------------
# Required field validation
# ---------------------------------------------------------------------------

class TestRequiredFields:
    @pytest.mark.parametrize("missing_field", [
        "id", "version", "phase", "task_id", "tk_source", "tk_tokens",
        "validation", "differential", "judge",
    ])
    def test_missing_required_field(self, missing_field: str) -> None:
        entry = _valid_entry()
        del entry[missing_field]
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any(missing_field in e for e in errors)


# ---------------------------------------------------------------------------
# Type validation
# ---------------------------------------------------------------------------

class TestTypeValidation:
    def test_id_wrong_type(self) -> None:
        entry = _valid_entry()
        entry["id"] = 12345
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("id" in e for e in errors)

    def test_version_wrong_type(self) -> None:
        entry = _valid_entry()
        entry["version"] = "1"
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("version" in e for e in errors)

    def test_tk_tokens_wrong_type(self) -> None:
        entry = _valid_entry()
        entry["tk_tokens"] = 4.5
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("tk_tokens" in e for e in errors)

    def test_task_id_wrong_type(self) -> None:
        entry = _valid_entry()
        entry["task_id"] = 42
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("task_id" in e for e in errors)

    def test_tk_source_wrong_type(self) -> None:
        entry = _valid_entry()
        entry["tk_source"] = 123
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("tk_source" in e for e in errors)


# ---------------------------------------------------------------------------
# Phase enum validation
# ---------------------------------------------------------------------------

class TestPhaseEnum:
    def test_invalid_phase(self) -> None:
        entry = _valid_entry()
        entry["phase"] = "D"
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("phase" in e for e in errors)

    def test_phase_case_sensitive(self) -> None:
        entry = _valid_entry()
        entry["phase"] = "a"
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False


# ---------------------------------------------------------------------------
# Nested object validation
# ---------------------------------------------------------------------------

class TestNestedValidation:
    def test_validation_not_object(self) -> None:
        entry = _valid_entry()
        entry["validation"] = "bad"
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_validation_missing_exit_code(self) -> None:
        entry = _valid_entry()
        entry["validation"] = {"error_codes": []}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("compiler_exit_code" in e for e in errors)

    def test_validation_missing_error_codes(self) -> None:
        entry = _valid_entry()
        entry["validation"] = {"compiler_exit_code": 0}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("error_codes" in e for e in errors)

    def test_validation_exit_code_wrong_type(self) -> None:
        entry = _valid_entry()
        entry["validation"] = {"compiler_exit_code": "zero", "error_codes": []}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_differential_not_object(self) -> None:
        entry = _valid_entry()
        entry["differential"] = []
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_differential_missing_languages(self) -> None:
        entry = _valid_entry()
        entry["differential"] = {"majority_output": "42"}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_differential_missing_majority(self) -> None:
        entry = _valid_entry()
        entry["differential"] = {"languages_agreed": ["python"]}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_judge_not_object(self) -> None:
        entry = _valid_entry()
        entry["judge"] = True
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_judge_missing_accepted(self) -> None:
        entry = _valid_entry()
        entry["judge"] = {"score": 0.5}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_judge_missing_score(self) -> None:
        entry = _valid_entry()
        entry["judge"] = {"accepted": True}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_judge_score_out_of_range(self) -> None:
        entry = _valid_entry()
        entry["judge"] = {"accepted": True, "score": 1.5}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False
        assert any("score" in e for e in errors)

    def test_judge_score_negative(self) -> None:
        entry = _valid_entry()
        entry["judge"] = {"accepted": True, "score": -0.1}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False

    def test_judge_accepted_wrong_type(self) -> None:
        entry = _valid_entry()
        entry["judge"] = {"accepted": 1, "score": 0.5}
        ok, errors = validate_entry(entry, schema=_schema())
        assert ok is False


# ---------------------------------------------------------------------------
# JSONL I/O
# ---------------------------------------------------------------------------

class TestJsonlIO:
    def test_save_and_load_roundtrip(self) -> None:
        entry = _valid_entry()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            save_entry(entry, path)
            loaded = load_corpus(path)
            assert len(loaded) == 1
            assert loaded[0] == entry

    def test_load_nonexistent_file(self) -> None:
        loaded = load_corpus(Path("/tmp/nonexistent_test_corpus.jsonl"))
        assert loaded == []

    def test_save_multiple_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "multi.jsonl"
            for i in range(3):
                entry = _valid_entry()
                entry["id"] = f"corpus-A-{i:04d}"
                save_entry(entry, path)
            loaded = load_corpus(path)
            assert len(loaded) == 3
            assert [e["id"] for e in loaded] == [
                "corpus-A-0000", "corpus-A-0001", "corpus-A-0002"
            ]

    def test_load_skips_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.jsonl"
            with path.open("w") as f:
                f.write(json.dumps(_valid_entry()) + "\n")
                f.write("this is not json\n")
                f.write(json.dumps(_valid_entry()) + "\n")
            loaded = load_corpus(path)
            assert len(loaded) == 2


# ---------------------------------------------------------------------------
# CorpusEntry dataclass
# ---------------------------------------------------------------------------

class TestCorpusEntry:
    def test_to_dict_excludes_none(self) -> None:
        entry = CorpusEntry(
            id="test-1",
            version=1,
            phase="A",
            task_id="A-SRT-0001",
            tk_source="fn main() {}",
            tk_tokens=4,
            validation=ValidationRecord(compiler_exit_code=0, error_codes=[]),
            differential=DifferentialRecord(languages_agreed=["python"], majority_output="42"),
            judge=JudgeRecord(accepted=True, score=0.9),
        )
        d = entry.to_dict()
        assert "attempts" not in d
        assert "model" not in d

    def test_to_dict_includes_optional_when_set(self) -> None:
        entry = CorpusEntry(
            id="test-2",
            version=1,
            phase="B",
            task_id="B-LNK-0001",
            tk_source="fn main() {}",
            tk_tokens=4,
            validation=ValidationRecord(compiler_exit_code=0, error_codes=[]),
            differential=DifferentialRecord(languages_agreed=[], majority_output=""),
            judge=JudgeRecord(accepted=False, score=0.1),
            attempts=5,
            model="test-model",
        )
        d = entry.to_dict()
        assert d["attempts"] == 5
        assert d["model"] == "test-model"
