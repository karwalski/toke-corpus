#!/usr/bin/env python3
"""Compiler-as-verifier reward model for RL training.

Uses tkc --check --diag-json output as a reward signal for GRPO-compatible
reinforcement learning loops.

Reward tiers:
  +1.0  — compiles cleanly AND all tests pass
  +0.5  — compiles cleanly, partial test pass (or no tests available)
  +0.25 — compile fails but only warnings (no errors)
   0.0  — compile fails with errors

Usage::

    # Dry-run (no real compiler needed):
    python scripts/compiler_reward.py \\
        --predictions data/predictions.jsonl \\
        --output results/rewards.jsonl \\
        --dry-run --seed 42

    # Real evaluation:
    python scripts/compiler_reward.py \\
        --predictions data/predictions.jsonl \\
        --benchmark-dir data/benchmarks \\
        --output results/rewards.jsonl \\
        --tkc-path tkc

Story 9.5.1 -- Compiler-as-verifier reward model.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Reward constants
# ---------------------------------------------------------------------------

REWARD_FULL = 1.0       # compile ok + all tests pass
REWARD_PARTIAL = 0.5    # compile ok + partial tests (or no tests)
REWARD_WARN_ONLY = 0.25 # compile fail, warnings only
REWARD_FAIL = 0.0       # compile fail with errors

# ---------------------------------------------------------------------------
# Compiler interaction
# ---------------------------------------------------------------------------


def run_tkc_check(source: str, tkc_path: str) -> dict:
    """Run ``tkc --check --diag-json`` on *source* and return parsed result.

    Returns a dict with keys:
      - returncode: int
      - diagnostics: list[dict]
      - compile_ok: bool
      - has_errors: bool
      - has_warnings: bool
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toke", delete=False
    ) as f:
        f.write(source)
        f.flush()
        tmp_path = f.name

    try:
        result = subprocess.run(
            [tkc_path, "--check", "--diag-json", tmp_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {
            "returncode": 3,
            "diagnostics": [{"code": "TIMEOUT", "severity": "error",
                             "message": str(exc)}],
            "compile_ok": False,
            "has_errors": True,
            "has_warnings": False,
        }
    finally:
        os.unlink(tmp_path)

    diagnostics: list[dict] = []
    # tkc --diag-json emits one JSON object per line on stdout
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            diagnostics.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    has_errors = any(
        d.get("severity") == "error" for d in diagnostics
    )
    has_warnings = any(
        d.get("severity") == "warning" for d in diagnostics
    )

    return {
        "returncode": result.returncode,
        "diagnostics": diagnostics,
        "compile_ok": result.returncode == 0,
        "has_errors": has_errors,
        "has_warnings": has_warnings,
    }


def run_tests(source: str, test_cases: list[dict], tkc_path: str) -> tuple[int, int]:
    """Compile *source*, run against *test_cases*, return (passed, total).

    Each test case is ``{"input": "...", "expected_output": "..."}``.
    Returns (0, 0) when test_cases is empty.
    """
    if not test_cases:
        return 0, 0

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = Path(tmpdir) / "prog.toke"
        src_path.write_text(source)

        # Compile to executable
        exe_path = Path(tmpdir) / "prog"
        comp = subprocess.run(
            [tkc_path, str(src_path), "-o", str(exe_path)],
            capture_output=True, text=True, timeout=15,
        )
        if comp.returncode != 0:
            return 0, len(test_cases)

        passed = 0
        for tc in test_cases:
            try:
                run = subprocess.run(
                    [str(exe_path)],
                    input=tc.get("input", ""),
                    capture_output=True, text=True, timeout=10,
                )
                if run.stdout.strip() == tc.get("expected_output", "").strip():
                    passed += 1
            except (subprocess.TimeoutExpired, OSError):
                pass

        return passed, len(test_cases)


# ---------------------------------------------------------------------------
# Dry-run simulation
# ---------------------------------------------------------------------------


def simulate_check(source: str, rng: random.Random) -> dict:
    """Heuristic-based compilation outcome for --dry-run mode.

    Uses source length, keyword presence, and bracket balance to produce
    a plausible result without invoking the real compiler.
    """
    lines = source.strip().splitlines()
    # Bracket balance
    opens = source.count("(") + source.count("{") + source.count("[")
    closes = source.count(")") + source.count("}") + source.count("]")
    balanced = opens == closes

    has_fn = "fn " in source or "fn(" in source
    has_lp = "lp " in source or "lp(" in source
    reasonable_length = 5 <= len(lines) <= 200

    # Probability of clean compile based on heuristics
    score = 0.0
    if balanced:
        score += 0.4
    if has_fn:
        score += 0.2
    if reasonable_length:
        score += 0.2
    if has_lp or "if " in source:
        score += 0.1
    # Add small random noise
    score += rng.uniform(-0.1, 0.1)
    score = max(0.0, min(1.0, score))

    roll = rng.random()
    if roll < score * 0.6:
        # Clean compile
        return {
            "returncode": 0,
            "diagnostics": [],
            "compile_ok": True,
            "has_errors": False,
            "has_warnings": False,
        }
    elif roll < score * 0.8:
        # Compile ok but with warnings
        return {
            "returncode": 0,
            "diagnostics": [{"code": "W1010", "severity": "warning",
                             "message": "simulated warning"}],
            "compile_ok": True,
            "has_errors": False,
            "has_warnings": True,
        }
    elif roll < score * 0.95:
        # Warnings only (no errors but returncode != 0 scenario — treat as
        # compile failure with warnings only)
        return {
            "returncode": 1,
            "diagnostics": [{"code": "W1010", "severity": "warning",
                             "message": "simulated warning"}],
            "compile_ok": False,
            "has_errors": False,
            "has_warnings": True,
        }
    else:
        # Hard fail
        err_codes = ["E1001", "E2001", "E2003", "E3011", "E4010"]
        code = rng.choice(err_codes)
        return {
            "returncode": 1,
            "diagnostics": [{"code": code, "severity": "error",
                             "message": f"simulated error {code}"}],
            "compile_ok": False,
            "has_errors": True,
            "has_warnings": False,
        }


def simulate_tests(
    source: str, test_cases: list[dict], rng: random.Random,
) -> tuple[int, int]:
    """Simulate test execution for dry-run mode."""
    if not test_cases:
        return 0, 0
    total = len(test_cases)
    # Longer, more structured code passes more tests
    pass_prob = min(0.9, len(source) / 500.0 + 0.2)
    passed = sum(1 for _ in range(total) if rng.random() < pass_prob)
    return passed, total


# ---------------------------------------------------------------------------
# Reward computation
# ---------------------------------------------------------------------------


def compute_reward(
    compile_result: dict,
    tests_passed: int,
    tests_total: int,
) -> float:
    """Assign a reward score based on compilation and test outcomes."""
    if not compile_result["compile_ok"]:
        # Compile failed — check if warnings only
        if not compile_result["has_errors"] and compile_result["has_warnings"]:
            return REWARD_WARN_ONLY
        return REWARD_FAIL

    # Compile succeeded
    if tests_total == 0:
        return REWARD_PARTIAL  # no tests available

    if tests_passed == tests_total:
        return REWARD_FULL

    if tests_passed > 0:
        return REWARD_PARTIAL

    # Compiled but all tests failed
    return REWARD_PARTIAL


# ---------------------------------------------------------------------------
# Benchmark loader
# ---------------------------------------------------------------------------


def load_test_cases(task_id: str, benchmark_dir: Path | None) -> list[dict]:
    """Load test cases for *task_id* from the benchmark directory.

    Looks for ``<benchmark_dir>/<task_id>.yaml`` or ``.json`` containing a
    ``tests`` key with a list of ``{input, expected_output}`` dicts.
    """
    if benchmark_dir is None:
        return []

    for ext in (".yaml", ".yml", ".json"):
        p = benchmark_dir / f"{task_id}{ext}"
        if not p.exists():
            continue
        text = p.read_text()
        if ext in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-untyped]
                data = yaml.safe_load(text)
            except ImportError:
                print(
                    f"  warning: pyyaml not installed, skipping {p}",
                    file=sys.stderr,
                )
                return []
        else:
            data = json.loads(text)
        return data.get("tests", []) if isinstance(data, dict) else []

    return []


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------


def evaluate_batch(
    predictions_path: Path,
    benchmark_dir: Path | None,
    tkc_path: str,
    dry_run: bool,
    seed: int | None,
) -> list[dict]:
    """Evaluate all predictions and return GRPO-compatible result dicts."""
    rng = random.Random(seed)

    results: list[dict] = []
    with open(predictions_path) as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                pred = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"  warning: skipping malformed line {line_no}",
                    file=sys.stderr,
                )
                continue

            task_id = pred.get("task_id", f"unknown_{line_no}")
            source = pred.get("source", "")

            # 1. Compile check
            if dry_run:
                compile_result = simulate_check(source, rng)
            else:
                compile_result = run_tkc_check(source, tkc_path)

            # 2. Test execution
            test_cases = load_test_cases(task_id, benchmark_dir)
            if compile_result["compile_ok"] and test_cases:
                if dry_run:
                    passed, total = simulate_tests(source, test_cases, rng)
                else:
                    passed, total = run_tests(source, test_cases, tkc_path)
            else:
                passed, total = 0, len(test_cases)

            # 3. Reward
            reward = compute_reward(compile_result, passed, total)

            results.append({
                "task_id": task_id,
                "source": source,
                "reward": reward,
                "diagnostics": compile_result["diagnostics"],
                "compile_ok": compile_result["compile_ok"],
                "tests_passed": passed,
                "tests_total": total,
            })

    return results


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def print_statistics(results: list[dict], file=sys.stdout) -> None:
    """Print reward distribution, mean per tier, and failure modes."""
    if not results:
        print("No results to report.", file=file)
        return

    rewards = [r["reward"] for r in results]
    n = len(rewards)

    # -- Reward distribution histogram --
    buckets = Counter(rewards)
    print("=== Reward Distribution ===", file=file)
    for val in sorted(buckets.keys()):
        count = buckets[val]
        bar = "#" * int(40 * count / n)
        print(f"  {val:5.2f}  {count:4d}/{n}  ({100*count/n:5.1f}%)  {bar}",
              file=file)

    # -- Mean reward --
    mean_reward = sum(rewards) / n
    print(f"\nMean reward: {mean_reward:.4f}  (n={n})", file=file)

    # -- Mean reward per difficulty tier (heuristic: source length) --
    tiers: dict[str, list[float]] = {"short": [], "medium": [], "long": []}
    for r in results:
        src_len = len(r["source"].splitlines())
        if src_len <= 10:
            tiers["short"].append(r["reward"])
        elif src_len <= 40:
            tiers["medium"].append(r["reward"])
        else:
            tiers["long"].append(r["reward"])

    print("\n=== Mean Reward by Difficulty Tier (line count) ===", file=file)
    for tier_name in ("short", "medium", "long"):
        vals = tiers[tier_name]
        if vals:
            print(f"  {tier_name:8s}  mean={sum(vals)/len(vals):.4f}  n={len(vals)}",
                  file=file)
        else:
            print(f"  {tier_name:8s}  (no samples)", file=file)

    # -- Most common failure modes (error codes for reward=0.0) --
    error_codes: Counter[str] = Counter()
    for r in results:
        if r["reward"] == REWARD_FAIL:
            for d in r["diagnostics"]:
                code = d.get("code", "UNKNOWN")
                error_codes[code] += 1

    if error_codes:
        print("\n=== Most Common Failure Modes (reward=0.0) ===", file=file)
        for code, count in error_codes.most_common(10):
            print(f"  {code:10s}  {count}", file=file)

    # -- SFT vs RL comparison placeholder --
    print("\n=== SFT vs RL Comparison ===", file=file)
    print("  (placeholder — populate after RL training epochs)", file=file)
    print(f"  SFT-only baseline:  --", file=file)
    print(f"  RL epoch 1:         --", file=file)
    print(f"  RL epoch 2:         --", file=file)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compiler-as-verifier reward model for RL training.",
    )
    p.add_argument(
        "--predictions", type=Path, required=True,
        help="JSONL file with {task_id, source} per line.",
    )
    p.add_argument(
        "--benchmark-dir", type=Path, default=None,
        help="Directory with per-task YAML/JSON test definitions.",
    )
    p.add_argument(
        "--output", type=Path, required=True,
        help="Output JSONL path for reward-annotated results.",
    )
    p.add_argument(
        "--tkc-path", type=str, default="tkc",
        help="Path to the tkc compiler binary (default: tkc).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Simulate compilation outcomes via heuristics.",
    )
    p.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for dry-run simulation.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if not args.predictions.exists():
        print(f"error: predictions file not found: {args.predictions}",
              file=sys.stderr)
        sys.exit(1)

    # Evaluate
    results = evaluate_batch(
        predictions_path=args.predictions,
        benchmark_dir=args.benchmark_dir,
        tkc_path=args.tkc_path,
        dry_run=args.dry_run,
        seed=args.seed,
    )

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    print(f"Wrote {len(results)} results to {args.output}\n", file=sys.stderr)

    # Statistics to stdout
    print_statistics(results)


if __name__ == "__main__":
    main()
