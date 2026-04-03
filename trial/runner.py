"""Capability trial runner for model evaluation.

Sends a fixed set of trial tasks to every candidate provider, validates
responses with ``tkc --check``, and records per-task metrics. A single
correction attempt is made on first-pass failures to measure correction
success rate.

Story 8.1.5 — Model capability trial framework.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dispatch.base import GenerateResult, ProviderClient
from generator.curriculum import CurriculumGenerator, TaskSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(
    r"```(?:toke|tk)?\s*\n(.*?)```",
    re.DOTALL,
)

_MODULE_RE = re.compile(r"^M=\w+;", re.MULTILINE)


@dataclass(frozen=True)
class TrialTaskResult:
    """Result of a single provider x task trial evaluation."""

    task_id: str
    provider_name: str
    compiled: bool
    correction_compiled: bool
    input_tokens: int
    output_tokens: int
    cost: float
    latency_ms: float
    error_output: str | None


@dataclass
class TrialResults:
    """Aggregated results from a full trial run."""

    results: list[TrialTaskResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# Source extraction helpers
# ---------------------------------------------------------------------------


def extract_toke_source(raw: str) -> str:
    """Extract toke source from model output.

    Handles markdown fences, preamble text, and trailing commentary.
    Returns the cleaned toke source ready for compilation.
    """
    # Try extracting from markdown fences first.
    match = _FENCE_RE.search(raw)
    if match:
        return match.group(1).strip()

    # No fences — look for the first line starting with M= (module decl)
    # and take everything from there.
    mod_match = _MODULE_RE.search(raw)
    if mod_match:
        return raw[mod_match.start():].strip()

    # Last resort: return the whole thing stripped.
    return raw.strip()


# ---------------------------------------------------------------------------
# Compiler validation
# ---------------------------------------------------------------------------


def validate_toke(source: str, tkc_path: str) -> tuple[bool, str | None]:
    """Write *source* to a temp file and run ``tkc --check``.

    Returns ``(passed, error_output)``. ``error_output`` is ``None`` when
    the program compiles cleanly.
    """
    with tempfile.NamedTemporaryFile(
        suffix=".tk", mode="w", delete=True
    ) as tmp:
        tmp.write(source)
        tmp.flush()
        result = subprocess.run(
            [tkc_path, "--check", tmp.name],
            capture_output=True,
            text=True,
            timeout=10,
        )
    if result.returncode == 0:
        return True, None
    error_text = (result.stderr or result.stdout).strip()
    return False, error_text


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


def _format_prompt(
    template: str, spec: TaskSpec
) -> str:
    """Fill the generation template with task-specific values."""
    return template.format(
        category=spec.category,
        task_description=spec.description,
        expected_signature=spec.expected_signature,
    )


def _correction_prompt(
    original_source: str,
    error_output: str,
    spec: TaskSpec,
) -> str:
    """Build a correction prompt from a failed first-pass attempt."""
    return (
        f"Your toke program for task {spec.task_id} produced the following "
        f"compiler error:\n\n{error_output}\n\n"
        f"The original source was:\n```toke\n{original_source}\n```\n\n"
        f"Rewrite the toke program to fix these errors. "
        f"Output ONLY the corrected toke source code, no explanations."
    )


# ---------------------------------------------------------------------------
# TrialRunner
# ---------------------------------------------------------------------------


class TrialRunner:
    """Run a capability trial across providers and task specs.

    For each (provider, task) pair the runner:
    1. Generates toke code via the provider.
    2. Validates with ``tkc --check``.
    3. On failure, sends a correction prompt (1 retry) and re-validates.
    """

    def __init__(
        self,
        providers: list[ProviderClient],
        task_specs: list[TaskSpec],
        tkc_path: str,
        system_prompt: str,
        generate_template: str,
        concurrency: int = 20,
    ) -> None:
        self._providers = providers
        self._task_specs = task_specs
        self._tkc_path = tkc_path
        self._system_prompt = system_prompt
        self._generate_template = generate_template
        self._semaphore = asyncio.Semaphore(concurrency)

    # -- public API ---------------------------------------------------------

    async def run(self) -> TrialResults:
        """Execute the full trial, one provider at a time to limit memory."""
        trial = TrialResults(
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        total_pairs = len(self._providers) * len(self._task_specs)
        logger.info(
            "Starting trial: %d providers x %d tasks = %d evaluations",
            len(self._providers),
            len(self._task_specs),
            total_pairs,
        )

        batch_size = self._semaphore._value

        for provider in self._providers:
            logger.info(
                "Trial: starting provider %s (%d tasks)",
                provider.name,
                len(self._task_specs),
            )
            for i in range(0, len(self._task_specs), batch_size):
                batch_specs = self._task_specs[i:i + batch_size]
                tasks = [
                    asyncio.create_task(self._evaluate(provider, spec))
                    for spec in batch_specs
                ]
                completed = await asyncio.gather(*tasks, return_exceptions=True)
                for item in completed:
                    if isinstance(item, BaseException):
                        logger.error("Trial task raised exception: %s", item)
                        continue
                    trial.results.append(item)
                logger.info(
                    "Trial: %s batch %d-%d done (%d results so far)",
                    provider.name, i, i + len(batch_specs), len(trial.results),
                )

        trial.finished_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Trial complete: %d results in %s -> %s",
            len(trial.results),
            trial.started_at,
            trial.finished_at,
        )
        return trial

    # -- internals ----------------------------------------------------------

    async def _evaluate(
        self,
        provider: ProviderClient,
        spec: TaskSpec,
    ) -> TrialTaskResult:
        """Evaluate a single provider x task pair with optional correction."""
        async with self._semaphore:
            prompt = _format_prompt(self._generate_template, spec)

            total_input = 0
            total_output = 0
            total_cost = 0.0

            t0 = time.monotonic()

            # -- first pass --------------------------------------------------
            try:
                gen_result = await provider.generate(
                    self._system_prompt, prompt
                )
            except Exception:
                logger.exception(
                    "Provider %s failed on task %s",
                    provider.name,
                    spec.task_id,
                )
                elapsed = (time.monotonic() - t0) * 1000
                return TrialTaskResult(
                    task_id=spec.task_id,
                    provider_name=provider.name,
                    compiled=False,
                    correction_compiled=False,
                    input_tokens=0,
                    output_tokens=0,
                    cost=0.0,
                    latency_ms=elapsed,
                    error_output="provider exception",
                )

            total_input += gen_result.input_tokens
            total_output += gen_result.output_tokens
            total_cost += gen_result.cost

            source = extract_toke_source(gen_result.text)
            compiled, error_output = validate_toke(source, self._tkc_path)

            # -- correction pass (1 retry on failure) -------------------------
            correction_compiled = False
            if not compiled and error_output:
                correction_prompt = _correction_prompt(
                    source, error_output, spec
                )
                try:
                    corr_result = await provider.generate(
                        self._system_prompt, correction_prompt
                    )
                    total_input += corr_result.input_tokens
                    total_output += corr_result.output_tokens
                    total_cost += corr_result.cost

                    corr_source = extract_toke_source(corr_result.text)
                    correction_compiled, corr_error = validate_toke(
                        corr_source, self._tkc_path
                    )
                    if not correction_compiled:
                        # Keep original error for reporting.
                        error_output = corr_error or error_output
                except Exception:
                    logger.exception(
                        "Correction failed for %s on task %s",
                        provider.name,
                        spec.task_id,
                    )

            elapsed = (time.monotonic() - t0) * 1000

            return TrialTaskResult(
                task_id=spec.task_id,
                provider_name=provider.name,
                compiled=compiled,
                correction_compiled=correction_compiled,
                input_tokens=total_input,
                output_tokens=total_output,
                cost=total_cost,
                latency_ms=elapsed,
                error_output=error_output if not compiled else None,
            )

    # -- serialisation -------------------------------------------------------

    @staticmethod
    def save_results(results: TrialResults, path: Path) -> None:
        """Write trial results to a JSON file."""
        data = {
            "started_at": results.started_at,
            "finished_at": results.finished_at,
            "count": len(results.results),
            "results": [asdict(r) for r in results.results],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
        logger.info("Trial results saved to %s", path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _main(argv: list[str] | None = None) -> None:
    """CLI entry point for standalone trial execution."""
    parser = argparse.ArgumentParser(
        description="Run a model capability trial for toke corpus generation."
    )
    parser.add_argument(
        "--tasks",
        type=int,
        default=500,
        help="Number of trial tasks to generate (default: 500).",
    )
    parser.add_argument(
        "--providers",
        type=str,
        required=True,
        help="Comma-separated list of provider names to trial.",
    )
    parser.add_argument(
        "--tkc",
        type=str,
        default="tkc",
        help="Path to the tkc compiler binary (default: 'tkc').",
    )
    parser.add_argument(
        "--system-prompt",
        type=str,
        default="prompts/system.md",
        help="Path to the system prompt file.",
    )
    parser.add_argument(
        "--generate-template",
        type=str,
        default="prompts/generate_toke.md",
        help="Path to the generation prompt template.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Max concurrent API calls (default: 20).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="metrics/trial_results.json",
        help="Output path for trial results JSON.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for curriculum generation (default: 42).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Generate trial task specs from the curriculum.
    curriculum = CurriculumGenerator(seed=args.seed, total_tasks=args.tasks)
    task_specs = curriculum.generate()
    logger.info("Generated %d trial task specs", len(task_specs))

    # Provider instantiation is deferred — in standalone mode we print
    # what would happen and exit. Real provider wiring is done by the
    # orchestrator which passes constructed ProviderClient instances.
    requested = [p.strip() for p in args.providers.split(",")]
    logger.info(
        "Requested providers: %s. "
        "Provider construction must be handled by the orchestrator. "
        "Use TrialRunner programmatically with constructed ProviderClient "
        "instances.",
        requested,
    )

    # Read prompt files.
    system_prompt = Path(args.system_prompt).read_text()
    generate_template = Path(args.generate_template).read_text()

    logger.info(
        "Trial configured: %d tasks, %d providers, concurrency=%d, "
        "output=%s",
        len(task_specs),
        len(requested),
        args.concurrency,
        args.output,
    )
    logger.info(
        "To run the trial, construct ProviderClient instances and call "
        "TrialRunner.run() programmatically."
    )


if __name__ == "__main__":
    asyncio.run(_main())
