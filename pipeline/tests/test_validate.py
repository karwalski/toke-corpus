"""
Tests for pipeline.validate — Story 2.11.1.

All subprocess calls are mocked; no real tkc binary required.
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pipeline.validate import ValidationResult, validate_toke, _parse_diag_json


# ---------------------------------------------------------------------------
# _parse_diag_json unit tests
# ---------------------------------------------------------------------------

class TestParseDiagJson:
    def test_empty_input(self) -> None:
        diags, codes = _parse_diag_json("")
        assert diags == []
        assert codes == []

    def test_single_diagnostic(self) -> None:
        line = json.dumps({"code": "E1001", "message": "type mismatch"})
        diags, codes = _parse_diag_json(line)
        assert len(diags) == 1
        assert codes == ["E1001"]

    def test_multiple_diagnostics(self) -> None:
        lines = "\n".join([
            json.dumps({"code": "E1001", "message": "type error"}),
            json.dumps({"code": "E2002", "message": "undefined var"}),
        ])
        diags, codes = _parse_diag_json(lines)
        assert len(diags) == 2
        assert codes == ["E1001", "E2002"]

    def test_non_json_lines_skipped(self) -> None:
        stdout = "info: checking file...\n" + json.dumps({"code": "E1001"}) + "\ndone."
        diags, codes = _parse_diag_json(stdout)
        assert len(diags) == 1
        assert codes == ["E1001"]

    def test_diagnostic_without_code(self) -> None:
        line = json.dumps({"message": "warning", "severity": "warn"})
        diags, codes = _parse_diag_json(line)
        assert len(diags) == 1
        assert codes == []


# ---------------------------------------------------------------------------
# validate_toke with mocked subprocess
# ---------------------------------------------------------------------------

def _mock_run_success(**kwargs):
    """Return a CompletedProcess simulating tkc success."""
    return subprocess.CompletedProcess(
        args=kwargs.get("args", []),
        returncode=0,
        stdout="",
        stderr="",
    )


def _mock_run_failure(**kwargs):
    """Return a CompletedProcess simulating tkc failure."""
    diag = json.dumps({"code": "E1001", "message": "type mismatch", "line": 3})
    return subprocess.CompletedProcess(
        args=kwargs.get("args", []),
        returncode=1,
        stdout=diag,
        stderr="error: compilation failed",
    )


class TestValidateToke:
    @patch("pipeline.validate.subprocess.run")
    def test_successful_validation(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _mock_run_success()
        result = validate_toke("fn main() {}")
        assert result.passed is True
        assert result.exit_code == 0
        assert result.error_codes == []
        assert result.compile_success is True

    @patch("pipeline.validate.subprocess.run")
    def test_failed_validation(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _mock_run_failure()
        result = validate_toke("bad source")
        assert result.passed is False
        assert result.exit_code == 1
        assert "E1001" in result.error_codes
        assert len(result.diagnostics) == 1

    @patch("pipeline.validate.subprocess.run")
    def test_check_passes_compile_fails(self, mock_run: MagicMock) -> None:
        """Check pass succeeds but full compile fails."""
        def side_effect(cmd, **kwargs):
            if "--check" in cmd:
                return _mock_run_success(args=cmd)
            # full compile fails
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="link error")

        mock_run.side_effect = side_effect
        result = validate_toke("partial source")
        assert result.passed is True
        assert result.compile_success is False

    @patch("pipeline.validate.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_tkc_binary(self, mock_run: MagicMock) -> None:
        result = validate_toke("fn main() {}")
        assert result.passed is False
        assert result.exit_code == 127
        assert "E0000" in result.error_codes
        assert "not found" in result.stderr_raw

    @patch("pipeline.validate.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="tkc", timeout=30))
    def test_timeout_handling(self, mock_run: MagicMock) -> None:
        result = validate_toke("fn main() {}")
        assert result.passed is False
        assert result.exit_code == -1
        assert "ETIMEOUT" in result.error_codes
        assert "timed out" in result.stderr_raw


class TestValidationResult:
    """Sanity checks on the dataclass itself."""

    def test_default_values(self) -> None:
        r = ValidationResult(passed=True, exit_code=0)
        assert r.error_codes == []
        assert r.diagnostics == []
        assert r.compile_success is False
        assert r.stderr_raw == ""
