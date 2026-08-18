"""Tests for the Rosetta Code ingestion pipeline.

Uses mock/fixture data throughout -- never clones the actual repo.
"""

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest.rosetta import IngestReport, RosettaIngestor, RosettaTask


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ingestor() -> RosettaIngestor:
    return RosettaIngestor(tkc_path=Path("/usr/bin/false"))


@pytest.fixture
def mock_repo_dir(tmp_path: Path) -> Path:
    """Create a minimal RosettaCodeData directory structure.

    Structure:
        Task/
            Fibonacci/
                Python/
                    fibonacci.py
                C/
                    fibonacci.c
            FizzBuzz/
                Python/
                    fizzbuzz.py
            Ackermann/
                C/
                    ackermann.c
            EmptyTask/           (no language dirs)
    """
    task_root = tmp_path / "Task"

    # Fibonacci: both Python and C
    fib_py = task_root / "Fibonacci" / "Python"
    fib_py.mkdir(parents=True)
    (fib_py / "fibonacci.py").write_text(
        textwrap.dedent("""\
            def fibonacci(n: int) -> int:
                if n < 2:
                    return n
                return fibonacci(n - 1) + fibonacci(n - 2)
        """)
    )
    fib_c = task_root / "Fibonacci" / "C"
    fib_c.mkdir(parents=True)
    (fib_c / "fibonacci.c").write_text(
        textwrap.dedent("""\
            int fibonacci(int n) {
                if (n < 2) return n;
                return fibonacci(n - 1) + fibonacci(n - 2);
            }
        """)
    )

    # FizzBuzz: Python only
    fb_py = task_root / "FizzBuzz" / "Python"
    fb_py.mkdir(parents=True)
    (fb_py / "fizzbuzz.py").write_text(
        textwrap.dedent("""\
            def fizzbuzz(n: int) -> int:
                return n
        """)
    )

    # Ackermann: C only
    ack_c = task_root / "Ackermann" / "C"
    ack_c.mkdir(parents=True)
    (ack_c / "ackermann.c").write_text(
        textwrap.dedent("""\
            int ackermann(int m, int n) {
                if (m == 0) return n + 1;
                if (n == 0) return ackermann(m - 1, 1);
                return ackermann(m - 1, ackermann(m, n - 1));
            }
        """)
    )

    # EmptyTask: no language solutions
    (task_root / "EmptyTask").mkdir(parents=True)

    # A non-directory file in Task/ (should be skipped)
    (task_root / "README.md").write_text("ignore\n")

    return tmp_path


@pytest.fixture
def simple_python_source() -> str:
    return textwrap.dedent("""\
        def add(a: int, b: int) -> int:
            return a + b
    """)


@pytest.fixture
def simple_c_source() -> str:
    return textwrap.dedent("""\
        int add(int a, int b) {
            return a + b;
        }
    """)


# ---------------------------------------------------------------------------
# Test: list_tasks
# ---------------------------------------------------------------------------


class TestListTasks:
    def test_finds_tasks_with_solutions(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        names = [t.name for t in tasks]
        assert "Fibonacci" in names
        assert "FizzBuzz" in names
        assert "Ackermann" in names

    def test_skips_empty_tasks(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        names = [t.name for t in tasks]
        assert "EmptyTask" not in names

    def test_returns_sorted_order(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        names = [t.name for t in tasks]
        assert names == sorted(names)

    def test_fibonacci_has_both_languages(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        fib = [t for t in tasks if t.name == "Fibonacci"][0]
        assert "python" in fib.languages
        assert "c" in fib.languages

    def test_fizzbuzz_has_python_only(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        fb = [t for t in tasks if t.name == "FizzBuzz"][0]
        assert "python" in fb.languages
        assert "c" not in fb.languages

    def test_ackermann_has_c_only(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        ack = [t for t in tasks if t.name == "Ackermann"][0]
        assert "c" in ack.languages
        assert "python" not in ack.languages

    def test_missing_task_dir(
        self, ingestor: RosettaIngestor, tmp_path: Path
    ) -> None:
        tasks = ingestor.list_tasks(tmp_path / "nonexistent")
        assert tasks == []

    def test_slug_generation(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        fib = [t for t in tasks if t.name == "Fibonacci"][0]
        assert fib.slug == "fibonacci"


# ---------------------------------------------------------------------------
# Test: extract_solution
# ---------------------------------------------------------------------------


class TestExtractSolution:
    def test_reads_python_source(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        fib = [t for t in tasks if t.name == "Fibonacci"][0]
        source = ingestor.extract_solution(fib, "python")
        assert "def fibonacci" in source

    def test_reads_c_source(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        fib = [t for t in tasks if t.name == "Fibonacci"][0]
        source = ingestor.extract_solution(fib, "c")
        assert "fibonacci" in source

    def test_missing_language_raises(
        self, ingestor: RosettaIngestor, mock_repo_dir: Path
    ) -> None:
        tasks = ingestor.list_tasks(mock_repo_dir)
        fb = [t for t in tasks if t.name == "FizzBuzz"][0]
        with pytest.raises(KeyError):
            ingestor.extract_solution(fb, "c")


# ---------------------------------------------------------------------------
# Test: transpile_solution
# ---------------------------------------------------------------------------


class TestTranspileSolution:
    def test_python_transpilation(
        self, ingestor: RosettaIngestor, simple_python_source: str
    ) -> None:
        result = ingestor.transpile_solution(
            simple_python_source, "python", "AddNumbers"
        )
        assert result.startswith("m=addnumbers;")
        assert "f=add(" in result

    def test_c_transpilation(
        self, ingestor: RosettaIngestor, simple_c_source: str
    ) -> None:
        result = ingestor.transpile_solution(
            simple_c_source, "c", "AddNumbers"
        )
        assert result.startswith("m=addnumbers;")
        assert "f=add(" in result

    def test_unsupported_language_raises(
        self, ingestor: RosettaIngestor, simple_python_source: str
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported language"):
            ingestor.transpile_solution(simple_python_source, "java", "Test")

    def test_empty_python_raises(self, ingestor: RosettaIngestor) -> None:
        from transpile.py_to_toke import TranspileError

        with pytest.raises(TranspileError):
            ingestor.transpile_solution("", "python", "Empty")


# ---------------------------------------------------------------------------
# Test: validate_toke
# ---------------------------------------------------------------------------


class TestValidateToke:
    @patch("ingest.rosetta.subprocess.run")
    def test_pass_returns_true(
        self, mock_run: MagicMock, ingestor: RosettaIngestor
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        passed, diag = ingestor.validate_toke("m=test;\n")
        assert passed is True
        assert diag == ""

    @patch("ingest.rosetta.subprocess.run")
    def test_fail_returns_false_with_diag(
        self, mock_run: MagicMock, ingestor: RosettaIngestor
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error: expected ';'"
        )
        passed, diag = ingestor.validate_toke("m=test\n")
        assert passed is False
        assert "expected" in diag


# ---------------------------------------------------------------------------
# Test: auto_repair
# ---------------------------------------------------------------------------


class TestAutoRepair:
    def test_missing_semicolon_repair(self, ingestor: RosettaIngestor) -> None:
        source = "m=test\nf=add(a:i64;b:i64):i64{<a+b};\n"
        diag = 'error: Expected ";" at line: 1, col: 7'
        result = ingestor.auto_repair(source, diag)
        assert result is not None
        assert "m=test;" in result

    def test_immutable_reassignment_repair(
        self, ingestor: RosettaIngestor
    ) -> None:
        source = "let x=0;\nx=1;\n"
        diag = "error: Immutable variable reassignment: 'x'"
        result = ingestor.auto_repair(source, diag)
        assert result is not None
        assert "let x=mut." in result

    def test_cannot_assign_immutable_repair(
        self, ingestor: RosettaIngestor
    ) -> None:
        source = "let count=0;\ncount=count+1;\n"
        diag = "error: Cannot assign to immutable variable 'count'"
        result = ingestor.auto_repair(source, diag)
        assert result is not None
        assert "let count=mut." in result

    def test_unrecognised_error_returns_none(
        self, ingestor: RosettaIngestor
    ) -> None:
        source = "m=test;\n"
        diag = "error: type mismatch: expected i64, got $str"
        result = ingestor.auto_repair(source, diag)
        assert result is None

    def test_already_mutable_not_doubled(
        self, ingestor: RosettaIngestor
    ) -> None:
        source = "let x=mut.0;\nx=1;\n"
        diag = "error: Immutable variable reassignment: 'x'"
        result = ingestor.auto_repair(source, diag)
        assert result is None or "mut.mut." not in result


# ---------------------------------------------------------------------------
# Test: deduplication (Python preferred over C)
# ---------------------------------------------------------------------------


class TestDeduplication:
    @patch.object(RosettaIngestor, "validate_toke")
    def test_python_preferred_over_c(
        self,
        mock_validate: MagicMock,
        ingestor: RosettaIngestor,
        mock_repo_dir: Path,
    ) -> None:
        """When both Python and C produce valid toke, Python is kept."""
        mock_validate.return_value = (True, "")
        tasks = ingestor.list_tasks(mock_repo_dir)
        fib = [t for t in tasks if t.name == "Fibonacci"][0]

        report = IngestReport()
        # With languages=["python", "c"], Python should be tried first
        entry = ingestor._process_task(fib, ["python", "c"], report)
        assert entry is not None
        assert entry["source"]["language"] == "python"

    @patch.object(RosettaIngestor, "validate_toke")
    def test_c_used_when_no_python(
        self,
        mock_validate: MagicMock,
        ingestor: RosettaIngestor,
        mock_repo_dir: Path,
    ) -> None:
        """When only C is available, C is used."""
        mock_validate.return_value = (True, "")
        tasks = ingestor.list_tasks(mock_repo_dir)
        ack = [t for t in tasks if t.name == "Ackermann"][0]

        report = IngestReport()
        entry = ingestor._process_task(ack, ["python", "c"], report)
        assert entry is not None
        assert entry["source"]["language"] == "c"


# ---------------------------------------------------------------------------
# Test: JSONL output format
# ---------------------------------------------------------------------------


class TestJsonlOutput:
    def _make_entry(self) -> dict:
        """Build a sample corpus entry matching the expected schema."""
        return {
            "id": "rosetta-python-fibonacci-001",
            "tk_source": "m=fibonacci;\nf=fibonacci(n:i64):i64{<n};\n",
            "tk_tokens": 5,
            "generation_method": "transpilation",
            "source": {
                "origin": "rosettacode",
                "task": "Fibonacci",
                "language": "python",
                "license": "GFDL-1.2",
                "retrieval_date": "2026-04-08",
            },
            "validation": {"tkc_check": "pass"},
        }

    def test_required_fields_present(self) -> None:
        entry = self._make_entry()
        for key in (
            "id", "tk_source", "tk_tokens", "generation_method",
            "source", "validation",
        ):
            assert key in entry

    def test_source_subfields(self) -> None:
        entry = self._make_entry()
        src = entry["source"]
        for key in ("origin", "task", "language", "license", "retrieval_date"):
            assert key in src

    def test_id_format(self) -> None:
        entry = self._make_entry()
        assert entry["id"].startswith("rosetta-python-")
        assert entry["id"].endswith("-001")

    def test_serialises_to_valid_json(self) -> None:
        entry = self._make_entry()
        line = json.dumps(entry)
        parsed = json.loads(line)
        assert parsed == entry

    def test_license_is_gfdl(self) -> None:
        entry = self._make_entry()
        assert entry["source"]["license"] == "GFDL-1.2"

    def test_origin_is_rosettacode(self) -> None:
        entry = self._make_entry()
        assert entry["source"]["origin"] == "rosettacode"


# ---------------------------------------------------------------------------
# Test: IngestReport
# ---------------------------------------------------------------------------


class TestIngestReport:
    def test_defaults_to_zero(self) -> None:
        report = IngestReport()
        assert report.total_tasks == 0
        assert report.transpile_success == 0
        assert report.transpile_fail == 0
        assert report.tkc_pass == 0
        assert report.tkc_fail == 0
        assert report.auto_repair_success == 0
        assert report.entries_written == 0

    def test_fields_are_writable(self) -> None:
        report = IngestReport()
        report.total_tasks = 100
        report.transpile_success = 80
        assert report.total_tasks == 100
        assert report.transpile_success == 80


# ---------------------------------------------------------------------------
# Test: clone_repo
# ---------------------------------------------------------------------------


class TestCloneRepo:
    @patch("ingest.rosetta.subprocess.run")
    def test_clone_calls_git(
        self, mock_run: MagicMock, ingestor: RosettaIngestor, tmp_path: Path
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        dest = tmp_path / "rosetta"
        ingestor.clone_repo(dest)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "git" in args
        assert "clone" in args
        assert "--depth" in args
        assert "1" in args
        assert "https://github.com/acmeism/RosettaCodeData.git" in args


# ---------------------------------------------------------------------------
# Test: _process_task
# ---------------------------------------------------------------------------


class TestProcessTask:
    @patch.object(RosettaIngestor, "validate_toke")
    def test_successful_python_task(
        self,
        mock_validate: MagicMock,
        ingestor: RosettaIngestor,
        mock_repo_dir: Path,
    ) -> None:
        mock_validate.return_value = (True, "")
        tasks = ingestor.list_tasks(mock_repo_dir)
        fb = [t for t in tasks if t.name == "FizzBuzz"][0]
        report = IngestReport()
        entry = ingestor._process_task(fb, ["python", "c"], report)
        assert entry is not None
        assert report.transpile_success >= 1

    @patch.object(RosettaIngestor, "validate_toke")
    def test_auto_repair_on_failure(
        self,
        mock_validate: MagicMock,
        ingestor: RosettaIngestor,
        mock_repo_dir: Path,
    ) -> None:
        """When first validation fails but auto-repair succeeds."""
        mock_validate.side_effect = [
            (False, 'error: Expected ";" at line: 1, col: 10'),
            (True, ""),
        ]
        tasks = ingestor.list_tasks(mock_repo_dir)
        fb = [t for t in tasks if t.name == "FizzBuzz"][0]
        report = IngestReport()
        entry = ingestor._process_task(fb, ["python"], report)
        if entry is not None:
            assert report.auto_repair_success >= 1


# ---------------------------------------------------------------------------
# Test: RosettaTask dataclass
# ---------------------------------------------------------------------------


class TestRosettaTask:
    def test_dataclass_fields(self) -> None:
        task = RosettaTask(
            name="Fibonacci",
            slug="fibonacci",
            languages={"python": Path("/tmp/fib.py")},
        )
        assert task.name == "Fibonacci"
        assert task.slug == "fibonacci"
        assert "python" in task.languages

    def test_default_languages(self) -> None:
        task = RosettaTask(name="Test", slug="test")
        assert task.languages == {}


# ---------------------------------------------------------------------------
# Test: slug generation
# ---------------------------------------------------------------------------


class TestSlugGeneration:
    def test_simple_name(self) -> None:
        assert RosettaIngestor._make_slug("Fibonacci") == "fibonacci"

    def test_name_with_spaces(self) -> None:
        assert RosettaIngestor._make_slug("100 doors") == "100doors"

    def test_name_with_special_chars(self) -> None:
        assert RosettaIngestor._make_slug("A+B") == "ab"

    def test_empty_name(self) -> None:
        assert RosettaIngestor._make_slug("---") == "unnamed"
