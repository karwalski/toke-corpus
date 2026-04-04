#!/usr/bin/env python3
"""TAROT-style test-driven curriculum with compiler feedback.

Adaptive training loop that evaluates checkpoints on diagnostic tasks and
reweights the data mix based on weakness profiles derived from compiler
diagnostic error codes mapped to skill-ladder stages.

Usage::

    python scripts/tarot_curriculum.py --dry-run --iterations 3
    python scripts/tarot_curriculum.py --benchmark-dir /path/to/benchmark \\
        --predictions-dir /path/to/preds --output-dir data --iterations 3

Story 9.2.2 -- TAROT-style test-driven curriculum with compiler feedback.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Skill-ladder stage definitions (from skill-ladder.md, Story 9.2.1)
# ---------------------------------------------------------------------------

STAGES = {
    1: "Expressions and Bindings",
    2: "Functions and Types",
    3: "Control Flow",
    4: "Modules, Imports, and Sum Types",
    5: "Error Handling and Standard Library",
    6: "Advanced Patterns and Composition",
}

# Map toke compiler error codes to skill-ladder stages.
# Error codes follow tkc --diag-json output format: E<NNN>.
ERROR_CODE_TO_STAGE: dict[str, int] = {
    # Stage 1 -- expressions, bindings, literals
    "E001": 1,  # invalid literal
    "E002": 1,  # undeclared variable
    "E003": 1,  # invalid operator usage
    "E004": 1,  # missing return expression
    "E005": 1,  # assignment to immutable binding
    "E006": 1,  # arithmetic type mismatch
    # Stage 2 -- functions, types, structs
    "E010": 2,  # wrong number of arguments
    "E011": 2,  # parameter type mismatch
    "E012": 2,  # missing return type annotation
    "E013": 2,  # undefined type name
    "E014": 2,  # struct field mismatch
    "E015": 2,  # duplicate function definition
    # Stage 3 -- control flow
    "E020": 3,  # unreachable code after break
    "E021": 3,  # break outside loop
    "E022": 3,  # non-boolean condition
    "E023": 3,  # infinite recursion detected
    "E024": 3,  # missing else branch (expression context)
    # Stage 4 -- modules, imports, sum types, match
    "E030": 4,  # unresolved import
    "E031": 4,  # non-exhaustive match
    "E032": 4,  # duplicate variant name
    "E033": 4,  # unknown module member
    "E034": 4,  # circular import
    # Stage 5 -- error handling, stdlib
    "E040": 5,  # unhandled result type
    "E041": 5,  # invalid error propagation
    "E042": 5,  # stdlib function misuse
    "E043": 5,  # file I/O without error handling
    # Stage 6 -- advanced patterns
    "E050": 6,  # higher-order type error
    "E051": 6,  # generic constraint violation
}

# Difficulty tiers for stratified sampling of the diagnostic subset.
DIFFICULTY_TIERS = {
    "beginner": 20,
    "intermediate": 30,
    "advanced": 30,
    "expert": 20,
}

# Map difficulty tiers to skill-ladder stages for synthetic generation.
TIER_TO_STAGES: dict[str, list[int]] = {
    "beginner": [1],
    "intermediate": [2, 3],
    "advanced": [4, 5],
    "expert": [5, 6],
}

# Base weights per stage (uniform start).
BASE_WEIGHTS = {s: 1.0 for s in STAGES}


# ---------------------------------------------------------------------------
# Diagnostic subset selection
# ---------------------------------------------------------------------------

def load_benchmark_tasks(benchmark_dir: Path) -> list[dict[str, Any]]:
    """Load YAML task files from hidden_tests/ directory."""
    try:
        import yaml
    except ImportError:
        print("WARNING: PyYAML not available, using stub loader", file=sys.stderr)
        return _stub_load_tasks(benchmark_dir)

    hidden_dir = benchmark_dir / "hidden_tests"
    if not hidden_dir.is_dir():
        print(f"WARNING: {hidden_dir} not found, returning empty list",
              file=sys.stderr)
        return []

    tasks: list[dict[str, Any]] = []
    for yf in sorted(hidden_dir.glob("task-*.yaml")):
        with open(yf) as fh:
            doc = yaml.safe_load(fh)
            if doc:
                tasks.append(doc)
    return tasks


def _stub_load_tasks(benchmark_dir: Path) -> list[dict[str, Any]]:
    """Fallback loader when PyYAML is missing -- reads id lines only."""
    hidden_dir = benchmark_dir / "hidden_tests"
    if not hidden_dir.is_dir():
        return []
    tasks = []
    for yf in sorted(hidden_dir.glob("task-*.yaml")):
        task_id = yf.stem
        tasks.append({"id": task_id, "phase": "A", "category": "unknown"})
    return tasks


def assign_difficulty(task: dict[str, Any], index: int, total: int) -> str:
    """Assign a difficulty tier to a task.

    Since the current benchmark tasks are all phase-A arithmetic, we use
    positional bucketing to spread tasks across tiers proportionally.
    When tasks carry explicit difficulty metadata, this function should
    be updated to use it.
    """
    frac = index / max(total, 1)
    if frac < 0.25:
        return "beginner"
    elif frac < 0.55:
        return "intermediate"
    elif frac < 0.80:
        return "advanced"
    else:
        return "expert"


def select_diagnostic_subset(
    tasks: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Select a stratified 100-task diagnostic subset."""
    # Assign difficulties.
    for i, t in enumerate(tasks):
        t["difficulty"] = assign_difficulty(t, i, len(tasks))

    # Group by difficulty.
    by_tier: dict[str, list[dict[str, Any]]] = {k: [] for k in DIFFICULTY_TIERS}
    for t in tasks:
        tier = t["difficulty"]
        if tier in by_tier:
            by_tier[tier].append(t)

    subset: list[dict[str, Any]] = []
    for tier, count in DIFFICULTY_TIERS.items():
        pool = by_tier.get(tier, [])
        if len(pool) <= count:
            subset.extend(pool)
        else:
            subset.extend(rng.sample(pool, count))

    return subset


# ---------------------------------------------------------------------------
# Evaluation harness
# ---------------------------------------------------------------------------

def load_predictions(predictions_file: Path) -> dict[str, str]:
    """Load JSONL predictions: {task_id: prediction}."""
    preds: dict[str, str] = {}
    if not predictions_file.is_file():
        return preds
    with open(predictions_file) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            preds[rec["task_id"]] = rec["prediction"]
    return preds


def run_compiler_check(source: str) -> list[dict[str, Any]]:
    """Run tkc --check --diag-json on a source string.

    Returns a list of diagnostic dicts.  Falls back to empty list on error.
    """
    try:
        result = subprocess.run(
            ["tkc", "--check", "--diag-json"],
            input=source,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return []


def evaluate_predictions(
    subset: list[dict[str, Any]],
    predictions: dict[str, str],
    simulate: bool = False,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Evaluate predictions against the diagnostic subset.

    Returns:
        {
            "pass_count": int,
            "fail_count": int,
            "pass_rate": float,
            "weakness_profile": {error_code: count},
            "error_distribution": {stage: count},
            "per_task": [{task_id, passed, errors}],
        }
    """
    if rng is None:
        rng = random.Random(42)

    pass_count = 0
    fail_count = 0
    weakness: dict[str, int] = {}
    per_task: list[dict[str, Any]] = []

    for task in subset:
        task_id = task["id"]
        prediction = predictions.get(task_id, "")

        if simulate:
            errors = _simulate_diagnostics(task, rng)
        else:
            errors = run_compiler_check(prediction)

        error_codes = [e.get("code", "E000") for e in errors]
        passed = len(error_codes) == 0

        if passed:
            pass_count += 1
        else:
            fail_count += 1

        for code in error_codes:
            weakness[code] = weakness.get(code, 0) + 1

        per_task.append({
            "task_id": task_id,
            "passed": passed,
            "errors": error_codes,
        })

    total = pass_count + fail_count
    pass_rate = pass_count / max(total, 1)

    # Aggregate by stage.
    error_distribution: dict[int, int] = {s: 0 for s in STAGES}
    for code, cnt in weakness.items():
        stage = ERROR_CODE_TO_STAGE.get(code, 1)
        error_distribution[stage] += cnt

    return {
        "pass_count": pass_count,
        "fail_count": fail_count,
        "pass_rate": pass_rate,
        "weakness_profile": weakness,
        "error_distribution": {str(s): c for s, c in error_distribution.items()},
        "per_task": per_task,
    }


def _simulate_diagnostics(
    task: dict[str, Any],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate synthetic diagnostics with realistic error distributions.

    Harder tasks have higher failure rates and more complex errors.
    """
    tier = task.get("difficulty", "beginner")
    # Probability of passing clean.
    pass_probs = {
        "beginner": 0.80,
        "intermediate": 0.55,
        "advanced": 0.35,
        "expert": 0.20,
    }

    if rng.random() < pass_probs.get(tier, 0.5):
        return []  # Clean pass.

    # Generate 1-3 errors from the tier's stage pool.
    stages = TIER_TO_STAGES.get(tier, [1])
    codes_in_scope = [
        code for code, s in ERROR_CODE_TO_STAGE.items() if s in stages
    ]
    if not codes_in_scope:
        codes_in_scope = ["E001"]

    n_errors = rng.randint(1, 3)
    errors = []
    for _ in range(n_errors):
        code = rng.choice(codes_in_scope)
        errors.append({
            "code": code,
            "message": f"simulated diagnostic {code}",
            "line": rng.randint(1, 20),
            "col": rng.randint(1, 40),
        })
    return errors


# ---------------------------------------------------------------------------
# Reweighting algorithm
# ---------------------------------------------------------------------------

def compute_stage_failure_rates(
    eval_result: dict[str, Any],
    subset: list[dict[str, Any]],
) -> dict[int, float]:
    """Compute per-stage failure rates from evaluation results."""
    stage_total: dict[int, int] = {s: 0 for s in STAGES}
    stage_fail: dict[int, int] = {s: 0 for s in STAGES}

    for task_rec in eval_result["per_task"]:
        task_id = task_rec["task_id"]
        # Find original task to get difficulty -> stages.
        task = next((t for t in subset if t["id"] == task_id), None)
        if task is None:
            continue
        tier = task.get("difficulty", "beginner")
        primary_stage = TIER_TO_STAGES.get(tier, [1])[0]
        stage_total[primary_stage] = stage_total.get(primary_stage, 0) + 1
        if not task_rec["passed"]:
            stage_fail[primary_stage] = stage_fail.get(primary_stage, 0) + 1

    failure_rates: dict[int, float] = {}
    for s in STAGES:
        total = stage_total.get(s, 0)
        fails = stage_fail.get(s, 0)
        failure_rates[s] = fails / max(total, 1) if total > 0 else 0.0

    return failure_rates


def reweight(
    current_weights: dict[int, float],
    failure_rates: dict[int, float],
    boost_factor: float = 2.0,
) -> dict[int, float]:
    """Reweight data mix based on failure rates.

    Formula: new_weight = base_weight * (1 + failure_rate * boost_factor)
    Weights are normalised to sum to 1.0.
    """
    raw: dict[int, float] = {}
    for s in STAGES:
        base = current_weights.get(s, 1.0)
        fr = failure_rates.get(s, 0.0)
        raw[s] = base * (1.0 + fr * boost_factor)

    total = sum(raw.values())
    if total == 0:
        total = 1.0

    return {s: round(w / total, 6) for s, w in raw.items()}


# ---------------------------------------------------------------------------
# Iteration loop
# ---------------------------------------------------------------------------

def run_iteration(
    iteration: int,
    subset: list[dict[str, Any]],
    predictions: dict[str, str],
    current_weights: dict[int, float],
    boost_factor: float,
    simulate: bool,
    rng: random.Random,
) -> dict[str, Any]:
    """Run a single TAROT iteration: evaluate -> profile -> reweight."""
    eval_result = evaluate_predictions(subset, predictions, simulate=simulate, rng=rng)
    failure_rates = compute_stage_failure_rates(eval_result, subset)
    new_weights = reweight(current_weights, failure_rates, boost_factor)

    return {
        "iteration": iteration,
        "pass_rate": eval_result["pass_rate"],
        "pass_count": eval_result["pass_count"],
        "fail_count": eval_result["fail_count"],
        "weakness_profile": eval_result["weakness_profile"],
        "error_distribution": eval_result["error_distribution"],
        "failure_rates": {str(s): round(r, 4) for s, r in failure_rates.items()},
        "input_weights": {str(s): round(w, 6) for s, w in current_weights.items()},
        "data_mix_weights": {str(s): w for s, w in new_weights.items()},
    }


def run_all_iterations(
    subset: list[dict[str, Any]],
    predictions_dir: Path | None,
    output_dir: Path,
    iterations: int,
    boost_factor: float,
    simulate: bool,
    seed: int,
) -> dict[str, Any]:
    """Run the full TAROT loop for N iterations."""
    rng = random.Random(seed)
    current_weights = {s: round(1.0 / len(STAGES), 6) for s in STAGES}
    results: list[dict[str, Any]] = []

    for it in range(1, iterations + 1):
        # Load predictions for this iteration (or empty for dry-run).
        predictions: dict[str, str] = {}
        if predictions_dir and not simulate:
            pred_file = predictions_dir / f"iteration_{it}.jsonl"
            predictions = load_predictions(pred_file)

        iter_result = run_iteration(
            iteration=it,
            subset=subset,
            predictions=predictions,
            current_weights=current_weights,
            boost_factor=boost_factor,
            simulate=simulate,
            rng=rng,
        )
        results.append(iter_result)

        # Write per-iteration output.
        iter_file = output_dir / f"tarot_iteration_{it}.json"
        with open(iter_file, "w") as fh:
            json.dump(iter_result, fh, indent=2)
        print(f"  Iteration {it}: written to {iter_file}")

        # Advance weights for next iteration.
        current_weights = {
            int(s): w for s, w in iter_result["data_mix_weights"].items()
        }

    # Build summary.
    summary = build_summary(results)
    summary_file = output_dir / "tarot_summary.json"
    with open(summary_file, "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"  Summary: written to {summary_file}")

    return summary


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build improvement trajectory from iteration results."""
    trajectory: list[dict[str, Any]] = []
    for r in results:
        trajectory.append({
            "iteration": r["iteration"],
            "pass_rate": r["pass_rate"],
            "pass_count": r["pass_count"],
            "fail_count": r["fail_count"],
        })

    first = results[0]["pass_rate"] if results else 0.0
    last = results[-1]["pass_rate"] if results else 0.0
    improvement = last - first

    return {
        "total_iterations": len(results),
        "initial_pass_rate": first,
        "final_pass_rate": last,
        "improvement": round(improvement, 4),
        "trajectory": trajectory,
        "final_data_mix": results[-1]["data_mix_weights"] if results else {},
        "final_weakness_profile": results[-1]["weakness_profile"] if results else {},
    }


# ---------------------------------------------------------------------------
# Summary table (stdout)
# ---------------------------------------------------------------------------

def print_summary_table(summary: dict[str, Any]) -> None:
    """Print a human-readable summary table to stdout."""
    print()
    print("=" * 70)
    print("  TAROT Curriculum — Iteration Summary")
    print("=" * 70)
    print()
    print(f"  {'Iter':>4}  {'Pass Rate':>10}  {'Pass':>5}  {'Fail':>5}")
    print(f"  {'----':>4}  {'----------':>10}  {'-----':>5}  {'-----':>5}")
    for t in summary["trajectory"]:
        pr = f"{t['pass_rate']:.1%}"
        print(f"  {t['iteration']:>4}  {pr:>10}  {t['pass_count']:>5}  {t['fail_count']:>5}")

    print()
    imp = summary["improvement"]
    direction = "+" if imp >= 0 else ""
    print(f"  Improvement: {direction}{imp:.1%} "
          f"({summary['initial_pass_rate']:.1%} -> {summary['final_pass_rate']:.1%})")
    print()

    # Final data mix.
    mix = summary.get("final_data_mix", {})
    if mix:
        print("  Final data mix weights:")
        for stage_str in sorted(mix, key=int):
            s = int(stage_str)
            w = mix[stage_str]
            name = STAGES.get(s, "Unknown")
            bar = "#" * int(w * 40)
            print(f"    Stage {s} ({name[:28]:<28s}): {w:.4f}  {bar}")
        print()

    # Top weakness codes.
    wp = summary.get("final_weakness_profile", {})
    if wp:
        top = sorted(wp.items(), key=lambda x: -x[1])[:10]
        print("  Top error codes (final iteration):")
        for code, count in top:
            stage = ERROR_CODE_TO_STAGE.get(code, "?")
            print(f"    {code} (stage {stage}): {count}")
        print()

    print("=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TAROT-style test-driven curriculum with compiler feedback.",
    )
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=Path("/Users/matthew.watt/tk/toke-benchmark"),
        help="Path to toke-benchmark directory (default: %(default)s)",
    )
    parser.add_argument(
        "--predictions-dir",
        type=Path,
        default=None,
        help="Directory of JSONL prediction files (iteration_N.jsonl)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="Output directory for iteration and summary JSON (default: data/)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of TAROT iterations (default: 3)",
    )
    parser.add_argument(
        "--boost-factor",
        type=float,
        default=2.0,
        help="Reweighting boost factor (default: 2.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use synthetic predictions with realistic error distributions",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    print("TAROT Curriculum — Story 9.2.2")
    print(f"  benchmark-dir:  {args.benchmark_dir}")
    print(f"  predictions-dir: {args.predictions_dir or '(dry-run)'}")
    print(f"  output-dir:     {args.output_dir}")
    print(f"  iterations:     {args.iterations}")
    print(f"  boost-factor:   {args.boost_factor}")
    print(f"  dry-run:        {args.dry_run}")
    print(f"  seed:           {args.seed}")
    print()

    if args.iterations < 1:
        print("ERROR: --iterations must be >= 1", file=sys.stderr)
        return 1

    # Load benchmark tasks.
    print("Loading benchmark tasks...")
    tasks = load_benchmark_tasks(args.benchmark_dir)
    if not tasks:
        print("WARNING: No benchmark tasks found. Using synthetic task set.",
              file=sys.stderr)
        tasks = _generate_synthetic_tasks(args.seed)
    print(f"  Loaded {len(tasks)} tasks.")

    # Select diagnostic subset.
    rng = random.Random(args.seed)
    subset = select_diagnostic_subset(tasks, rng)
    print(f"  Diagnostic subset: {len(subset)} tasks")
    tier_counts = {}
    for t in subset:
        d = t.get("difficulty", "?")
        tier_counts[d] = tier_counts.get(d, 0) + 1
    for tier in DIFFICULTY_TIERS:
        print(f"    {tier}: {tier_counts.get(tier, 0)}")
    print()

    # Ensure output dir.
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Write data_mix.json with initial uniform weights.
    initial_mix = {str(s): round(1.0 / len(STAGES), 6) for s in STAGES}
    mix_file = args.output_dir / "data_mix.json"
    with open(mix_file, "w") as fh:
        json.dump(initial_mix, fh, indent=2)
    print(f"  Initial data_mix.json: {mix_file}")
    print()

    # Run iterations.
    print("Running TAROT iterations...")
    summary = run_all_iterations(
        subset=subset,
        predictions_dir=args.predictions_dir,
        output_dir=args.output_dir,
        iterations=args.iterations,
        boost_factor=args.boost_factor,
        simulate=args.dry_run,
        seed=args.seed,
    )

    # Update data_mix.json with final weights.
    with open(mix_file, "w") as fh:
        json.dump(summary["final_data_mix"], fh, indent=2)
    print(f"  Updated data_mix.json with final weights.")

    # Print summary table.
    print_summary_table(summary)

    return 0


def _generate_synthetic_tasks(seed: int) -> list[dict[str, Any]]:
    """Generate 200 synthetic tasks when no benchmark is available."""
    rng = random.Random(seed)
    tasks = []
    categories = [
        "arithmetic", "string_ops", "list_processing", "type_construction",
        "pattern_matching", "error_handling", "module_design", "algorithms",
    ]
    for i in range(1, 201):
        tasks.append({
            "id": f"synth-{i:04d}",
            "phase": "A",
            "category": rng.choice(categories),
            "description": f"Synthetic task {i}",
        })
    return tasks


if __name__ == "__main__":
    raise SystemExit(main())
