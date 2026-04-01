"""Differential testing via reference language compilation.

Since tkc cannot yet produce executables, validation is:
1. toke passes tkc --check (handled by CompilerValidator)
2. Reference implementations (Python, C, Java) compile/run successfully
3. At least 2 of 3 reference languages produce non-empty output

This is Phase A validation — full 4-language output comparison
will be enabled when tkc gains execution support.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from validate.compiler import CompileResult, CompilerValidator

logger = logging.getLogger(__name__)

LANGUAGES: list[str] = ["toke", "python", "c", "java"]


@dataclass
class DiffResult:
    """Aggregated result of differential testing across all test inputs."""

    passed: bool
    languages_agreed: list[str]
    majority_output: str
    outputs: dict[str, str]
    toke_agrees: bool
    discarded_reason: str | None = None


class DifferentialTester:
    """Validate reference implementations compile and run."""

    def __init__(self, compiler: CompilerValidator) -> None:
        self.compiler = compiler

    async def test(
        self,
        toke_src: str,
        python_src: str,
        c_src: str,
        java_src: str,
        test_inputs: list[dict[str, str]],
        *,
        min_refs: int = 2,
    ) -> DiffResult:
        """Run reference impls and check they produce output.

        Phase A validation: at least *min_refs* of 3 reference languages
        must compile and produce non-empty stdout. Toke is compile-only.
        """
        # Run the 3 reference implementations
        ref_results: list[CompileResult] = await asyncio.gather(
            self.compiler.run_python(python_src),
            self.compiler.run_c(c_src),
            self.compiler.run_java(java_src),
        )

        ref_langs = ["python", "c", "java"]
        outputs: dict[str, str] = {"toke": "(compile-only)"}
        succeeded: list[str] = []

        for lang, result in zip(ref_langs, ref_results):
            stdout = result.stdout.strip() if result.success else ""
            outputs[lang] = stdout
            if result.success and stdout:
                succeeded.append(lang)

        if len(succeeded) < min_refs:
            logger.info(
                "Only %d of 3 reference languages produced output: %s",
                len(succeeded),
                succeeded,
            )
            return DiffResult(
                passed=False,
                languages_agreed=succeeded,
                majority_output="",
                outputs=outputs,
                toke_agrees=False,
                discarded_reason="insufficient reference implementations",
            )

        # Use the first successful output as the "majority"
        majority = outputs[succeeded[0]]

        return DiffResult(
            passed=True,
            languages_agreed=sorted(succeeded),
            majority_output=majority,
            outputs=outputs,
            toke_agrees=True,
        )
