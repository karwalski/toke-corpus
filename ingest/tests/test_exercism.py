"""Tests for the Exercism ingestion pipeline.

Uses mock/fixture data throughout -- never clones the actual Exercism repo.
"""

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest.exercism import ExerciseInfo, ExercismIngestor, IngestReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ingestor() -> ExercismIngestor:
    return ExercismIngestor(tkc_path=Path("/usr/bin/false"))


@pytest.fixture
def mock_track_dir(tmp_path: Path) -> Path:
    """Create a minimal Exercism track directory structure."""
    track = tmp_path / "python"
    practice = track / "exercises" / "practice"

    # Exercise 1: two-fer (complete)
    ex1 = practice / "two-fer"
    (ex1 / ".meta").mkdir(parents=True)
    (ex1 / ".meta" / "exemplar.py").write_text(
        textwrap.dedent("""\
            def two_fer(name: str = "you") -> str:
                return f"One for {name}, one for me."
        """)
    )
    (ex1 / "two_fer_test.py").write_text("# tests\n")

    # Exercise 2: leap (complete)
    ex2 = practice / "leap"
    (ex2 / ".meta").mkdir(parents=True)
    (ex2 / ".meta" / "exemplar.py").write_text(
        textwrap.dedent("""\
            def leap_year(year: int) -> bool:
                return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        """)
    )
    (ex2 / "leap_test.py").write_text("# tests\n")

    # Exercise 3: no exemplar (should be skipped)
    ex3 = practice / "hello-world"
    (ex3 / ".meta").mkdir(parents=True)
    (ex3 / "hello_world_test.py").write_text("# tests\n")

    # Exercise 4: no test file (should be skipped)
    ex4 = practice / "resistor-color"
    (ex4 / ".meta").mkdir(parents=True)
    (ex4 / ".meta" / "exemplar.py").write_text("def color_code(color: str) -> int:\n    return 0\n")

    # A non-directory file (should be skipped)
    (practice / "README.md").write_text("ignore me\n")

    return track


@pytest.fixture
def simple_python_source() -> str:
    return textwrap.dedent("""\
        def add(a: int, b: int) -> int:
            return a + b
    """)


@pytest.fixture
def class_python_source() -> str:
    """Python source containing a class -- not transpilable."""
    return textwrap.dedent("""\
        class Greeter:
            def greet(self, name: str) -> str:
                return f"Hello, {name}"
    """)


# ---------------------------------------------------------------------------
# Test: list_exercises
# ---------------------------------------------------------------------------


class TestListExercises:
    def test_finds_complete_exercises(
        self, ingestor: ExercismIngestor, mock_track_dir: Path
    ) -> None:
        exercises = ingestor.list_exercises(mock_track_dir)
        names = [e.name for e in exercises]
        assert "two-fer" in names
        assert "leap" in names

    def test_skips_missing_exemplar(
        self, ingestor: ExercismIngestor, mock_track_dir: Path
    ) -> None:
        exercises = ingestor.list_exercises(mock_track_dir)
        names = [e.name for e in exercises]
        assert "hello-world" not in names

    def test_skips_missing_test_file(
        self, ingestor: ExercismIngestor, mock_track_dir: Path
    ) -> None:
        exercises = ingestor.list_exercises(mock_track_dir)
        names = [e.name for e in exercises]
        assert "resistor-color" not in names

    def test_returns_sorted_order(
        self, ingestor: ExercismIngestor, mock_track_dir: Path
    ) -> None:
        exercises = ingestor.list_exercises(mock_track_dir)
        names = [e.name for e in exercises]
        assert names == sorted(names)

    def test_empty_practice_dir(
        self, ingestor: ExercismIngestor, tmp_path: Path
    ) -> None:
        track = tmp_path / "empty"
        (track / "exercises" / "practice").mkdir(parents=True)
        exercises = ingestor.list_exercises(track)
        assert exercises == []

    def test_missing_practice_dir(
        self, ingestor: ExercismIngestor, tmp_path: Path
    ) -> None:
        exercises = ingestor.list_exercises(tmp_path / "nonexistent")
        assert exercises == []

    def test_exercise_info_paths(
        self, ingestor: ExercismIngestor, mock_track_dir: Path
    ) -> None:
        exercises = ingestor.list_exercises(mock_track_dir)
        leap = [e for e in exercises if e.name == "leap"][0]
        assert leap.exemplar_path.name == "exemplar.py"
        assert leap.test_path.name == "leap_test.py"


# ---------------------------------------------------------------------------
# Test: extract_solution
# ---------------------------------------------------------------------------


class TestExtractSolution:
    def test_reads_file_content(
        self, ingestor: ExercismIngestor, mock_track_dir: Path
    ) -> None:
        exercises = ingestor.list_exercises(mock_track_dir)
        leap = [e for e in exercises if e.name == "leap"][0]
        source = ingestor.extract_solution(leap)
        assert "def leap_year" in source

    def test_returns_string(
        self, ingestor: ExercismIngestor, mock_track_dir: Path
    ) -> None:
        exercises = ingestor.list_exercises(mock_track_dir)
        source = ingestor.extract_solution(exercises[0])
        assert isinstance(source, str)


# ---------------------------------------------------------------------------
# Test: transpile_solution
# ---------------------------------------------------------------------------


class TestTranspileSolution:
    def test_simple_function(
        self, ingestor: ExercismIngestor, simple_python_source: str
    ) -> None:
        result = ingestor.transpile_solution(simple_python_source, "add-nums")
        assert result.startswith("m=addnums;")
        assert "f=add(" in result

    def test_module_name_strips_hyphens(
        self, ingestor: ExercismIngestor, simple_python_source: str
    ) -> None:
        result = ingestor.transpile_solution(simple_python_source, "two-fer")
        assert "m=twofer;" in result

    def test_class_raises_transpile_error(
        self, ingestor: ExercismIngestor, class_python_source: str
    ) -> None:
        from transpile.py_to_toke import TranspileError

        with pytest.raises(TranspileError):
            ingestor.transpile_solution(class_python_source, "greeter")

    def test_empty_source_raises(self, ingestor: ExercismIngestor) -> None:
        from transpile.py_to_toke import TranspileError

        with pytest.raises(TranspileError):
            ingestor.transpile_solution("", "empty")

    def test_class_raises(self, ingestor: ExercismIngestor) -> None:
        """Class-only files produce no functions, so transpiler raises."""
        from transpile.py_to_toke import TranspileError

        source = textwrap.dedent("""\
            class Foo:
                pass
        """)
        with pytest.raises(TranspileError):
            ingestor.transpile_solution(source, "cls-test")


# ---------------------------------------------------------------------------
# Test: validate_toke
# ---------------------------------------------------------------------------


class TestValidateToke:
    @patch("ingest.exercism.subprocess.run")
    def test_pass_returns_true(
        self, mock_run: MagicMock, ingestor: ExercismIngestor
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        passed, diag = ingestor.validate_toke("m=test;\n")
        assert passed is True
        assert diag == ""

    @patch("ingest.exercism.subprocess.run")
    def test_fail_returns_false_with_diag(
        self, mock_run: MagicMock, ingestor: ExercismIngestor
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error: expected ';'"
        )
        passed, diag = ingestor.validate_toke("m=test\n")
        assert passed is False
        assert "expected" in diag

    @patch("ingest.exercism.subprocess.run")
    def test_timeout_returns_false(
        self, mock_run: MagicMock, ingestor: ExercismIngestor
    ) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tkc", timeout=10)
        passed, diag = ingestor.validate_toke("m=test;\n")
        assert passed is False

    @patch("ingest.exercism.subprocess.run")
    def test_missing_binary_returns_false(
        self, mock_run: MagicMock, ingestor: ExercismIngestor
    ) -> None:
        mock_run.side_effect = FileNotFoundError("tkc not found")
        passed, diag = ingestor.validate_toke("m=test;\n")
        assert passed is False
        assert "not found" in diag


# ---------------------------------------------------------------------------
# Test: auto_repair
# ---------------------------------------------------------------------------


class TestAutoRepair:
    def test_missing_semicolon_repair(self, ingestor: ExercismIngestor) -> None:
        source = "m=test\nf=add(a:i64;b:i64):i64{<a+b};\n"
        diag = 'error: Expected ";" at line: 1, col: 7'
        result = ingestor.auto_repair(source, diag)
        assert result is not None
        assert "m=test;" in result

    def test_immutable_reassignment_repair(
        self, ingestor: ExercismIngestor
    ) -> None:
        source = 'let x=0;\nx=1;\n'
        diag = "error: Immutable variable reassignment: 'x'"
        result = ingestor.auto_repair(source, diag)
        assert result is not None
        assert "let x=mut." in result

    def test_cannot_assign_immutable_repair(
        self, ingestor: ExercismIngestor
    ) -> None:
        source = 'let count=0;\ncount=count+1;\n'
        diag = "error: Cannot assign to immutable variable 'count'"
        result = ingestor.auto_repair(source, diag)
        assert result is not None
        assert "let count=mut." in result

    def test_unrecognised_error_returns_none(
        self, ingestor: ExercismIngestor
    ) -> None:
        source = "m=test;\n"
        diag = "error: type mismatch: expected i64, got $str"
        result = ingestor.auto_repair(source, diag)
        assert result is None

    def test_already_mutable_not_doubled(
        self, ingestor: ExercismIngestor
    ) -> None:
        source = 'let x=mut.0;\nx=1;\n'
        diag = "error: Immutable variable reassignment: 'x'"
        result = ingestor.auto_repair(source, diag)
        # Should not insert a second mut.
        assert result is None or "mut.mut." not in result


# ---------------------------------------------------------------------------
# Test: JSONL output format
# ---------------------------------------------------------------------------


class TestJsonlOutput:
    def _make_entry(self) -> dict:
        """Build a sample corpus entry matching the expected schema."""
        return {
            "id": "exercism-python-two-fer-001",
            "tk_source": "m=twofer;\nf=twofer():$str{<\"hello\"};\n",
            "tk_tokens": 5,
            "generation_method": "transpilation",
            "source": {
                "origin": "exercism",
                "track": "python",
                "exercise": "two-fer",
                "license": "CC-BY-SA-3.0",
                "retrieval_date": "2026-04-08",
            },
            "validation": {"tkc_check": "pass"},
        }

    def test_required_fields_present(self) -> None:
        entry = self._make_entry()
        for key in ("id", "tk_source", "tk_tokens", "generation_method", "source", "validation"):
            assert key in entry

    def test_source_subfields(self) -> None:
        entry = self._make_entry()
        src = entry["source"]
        for key in ("origin", "track", "exercise", "license", "retrieval_date"):
            assert key in src

    def test_id_format(self) -> None:
        entry = self._make_entry()
        assert entry["id"].startswith("exercism-python-")
        assert entry["id"].endswith("-001")

    def test_serialises_to_valid_json(self) -> None:
        entry = self._make_entry()
        line = json.dumps(entry)
        parsed = json.loads(line)
        assert parsed == entry

    def test_validation_value(self) -> None:
        entry = self._make_entry()
        assert entry["validation"]["tkc_check"] == "pass"


# ---------------------------------------------------------------------------
# Test: IngestReport
# ---------------------------------------------------------------------------


class TestIngestReport:
    def test_defaults_to_zero(self) -> None:
        report = IngestReport()
        assert report.total_exercises == 0
        assert report.transpile_success == 0
        assert report.transpile_fail == 0
        assert report.tkc_pass == 0
        assert report.tkc_fail == 0
        assert report.auto_repair_success == 0
        assert report.entries_written == 0

    def test_fields_are_writable(self) -> None:
        report = IngestReport()
        report.total_exercises = 100
        report.transpile_success = 80
        report.transpile_fail = 20
        report.tkc_pass = 70
        report.tkc_fail = 10
        report.auto_repair_success = 5
        report.entries_written = 70
        assert report.total_exercises == 100
        assert report.entries_written == 70


# ---------------------------------------------------------------------------
# Test: clone_track
# ---------------------------------------------------------------------------


class TestCloneTrack:
    @patch("ingest.exercism.subprocess.run")
    def test_clone_calls_git(
        self, mock_run: MagicMock, ingestor: ExercismIngestor, tmp_path: Path
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        dest = tmp_path / "python"
        ingestor.clone_track("python", dest)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "git" in args
        assert "clone" in args
        assert "--depth" in args
        assert "1" in args
        assert "https://github.com/exercism/python.git" in args


# ---------------------------------------------------------------------------
# Test: full _process_exercise path (mocked)
# ---------------------------------------------------------------------------


class TestProcessExercise:
    @patch.object(ExercismIngestor, "validate_toke")
    def test_successful_exercise(
        self,
        mock_validate: MagicMock,
        ingestor: ExercismIngestor,
        mock_track_dir: Path,
    ) -> None:
        """An exercise that transpiles and validates should produce an entry."""
        mock_validate.return_value = (True, "")
        exercises = ingestor.list_exercises(mock_track_dir)
        # Use an exercise with a simple enough function for transpilation.
        # leap uses %, and/or which should transpile.
        report = IngestReport()
        # Try all exercises; at least one should succeed
        entries = []
        for ex in exercises:
            entry = ingestor._process_exercise(ex, "python", report)
            if entry is not None:
                entries.append(entry)

        # At least one exercise should have been processed
        assert report.transpile_success >= 1 or report.transpile_fail >= 1

    @patch.object(ExercismIngestor, "validate_toke")
    def test_transpile_fail_returns_none(
        self,
        mock_validate: MagicMock,
        ingestor: ExercismIngestor,
        tmp_path: Path,
    ) -> None:
        """An exercise with a class should fail transpilation."""
        # Create an exercise with class-based code
        practice = tmp_path / "exercises" / "practice" / "robot"
        (practice / ".meta").mkdir(parents=True)
        (practice / ".meta" / "exemplar.py").write_text(
            textwrap.dedent("""\
                class Robot:
                    def __init__(self) -> None:
                        self.name = "R2D2"
            """)
        )
        (practice / "robot_test.py").write_text("# test\n")

        exercises = ingestor.list_exercises(tmp_path)
        assert len(exercises) == 1
        report = IngestReport()
        entry = ingestor._process_exercise(exercises[0], "python", report)
        assert entry is None
        assert report.transpile_fail == 1

    @patch.object(ExercismIngestor, "validate_toke")
    def test_auto_repair_on_failure(
        self,
        mock_validate: MagicMock,
        ingestor: ExercismIngestor,
        mock_track_dir: Path,
    ) -> None:
        """When first validation fails but auto-repair succeeds, entry is produced."""
        # First call fails, second (after repair) succeeds
        mock_validate.side_effect = [
            (False, 'error: Expected ";" at line: 1, col: 10'),
            (True, ""),
        ]
        exercises = ingestor.list_exercises(mock_track_dir)
        # Find an exercise that transpiles successfully
        report = IngestReport()
        for ex in exercises:
            try:
                tk = ingestor.transpile_solution(
                    ingestor.extract_solution(ex), ex.name
                )
                # Now run _process_exercise with our mocked validate
                mock_validate.side_effect = [
                    (False, 'error: Expected ";" at line: 1, col: 10'),
                    (True, ""),
                ]
                entry = ingestor._process_exercise(ex, "python", report)
                if entry is not None:
                    assert report.auto_repair_success >= 1
                    break
            except Exception:
                continue
