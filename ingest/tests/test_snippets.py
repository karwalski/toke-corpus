"""Tests for the snippet collection ingestion pipeline (Story 9.4.3).

Uses mock/fixture data throughout -- never clones actual repos.
"""

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest.snippets import (
    DEFAULT_REPOS,
    IngestReport,
    Snippet,
    SnippetIngestor,
    SnippetRepo,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ingestor() -> SnippetIngestor:
    return SnippetIngestor(tkc_path=Path("/usr/bin/false"))


@pytest.fixture
def mock_python_md_repo(tmp_path: Path) -> Path:
    """Create a minimal 30-seconds-of-python-style repo."""
    repo = tmp_path / "30-seconds-of-python"
    snippets_dir = repo / "snippets"
    snippets_dir.mkdir(parents=True)

    # Snippet 1: a simple function in markdown
    (snippets_dir / "clamp.md").write_text(
        textwrap.dedent("""\
            # clamp

            Clamps a number within the inclusive range.

            ```python
            def clamp(num: int, a: int, b: int) -> int:
                if num < a:
                    return a
                if num > b:
                    return b
                return num
            ```

            ```python
            clamp(1, 10, 20)  # 10
            ```
        """)
    )

    # Snippet 2: another function
    (snippets_dir / "is_even.md").write_text(
        textwrap.dedent("""\
            # is_even

            Checks if a number is even.

            ```python
            def is_even(num: int) -> bool:
                return num % 2 == 0
            ```
        """)
    )

    # Snippet 3: no function (should be filtered)
    (snippets_dir / "hello.md").write_text(
        textwrap.dedent("""\
            # hello

            Just a greeting.

            ```python
            greeting = "hello"
            print(greeting)
            ```
        """)
    )

    # Snippet 4: empty code block
    (snippets_dir / "empty.md").write_text(
        textwrap.dedent("""\
            # empty

            ```python
            ```
        """)
    )

    return repo


@pytest.fixture
def mock_go_repo(tmp_path: Path) -> Path:
    """Create a minimal go-by-example-style repo."""
    repo = tmp_path / "go-by-example"
    examples = repo / "examples"

    # Example with a non-main function
    values_dir = examples / "values"
    values_dir.mkdir(parents=True)
    (values_dir / "values.go").write_text(
        textwrap.dedent("""\
            package main

            import "fmt"

            func add(a int, b int) int {
                return a + b
            }

            func main() {
                fmt.Println(add(1, 2))
            }
        """)
    )

    # Example with only main (should extract nothing useful)
    hello_dir = examples / "hello-world"
    hello_dir.mkdir(parents=True)
    (hello_dir / "hello.go").write_text(
        textwrap.dedent("""\
            package main

            import "fmt"

            func main() {
                fmt.Println("Hello, World!")
            }
        """)
    )

    return repo


@pytest.fixture
def simple_snippet() -> Snippet:
    return Snippet(
        name="clamp",
        source=textwrap.dedent("""\
            def clamp(num: int, a: int, b: int) -> int:
                if num < a:
                    return a
                if num > b:
                    return b
                return num
        """),
        language="python",
        repo_name="30-seconds-of-python",
        license="CC-BY-4.0",
    )


@pytest.fixture
def go_snippet() -> Snippet:
    return Snippet(
        name="values-add",
        source="func add(a int, b int) int {\n    return a + b\n}",
        language="go",
        repo_name="go-by-example",
        license="CC-BY-3.0",
    )


# ---------------------------------------------------------------------------
# Test: Markdown code block extraction
# ---------------------------------------------------------------------------


class TestExtractPythonMd:
    def test_extracts_function_snippets(
        self, ingestor: SnippetIngestor, mock_python_md_repo: Path
    ) -> None:
        snippets = ingestor.extract_snippets_python_md(
            mock_python_md_repo, "snippets/**/*.md"
        )
        names = [s.name for s in snippets]
        assert "clamp" in names
        assert "is_even" in names

    def test_filters_non_function_snippets(
        self, ingestor: SnippetIngestor, mock_python_md_repo: Path
    ) -> None:
        snippets = ingestor.extract_snippets_python_md(
            mock_python_md_repo, "snippets/**/*.md"
        )
        names = [s.name for s in snippets]
        # "hello" only has a variable assignment, no def
        assert "hello" not in names

    def test_skips_empty_code_blocks(
        self, ingestor: SnippetIngestor, mock_python_md_repo: Path
    ) -> None:
        snippets = ingestor.extract_snippets_python_md(
            mock_python_md_repo, "snippets/**/*.md"
        )
        names = [s.name for s in snippets]
        assert "empty" not in names

    def test_snippet_has_correct_metadata(
        self, ingestor: SnippetIngestor, mock_python_md_repo: Path
    ) -> None:
        snippets = ingestor.extract_snippets_python_md(
            mock_python_md_repo, "snippets/**/*.md"
        )
        clamp = [s for s in snippets if s.name == "clamp"][0]
        assert clamp.language == "python"
        assert clamp.repo_name == "30-seconds-of-python"
        assert clamp.license == "CC-BY-4.0"
        assert "def clamp" in clamp.source

    def test_empty_directory_returns_empty_list(
        self, ingestor: SnippetIngestor, tmp_path: Path
    ) -> None:
        snippets = ingestor.extract_snippets_python_md(
            tmp_path, "snippets/**/*.md"
        )
        assert snippets == []


# ---------------------------------------------------------------------------
# Test: Go snippet extraction
# ---------------------------------------------------------------------------


class TestExtractGo:
    def test_finds_non_main_functions(
        self, ingestor: SnippetIngestor, mock_go_repo: Path
    ) -> None:
        snippets = ingestor.extract_snippets_go(mock_go_repo)
        names = [s.name for s in snippets]
        # Should find the add function but not main
        assert len(snippets) >= 1
        assert all(s.language == "go" for s in snippets)

    def test_skips_main_functions(
        self, ingestor: SnippetIngestor, mock_go_repo: Path
    ) -> None:
        snippets = ingestor.extract_snippets_go(mock_go_repo)
        sources = [s.source for s in snippets]
        # None of the extracted snippets should be a main() function
        for src in sources:
            assert not src.startswith("func main(")

    def test_missing_examples_dir(
        self, ingestor: SnippetIngestor, tmp_path: Path
    ) -> None:
        snippets = ingestor.extract_snippets_go(tmp_path)
        assert snippets == []


# ---------------------------------------------------------------------------
# Test: Python snippet transpilation
# ---------------------------------------------------------------------------


class TestTranspileSnippet:
    def test_python_snippet_produces_toke(
        self, ingestor: SnippetIngestor, simple_snippet: Snippet
    ) -> None:
        result = ingestor.transpile_snippet(simple_snippet)
        assert result is not None
        assert result.startswith("m=clamp;")
        assert "f=clamp(" in result

    def test_go_snippet_returns_none(
        self, ingestor: SnippetIngestor, go_snippet: Snippet
    ) -> None:
        result = ingestor.transpile_snippet(go_snippet)
        assert result is None

    def test_invalid_python_returns_none(
        self, ingestor: SnippetIngestor
    ) -> None:
        snippet = Snippet(
            name="bad",
            source="this is not valid python def (",
            language="python",
            repo_name="test",
            license="MIT",
        )
        result = ingestor.transpile_snippet(snippet)
        assert result is None

    def test_class_only_returns_none(
        self, ingestor: SnippetIngestor
    ) -> None:
        snippet = Snippet(
            name="cls",
            source="class Foo:\n    pass\n",
            language="python",
            repo_name="test",
            license="MIT",
        )
        result = ingestor.transpile_snippet(snippet)
        assert result is None


# ---------------------------------------------------------------------------
# Test: validate_toke
# ---------------------------------------------------------------------------


class TestValidateToke:
    @patch("ingest.snippets.subprocess.run")
    def test_pass_returns_true(
        self, mock_run: MagicMock, ingestor: SnippetIngestor
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        passed, diag = ingestor.validate_toke("m=test;\n")
        assert passed is True
        assert diag == ""

    @patch("ingest.snippets.subprocess.run")
    def test_fail_returns_false_with_diag(
        self, mock_run: MagicMock, ingestor: SnippetIngestor
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
    def test_missing_semicolon_repair(
        self, ingestor: SnippetIngestor
    ) -> None:
        source = "m=test\nf=add(a:i64;b:i64):i64{<a+b};\n"
        diag = 'error: Expected ";" at line: 1, col: 7'
        result = ingestor.auto_repair(source, diag)
        assert result is not None
        assert "m=test;" in result

    def test_immutable_reassignment_repair(
        self, ingestor: SnippetIngestor
    ) -> None:
        source = "let x=0;\nx=1;\n"
        diag = "error: Immutable variable reassignment: 'x'"
        result = ingestor.auto_repair(source, diag)
        assert result is not None
        assert "let x=mut." in result

    def test_unrecognised_error_returns_none(
        self, ingestor: SnippetIngestor
    ) -> None:
        source = "m=test;\n"
        diag = "error: type mismatch: expected i64, got $str"
        result = ingestor.auto_repair(source, diag)
        assert result is None


# ---------------------------------------------------------------------------
# Test: Stats tracking
# ---------------------------------------------------------------------------


class TestIngestReport:
    def test_defaults_to_zero(self) -> None:
        report = IngestReport()
        assert report.total_snippets == 0
        assert report.transpile_success == 0
        assert report.transpile_fail == 0
        assert report.tkc_pass == 0
        assert report.tkc_fail == 0
        assert report.auto_repair_success == 0
        assert report.entries_written == 0
        assert report.skipped_go == 0

    def test_fields_are_writable(self) -> None:
        report = IngestReport()
        report.total_snippets = 200
        report.transpile_success = 150
        report.transpile_fail = 50
        report.tkc_pass = 140
        report.tkc_fail = 10
        report.auto_repair_success = 5
        report.entries_written = 140
        report.skipped_go = 30
        assert report.total_snippets == 200
        assert report.entries_written == 140
        assert report.skipped_go == 30


# ---------------------------------------------------------------------------
# Test: JSONL output format
# ---------------------------------------------------------------------------


class TestJsonlOutput:
    def _make_entry(self) -> dict:
        return {
            "id": "snippet-30-seconds-of-python-clamp-001",
            "tk_source": "m=clamp;\nf=clamp(num:i64;a:i64;b:i64):i64{\n  if(num<a){<a};\n  if(num>b){<b};\n  <num\n};\n",
            "tk_tokens": 12,
            "generation_method": "transpilation",
            "source": {
                "origin": "30-seconds-of-python",
                "snippet": "clamp",
                "language": "python",
                "license": "CC-BY-4.0",
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
        for key in ("origin", "snippet", "language", "license", "retrieval_date"):
            assert key in src

    def test_id_format(self) -> None:
        entry = self._make_entry()
        assert entry["id"].startswith("snippet-")
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
# Test: SnippetRepo dataclass
# ---------------------------------------------------------------------------


class TestSnippetRepo:
    def test_default_repos_exist(self) -> None:
        assert len(DEFAULT_REPOS) == 2
        names = [r.name for r in DEFAULT_REPOS]
        assert "30-seconds-of-python" in names
        assert "go-by-example" in names

    def test_repo_fields(self) -> None:
        repo = DEFAULT_REPOS[0]
        assert repo.url.startswith("https://")
        assert repo.license
        assert repo.language in ("python", "go")
        assert repo.snippet_glob


# ---------------------------------------------------------------------------
# Test: clone_repo
# ---------------------------------------------------------------------------


class TestCloneRepo:
    @patch("ingest.snippets.subprocess.run")
    def test_clone_calls_git(
        self, mock_run: MagicMock, ingestor: SnippetIngestor, tmp_path: Path
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        repo = DEFAULT_REPOS[0]
        dest = tmp_path / repo.name
        ingestor.clone_repo(repo, dest)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "git" in args
        assert "clone" in args
        assert "--depth" in args
        assert "1" in args
        assert repo.url in args


# ---------------------------------------------------------------------------
# Test: _process_snippet with mocked validation
# ---------------------------------------------------------------------------


class TestProcessSnippet:
    @patch.object(SnippetIngestor, "validate_toke")
    def test_successful_python_snippet(
        self,
        mock_validate: MagicMock,
        ingestor: SnippetIngestor,
        simple_snippet: Snippet,
    ) -> None:
        mock_validate.return_value = (True, "")
        report = IngestReport()
        entry = ingestor._process_snippet(simple_snippet, report)
        assert entry is not None
        assert entry["id"].startswith("snippet-")
        assert report.transpile_success == 1
        assert report.tkc_pass == 1

    @patch.object(SnippetIngestor, "validate_toke")
    def test_go_snippet_skipped(
        self,
        mock_validate: MagicMock,
        ingestor: SnippetIngestor,
        go_snippet: Snippet,
    ) -> None:
        report = IngestReport()
        entry = ingestor._process_snippet(go_snippet, report)
        assert entry is None
        assert report.skipped_go == 1
        mock_validate.assert_not_called()
