"""Compiler validation for all four corpus languages.

Runs tkc, gcc, python3, and javac against generated source code,
capturing exit codes, stdout/stderr, and timing. All subprocess
invocations use asyncio with enforced timeouts and automatic
temp-file cleanup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default timeout for compilation and execution (seconds).
DEFAULT_TIMEOUT_S: int = 10


@dataclass
class CompileResult:
    """Result of compiling and optionally running a single program."""

    language: str
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    binary_path: str | None = None


class CompilerValidator:
    """Validate generated source in toke, Python, C, and Java."""

    def __init__(
        self,
        tkc_path: str,
        gcc_path: str = "gcc",
        python_path: str = "python3",
        javac_path: str = "javac",
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.tkc_path = tkc_path
        self.gcc_path = gcc_path
        self.python_path = python_path
        self.javac_path = javac_path
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run(
        self,
        args: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str, float]:
        """Run a subprocess, enforcing a timeout.

        Returns (exit_code, stdout, stderr, duration_ms).
        """
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_s,
            )
            elapsed = (time.monotonic() - start) * 1000.0
            return (
                proc.returncode or 0,
                stdout_bytes.decode(errors="replace"),
                stderr_bytes.decode(errors="replace"),
                elapsed,
            )
        except asyncio.TimeoutError:
            # Kill the process on timeout.
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except ProcessLookupError:
                pass
            elapsed = (time.monotonic() - start) * 1000.0
            return (
                -1,
                "",
                f"timeout after {self.timeout_s}s",
                elapsed,
            )

    @staticmethod
    def _write_temp(
        tmpdir: str,
        filename: str,
        source: str,
    ) -> str:
        """Write *source* to a file inside *tmpdir* and return the path."""
        path = os.path.join(tmpdir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        return path

    # ------------------------------------------------------------------
    # Per-language validators
    # ------------------------------------------------------------------

    async def validate_toke(self, source: str) -> CompileResult:
        """Syntax/type-check a toke source string via ``tkc --check``."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = self._write_temp(tmpdir, "program.tk", source)
            exit_code, stdout, stderr, ms = await self._run(
                [self.tkc_path, "--check", src_path],
            )
            return CompileResult(
                language="toke",
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=ms,
            )

    async def validate_python(self, source: str) -> CompileResult:
        """Syntax-check a Python source string by compiling to bytecode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = self._write_temp(tmpdir, "program.py", source)
            # Use -m py_compile for a syntax-only check.
            exit_code, stdout, stderr, ms = await self._run(
                [self.python_path, "-m", "py_compile", src_path],
            )
            return CompileResult(
                language="python",
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=ms,
            )

    async def validate_c(self, source: str) -> CompileResult:
        """Compile a C source string via gcc."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = self._write_temp(tmpdir, "program.c", source)
            out_path = os.path.join(tmpdir, "program")
            exit_code, stdout, stderr, ms = await self._run(
                [self.gcc_path, "-o", out_path, src_path],
            )
            binary = out_path if exit_code == 0 and os.path.isfile(out_path) else None
            return CompileResult(
                language="c",
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=ms,
                binary_path=binary,
            )

    async def validate_java(self, source: str) -> CompileResult:
        """Compile a Java source string via javac.

        The public class name is extracted from the source so that the
        file can be named correctly (Java requires filename == class name).
        """
        class_name = self._extract_java_class(source)
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = f"{class_name}.java"
            src_path = self._write_temp(tmpdir, filename, source)
            exit_code, stdout, stderr, ms = await self._run(
                [self.javac_path, src_path],
            )
            class_file = os.path.join(tmpdir, f"{class_name}.class")
            binary = class_file if exit_code == 0 and os.path.isfile(class_file) else None
            return CompileResult(
                language="java",
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=ms,
                binary_path=binary,
            )

    async def validate_all(
        self,
        toke_src: str,
        python_src: str,
        c_src: str,
        java_src: str,
    ) -> dict[str, CompileResult]:
        """Validate all four languages in parallel."""
        toke_r, py_r, c_r, java_r = await asyncio.gather(
            self.validate_toke(toke_src),
            self.validate_python(python_src),
            self.validate_c(c_src),
            self.validate_java(java_src),
        )
        return {
            "toke": toke_r,
            "python": py_r,
            "c": c_r,
            "java": java_r,
        }

    # ------------------------------------------------------------------
    # Execution helpers (used by DifferentialTester)
    # ------------------------------------------------------------------

    async def run_toke(
        self,
        source: str,
        stdin_data: str = "",
    ) -> CompileResult:
        """Compile a toke program to a binary and execute it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = self._write_temp(tmpdir, "program.tk", source)
            out_path = os.path.join(tmpdir, "program")

            # Compile to binary.
            exit_code, stdout, stderr, ms_compile = await self._run(
                [self.tkc_path, "--out", "binary", src_path, "-o", out_path],
            )
            if exit_code != 0:
                return CompileResult(
                    language="toke",
                    success=False,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=ms_compile,
                )

            # Execute the binary.
            exit_code, stdout, stderr, ms_run = await self._run_with_stdin(
                [out_path],
                stdin_data,
            )
            return CompileResult(
                language="toke",
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=ms_compile + ms_run,
                binary_path=out_path,
            )

    async def run_python(
        self,
        source: str,
        stdin_data: str = "",
    ) -> CompileResult:
        """Run a Python program and capture output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = self._write_temp(tmpdir, "program.py", source)
            exit_code, stdout, stderr, ms = await self._run_with_stdin(
                [self.python_path, src_path],
                stdin_data,
            )
            return CompileResult(
                language="python",
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=ms,
            )

    async def run_c(
        self,
        source: str,
        stdin_data: str = "",
    ) -> CompileResult:
        """Compile and run a C program."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = self._write_temp(tmpdir, "program.c", source)
            out_path = os.path.join(tmpdir, "program")

            exit_code, stdout, stderr, ms_compile = await self._run(
                [self.gcc_path, "-o", out_path, src_path],
            )
            if exit_code != 0:
                return CompileResult(
                    language="c",
                    success=False,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=ms_compile,
                )

            exit_code, stdout, stderr, ms_run = await self._run_with_stdin(
                [out_path],
                stdin_data,
            )
            return CompileResult(
                language="c",
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=ms_compile + ms_run,
                binary_path=out_path,
            )

    async def run_java(
        self,
        source: str,
        stdin_data: str = "",
    ) -> CompileResult:
        """Compile and run a Java program."""
        class_name = self._extract_java_class(source)
        with tempfile.TemporaryDirectory() as tmpdir:
            filename = f"{class_name}.java"
            src_path = self._write_temp(tmpdir, filename, source)

            exit_code, stdout, stderr, ms_compile = await self._run(
                [self.javac_path, src_path],
            )
            if exit_code != 0:
                return CompileResult(
                    language="java",
                    success=False,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=ms_compile,
                )

            exit_code, stdout, stderr, ms_run = await self._run_with_stdin(
                ["java", "-cp", tmpdir, class_name],
                stdin_data,
            )
            return CompileResult(
                language="java",
                success=exit_code == 0,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=ms_compile + ms_run,
            )

    # ------------------------------------------------------------------
    # Shared subprocess runner with stdin support
    # ------------------------------------------------------------------

    async def _run_with_stdin(
        self,
        args: list[str],
        stdin_data: str = "",
        cwd: str | None = None,
    ) -> tuple[int, str, str, float]:
        """Run a subprocess, feeding *stdin_data* to its stdin."""
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode()),
                timeout=self.timeout_s,
            )
            elapsed = (time.monotonic() - start) * 1000.0
            return (
                proc.returncode or 0,
                stdout_bytes.decode(errors="replace"),
                stderr_bytes.decode(errors="replace"),
                elapsed,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except ProcessLookupError:
                pass
            elapsed = (time.monotonic() - start) * 1000.0
            return (
                -1,
                "",
                f"timeout after {self.timeout_s}s",
                elapsed,
            )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_java_class(source: str) -> str:
        """Extract the public class name from Java source.

        Falls back to ``Program`` if no public class declaration is found.
        """
        match = re.search(r"public\s+class\s+(\w+)", source)
        return match.group(1) if match else "Program"
