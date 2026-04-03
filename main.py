"""Orchestrator main loop for toke corpus generation.

Single entry point that runs the complete pipeline from curriculum
generation through corpus packaging. Wires together all component
modules: curriculum, dispatch, trial, validation, correction, and
storage.

Story 8.1.10 -- Orchestrator main loop and end-to-end integration.

Usage::

    python main.py --config config.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any

import yaml

from correct.escalate import EscalationEngine
from correct.loop import CorrectionLoop, extract_toke_source
from dispatch.anthropic import AnthropicClient
from dispatch.base import CostTracker, GenerateResult, ProviderClient
from dispatch.deepseek import DeepSeekClient
from dispatch.gemini import GeminiClient
from dispatch.openai import OpenAIClient
from dispatch.xai import XAIClient
from dispatch.pool import PoolConfig, PoolManager
from generator.curriculum import CATEGORIES, CurriculumGenerator, TaskSpec
from store.checkpoint import Checkpoint
from store.metrics import MetricsCollector
from store.writer import CorpusWriter, count_tokens
from trial.runner import TrialRunner
from trial.scorer import PoolAllocation, TrialScorer
from validate.compiler import CompilerValidator
from validate.dedup import Deduplicator
from validate.diff_test import DifferentialTester
from validate.autofixer import AutoFixer
from validate.quality import QualityScorer
from transpile.c_to_toke import CToTokeTranspiler, TranspileError


def compact_toke(src: str) -> str:
    """Remove all non-essential whitespace from toke source."""
    result = []
    in_str = False
    i = 0
    while i < len(src):
        c = src[i]
        if c == '"' and (i == 0 or src[i - 1] != '\\'):
            in_str = not in_str
            result.append(c)
        elif in_str:
            result.append(c)
        elif c in ' \t\n\r':
            if result and i + 1 < len(src):
                prev = result[-1]
                nxt = src[i + 1]
                if (prev.isalnum() or prev == '_') and (nxt.isalnum() or nxt == '_'):
                    result.append(' ')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

_PROMPT_FILES: list[str] = [
    "system.md",
    "generate_toke.md",
    "generate_python.md",
    "generate_c.md",
    "generate_java.md",
    "generate_tests.md",
    "correct.md",
]


def load_prompts(prompts_dir: str) -> dict[str, str]:
    """Load all prompt templates from the prompts directory."""
    prompts: dict[str, str] = {}
    pdir = Path(prompts_dir)
    for filename in _PROMPT_FILES:
        path = pdir / filename
        if path.is_file():
            prompts[filename.removesuffix(".md")] = path.read_text(encoding="utf-8")
        else:
            logger.warning("Prompt file not found: %s", path)

    # Build category-specific system prompts from base + category addendum.
    base_path = pdir / "system_base.md"
    if base_path.is_file():
        base_text = base_path.read_text(encoding="utf-8")
        cat_dir = pdir / "category"
        for cat in CATEGORIES:
            cat_path = cat_dir / f"{cat}.md"
            if cat_path.is_file():
                cat_text = cat_path.read_text(encoding="utf-8")
                prompts[f"system_{cat}"] = base_text + "\n\n" + cat_text
            else:
                prompts[f"system_{cat}"] = base_text
        logger.info(
            "Loaded category-specific system prompts for %d categories",
            len(CATEGORIES),
        )

    return prompts


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------

_PROVIDER_CONSTRUCTORS: dict[str, type[ProviderClient]] = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "xai": XAIClient,
    "gemini": GeminiClient,
    "deepseek": DeepSeekClient,
}


def build_providers(
    config: dict[str, Any],
) -> dict[str, ProviderClient]:
    """Construct provider clients from config.

    API keys are read from environment variables by each provider's
    __init__.  Config supplies model name, tier, and cost rates.
    """
    providers: dict[str, ProviderClient] = {}
    providers_cfg: dict[str, Any] = config.get("providers", {})

    for name, pcfg in providers_cfg.items():
        cls = _PROVIDER_CONSTRUCTORS.get(name)
        if cls is None:
            logger.warning("Unknown provider '%s' in config -- skipping", name)
            continue

        try:
            providers[name] = cls(
                model=pcfg.get("model", ""),
                tier=pcfg.get("tier", 1),
                cost_per_input_mtok=pcfg.get("cost_input", 0.0),
                cost_per_output_mtok=pcfg.get("cost_output", 0.0),
            )
            logger.info(
                "Initialised provider %s (model=%s, tier=%d)",
                name,
                pcfg.get("model"),
                pcfg.get("tier", 1),
            )
        except (ValueError, KeyError) as exc:
            logger.error("Failed to initialise provider %s: %s", name, exc)

    return providers


# ---------------------------------------------------------------------------
# Prompt formatting helpers
# ---------------------------------------------------------------------------


def format_toke_prompt(template: str, task: TaskSpec) -> str:
    """Format the toke generation prompt for a task."""
    return template.format(
        category=task.category,
        task_description=task.description,
        expected_signature=task.expected_signature,
    )


def format_ref_prompt(template: str, task: TaskSpec) -> str:
    """Format a reference implementation prompt (Python/C/Java)."""
    return template.format(
        category=task.category,
        task_description=task.description,
        expected_signature=task.expected_signature,
    )


def format_tests_prompt(template: str, task: TaskSpec) -> str:
    """Format the test inputs generation prompt."""
    return template.format(
        category=task.category,
        task_description=task.description,
        expected_signature=task.expected_signature,
    )


# ---------------------------------------------------------------------------
# Source extraction from LLM responses
# ---------------------------------------------------------------------------

# Re-use the extraction function from correct.loop for toke.
# For reference implementations, we do a simpler extraction.

_CODE_FENCE_RE = re.compile(r"```\w*\s*\n(.*?)```", re.DOTALL)


def _extract_fenced(text: str) -> str:
    """Extract source from a fenced code block, or return raw text."""
    match = _CODE_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _extract_test_inputs(text: str) -> list[dict[str, str]]:
    """Parse test inputs JSON from LLM response."""
    # Try to find JSON array in fenced block or raw text.
    raw = _extract_fenced(text)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            # Convert to the format expected by DifferentialTester:
            # list of dicts with optional "stdin" key.
            inputs: list[dict[str, str]] = []
            for item in data:
                if isinstance(item, dict):
                    # Build stdin from inputs array.
                    item_inputs = item.get("inputs", [])
                    stdin_val = "\n".join(str(v) for v in item_inputs)
                    inputs.append({"stdin": stdin_val})
            return inputs if inputs else [{"stdin": ""}]
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse test inputs JSON from LLM response")
    return [{"stdin": ""}]


# ---------------------------------------------------------------------------
# Deferred failure logging for batch analysis
# ---------------------------------------------------------------------------

_DEFERRED_LOCK = asyncio.Lock() if hasattr(asyncio, "Lock") else None


def _log_deferred_failure(
    task: TaskSpec,
    source: str,
    compile_result: Any,
    model: str,
    category: str,
    log_dir: str,
) -> None:
    """Append failed task to JSONL for later pattern analysis."""
    from pathlib import Path as _Path

    deferred_path = _Path(log_dir) / "deferred_failures.jsonl"
    entry = json.dumps(
        {
            "task_id": task.task_id,
            "category": category,
            "model": model,
            "description": task.description,
            "source": source,
            "stderr": compile_result.stderr if hasattr(compile_result, "stderr") else "",
            "exit_code": compile_result.exit_code if hasattr(compile_result, "exit_code") else -1,
        },
        ensure_ascii=False,
    )
    try:
        with open(deferred_path, "a", encoding="utf-8") as fh:
            fh.write(entry + "\n")
    except OSError:
        logger.debug("Could not write deferred failure for %s", task.task_id)


# ---------------------------------------------------------------------------
# Pool configuration from trial or config
# ---------------------------------------------------------------------------


def pool_config_from_allocation(
    allocation: PoolAllocation,
    config: dict[str, Any],
) -> PoolConfig:
    """Build a PoolConfig from trial-based allocation."""
    pool_cfg = config.get("pool", {})
    return PoolConfig(
        tier1_providers=list(allocation.tier1),
        tier2_providers=list(allocation.tier2),
        tier2_minimum_pct=allocation.tier2_minimum_pct,
        category_tier2_overrides=pool_cfg.get("category_overrides", {}),
    )


def pool_config_from_config(
    providers: dict[str, ProviderClient],
    config: dict[str, Any],
) -> PoolConfig:
    """Build a PoolConfig from static config (when trial is skipped)."""
    pool_cfg = config.get("pool", {})
    tier2_min = pool_cfg.get("tier2_minimum_pct", 0.40)

    tier1: list[tuple[str, float]] = []
    tier2: list[tuple[str, float]] = []

    providers_cfg: dict[str, Any] = config.get("providers", {})
    for name, pcfg in providers_cfg.items():
        if name not in providers:
            continue
        tier = pcfg.get("tier", 1)
        if tier == 2:
            tier2.append((name, 1.0))
        else:
            tier1.append((name, 1.0))

    # Normalise fractions within each tier.
    if tier1:
        frac = 1.0 / len(tier1)
        tier1 = [(n, frac) for n, _ in tier1]
    if tier2:
        frac = 1.0 / len(tier2)
        tier2 = [(n, frac) for n, _ in tier2]

    return PoolConfig(
        tier1_providers=tier1,
        tier2_providers=tier2,
        tier2_minimum_pct=tier2_min,
        category_tier2_overrides=pool_cfg.get("category_overrides", {}),
    )


# ---------------------------------------------------------------------------
# Tier 1 provider selection helper
# ---------------------------------------------------------------------------


def get_tier1_provider(providers: dict[str, ProviderClient]) -> ProviderClient | None:
    """Return the first Tier 1 provider, or any provider as fallback."""
    for p in providers.values():
        if p.tier == 1:
            return p
    # Fallback to first available.
    return next(iter(providers.values()), None)


def get_tier2_providers(providers: dict[str, ProviderClient]) -> list[ProviderClient]:
    """Return all Tier 2 providers."""
    return [p for p in providers.values() if p.tier == 2]


# ---------------------------------------------------------------------------
# Single task processing
# ---------------------------------------------------------------------------


async def process_task(
    task: TaskSpec,
    prompts: dict[str, str],
    pool: PoolManager,
    compiler: CompilerValidator,
    diff_tester: DifferentialTester,
    quality_scorer: QualityScorer,
    dedup: Deduplicator,
    correction_loop: CorrectionLoop,
    escalation: EscalationEngine,
    writer: CorpusWriter,
    metrics: MetricsCollector,
    cost_tracker: CostTracker,
    providers: dict[str, ProviderClient],
    config: dict[str, Any],
    completed_ids: set[str],
) -> bool:
    """Process a single task through the full pipeline.

    Returns True if the task was accepted, False otherwise.
    """
    # Use category-specific system prompt for toke generation (smaller, focused).
    # Fall back to full system prompt if category-specific not available.
    category = task.category
    system_prompt = prompts.get(f"system_{category}", prompts.get("system", ""))
    toke_template = prompts.get("generate_toke", "")
    python_template = prompts.get("generate_python", "")
    c_template = prompts.get("generate_c", "")
    java_template = prompts.get("generate_java", "")
    tests_template = prompts.get("generate_tests", "")

    task_cost = 0.0

    # Step 1: Format prompt and generate toke source via pool.
    toke_prompt = format_toke_prompt(toke_template, task)

    try:
        gen_result: GenerateResult = await pool.generate(
            task, system_prompt, toke_prompt
        )
    except Exception:
        logger.error("Generation failed for task %s", task.task_id, exc_info=True)
        metrics.record_failed(task.task_id, "unknown", category, 0.0)
        return False

    task_cost += gen_result.cost
    toke_source = extract_toke_source(gen_result.text)
    model_name = gen_result.model

    # Step 2: Generate reference implementations via a Tier 1 provider.
    ref_provider = get_tier1_provider(providers)
    if ref_provider is None:
        logger.error("No provider available for reference generation")
        metrics.record_failed(task.task_id, model_name, category, task_cost)
        return False

    try:
        py_prompt = format_ref_prompt(python_template, task)
        c_prompt = format_ref_prompt(c_template, task)
        java_prompt = format_ref_prompt(java_template, task)
        tests_prompt = format_tests_prompt(tests_template, task)

        py_result, c_result, java_result, tests_result = await asyncio.gather(
            ref_provider.generate(system_prompt, py_prompt),
            ref_provider.generate(system_prompt, c_prompt),
            ref_provider.generate(system_prompt, java_prompt),
            ref_provider.generate(system_prompt, tests_prompt),
        )
    except Exception:
        logger.error(
            "Reference generation failed for task %s",
            task.task_id,
            exc_info=True,
        )
        metrics.record_failed(task.task_id, model_name, category, task_cost)
        return False

    task_cost += py_result.cost + c_result.cost + java_result.cost + tests_result.cost
    python_src = _extract_fenced(py_result.text)
    c_src = _extract_fenced(c_result.text)
    java_src = _extract_fenced(java_result.text)
    test_inputs = _extract_test_inputs(tests_result.text)

    # Step 3: Validate all four languages.
    try:
        compile_results = await compiler.validate_all(
            toke_source, python_src, c_src, java_src
        )
    except Exception:
        logger.error(
            "Compilation validation failed for task %s",
            task.task_id,
            exc_info=True,
        )
        metrics.record_failed(task.task_id, model_name, category, task_cost)
        return False

    toke_compile = compile_results["toke"]

    # Step 4a: If toke fails, try auto-fix + transpiler (zero API cost).
    attempts = 1
    autofixer = AutoFixer()
    transpiler = CToTokeTranspiler()

    if not toke_compile.success:
        # Build candidate sources from mechanical recovery paths.
        candidates: list[tuple[str, str]] = []  # (label, source)

        # Path A: Auto-fix common LLM mistakes.
        fixed_source, fixes = autofixer.fix(toke_source)
        if fixes:
            logger.info(
                "Auto-fixer applied %d fixes for %s: %s",
                len(fixes), task.task_id, ", ".join(fixes),
            )
            candidates.append(("autofixed", fixed_source))

        # Path B: Transpile from C reference.
        if c_src:
            try:
                mod_name = task.task_id.replace("-", "").lower()
                transpiled = transpiler.transpile(c_src, mod_name)
                candidates.append(("transpiled", transpiled))
            except TranspileError as exc:
                logger.debug(
                    "Transpile failed for %s: %s", task.task_id, exc,
                )

        # Validate all candidates in parallel.
        if candidates:
            cand_results = await asyncio.gather(
                *[compiler.validate_toke(src) for _, src in candidates]
            )
            for (label, src), result in zip(candidates, cand_results):
                if result.success:
                    logger.info(
                        "Task %s rescued by %s path", task.task_id, label,
                    )
                    toke_source = src
                    toke_compile = result
                    break

    # Step 4b: If STILL failing, LLM correction + single escalation.
    # First attempt with original provider, then one tier-2 escalation.
    # If both fail, defer for batch pattern analysis.
    if not toke_compile.success:
        try:
            corr_result = await correction_loop.correct(
                task=task,
                original_source=toke_source,
                compile_result=toke_compile,
                diff_result=None,
                provider=ref_provider,
                system_prompt=system_prompt,
            )
            attempts += len(corr_result.attempts)
            task_cost += corr_result.total_cost

            if corr_result.success and corr_result.final_source is not None:
                toke_source = corr_result.final_source
                toke_compile = await compiler.validate_toke(toke_source)
            else:
                # First escalation: try one tier-2 provider before deferring.
                tier2_providers = [
                    p for name, p in providers.items()
                    if name != model_name and name != "pool"
                ]
                escalated = False
                if tier2_providers:
                    esc_provider = tier2_providers[0]
                    logger.info(
                        "Escalating %s to %s",
                        task.task_id,
                        getattr(esc_provider, "model", "tier2"),
                    )
                    esc_result = await correction_loop.correct(
                        task=task,
                        original_source=toke_source,
                        compile_result=toke_compile,
                        diff_result=None,
                        provider=esc_provider,
                        system_prompt=system_prompt,
                    )
                    attempts += len(esc_result.attempts)
                    task_cost += esc_result.total_cost
                    if esc_result.success and esc_result.final_source is not None:
                        toke_source = esc_result.final_source
                        toke_compile = await compiler.validate_toke(toke_source)
                        escalated = True

                if not escalated:
                    # Defer remaining escalations for batch analysis.
                    _log_deferred_failure(
                        task, toke_source, toke_compile, model_name, category,
                        config.get("log_dir", "logs"),
                    )
                    metrics.record_failed(
                        task.task_id, model_name, category, task_cost
                    )
                    return False
        except Exception:
            logger.error(
                "Correction failed for task %s",
                task.task_id,
                exc_info=True,
            )
            metrics.record_failed(task.task_id, model_name, category, task_cost)
            return False

    # Step 5: Differential testing.
    # Relax reference threshold for A-ERR — toke compiles fine but
    # error-handling reference implementations often fail.
    min_refs = 1 if category == "A-ERR" else 2
    try:
        diff_result = await diff_tester.test(
            toke_source, python_src, c_src, java_src, test_inputs,
            min_refs=min_refs,
        )
    except Exception:
        logger.error(
            "Differential testing failed for task %s",
            task.task_id,
            exc_info=True,
        )
        metrics.record_failed(task.task_id, model_name, category, task_cost)
        return False

    # Step 6: Quality scoring.
    quality = quality_scorer.score(
        task_id=task.task_id,
        toke_src=toke_source,
        python_src=python_src,
        compile_result=toke_compile,
        diff_result=diff_result,
    )

    # Step 7: Dedup check.
    is_unique = dedup.add(task.task_id, toke_source)
    if not is_unique:
        logger.info("Task %s rejected: duplicate", task.task_id)
        metrics.record_failed(task.task_id, model_name, category, task_cost)
        return False

    # Step 8: Accept or reject.
    if not quality.accepted:
        logger.info(
            "Task %s rejected: quality score %.4f, reasons: %s",
            task.task_id,
            quality.score,
            "; ".join(quality.reasons),
        )
        metrics.record_failed(task.task_id, model_name, category, task_cost)
        return False

    # Step 9: Write to corpus.
    try:
        toke_source = compact_toke(toke_source)
        tk_tokens = count_tokens(toke_source)
        entry = writer.build_entry(
            task=task,
            toke_source=toke_source,
            model_name=model_name,
            attempts=attempts,
            compile_result=toke_compile,
            diff_result=diff_result,
            quality=quality,
            tk_tokens=tk_tokens,
            python_src=python_src,
            c_src=c_src,
            java_src=java_src,
        )
        writer.write(entry)
    except Exception:
        logger.error(
            "Failed to write corpus entry for task %s",
            task.task_id,
            exc_info=True,
        )
        metrics.record_failed(task.task_id, model_name, category, task_cost)
        return False

    metrics.record_accepted(task.task_id, model_name, category, task_cost)
    completed_ids.add(task.task_id)
    logger.info("Task %s accepted (%s, %s)", task.task_id, category, model_name)
    return True


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(log_dir: str | None, level: int = logging.INFO) -> None:
    """Configure logging with console and optional file handlers."""
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path / "pipeline.log", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="toke corpus generation pipeline orchestrator.",
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to YAML config file.",
    )
    parser.add_argument(
        "--tasks",
        type=int,
        default=None,
        help="Override total tasks (default from config).",
    )
    parser.add_argument(
        "--skip-trial",
        action="store_true",
        help="Skip capability trial, use config allocations.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate curriculum + format prompts but make no API calls.",
    )
    parser.add_argument(
        "--trial-only",
        action="store_true",
        help="Run trial and print scorecard, then exit.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Tasks per batch (default 1000).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Max concurrent API calls (default 20).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main async pipeline
# ---------------------------------------------------------------------------

# Shutdown flag set by signal handlers.
_shutdown_requested: bool = False


async def run_pipeline(args: argparse.Namespace) -> None:
    """Execute the full corpus generation pipeline."""
    global _shutdown_requested  # noqa: PLW0603

    # ---- a) Load config ---------------------------------------------------
    config_path = Path(args.config)
    with open(config_path, encoding="utf-8") as fh:
        config: dict[str, Any] = yaml.safe_load(fh)

    total_tasks = args.tasks or config.get("total_tasks", 50_000)
    seed = config.get("seed", 42)
    phase = config.get("phase", "A")
    tkc_path = config.get("tkc_path", os.environ.get("TKC_PATH", "tkc"))
    corpus_dir = config.get("corpus_dir", "corpus")
    metrics_dir = config.get("metrics_dir", "metrics")
    log_dir = config.get("log_dir", None)
    prompts_dir = config.get("prompts_dir", "prompts")
    cost_limit = config.get("cost_limit", float("inf"))
    validation_cfg = config.get("validation", {})
    timeout_s = validation_cfg.get("timeout_seconds", 10)
    max_corrections = validation_cfg.get("max_correction_attempts", 3)
    dedup_threshold = config.get("dedup", {}).get("threshold", 0.95)
    trial_cfg = config.get("trial", {})
    trial_tasks = trial_cfg.get("tasks", 500)
    min_compile_rate = trial_cfg.get("min_compile_rate", 0.50)

    # ---- Holdout isolation (hard invariant) --------------------------------
    holdout_cfg = config.get("holdout", {})
    holdout_file: str | None = holdout_cfg.get("file")
    holdout_ids_raw: list[str] = holdout_cfg.get("task_ids", [])

    holdout_task_ids: set[str] = set(holdout_ids_raw)
    if holdout_file:
        holdout_path = Path(holdout_file)
        if not holdout_path.is_file():
            logger.error(
                "holdout.file '%s' does not exist -- aborting", holdout_file
            )
            sys.exit(1)
        with open(holdout_path, encoding="utf-8") as hf:
            for line in hf:
                tid = line.strip()
                if tid and not tid.startswith("#"):
                    holdout_task_ids.add(tid)

    if not holdout_task_ids:
        logger.error(
            "No holdout task IDs configured.  Set holdout.task_ids or "
            "holdout.file in your config YAML.  The pipeline refuses to "
            "run without an explicit holdout set."
        )
        sys.exit(1)
    logger.info("Holdout isolation: %d task IDs will be blocked", len(holdout_task_ids))

    setup_logging(log_dir)
    logger.info("Pipeline starting: phase=%s, total_tasks=%d, seed=%d", phase, total_tasks, seed)

    # Load prompt templates.
    prompts = load_prompts(prompts_dir)

    # ---- b) Generate curriculum -------------------------------------------
    curriculum = CurriculumGenerator(seed=seed, total_tasks=total_tasks)
    task_specs = curriculum.generate()
    logger.info("Generated %d task specifications", len(task_specs))

    # ---- Dry-run mode: generate curriculum and print sample, then exit ----
    if args.dry_run:
        toke_template = prompts.get("generate_toke", "")
        for task in task_specs[:5]:
            prompt = format_toke_prompt(toke_template, task)
            logger.info("Dry-run task %s:\n%s", task.task_id, prompt[:200])
        logger.info(
            "Dry-run complete: %d tasks generated, no API calls made.",
            len(task_specs),
        )
        return

    # ---- c) Initialize providers ------------------------------------------
    providers = build_providers(config)
    if not providers:
        logger.error("No providers initialised -- aborting")
        return

    cost_tracker = CostTracker()

    # ---- d) Run trial (unless --skip-trial) --------------------------------
    allocation: PoolAllocation | None = None

    if args.trial_only or not args.skip_trial:
        system_prompt = prompts.get("system", "")
        toke_template = prompts.get("generate_toke", "")

        trial_specs = task_specs[:trial_tasks]
        runner = TrialRunner(
            providers=list(providers.values()),
            task_specs=trial_specs,
            tkc_path=tkc_path,
            system_prompt=system_prompt,
            generate_template=toke_template,
            concurrency=args.concurrency,
        )
        logger.info("Running capability trial with %d tasks...", len(trial_specs))
        trial_results = await runner.run()

        scorer = TrialScorer(trial_results, min_compile_rate=min_compile_rate)
        scorecard = scorer.score()
        allocation = scorer.recommend()

        # Save scorecard.
        scorecard_path = Path(metrics_dir) / "scorecard.json"
        TrialScorer.save_scorecard(scorecard, scorecard_path)

        # Save trial results.
        trial_path = Path(metrics_dir) / "trial_results.json"
        TrialRunner.save_results(trial_results, trial_path)

        # Print scorecard summary.
        logger.info("=== Trial Scorecard ===")
        for ms in scorecard.scores:
            logger.info(
                "  %s: composite=%.4f first_pass=%.1f%% correction=%.1f%% "
                "cost_per_accepted=$%.4f %s",
                ms.provider_name,
                ms.composite_score,
                ms.first_pass_compile_rate * 100,
                ms.correction_success_rate * 100,
                ms.cost_per_accepted,
                "PASS" if ms.passed else "DROPPED",
            )
        if scorecard.dropped:
            logger.info("  Dropped providers: %s", ", ".join(scorecard.dropped))

        if args.trial_only:
            logger.info("Trial-only mode -- exiting.")
            return

    # ---- e) Configure pool ------------------------------------------------
    if allocation is not None:
        pool_cfg = pool_config_from_allocation(allocation, config)
    else:
        pool_cfg = pool_config_from_config(providers, config)

    pool = PoolManager(
        providers=providers,
        config=pool_cfg,
        cost_tracker=cost_tracker,
        seed=seed,
    )

    # ---- f) Resume check --------------------------------------------------
    checkpoint_path = os.path.join(metrics_dir, "checkpoint.json")
    checkpoint = Checkpoint(checkpoint_path)
    completed_ids: set[str] = set()

    if args.resume and checkpoint.exists():
        completed_ids, _prev_metrics = checkpoint.load()
        logger.info("Resumed from checkpoint: %d tasks already completed", len(completed_ids))

    # Always scan corpus directory for existing entries to avoid re-processing.
    corpus_path = Path(corpus_dir)
    if corpus_path.exists():
        import glob as _glob
        for entry_file in _glob.glob(str(corpus_path / "**" / "*.json"), recursive=True):
            try:
                with open(entry_file, encoding="utf-8") as _ef:
                    _entry = json.load(_ef)
                    _tid = _entry.get("task_id", "")
                    if _tid:
                        completed_ids.add(_tid)
            except Exception:
                pass
        logger.info("Scanned corpus: %d existing entries found, will skip", len(completed_ids))

    # Filter out already-completed tasks.
    remaining_tasks = [t for t in task_specs if t.task_id not in completed_ids]
    logger.info(
        "Tasks to process: %d (%d already completed)",
        len(remaining_tasks),
        len(completed_ids),
    )

    # ---- Initialise pipeline components -----------------------------------
    compiler = CompilerValidator(tkc_path=tkc_path, timeout_s=timeout_s)
    diff_tester = DifferentialTester(compiler)
    quality_scorer = QualityScorer(holdout_task_ids=holdout_task_ids)
    dedup = Deduplicator(threshold=dedup_threshold)
    correction_template = prompts.get("correct", "")
    correction_loop = CorrectionLoop(
        max_attempts=max_corrections,
        correction_template=correction_template,
    )
    escalation = EscalationEngine(
        correction_loop=correction_loop,
        pool_manager=pool,
    )
    writer = CorpusWriter(corpus_dir=corpus_dir, holdout_task_ids=holdout_task_ids)
    metrics = MetricsCollector(
        total_tasks=total_tasks,
        metrics_dir=metrics_dir,
    )

    # ---- Signal handlers for graceful shutdown ----------------------------
    def _handle_signal(signum: int, _frame: Any) -> None:
        global _shutdown_requested  # noqa: PLW0603
        sig_name = signal.Signals(signum).name
        logger.warning("Received %s -- requesting graceful shutdown", sig_name)
        _shutdown_requested = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ---- g) Main generation loop ------------------------------------------
    semaphore = asyncio.Semaphore(args.concurrency)
    batch_size = args.batch_size
    total_processed = 0

    # Category-level rejection tracking — hold categories with high reject rate
    cat_accepted: dict[str, int] = {c: 0 for c in CATEGORIES}
    cat_rejected: dict[str, int] = {c: 0 for c in CATEGORIES}
    cat_held: set[str] = set()
    HOLD_WINDOW = 50  # evaluate after this many tasks per category
    HOLD_REJECT_RATE = 0.80  # hold if rejection rate exceeds this

    for batch_start in range(0, len(remaining_tasks), batch_size):
        if _shutdown_requested:
            break

        batch = remaining_tasks[batch_start : batch_start + batch_size]

        # Filter out held categories
        active_batch = [t for t in batch if t.category not in cat_held]
        skipped = len(batch) - len(active_batch)
        if skipped:
            logger.info(
                "Skipped %d tasks from held categories: %s",
                skipped, ", ".join(sorted(cat_held)),
            )

        if not active_batch:
            total_processed += len(batch)
            continue

        logger.info(
            "Processing batch %d-%d of %d (%d active, %d held)",
            batch_start,
            batch_start + len(batch),
            len(remaining_tasks),
            len(active_batch),
            skipped,
        )

        async def _process_with_semaphore(task: TaskSpec) -> tuple[TaskSpec, bool]:
            async with semaphore:
                result = await process_task(
                    task=task,
                    prompts=prompts,
                    pool=pool,
                    compiler=compiler,
                    diff_tester=diff_tester,
                    quality_scorer=quality_scorer,
                    dedup=dedup,
                    correction_loop=correction_loop,
                    escalation=escalation,
                    writer=writer,
                    metrics=metrics,
                    cost_tracker=cost_tracker,
                    providers=providers,
                    config=config,
                    completed_ids=completed_ids,
                )
                return task, result

        tasks: list[asyncio.Task[tuple[TaskSpec, bool]]] = []
        for task in active_batch:
            if _shutdown_requested:
                break

            # Cost limit check.
            if cost_tracker.total() >= cost_limit:
                logger.warning(
                    "Cost limit reached ($%.2f >= $%.2f) -- stopping",
                    cost_tracker.total(),
                    cost_limit,
                )
                _shutdown_requested = True
                break

            metrics.record_dispatch(task.task_id, "pool", task.category)
            tasks.append(asyncio.create_task(_process_with_semaphore(task)))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for item in results:
                if isinstance(item, BaseException):
                    logger.error(
                        "Task processing raised exception: %s",
                        item,
                        exc_info=item,
                    )
                    continue
                task_obj, accepted = item
                cat = task_obj.category
                if accepted:
                    cat_accepted[cat] = cat_accepted.get(cat, 0) + 1
                else:
                    cat_rejected[cat] = cat_rejected.get(cat, 0) + 1

                # Check if category should be held
                total_cat = cat_accepted.get(cat, 0) + cat_rejected.get(cat, 0)
                if (
                    cat not in cat_held
                    and total_cat >= HOLD_WINDOW
                    and total_cat % HOLD_WINDOW == 0
                ):
                    reject_rate = cat_rejected.get(cat, 0) / total_cat
                    if reject_rate >= HOLD_REJECT_RATE:
                        cat_held.add(cat)
                        logger.warning(
                            "HOLD category %s: %.0f%% rejection rate "
                            "(%d/%d) — skipping until fixed",
                            cat,
                            reject_rate * 100,
                            cat_rejected.get(cat, 0),
                            total_cat,
                        )

        total_processed += len(batch)

        # Checkpoint every 100 tasks (or at end of batch).
        if total_processed % 100 < batch_size or _shutdown_requested:
            checkpoint.save(completed_ids, metrics.snapshot())

        # Print summary every 500 tasks.
        if total_processed % 500 < batch_size:
            metrics.print_summary()
            # Log per-category stats
            for cat in CATEGORIES:
                a = cat_accepted.get(cat, 0)
                r = cat_rejected.get(cat, 0)
                t = a + r
                held = " [HELD]" if cat in cat_held else ""
                if t > 0:
                    logger.info(
                        "  %s: %d accepted, %d rejected (%.0f%% accept)%s",
                        cat, a, r, a / t * 100, held,
                    )

    # ---- j) Graceful shutdown: save final checkpoint ----------------------
    checkpoint.save(completed_ids, metrics.snapshot())
    metrics.save()

    # ---- i) End report ----------------------------------------------------
    snapshot = metrics.snapshot()
    logger.info("=== Final Report ===")
    logger.info("Total accepted: %d / %d", snapshot.accepted, snapshot.total_tasks)
    logger.info("Total failed: %d", snapshot.failed)
    logger.info("Total escalated: %d", snapshot.escalated)
    logger.info("Acceptance rate: %.1f%%", snapshot.accepted / max(snapshot.dispatched, 1) * 100)

    logger.info("--- Per-category breakdown ---")
    for cat, cm in sorted(snapshot.per_category.items()):
        logger.info(
            "  %s: accepted=%d, failed=%d, rate=%.1f%%",
            cat,
            cm.accepted,
            cm.failed,
            cm.acceptance_rate * 100,
        )

    logger.info("--- Per-model breakdown ---")
    for model, mm in sorted(snapshot.per_model.items()):
        logger.info(
            "  %s: accepted=%d, failed=%d, rate=%.1f%%, cost=$%.4f",
            model,
            mm.accepted,
            mm.failed,
            mm.acceptance_rate * 100,
            mm.cost,
        )

    logger.info("--- Cost ---")
    logger.info("  Total API cost: $%.4f", snapshot.cost.api_total)
    for provider, pcost in sorted(snapshot.cost.by_provider.items()):
        logger.info("  %s: $%.4f", provider, pcost)

    logger.info(cost_tracker.summary())
    logger.info("Pipeline complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    args = parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
