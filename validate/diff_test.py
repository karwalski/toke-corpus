"""Differential testing: reference outputs must agree, and toke must match them.

Phase A, as decided by the owner (docs/owner-decisions-2026-10-01.md §4.16,
story 131.87). For every declared test input (``test_inputs``; a single run on
empty stdin when none are declared):

1. The three reference implementations (Python, C, Java) run on that input's
   stdin. A reference contributes its stripped stdout when it exits 0 with
   non-empty output.
2. **Majority agreement.** Outputs are compared in canonical form
   (``canonical_output``: booleans case-folded, decimal numbers by value,
   trailing whitespace per line dropped) -- the spelling differences between
   ``print(True)``, ``System.out.println(true)`` and ``printf("%f")`` are not
   disagreements. The most common reference output must be produced
   by at least ``min_refs`` references and must not be tied with another
   output. Three different answers fail; two of three agreeing pass, and the
   dissenter is left out of ``languages_agreed``.
3. **toke runs.** The toke program is built with tkc and executed on the same
   stdin; it must exit 0 and its stripped stdout must equal the reference
   majority (canonical form again). ``toke_agrees`` is True only when it does so on every input.

A task passes only when every input passes all three steps. Before 131.87 this
module passed any task where ``min_refs`` references printed *something*:
outputs were never compared, ``test_inputs`` was ignored and ``toke_agrees``
was always True (it was compile-only).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from validate.compiler import CompileResult, CompilerValidator

logger = logging.getLogger(__name__)

LANGUAGES: list[str] = ["toke", "python", "c", "java"]
REF_LANGS: list[str] = ["python", "c", "java"]

# discarded_reason values (stable strings; callers and reports key on them)
REASON_INSUFFICIENT = "insufficient reference implementations"
REASON_REFS_DISAGREE = "reference outputs disagree"
REASON_TOKE_FAILED = "toke program failed to build or run"
REASON_TOKE_DISAGREES = "toke output disagrees with reference majority"


@dataclass
class InputResult:
    """Outcome of one test input."""

    stdin: str
    outputs: dict[str, str]
    majority_output: str
    languages_agreed: list[str]
    toke_agrees: bool
    discarded_reason: str | None = None


@dataclass
class DiffResult:
    """Aggregated result of differential testing across all test inputs.

    ``majority_output`` and ``outputs`` describe the first test input (the
    shape the corpus record stores); ``per_input`` holds every input.
    """

    passed: bool
    languages_agreed: list[str]
    majority_output: str
    outputs: dict[str, str]
    toke_agrees: bool
    discarded_reason: str | None = None
    per_input: list[InputResult] = field(default_factory=list)


_BOOL_RE = re.compile(r"\b(?:true|false)\b", re.IGNORECASE)
_DECIMAL_RE = re.compile(r"(?<![\w.])-?\d+\.\d+(?:[eE][+-]?\d+)?(?![\w.])")


def _canon_decimal(m: re.Match[str]) -> str:
    v = float(m.group(0))
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


def canonical_output(text: str) -> str:
    """The comparison form of a program's stdout.

    Only spelling differences between languages are folded: ``True``/``true``/
    ``TRUE`` -> ``true``, a decimal literal -> its value (``2.500000`` and
    ``2.5`` -> ``2.5``, ``3.0`` -> ``3``), trailing spaces per line and leading/
    trailing blank lines dropped. ``0``/``1`` are NOT read as booleans and
    precision differences (``0.333333`` vs ``0.3333333333333333``) still differ.
    """
    lines = [_DECIMAL_RE.sub(_canon_decimal, _BOOL_RE.sub(lambda m: m.group(0).lower(), ln)).rstrip()
             for ln in text.strip().splitlines()]
    return "\n".join(lines)


def reference_majority(
    outputs: dict[str, str], min_refs: int,
) -> tuple[str | None, list[str], str | None]:
    """(majority_output, agreeing_languages, failure_reason) over reference outputs.

    *outputs* maps a reference language to its stripped stdout ("" when it
    failed or printed nothing). Outputs are grouped by ``canonical_output``;
    the majority returned is the raw output of the first agreeing language in
    *outputs* order. Pure, so a stored record can be re-judged.
    """
    produced = {lang: out for lang, out in outputs.items() if out}
    if len(produced) < min_refs:
        return None, sorted(produced), REASON_INSUFFICIENT
    canon = {lang: canonical_output(out) for lang, out in produced.items()}
    counts = Counter(canon.values()).most_common()
    top, n = counts[0]
    if n < min_refs or (len(counts) > 1 and counts[1][1] == n):
        return None, [], REASON_REFS_DISAGREE
    agreed = [lang for lang in produced if canon[lang] == top]
    return produced[agreed[0]], sorted(agreed), None


class DifferentialTester:
    """Run references and toke on every test input and compare outputs."""

    def __init__(self, compiler: CompilerValidator) -> None:
        self.compiler = compiler

    async def _one_input(
        self,
        toke_src: str,
        python_src: str,
        c_src: str,
        java_src: str,
        stdin: str,
        min_refs: int,
    ) -> InputResult:
        ref_results: list[CompileResult] = await asyncio.gather(
            self.compiler.run_python(python_src, stdin),
            self.compiler.run_c(c_src, stdin),
            self.compiler.run_java(java_src, stdin),
        )
        outputs: dict[str, str] = {}
        for lang, result in zip(REF_LANGS, ref_results):
            outputs[lang] = result.stdout.strip() if result.success else ""

        majority, agreed, reason = reference_majority(outputs, min_refs)
        if majority is None:
            logger.info("reference check failed (%s): %s", reason, outputs)
            return InputResult(stdin, {"toke": "", **outputs}, "", agreed, False, reason)

        toke = await self.compiler.run_toke(toke_src, stdin)
        toke_out = toke.stdout.strip() if toke.success else ""
        outputs = {"toke": toke_out, **outputs}
        if not toke.success:
            return InputResult(stdin, outputs, majority, agreed, False, REASON_TOKE_FAILED)
        if canonical_output(toke_out) != canonical_output(majority):
            return InputResult(stdin, outputs, majority, agreed, False, REASON_TOKE_DISAGREES)
        return InputResult(stdin, outputs, majority, agreed, True, None)

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
        """Differentially test one task over every declared test input.

        Stops at the first failing input (later inputs cannot rescue it).
        """
        inputs = test_inputs or [{"stdin": ""}]
        per_input: list[InputResult] = []
        for inp in inputs:
            r = await self._one_input(toke_src, python_src, c_src, java_src,
                                      inp.get("stdin", ""), min_refs)
            per_input.append(r)
            if r.discarded_reason is not None:
                break

        first = per_input[0]
        failed = next((r for r in per_input if r.discarded_reason is not None), None)
        agreed = set(first.languages_agreed)
        for r in per_input[1:]:
            agreed &= set(r.languages_agreed)
        return DiffResult(
            passed=failed is None,
            languages_agreed=sorted(agreed),
            majority_output=first.majority_output,
            outputs=first.outputs,
            toke_agrees=failed is None,
            discarded_reason=failed.discarded_reason if failed else None,
            per_input=per_input,
        )
