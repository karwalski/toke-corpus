"""Tests for AST harvesting and transpilation pipeline.

Uses fixtures and mocks throughout -- never clones actual repos.
"""

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ingest.ast_harvest import ASTHarvester, HarvestedFunction, MAX_LINES
from ingest.ast_transpile import ASTTranspiler, _sanitize_module_name
from ingest.repo_scanner import RepoScanner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def harvester() -> ASTHarvester:
    return ASTHarvester()


@pytest.fixture
def transpiler() -> ASTTranspiler:
    return ASTTranspiler()


@pytest.fixture
def scanner() -> RepoScanner:
    return RepoScanner()


@pytest.fixture
def simple_c_file(tmp_path: Path) -> Path:
    """Create a simple C file with harvestable functions."""
    src = tmp_path / "math_utils.c"
    src.write_text(textwrap.dedent("""\
        int add(int a, int b) {
            return a + b;
        }

        double multiply(double x, double y) {
            return x * y;
        }

        int square(int n) {
            return n * n;
        }
    """))
    return src


@pytest.fixture
def complex_c_file(tmp_path: Path) -> Path:
    """Create a C file with functions that should be skipped."""
    src = tmp_path / "complex.c"
    src.write_text(textwrap.dedent("""\
        #include <stdio.h>

        int main(int argc, char** argv) {
            printf("hello\\n");
            return 0;
        }

        void process(struct Data* d) {
            d->value = 42;
        }

        int* get_ptr(int* arr, int n) {
            return arr + n;
        }

        void use_malloc(int n) {
            int* p = malloc(n * sizeof(int));
            free(p);
        }
    """))
    return src


@pytest.fixture
def simple_python_file(tmp_path: Path) -> Path:
    """Create a Python file with harvestable functions."""
    src = tmp_path / "utils.py"
    src.write_text(textwrap.dedent("""\
        def add(a: int, b: int) -> int:
            return a + b

        def multiply(x: float, y: float) -> float:
            return x * y

        def is_even(n: int) -> bool:
            return n % 2 == 0

        def greet(name: str) -> str:
            return "Hello " + name
    """))
    return src


@pytest.fixture
def unannotated_python_file(tmp_path: Path) -> Path:
    """Create a Python file with unannotated functions."""
    src = tmp_path / "untyped.py"
    src.write_text(textwrap.dedent("""\
        def add(a, b):
            return a + b

        def no_return(x: int):
            print(x)
    """))
    return src


@pytest.fixture
def impure_python_file(tmp_path: Path) -> Path:
    """Create a Python file with impure functions."""
    src = tmp_path / "impure.py"
    src.write_text(textwrap.dedent("""\
        import os

        total = 0

        def read_file(path: str) -> str:
            f = open(path)
            return f.read()

        def use_global(x: int) -> int:
            global total
            total += x
            return total
    """))
    return src


@pytest.fixture
def class_python_file(tmp_path: Path) -> Path:
    """Create a Python file with class methods (should be skipped)."""
    src = tmp_path / "classes.py"
    src.write_text(textwrap.dedent("""\
        class Calculator:
            def add(self, a: int, b: int) -> int:
                return a + b
    """))
    return src


@pytest.fixture
def mock_repo(tmp_path: Path) -> Path:
    """Create a minimal repo structure with source files and license."""
    repo = tmp_path / "myrepo"
    repo.mkdir()

    # LICENSE file
    (repo / "LICENSE").write_text(
        "MIT License\n\nPermission is hereby granted, free of charge...\n"
    )

    # Source files
    src_dir = repo / "src"
    src_dir.mkdir()
    (src_dir / "math.py").write_text(textwrap.dedent("""\
        def square(n: int) -> int:
            return n * n

        def double(n: int) -> int:
            return n * 2
    """))

    # Test files (should be skipped)
    test_dir = repo / "tests"
    test_dir.mkdir()
    (test_dir / "test_math.py").write_text(textwrap.dedent("""\
        def test_square(x: int) -> bool:
            return square(x) == x * x
    """))

    # Vendor directory (should be skipped)
    vendor = repo / "vendor"
    vendor.mkdir()
    (vendor / "lib.py").write_text(textwrap.dedent("""\
        def vendor_func(a: int) -> int:
            return a
    """))

    return repo


@pytest.fixture
def long_c_function_file(tmp_path: Path) -> Path:
    """Create a C file with a function exceeding MAX_LINES."""
    body_lines = "\n".join(f"    int v{i} = {i};" for i in range(35))
    src = tmp_path / "long.c"
    src.write_text(f"int longfunc(int x) {{\n{body_lines}\n    return x;\n}}\n")
    return src


@pytest.fixture
def long_python_function_file(tmp_path: Path) -> Path:
    """Create a Python file with a function exceeding MAX_LINES."""
    body_lines = "\n".join(f"    v{i} = {i}" for i in range(35))
    src = tmp_path / "long.py"
    src.write_text(
        f"def longfunc(x: int) -> int:\n{body_lines}\n    return x\n"
    )
    return src


# ---------------------------------------------------------------------------
# Test: C function extraction
# ---------------------------------------------------------------------------


class TestCFunctionExtraction:
    def test_finds_simple_functions(
        self, harvester: ASTHarvester, simple_c_file: Path
    ) -> None:
        funcs = harvester.harvest_c_functions(simple_c_file)
        names = [f.name for f in funcs]
        assert "add" in names
        assert "multiply" in names
        assert "square" in names

    def test_extracts_correct_params(
        self, harvester: ASTHarvester, simple_c_file: Path
    ) -> None:
        funcs = harvester.harvest_c_functions(simple_c_file)
        add_func = [f for f in funcs if f.name == "add"][0]
        assert len(add_func.params) == 2
        assert add_func.params[0] == ("a", "int")
        assert add_func.params[1] == ("b", "int")

    def test_extracts_return_type(
        self, harvester: ASTHarvester, simple_c_file: Path
    ) -> None:
        funcs = harvester.harvest_c_functions(simple_c_file)
        add_func = [f for f in funcs if f.name == "add"][0]
        assert add_func.return_type == "int"
        mul_func = [f for f in funcs if f.name == "multiply"][0]
        assert mul_func.return_type == "double"

    def test_skips_complex_functions(
        self, harvester: ASTHarvester, complex_c_file: Path
    ) -> None:
        funcs = harvester.harvest_c_functions(complex_c_file)
        names = [f.name for f in funcs]
        assert "main" not in names
        assert "process" not in names
        assert "get_ptr" not in names
        assert "use_malloc" not in names

    def test_sets_language_to_c(
        self, harvester: ASTHarvester, simple_c_file: Path
    ) -> None:
        funcs = harvester.harvest_c_functions(simple_c_file)
        for func in funcs:
            assert func.language == "c"

    def test_sets_source_file(
        self, harvester: ASTHarvester, simple_c_file: Path
    ) -> None:
        funcs = harvester.harvest_c_functions(simple_c_file)
        for func in funcs:
            assert func.source_file == str(simple_c_file)

    def test_line_limit_enforced_c(
        self, harvester: ASTHarvester, long_c_function_file: Path
    ) -> None:
        funcs = harvester.harvest_c_functions(long_c_function_file)
        assert len(funcs) == 0

    def test_nonexistent_file_returns_empty(
        self, harvester: ASTHarvester, tmp_path: Path
    ) -> None:
        funcs = harvester.harvest_c_functions(tmp_path / "nonexistent.c")
        assert funcs == []


# ---------------------------------------------------------------------------
# Test: Python function extraction
# ---------------------------------------------------------------------------


class TestPythonFunctionExtraction:
    def test_finds_annotated_functions(
        self, harvester: ASTHarvester, simple_python_file: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(simple_python_file)
        names = [f.name for f in funcs]
        assert "add" in names
        assert "multiply" in names
        assert "is_even" in names
        assert "greet" in names

    def test_skips_unannotated_functions(
        self, harvester: ASTHarvester, unannotated_python_file: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(unannotated_python_file)
        assert len(funcs) == 0

    def test_skips_class_methods(
        self, harvester: ASTHarvester, class_python_file: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(class_python_file)
        assert len(funcs) == 0

    def test_skips_impure_functions(
        self, harvester: ASTHarvester, impure_python_file: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(impure_python_file)
        names = [f.name for f in funcs]
        assert "read_file" not in names
        assert "use_global" not in names

    def test_sets_language_to_python(
        self, harvester: ASTHarvester, simple_python_file: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(simple_python_file)
        for func in funcs:
            assert func.language == "python"

    def test_extracts_param_types(
        self, harvester: ASTHarvester, simple_python_file: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(simple_python_file)
        add_func = [f for f in funcs if f.name == "add"][0]
        assert len(add_func.params) == 2
        assert add_func.params[0][0] == "a"
        assert add_func.params[0][1] == "int"
        assert add_func.params[1][1] == "int"

    def test_extracts_return_type(
        self, harvester: ASTHarvester, simple_python_file: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(simple_python_file)
        add_func = [f for f in funcs if f.name == "add"][0]
        assert add_func.return_type == "int"

    def test_line_limit_enforced_python(
        self, harvester: ASTHarvester, long_python_function_file: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(long_python_function_file)
        assert len(funcs) == 0

    def test_nonexistent_file_returns_empty(
        self, harvester: ASTHarvester, tmp_path: Path
    ) -> None:
        funcs = harvester.harvest_python_functions(tmp_path / "nonexistent.py")
        assert funcs == []

    def test_syntax_error_file_returns_empty(
        self, harvester: ASTHarvester, tmp_path: Path
    ) -> None:
        bad = tmp_path / "bad.py"
        bad.write_text("def broken(:\n")
        funcs = harvester.harvest_python_functions(bad)
        assert funcs == []


# ---------------------------------------------------------------------------
# Test: Purity check
# ---------------------------------------------------------------------------


class TestPurityCheck:
    def test_pure_c_function(self, harvester: ASTHarvester) -> None:
        func = HarvestedFunction(
            name="add",
            params=[("a", "int"), ("b", "int")],
            return_type="int",
            body="{ return a + b; }",
            language="c",
            source_file="test.c",
        )
        assert harvester.is_pure(func) is True

    def test_impure_c_function_printf(self, harvester: ASTHarvester) -> None:
        func = HarvestedFunction(
            name="debug",
            params=[("x", "int")],
            return_type="void",
            body='{ printf("x=%d\\n", x); }',
            language="c",
            source_file="test.c",
        )
        assert harvester.is_pure(func) is False

    def test_impure_c_function_malloc(self, harvester: ASTHarvester) -> None:
        func = HarvestedFunction(
            name="alloc",
            params=[("n", "int")],
            return_type="void",
            body="{ int* p = malloc(n * sizeof(int)); free(p); }",
            language="c",
            source_file="test.c",
        )
        assert harvester.is_pure(func) is False

    def test_pure_python_function(self, harvester: ASTHarvester) -> None:
        func = HarvestedFunction(
            name="add",
            params=[("a", "int"), ("b", "int")],
            return_type="int",
            body="def add(a: int, b: int) -> int:\n    return a + b\n",
            language="python",
            source_file="test.py",
        )
        assert harvester.is_pure(func) is True

    def test_impure_python_function_global(
        self, harvester: ASTHarvester
    ) -> None:
        func = HarvestedFunction(
            name="inc",
            params=[("x", "int")],
            return_type="int",
            body="def inc(x: int) -> int:\n    global total\n    total += x\n    return total\n",
            language="python",
            source_file="test.py",
        )
        assert harvester.is_pure(func) is False


# ---------------------------------------------------------------------------
# Test: License detection
# ---------------------------------------------------------------------------


class TestLicenseDetection:
    def test_mit_license(self, scanner: RepoScanner, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_text(
            "MIT License\n\nPermission is hereby granted...\n"
        )
        result = scanner._detect_license(tmp_path)
        assert result == "MIT"

    def test_apache_license(self, scanner: RepoScanner, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_text(
            "Apache License\nVersion 2.0, January 2004\n"
        )
        result = scanner._detect_license(tmp_path)
        assert result == "Apache-2.0"

    def test_bsd_license(self, scanner: RepoScanner, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_text(
            "BSD 3-Clause License\n\nRedistribution...\n"
        )
        result = scanner._detect_license(tmp_path)
        assert result == "BSD-3-Clause"

    def test_unknown_license(self, scanner: RepoScanner, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_text("Some custom license text.\n")
        result = scanner._detect_license(tmp_path)
        assert result == "unknown"

    def test_no_license_file(self, scanner: RepoScanner, tmp_path: Path) -> None:
        result = scanner._detect_license(tmp_path)
        assert result == "unknown"


# ---------------------------------------------------------------------------
# Test: Repo scanner
# ---------------------------------------------------------------------------


class TestRepoScanner:
    def test_scan_repo_finds_functions(
        self, scanner: RepoScanner, mock_repo: Path
    ) -> None:
        funcs = scanner.scan_repo(mock_repo, "python")
        names = [f.name for f in funcs]
        assert "square" in names
        assert "double" in names

    def test_scan_repo_skips_test_files(
        self, scanner: RepoScanner, mock_repo: Path
    ) -> None:
        funcs = scanner.scan_repo(mock_repo, "python")
        source_files = [f.source_file for f in funcs]
        for sf in source_files:
            assert "test_" not in Path(sf).name

    def test_scan_repo_skips_vendor(
        self, scanner: RepoScanner, mock_repo: Path
    ) -> None:
        funcs = scanner.scan_repo(mock_repo, "python")
        names = [f.name for f in funcs]
        assert "vendor_func" not in names

    def test_scan_repo_detects_license(
        self, scanner: RepoScanner, mock_repo: Path
    ) -> None:
        funcs = scanner.scan_repo(mock_repo, "python")
        for func in funcs:
            assert func.license == "MIT"

    def test_unsupported_language(
        self, scanner: RepoScanner, mock_repo: Path
    ) -> None:
        funcs = scanner.scan_repo(mock_repo, "haskell")
        assert funcs == []


# ---------------------------------------------------------------------------
# Test: Transpilation
# ---------------------------------------------------------------------------


class TestTranspilation:
    def test_transpile_python_produces_toke(
        self, transpiler: ASTTranspiler
    ) -> None:
        func = HarvestedFunction(
            name="add",
            params=[("a", "int"), ("b", "int")],
            return_type="int",
            body="def add(a: int, b: int) -> int:\n    return a + b\n",
            language="python",
            source_file="test.py",
        )
        result = transpiler.transpile(func)
        assert result is not None
        assert "m=" in result
        assert "f=add(" in result
        assert "<a+b" in result

    def test_transpile_python_returns_none_on_failure(
        self, transpiler: ASTTranspiler
    ) -> None:
        func = HarvestedFunction(
            name="bad",
            params=[],
            return_type="int",
            body="this is not python",
            language="python",
            source_file="test.py",
        )
        result = transpiler.transpile(func)
        assert result is None

    def test_transpile_unsupported_language(
        self, transpiler: ASTTranspiler
    ) -> None:
        func = HarvestedFunction(
            name="foo",
            params=[],
            return_type="int",
            body="fn foo() -> i32 { 42 }",
            language="rust",
            source_file="test.rs",
        )
        result = transpiler.transpile(func)
        assert result is None


# ---------------------------------------------------------------------------
# Test: Module name sanitization
# ---------------------------------------------------------------------------


class TestModuleNameSanitization:
    def test_removes_underscores(self) -> None:
        assert _sanitize_module_name("my_func") == "myfunc"

    def test_lowercase(self) -> None:
        assert _sanitize_module_name("MyFunc") == "myfunc"

    def test_digit_prefix(self) -> None:
        result = _sanitize_module_name("123func")
        assert not result[0].isdigit()

    def test_empty_becomes_mod(self) -> None:
        assert _sanitize_module_name("___") == "mod"


# ---------------------------------------------------------------------------
# Test: JSONL output format
# ---------------------------------------------------------------------------


class TestJsonlOutputFormat:
    def test_entry_has_required_fields(self) -> None:
        from scripts.run_ast_harvest import make_entry

        func = HarvestedFunction(
            name="add",
            params=[("a", "int"), ("b", "int")],
            return_type="int",
            body="def add(a: int, b: int) -> int:\n    return a + b\n",
            language="python",
            source_file="test.py",
            license="MIT",
        )
        repo_info = {
            "url": "https://github.com/test/repo.git",
            "name": "repo",
            "language": "python",
            "license": "MIT",
        }
        entry = make_entry(func, "m=add;\nf=add(a:i64;b:i64):i64{<a+b};\n", repo_info, 1)

        assert "id" in entry
        assert "tk_source" in entry
        assert "generation_method" in entry
        assert entry["generation_method"] == "ast_harvest"
        assert "source" in entry
        assert "validation" in entry

    def test_entry_id_format(self) -> None:
        from scripts.run_ast_harvest import make_entry

        func = HarvestedFunction(
            name="add",
            params=[], return_type="int", body="",
            language="python", source_file="test.py", license="MIT",
        )
        repo_info = {
            "url": "https://github.com/test/repo.git",
            "name": "repo", "language": "python", "license": "MIT",
        }
        entry = make_entry(func, "m=add;", repo_info, 1)
        assert entry["id"].startswith("ast-python-repo-")

    def test_entry_serialises_to_json(self) -> None:
        from scripts.run_ast_harvest import make_entry

        func = HarvestedFunction(
            name="add",
            params=[], return_type="int", body="",
            language="c", source_file="test.c", license="MIT",
        )
        repo_info = {
            "url": "https://github.com/test/repo.git",
            "name": "repo", "language": "c", "license": "MIT",
        }
        entry = make_entry(func, "m=add;", repo_info, 1)
        line = json.dumps(entry)
        parsed = json.loads(line)
        assert parsed == entry


# ---------------------------------------------------------------------------
# Test: Clone and scan (mocked)
# ---------------------------------------------------------------------------


class TestCloneAndScan:
    @patch("ingest.repo_scanner.subprocess.run")
    def test_clone_and_scan_calls_git(
        self,
        mock_run: MagicMock,
        scanner: RepoScanner,
        mock_repo: Path,
    ) -> None:
        """Verify clone_and_scan calls git clone with correct args."""
        # We mock subprocess.run to prevent actual cloning
        # but also need to handle the temporary directory
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        # This will fail because the tmpdir won't have files after mock clone
        # but we can verify the git call was made
        try:
            scanner.clone_and_scan("https://github.com/test/repo.git", "python")
        except Exception:
            pass
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "git" in args
        assert "clone" in args
        assert "--depth" in args
