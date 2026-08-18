"""End-to-end transpilation pipeline: task -> Python prompt -> toke corpus entry.

Takes TaskSpecV2 tasks from the curriculum generator, produces LLM prompts for
Python solutions, parses LLM responses, transpiles to toke via PyToTokeTranspiler,
validates with tkc, and produces corpus entry dicts.
"""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from generator.curriculum_v2 import TaskSpecV2
from pipeline.task_to_python_prompt import generate_python_prompt
from transpile.py_to_toke import PyToTokeTranspiler, TranspileError

logger = logging.getLogger(__name__)

TKC_BINARY = Path("/Users/matthew.watt/tk/toke/tkc")


class TranspilePipeline:
    """Full pipeline: prompt generation -> Python parsing -> transpile -> validate."""

    def __init__(self, tkc_path: Path | None = None) -> None:
        self.tkc_path = tkc_path or TKC_BINARY
        self.transpiler = PyToTokeTranspiler()

    # -- public API ---------------------------------------------------------

    def generate_prompt(self, task: TaskSpecV2) -> str:
        """Create an LLM prompt requesting a Python solution for *task*."""
        return generate_python_prompt(task)

    def parse_python_response(self, response: str) -> str:
        """Extract Python source code from an LLM response.

        Handles:
        - Fenced markdown code blocks (```python ... ``` or ``` ... ```)
        - Raw Python code (no fences)
        - Multiple code blocks (takes the first one that looks like a function)

        Returns:
            Cleaned Python source string.

        Raises:
            ValueError: If no Python code can be extracted.
        """
        # Try fenced code blocks first
        blocks = re.findall(
            r"```(?:python)?\s*\n(.*?)```",
            response,
            re.DOTALL,
        )

        if blocks:
            # Prefer the first block containing a def statement
            for block in blocks:
                if "def " in block:
                    return block.strip() + "\n"
            # Fall back to first block
            return blocks[0].strip() + "\n"

        # No code blocks — try to find a bare function definition
        lines = response.strip().splitlines()
        func_lines: list[str] = []
        in_func = False
        for line in lines:
            if line.startswith("def "):
                in_func = True
            if in_func:
                func_lines.append(line)

        if func_lines:
            return "\n".join(func_lines).strip() + "\n"

        raise ValueError("No Python code found in LLM response")

    def transpile_to_toke(self, python_source: str, task_id: str) -> str:
        """Transpile Python source to toke using PyToTokeTranspiler.

        Args:
            python_source: Type-annotated Python function source.
            task_id: Used to derive the toke module name.

        Returns:
            toke source code string.

        Raises:
            TranspileError: If transpilation fails.
        """
        module_name = self._task_id_to_module(task_id)
        return self.transpiler.transpile(python_source, module_name)

    def validate_toke(self, toke_source: str) -> tuple[bool, str]:
        """Validate toke source using ``tkc --check``.

        Returns:
            ``(passed, diagnostic_output)``. *passed* is True when tkc exits 0.
        """
        with tempfile.NamedTemporaryFile(
            suffix=".tk", mode="w", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(toke_source)
            tmp_path = Path(tmp.name)

        try:
            result = subprocess.run(
                [str(self.tkc_path), "--check", str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return False, str(exc)
        finally:
            tmp_path.unlink(missing_ok=True)

    def process_task(
        self, task: TaskSpecV2, python_response: str
    ) -> dict | None:
        """Run the full pipeline for a single task.

        Steps: parse Python response -> transpile to toke -> validate -> build
        corpus entry.

        Args:
            task: The curriculum task specification.
            python_response: Raw LLM response containing Python code.

        Returns:
            A corpus entry dict, or None if any step fails.
        """
        # 1. Parse Python from LLM response
        try:
            python_source = self.parse_python_response(python_response)
        except ValueError as exc:
            logger.debug("Parse failed for %s: %s", task.task_id, exc)
            return None

        # 2. Transpile to toke
        try:
            toke_source = self.transpile_to_toke(python_source, task.task_id)
        except TranspileError as exc:
            logger.debug("Transpile failed for %s: %s", task.task_id, exc)
            return None

        # 3. Validate with tkc
        passed, diag = self.validate_toke(toke_source)
        if not passed:
            logger.debug(
                "tkc validation failed for %s: %s", task.task_id, diag
            )
            return None

        # 4. Build corpus entry
        tk_tokens = len(toke_source.split())
        entry = {
            "id": f"task-{task.task_id}-001",
            "tk_source": toke_source,
            "tk_tokens": tk_tokens,
            "generation_method": "transpilation_pipeline",
            "source": {
                "origin": "curriculum_v2",
                "task_id": task.task_id,
                "category": task.category,
            },
            "validation": {"tkc_check": "pass"},
            "references": {"python_source": python_source},
        }
        return entry

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _task_id_to_module(task_id: str) -> str:
        """Convert a task ID to a valid toke module name."""
        name = task_id.lower().replace("-", "").replace("_", "")
        if not name:
            name = "mod"
        if name[0].isdigit():
            name = "m" + name
        return name
