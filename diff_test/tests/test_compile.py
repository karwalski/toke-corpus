"""
Tests for diff_test.compile — Story 2.11.1.

All subprocess calls are mocked; no real tkc or cc binary required.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from diff_test.compile import CompileResult, compile_toke, compile_c_reference


# ---------------------------------------------------------------------------
# compile_toke tests
# ---------------------------------------------------------------------------

class TestCompileToke:
    @patch("diff_test.compile.subprocess.run")
    def test_successful_compilation(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_path = Path(tmpdir) / "toke_out"
            # Simulate the binary being created
            bin_path.touch()

            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = compile_toke("fn main() {}", "tkc", tmpdir)

            assert result.success is True
            assert result.binary_path == bin_path
            assert result.errors == ""

    @patch("diff_test.compile.subprocess.run")
    def test_compilation_failure(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error: syntax error at line 1"
            )
            result = compile_toke("bad source", "tkc", tmpdir)

            assert result.success is False
            assert result.binary_path is None
            assert "syntax error" in result.errors

    @patch("diff_test.compile.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_tkc_binary(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = compile_toke("fn main() {}", "/no/such/tkc", tmpdir)
            assert result.success is False
            assert result.binary_path is None
            assert "not found" in result.errors

    @patch("diff_test.compile.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="tkc", timeout=30))
    def test_timeout(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = compile_toke("fn main() {}", "tkc", tmpdir)
            assert result.success is False
            assert result.binary_path is None
            assert "timed out" in result.errors

    @patch("diff_test.compile.subprocess.run")
    def test_writes_source_file(self, mock_run: MagicMock) -> None:
        """Verify the source is written to disk before compilation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            compile_toke("fn main() { return 42; }", "tkc", tmpdir)

            src_path = Path(tmpdir) / "input.toke"
            assert src_path.exists()
            assert src_path.read_text() == "fn main() { return 42; }"

    @patch("diff_test.compile.subprocess.run")
    def test_binary_not_created(self, mock_run: MagicMock) -> None:
        """If tkc exits 0 but produces no binary, binary_path is None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = compile_toke("fn main() {}", "tkc", tmpdir)
            # binary_path should be None because bin_path doesn't exist
            assert result.success is True
            assert result.binary_path is None


# ---------------------------------------------------------------------------
# compile_c_reference tests
# ---------------------------------------------------------------------------

class TestCompileCReference:
    @patch("diff_test.compile.subprocess.run")
    def test_successful_c_compilation(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_path = Path(tmpdir) / "c_ref"
            bin_path.touch()

            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            result = compile_c_reference("int main() { return 0; }", tmpdir)

            assert result.success is True
            assert result.binary_path == bin_path
            assert result.errors == ""

    @patch("diff_test.compile.subprocess.run")
    def test_c_compilation_failure(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error: missing semicolon"
            )
            result = compile_c_reference("bad c code", tmpdir)

            assert result.success is False
            assert "missing semicolon" in result.errors

    @patch("diff_test.compile.subprocess.run")
    def test_extra_flags_passed(self, mock_run: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )
            compile_c_reference("int main() {}", tmpdir, extra_flags=["-lm"])

            # Check the command includes -lm
            cmd = mock_run.call_args[0][0]
            assert "-lm" in cmd

    @patch("diff_test.compile.subprocess.run")
    def test_fallback_to_gcc(self, mock_run: MagicMock) -> None:
        """When cc is not found, should try gcc."""
        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if cmd[0] == "cc":
                raise FileNotFoundError
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            bin_path = Path(tmpdir) / "c_ref"
            bin_path.touch()
            result = compile_c_reference("int main() {}", tmpdir)
            assert call_count == 2  # cc failed, then gcc

    @patch("diff_test.compile.subprocess.run", side_effect=FileNotFoundError)
    def test_no_c_compiler(self, mock_run: MagicMock) -> None:
        """When both cc and gcc are missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = compile_c_reference("int main() {}", tmpdir)
            assert result.success is False
            assert "No C compiler found" in result.errors


# ---------------------------------------------------------------------------
# CompileResult dataclass
# ---------------------------------------------------------------------------

class TestCompileResult:
    def test_basic_construction(self) -> None:
        r = CompileResult(success=True, binary_path=Path("/tmp/out"), errors="")
        assert r.success is True
        assert r.binary_path == Path("/tmp/out")

    def test_failure_construction(self) -> None:
        r = CompileResult(success=False, binary_path=None, errors="failed")
        assert r.success is False
        assert r.binary_path is None
