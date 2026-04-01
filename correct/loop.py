"""Correction loop for failed toke programs.

Resubmits programs with structured error feedback, up to max_attempts
per provider. Extracts toke source from LLM responses that may include
markdown fences and explanatory text.

Story 8.1.8 — Correction loop and escalation engine.
"""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dispatch.base import GenerateResult, ProviderClient
from generator.curriculum import TaskSpec
from validate.compiler import CompileResult
from validate.diff_test import DiffResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CorrectionAttempt:
    """Record of a single correction attempt."""

    attempt_number: int
    provider_name: str
    source: str
    compiled: bool
    diff_passed: bool
    error_output: str
    cost: float


@dataclass
class CorrectionResult:
    """Aggregate result from all correction attempts on one task."""

    task_id: str
    success: bool
    final_source: str | None
    attempts: list[CorrectionAttempt]
    escalated: bool
    total_cost: float


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------

# Matches fenced code blocks that start on their own line.
# Captures the language tag (group 1) and contents (group 2).
_FENCE_PATTERN = re.compile(
    r"^```(\w*)\s*\n(.*?)^```\s*$",
    re.DOTALL | re.MULTILINE,
)

# Matches the toke module declaration that every valid program starts with.
_MODULE_DECL_PATTERN = re.compile(r"^M=\w+;", re.MULTILINE)


def extract_toke_source(text: str) -> str:
    """Extract toke source code from an LLM response.

    Strategy:
      1. If the response contains a fenced code block, use its contents.
         If multiple fenced blocks exist, prefer the one containing ``M=``.
      2. Otherwise, find the line starting with ``M=`` and take everything
         from there to the end of the text.
      3. Fallback: return the raw text stripped of leading/trailing whitespace.
    """
    # Strategy 1: fenced code blocks.
    fenced_blocks = _FENCE_PATTERN.findall(text)
    if fenced_blocks:
        # Each match is (language_tag, contents). Filter to toke/tk/empty tags.
        toke_blocks = [
            contents
            for tag, contents in fenced_blocks
            if tag in ("", "toke", "tk")
        ]
        candidates = toke_blocks if toke_blocks else [c for _, c in fenced_blocks]

        # Prefer block that contains a module declaration.
        for block in candidates:
            if _MODULE_DECL_PATTERN.search(block):
                return block.strip()
        # No block has M=, use the first candidate.
        return candidates[0].strip()

    # Strategy 2: find M= declaration and take everything after it.
    match = _MODULE_DECL_PATTERN.search(text)
    if match:
        return text[match.start() :].strip()

    # Strategy 3: raw text.
    return text.strip()


# ---------------------------------------------------------------------------
# CorrectionLoop
# ---------------------------------------------------------------------------


class CorrectionLoop:
    """Resubmit a failed program with structured error feedback.

    Each attempt sends the LATEST failed source and its compiler
    diagnostic back to the provider for correction.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        correction_template: str = "",
    ) -> None:
        self.max_attempts = max_attempts
        self.correction_template = correction_template

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def correct(
        self,
        task: TaskSpec,
        original_source: str,
        compile_result: CompileResult,
        diff_result: DiffResult | None,
        provider: ProviderClient,
        system_prompt: str,
    ) -> CorrectionResult:
        """Run up to *max_attempts* correction rounds.

        Returns a ``CorrectionResult`` summarising all attempts.
        """
        attempts: list[CorrectionAttempt] = []
        total_cost = 0.0

        current_source = original_source
        current_error = compile_result.stderr

        # Determine expected output from diff test majority vote.
        expected_output = ""
        if diff_result is not None:
            expected_output = diff_result.majority_output

        for attempt_num in range(1, self.max_attempts + 1):
            prompt = self._build_prompt(
                task=task,
                source=current_source,
                error_output=current_error,
                expected_output=expected_output,
            )

            logger.info(
                "Correction attempt %d/%d for %s via %s",
                attempt_num,
                self.max_attempts,
                task.task_id,
                provider.name,
            )

            result: GenerateResult = await provider.generate(system_prompt, prompt)
            total_cost += result.cost

            corrected_source = extract_toke_source(result.text)

            # Validate corrected source with tkc --check.
            check_result = self._check_source(corrected_source)
            compiled = check_result.returncode == 0
            error_output = check_result.stderr if not compiled else ""

            attempt = CorrectionAttempt(
                attempt_number=attempt_num,
                provider_name=provider.name,
                source=corrected_source,
                compiled=compiled,
                diff_passed=False,  # Caller must run diff test separately.
                error_output=error_output,
                cost=result.cost,
            )
            attempts.append(attempt)

            if compiled:
                logger.info(
                    "Correction succeeded on attempt %d for %s",
                    attempt_num,
                    task.task_id,
                )
                return CorrectionResult(
                    task_id=task.task_id,
                    success=True,
                    final_source=corrected_source,
                    attempts=attempts,
                    escalated=False,
                    total_cost=total_cost,
                )

            # Feed the latest failure into the next attempt.
            current_source = corrected_source
            current_error = error_output

        # All attempts exhausted.
        logger.warning(
            "All %d correction attempts failed for %s via %s",
            self.max_attempts,
            task.task_id,
            provider.name,
        )
        return CorrectionResult(
            task_id=task.task_id,
            success=False,
            final_source=None,
            attempts=attempts,
            escalated=False,
            total_cost=total_cost,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        task: TaskSpec,
        source: str,
        error_output: str,
        expected_output: str,
    ) -> str:
        """Build correction prompt from template or sensible default."""
        if self.correction_template:
            return self.correction_template.format(
                task_description=task.description,
                original_code=source,
                diagnostic_json=error_output,
                expected_output=expected_output,
                grammar_subset="",  # Placeholder — caller may inject grammar.
            )

        # Inline fallback when no template is provided.
        parts = [
            "The following toke program failed to compile. "
            "Fix the errors and return the corrected program.",
            f"\n**Task:** {task.description}",
            f"\n**Original code:**\n```\n{source}\n```",
            f"\n**Compiler diagnostic:**\n```\n{error_output}\n```",
        ]
        if expected_output:
            parts.append(
                f"\n**Expected output:**\n```\n{expected_output}\n```"
            )
        parts.append(
            "\nOutput ONLY the corrected toke source code. "
            "No explanations, no markdown fences. "
            "The program must start with `M=` module declaration."
        )
        return "\n".join(parts)

    @staticmethod
    def _check_source(source: str) -> subprocess.CompletedProcess[str]:
        """Run ``tkc --check`` on *source* and return the process result.

        Uses a temporary file. The subprocess is synchronous because
        tkc --check completes in <50ms.
        """
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".tk",
            delete=True,
            encoding="utf-8",
        ) as tmp:
            tmp.write(source)
            tmp.flush()
            return subprocess.run(
                ["tkc", "--check", tmp.name],
                capture_output=True,
                text=True,
                timeout=10,
            )
