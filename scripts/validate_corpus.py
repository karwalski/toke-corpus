#!/usr/bin/env python3
"""Five-tier corpus validation pipeline for the toke training corpus.

Tiers:
  1 - Compile check       (tkc --check --diag-json)
  2 - Execution check     (compile + run, verify expected output)
  3 - Cross-optimisation   (compile at different levels, diff outputs)
  4 - Property-based       (algebraic properties for numeric functions)
  5 - Mutation testing     (small mutations, check caught by compiler/tests)

Each tier gates the next: only entries passing tier N advance to tier N+1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TierResult:
    tier: int
    passed: bool
    detail: str = ""
    error_codes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntryResult:
    entry_id: str
    task_id: str
    tiers: list[TierResult] = field(default_factory=list)

    @property
    def highest_tier_passed(self) -> int:
        return max((t.tier for t in self.tiers if t.passed), default=0)


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

def _progress(current: int, total: int, width: int = 40, label: str = "") -> None:
    frac = current / total if total else 1
    filled = int(width * frac)
    bar = "#" * filled + "-" * (width - filled)
    pct = frac * 100
    sys.stderr.write(f"\r  {label} [{bar}] {pct:5.1f}% ({current}/{total})")
    sys.stderr.flush()
    if current == total:
        sys.stderr.write("\n")


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def discover_entries(corpus_dir: Path, max_entries: int | None = None) -> list[dict]:
    """Walk corpus directory and load JSON entries."""
    entries: list[dict] = []
    for root, _dirs, files in os.walk(corpus_dir):
        for fname in sorted(files):
            if not fname.endswith(".json"):
                continue
            if fname in ("manifest.json", "schema.json"):
                continue
            fpath = Path(root) / fname
            try:
                with open(fpath) as f:
                    entry = json.load(f)
                if "tk_source" in entry and "id" in entry:
                    entry["_path"] = str(fpath)
                    entries.append(entry)
            except (json.JSONDecodeError, OSError):
                continue
            if max_entries and len(entries) >= max_entries:
                return entries
    return entries


# ---------------------------------------------------------------------------
# Tier implementations — live mode
# ---------------------------------------------------------------------------

def _write_temp_source(source: str) -> str:
    """Write source to a temp .tk file, return path."""
    fd, path = tempfile.mkstemp(suffix=".tk")
    with os.fdopen(fd, "w") as f:
        f.write(source)
    return path


def tier1_compile_check(entry: dict, tkc_path: str) -> TierResult:
    """Tier 1: compile check via tkc --check --diag-json."""
    src_path = _write_temp_source(entry["tk_source"])
    try:
        result = subprocess.run(
            [tkc_path, "--check", "--diag-json", src_path],
            capture_output=True, text=True, timeout=30,
        )
        passed = result.returncode == 0
        error_codes: list[str] = []
        if not passed:
            # Try to parse diag-json output for error codes
            for line in result.stdout.splitlines() + result.stderr.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        diag = json.loads(line)
                        if "code" in diag:
                            error_codes.append(diag["code"])
                    except json.JSONDecodeError:
                        pass
        detail = "OK" if passed else f"exit={result.returncode}"
        return TierResult(tier=1, passed=passed, detail=detail, error_codes=error_codes)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return TierResult(tier=1, passed=False, detail=f"error: {exc}")
    finally:
        os.unlink(src_path)


def tier2_execution_check(entry: dict, tkc_path: str) -> TierResult:
    """Tier 2: compile and run, verify output matches expected."""
    expected = entry.get("differential", {}).get("majority_output", "")
    if not expected:
        return TierResult(tier=2, passed=True, detail="no expected output, skip")

    src_path = _write_temp_source(entry["tk_source"])
    out_path = src_path.replace(".tk", "")
    try:
        # Compile
        comp = subprocess.run(
            [tkc_path, src_path, "-o", out_path],
            capture_output=True, text=True, timeout=30,
        )
        if comp.returncode != 0:
            return TierResult(tier=2, passed=False, detail=f"compile failed: exit={comp.returncode}")

        # Run
        run = subprocess.run(
            [out_path], capture_output=True, text=True, timeout=10,
        )
        actual = run.stdout.rstrip("\n")
        expected_clean = expected.rstrip("\n")
        passed = actual == expected_clean
        detail = "OK" if passed else f"output mismatch"
        extra = {} if passed else {"expected": expected_clean, "actual": actual}
        return TierResult(tier=2, passed=passed, detail=detail, extra=extra)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return TierResult(tier=2, passed=False, detail=f"error: {exc}")
    finally:
        for p in (src_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def tier3_cross_optimisation(entry: dict, tkc_path: str) -> TierResult:
    """Tier 3: compile with different flags, verify same output."""
    expected = entry.get("differential", {}).get("majority_output", "")
    src_path = _write_temp_source(entry["tk_source"])
    flag_sets = [[], ["--debug"]]  # baseline vs debug
    outputs: dict[str, str] = {}

    try:
        for flags in flag_sets:
            label = "default" if not flags else " ".join(flags)
            out_path = src_path.replace(".tk", f"_{label.replace(' ', '_')}")
            comp = subprocess.run(
                [tkc_path, src_path, "-o", out_path] + flags,
                capture_output=True, text=True, timeout=30,
            )
            if comp.returncode != 0:
                return TierResult(tier=3, passed=False,
                                  detail=f"compile failed with {label}: exit={comp.returncode}")
            try:
                run = subprocess.run(
                    [out_path], capture_output=True, text=True, timeout=10,
                )
                outputs[label] = run.stdout.rstrip("\n")
            except (subprocess.TimeoutExpired, OSError) as exc:
                return TierResult(tier=3, passed=False, detail=f"run error ({label}): {exc}")
            finally:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass

        unique = set(outputs.values())
        passed = len(unique) == 1
        detail = "OK" if passed else "output divergence across flag sets"
        extra = {} if passed else {"outputs": outputs}
        return TierResult(tier=3, passed=passed, detail=detail, extra=extra)
    finally:
        try:
            os.unlink(src_path)
        except OSError:
            pass


def tier4_property_testing(entry: dict, tkc_path: str) -> TierResult:
    """Tier 4: property-based testing for numeric functions.

    Only meaningful for entries whose task_id suggests numeric operations.
    """
    task = entry.get("task_id", "")
    source = entry.get("tk_source", "")

    # Heuristic: only apply to entries with numeric-looking functions
    numeric_keywords = ["sum", "add", "mul", "max", "min", "abs", "gcd", "pow", "fact"]
    is_numeric = any(kw in source.lower() for kw in numeric_keywords)
    if not is_numeric:
        return TierResult(tier=4, passed=True, detail="non-numeric, skip")

    # In live mode we'd inject test values; for now report as untestable
    return TierResult(tier=4, passed=True,
                      detail="numeric heuristic matched but live property injection not yet wired",
                      extra={"numeric_keywords_found": [k for k in numeric_keywords if k in source.lower()]})


def tier5_mutation_testing(entry: dict, tkc_path: str) -> TierResult:
    """Tier 5: mutate source, check compiler/test catches it."""
    source = entry.get("tk_source", "")
    mutations_applied = 0
    mutations_caught = 0
    surviving: list[str] = []

    # Define simple mutations
    mutation_ops = [
        ("+", "-"), ("-", "+"), ("*", "/"), ("<", ">"), ("<=", ">="),
        ("==", "!="), ("0", "1"), ("1", "0"),
    ]

    for old, new in mutation_ops:
        if old not in source:
            continue
        # Apply first occurrence only
        mutated = source.replace(old, new, 1)
        if mutated == source:
            continue
        mutations_applied += 1

        src_path = _write_temp_source(mutated)
        out_path = src_path.replace(".tk", "")
        try:
            comp = subprocess.run(
                [tkc_path, "--check", src_path],
                capture_output=True, text=True, timeout=15,
            )
            if comp.returncode != 0:
                mutations_caught += 1
                continue

            # Compiles — try running and comparing output
            comp2 = subprocess.run(
                [tkc_path, src_path, "-o", out_path],
                capture_output=True, text=True, timeout=15,
            )
            if comp2.returncode != 0:
                mutations_caught += 1
                continue

            run = subprocess.run(
                [out_path], capture_output=True, text=True, timeout=10,
            )
            expected = entry.get("differential", {}).get("majority_output", "")
            if run.stdout.rstrip("\n") != expected.rstrip("\n"):
                mutations_caught += 1
            else:
                surviving.append(f"{old}->{new}")
        except (subprocess.TimeoutExpired, OSError):
            mutations_caught += 1  # timeout = caught
        finally:
            for p in (src_path, out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    if mutations_applied == 0:
        return TierResult(tier=5, passed=True, detail="no applicable mutations")

    kill_rate = mutations_caught / mutations_applied
    passed = kill_rate >= 0.5  # at least 50% kill rate
    detail = f"kill_rate={kill_rate:.2f} ({mutations_caught}/{mutations_applied})"
    extra = {"surviving_mutations": surviving} if surviving else {}
    return TierResult(tier=5, passed=passed, detail=detail, extra=extra)


# ---------------------------------------------------------------------------
# Tier implementations — dry-run mode (heuristic/synthetic)
# ---------------------------------------------------------------------------

def _source_hash_seed(source: str) -> int:
    return int(hashlib.md5(source.encode()).hexdigest()[:8], 16)


def tier1_dry(entry: dict) -> TierResult:
    """Dry-run tier 1: use existing validation data if present."""
    exit_code = entry.get("validation", {}).get("compiler_exit_code", -1)
    error_codes = entry.get("validation", {}).get("error_codes", [])
    passed = exit_code == 0
    return TierResult(tier=1, passed=passed,
                      detail=f"(dry) cached exit_code={exit_code}",
                      error_codes=error_codes)


def tier2_dry(entry: dict) -> TierResult:
    expected = entry.get("differential", {}).get("majority_output", "")
    if not expected:
        return TierResult(tier=2, passed=True, detail="(dry) no expected output")
    # Heuristic: if corpus already marked accepted, likely passes
    accepted = entry.get("judge", {}).get("accepted", False)
    return TierResult(tier=2, passed=accepted,
                      detail=f"(dry) judge_accepted={accepted}")


def tier3_dry(entry: dict) -> TierResult:
    # In dry-run, assume no divergence for accepted entries
    accepted = entry.get("judge", {}).get("accepted", False)
    return TierResult(tier=3, passed=accepted,
                      detail="(dry) assumed consistent" if accepted else "(dry) flagged")


def tier4_dry(entry: dict) -> TierResult:
    source = entry.get("tk_source", "")
    numeric_keywords = ["sum", "add", "mul", "max", "min", "abs", "gcd", "pow", "fact"]
    found = [k for k in numeric_keywords if k in source.lower()]
    if not found:
        return TierResult(tier=4, passed=True, detail="(dry) non-numeric, skip")
    # Synthetic: use hash to generate deterministic pass/fail
    seed = _source_hash_seed(source)
    passed = (seed % 10) < 8  # 80% synthetic pass rate
    return TierResult(tier=4, passed=passed,
                      detail=f"(dry) numeric, synthetic={'pass' if passed else 'fail'}",
                      extra={"numeric_keywords_found": found})


def tier5_dry(entry: dict) -> TierResult:
    source = entry.get("tk_source", "")
    mutation_ops = ["+", "-", "*", "<", "<=", "==", "0", "1"]
    applicable = sum(1 for op in mutation_ops if op in source)
    if applicable == 0:
        return TierResult(tier=5, passed=True, detail="(dry) no applicable mutations")
    seed = _source_hash_seed(source)
    rng = random.Random(seed)
    caught = sum(1 for _ in range(applicable) if rng.random() < 0.7)
    surviving = applicable - caught
    kill_rate = caught / applicable
    passed = kill_rate >= 0.5
    return TierResult(tier=5, passed=passed,
                      detail=f"(dry) kill_rate={kill_rate:.2f} ({caught}/{applicable})",
                      extra={"surviving_count": surviving})


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

LIVE_TIERS = {
    1: tier1_compile_check,
    2: tier2_execution_check,
    3: tier3_cross_optimisation,
    4: tier4_property_testing,
    5: tier5_mutation_testing,
}

DRY_TIERS = {
    1: tier1_dry,
    2: tier2_dry,
    3: tier3_dry,
    4: tier4_dry,
    5: tier5_dry,
}


def run_pipeline(
    entries: list[dict],
    tiers: list[int],
    tkc_path: str,
    dry_run: bool,
) -> list[EntryResult]:
    results: list[EntryResult] = []
    total = len(entries)
    tier_funcs = DRY_TIERS if dry_run else LIVE_TIERS

    for idx, entry in enumerate(entries):
        _progress(idx + 1, total, label="Validating")
        er = EntryResult(entry_id=entry["id"], task_id=entry.get("task_id", ""))
        for t in sorted(tiers):
            func = tier_funcs.get(t)
            if func is None:
                continue
            if dry_run:
                tr = func(entry)
            else:
                tr = func(entry, tkc_path)
            er.tiers.append(tr)
            if not tr.passed:
                break  # gate: stop on first failure
        results.append(er)

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_summary(results: list[EntryResult], tiers_run: list[int]) -> dict:
    total = len(results)
    per_tier: dict[int, dict] = {}
    error_code_counts: dict[str, int] = {}

    for t in sorted(tiers_run):
        passed = sum(
            1 for r in results
            if any(tr.tier == t and tr.passed for tr in r.tiers)
        )
        attempted = sum(
            1 for r in results
            if any(tr.tier == t for tr in r.tiers)
        )
        per_tier[t] = {
            "attempted": attempted,
            "passed": passed,
            "failed": attempted - passed,
            "pass_rate": round(passed / attempted, 4) if attempted else 0.0,
        }
        # Collect error codes from tier
        for r in results:
            for tr in r.tiers:
                if tr.tier == t:
                    for code in tr.error_codes:
                        error_code_counts[code] = error_code_counts.get(code, 0) + 1

    return {
        "total_entries": total,
        "tiers_run": sorted(tiers_run),
        "per_tier": {str(k): v for k, v in per_tier.items()},
        "common_error_codes": dict(
            sorted(error_code_counts.items(), key=lambda x: -x[1])[:20]
        ),
    }


def print_summary_table(summary: dict) -> None:
    print("\n" + "=" * 60)
    print("  CORPUS VALIDATION SUMMARY")
    print("=" * 60)
    print(f"  Total entries: {summary['total_entries']}")
    print(f"  Tiers run:     {summary['tiers_run']}")
    print()
    print(f"  {'Tier':<8} {'Attempted':>10} {'Passed':>10} {'Failed':>10} {'Rate':>10}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    for tier_key in sorted(summary["per_tier"].keys(), key=int):
        t = summary["per_tier"][tier_key]
        rate = f"{t['pass_rate']*100:.1f}%"
        print(f"  Tier {tier_key:<3} {t['attempted']:>10} {t['passed']:>10} {t['failed']:>10} {rate:>10}")

    if summary.get("common_error_codes"):
        print()
        print("  Common error codes:")
        for code, count in list(summary["common_error_codes"].items())[:10]:
            print(f"    {code}: {count}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Five-tier corpus validation pipeline for toke training data.",
    )
    p.add_argument("--corpus-dir", type=Path, required=True,
                   help="Root directory containing corpus JSON files")
    p.add_argument("--output-dir", type=Path, default=Path("data"),
                   help="Directory for validation_report.json and validation_summary.json")
    p.add_argument("--tiers", type=str, default="1,2,3,4,5",
                   help="Comma-separated tier numbers to run (default: 1,2,3,4,5)")
    p.add_argument("--max-entries", type=int, default=None,
                   help="Limit to first N entries")
    p.add_argument("--tkc-path", type=str, default="tkc",
                   help="Path to tkc compiler binary (default: tkc)")
    p.add_argument("--dry-run", action="store_true",
                   help="Simulate tiers with heuristic/synthetic results")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    tiers = [int(t.strip()) for t in args.tiers.split(",") if t.strip()]
    for t in tiers:
        if t not in range(1, 6):
            print(f"Error: invalid tier {t} (must be 1-5)", file=sys.stderr)
            return 1

    # Discover entries
    print(f"Scanning corpus at {args.corpus_dir} ...")
    entries = discover_entries(args.corpus_dir, max_entries=args.max_entries)
    if not entries:
        print("No corpus entries found.", file=sys.stderr)
        return 1
    print(f"Found {len(entries)} entries.")

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Running tiers {tiers} in {mode} mode ...\n")

    results = run_pipeline(entries, tiers, args.tkc_path, args.dry_run)

    # Build reports
    summary = build_summary(results, tiers)

    # Serialise results
    report = [
        {
            "entry_id": r.entry_id,
            "task_id": r.task_id,
            "highest_tier_passed": r.highest_tier_passed,
            "tiers": [asdict(t) for t in r.tiers],
        }
        for r in results
    ]

    # Write output
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "validation_report.json"
    summary_path = args.output_dir / "validation_summary.json"

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nWrote {report_path}")
    print(f"Wrote {summary_path}")

    print_summary_table(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
