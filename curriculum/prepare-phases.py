#!/usr/bin/env python3
"""
Curriculum phase data preparation for toke training.

Reads corpus_default.jsonl and generates phase-specific JSONL files
for the 6-phase progressive curriculum.

Usage:
    python3 prepare-phases.py [--corpus PATH] [--outdir PATH] [--max-per-phase N]

Output:
    phase1_token_completion.jsonl
    phase2_statement_completion.jsonl
    phase3_function_completion.jsonl
    phase4_multi_function.jsonl
    phase5_multi_module.jsonl
    phase6_application.jsonl
    stats.json
"""

import argparse
import json
import os
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

# Reproducible splits
RANDOM_SEED = 42

# Phase targets (from training-next-phase.md spec)
PHASE_TARGETS = {
    1: 10_000,
    2: 20_000,
    3: 30_000,
    4: 15_000,
    5: 5_000,
    6: 1_000,
}


def parse_args():
    p = argparse.ArgumentParser(description="Prepare curriculum phase data")
    p.add_argument(
        "--corpus",
        default=None,
        help="Path to corpus JSONL (default: ../data/corpus_default.jsonl)",
    )
    p.add_argument(
        "--outdir",
        default=None,
        help="Output directory (default: same directory as this script)",
    )
    p.add_argument(
        "--max-per-phase",
        type=int,
        default=0,
        help="Max records per phase (0 = use PHASE_TARGETS)",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Complexity analysis
# ---------------------------------------------------------------------------

def analyse_record(record):
    """Extract complexity metrics from a corpus record."""
    src = record.get("tk_source", "")
    if not src:
        return None

    stmt_count = src.count(";")
    func_count = src.count("f=")
    import_count = src.count("i=")
    src_len = len(src)

    # Extract function names (may contain camelCase in older corpus records)
    func_names = re.findall(r"f=([a-zA-Z][a-zA-Z0-9]*)\(", src)

    # Check for inter-function calls: does any function body reference another function?
    has_cross_calls = False
    if len(func_names) >= 2:
        # Split source into function bodies (rough heuristic)
        for name in func_names:
            # Check if this function name appears in another function's body
            others = [n for n in func_names if n != name]
            for other in others:
                # Look for other( in the source after the first function definition
                pattern = rf"{other}\("
                if len(re.findall(pattern, src)) > 1:
                    # Name appears more than once (definition + call)
                    has_cross_calls = True
                    break
            if has_cross_calls:
                break

    # Judge score if available
    judge = record.get("judge", {})
    score = judge.get("score", 0.0) if judge else 0.0

    return {
        "stmt_count": stmt_count,
        "func_count": func_count,
        "import_count": import_count,
        "src_len": src_len,
        "func_names": func_names,
        "has_cross_calls": has_cross_calls,
        "score": score,
    }


def classify_phase(metrics):
    """Classify a record into its primary phase based on complexity.

    Returns a list of phases this record is suitable for.
    A record can appear in multiple phases (e.g., any record can be
    split for Phase 1 token completion).
    """
    phases = []
    sc = metrics["stmt_count"]
    fc = metrics["func_count"]
    ic = metrics["import_count"]
    sl = metrics["src_len"]

    # Phase 1: any record can generate token completion pairs
    phases.append(1)

    # Phase 2: statement completion — moderate complexity, 1-2 functions
    if 4 <= sc <= 12 and fc <= 2 and sl < 400:
        phases.append(2)

    # Phase 3: single function completion — exactly 1 function, 3+ statements
    if fc == 1 and sc >= 3 and sl < 500:
        phases.append(3)

    # Phase 4: multi-function — 2+ functions, moderate complexity
    if fc >= 2 and sc >= 5 and sl >= 100:
        phases.append(4)

    # Phase 5: multi-module candidate — 4+ functions, 3+ imports, complex
    if fc >= 4 and ic >= 3 and sc >= 15 and sl >= 350:
        phases.append(5)

    # Phase 6: application candidate — 5+ functions, 3+ imports, very complex
    if fc >= 5 and ic >= 3 and sc >= 25 and sl >= 500:
        phases.append(6)

    return phases


# ---------------------------------------------------------------------------
# Phase generators
# ---------------------------------------------------------------------------

def generate_phase1(record, metrics, rng):
    """Token completion: split source at random character position.

    Produces 2-3 splits per record.
    """
    src = record["tk_source"]
    results = []
    if len(src) < 20:
        return results

    num_splits = min(3, max(1, len(src) // 50))
    for _ in range(num_splits):
        # Split between 30% and 70% of source
        lo = max(10, int(len(src) * 0.3))
        hi = min(len(src) - 5, int(len(src) * 0.7))
        if lo >= hi:
            continue
        pos = rng.randint(lo, hi)

        # Avoid splitting at semicolon boundaries (too easy)
        attempts = 0
        while src[pos] == ";" and attempts < 10:
            pos = rng.randint(lo, hi)
            attempts += 1

        prefix = src[:pos]
        suffix = src[pos:]

        results.append({
            "phase": 1,
            "task": "token_completion",
            "input": prefix,
            "expected": suffix,
            "full_source": src,
            "source_id": record.get("id", ""),
        })

    return results


def generate_phase2(record, metrics, rng):
    """Statement completion: split at semicolon boundaries.

    The preceding statements become input; the next statement is expected.
    """
    src = record["tk_source"]
    results = []

    # Find all semicolon positions
    semi_positions = [i for i, c in enumerate(src) if c == ";"]
    if len(semi_positions) < 3:
        return results

    # Skip first semicolon (module declaration) and last (end of source)
    usable = semi_positions[1:-1]
    if not usable:
        return results

    # Pick 1-3 random split points
    num_splits = min(3, len(usable))
    chosen = rng.sample(usable, num_splits)

    for pos in chosen:
        prefix = src[: pos + 1]  # Include the semicolon
        remainder = src[pos + 1 :]

        # Find next semicolon for the target statement
        next_semi = remainder.find(";")
        if next_semi < 0:
            continue
        target_stmt = remainder[: next_semi + 1]

        # Skip trivial targets (just closing braces, etc.)
        stripped = target_stmt.strip().rstrip(";").strip()
        if len(stripped) < 3:
            continue

        results.append({
            "phase": 2,
            "task": "statement_completion",
            "input": prefix,
            "expected": target_stmt,
            "full_source": src,
            "source_id": record.get("id", ""),
        })

    return results


def extract_signature(func_src):
    """Extract the function signature from a function definition.

    e.g., 'f=add(a:i64;b:i64):i64{<a+b}' -> 'f=add(a:i64;b:i64):i64'
    """
    brace_pos = func_src.find("{")
    if brace_pos < 0:
        return func_src
    return func_src[:brace_pos]


def generate_phase3(record, metrics, rng):
    """Function completion: signature -> full function body."""
    src = record["tk_source"]
    results = []

    # Extract the single function (allow camelCase names from older corpus)
    func_match = re.search(r"(f=[a-zA-Z][a-zA-Z0-9]*\([^)]*\):[^{]*\{.*\})", src)
    if not func_match:
        return results

    func_src = func_match.group(1)
    sig = extract_signature(func_src)

    # Build a prompt from task_id if available
    task_id = record.get("task_id", "")
    description = ""
    if task_id:
        # Convert task_id like A-ARR-0001 to a rough description
        parts = task_id.split("-")
        if len(parts) >= 2:
            category = parts[1] if len(parts) > 1 else ""
            description = f" ({category.lower()} task)"

    prompt = f"Write a toke function: {sig}{description}"

    results.append({
        "phase": 3,
        "task": "function_completion",
        "input": prompt,
        "expected": func_src + ";",
        "full_source": src,
        "source_id": record.get("id", ""),
        "score": metrics.get("score", 0.0),
    })

    return results


def generate_phase4(record, metrics, rng):
    """Multi-function program: description -> complete module."""
    src = record["tk_source"]
    results = []

    func_names = metrics["func_names"]
    if len(func_names) < 2:
        return results

    task_id = record.get("task_id", "")
    func_list = ", ".join(func_names)
    prompt = (
        f"Write a toke program with functions: {func_list}. "
        f"Task: {task_id}"
    )

    results.append({
        "phase": 4,
        "task": "multi_function",
        "input": prompt,
        "expected": src,
        "source_id": record.get("id", ""),
        "func_count": len(func_names),
        "score": metrics.get("score", 0.0),
    })

    return results


def generate_phase5(record, metrics, rng):
    """Multi-module: decompose complex record into module descriptions."""
    src = record["tk_source"]
    results = []

    func_names = metrics["func_names"]
    import_count = metrics["import_count"]

    # For now, treat the whole record as a single-module example
    # with a description that references multi-module structure.
    # Full multi-module decomposition needs loke mining (future work).
    prompt = (
        f"Write a toke module with {len(func_names)} functions "
        f"and {import_count} imports. "
        f"Functions: {', '.join(func_names)}."
    )

    results.append({
        "phase": 5,
        "task": "multi_module",
        "input": prompt,
        "expected": src,
        "source_id": record.get("id", ""),
        "func_count": len(func_names),
        "import_count": import_count,
        "score": metrics.get("score", 0.0),
    })

    return results


def generate_phase6(record, metrics, rng):
    """Application: complex record as full application scaffold."""
    src = record["tk_source"]
    results = []

    func_names = metrics["func_names"]
    prompt = (
        f"Build a toke application with {len(func_names)} functions. "
        f"The application should include: {', '.join(func_names[:5])}."
    )

    results.append({
        "phase": 6,
        "task": "application",
        "input": prompt,
        "expected": src,
        "source_id": record.get("id", ""),
        "func_count": len(func_names),
        "import_count": metrics["import_count"],
        "stmt_count": metrics["stmt_count"],
        "score": metrics.get("score", 0.0),
    })

    return results


PHASE_GENERATORS = {
    1: generate_phase1,
    2: generate_phase2,
    3: generate_phase3,
    4: generate_phase4,
    5: generate_phase5,
    6: generate_phase6,
}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    corpus_path = args.corpus or str(script_dir.parent / "data" / "corpus_default.jsonl")
    outdir = Path(args.outdir) if args.outdir else script_dir

    if not os.path.isfile(corpus_path):
        print(f"ERROR: Corpus file not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    outdir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(RANDOM_SEED)

    targets = dict(PHASE_TARGETS)
    if args.max_per_phase > 0:
        targets = {p: args.max_per_phase for p in targets}

    # Phase buffers
    phase_data = defaultdict(list)
    phase_full = {p: False for p in range(1, 7)}

    # Stats
    total_records = 0
    skipped = 0
    phase_eligible = defaultdict(int)

    print(f"Reading corpus: {corpus_path}")
    print(f"Output directory: {outdir}")
    print()

    with open(corpus_path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            total_records += 1
            if total_records % 10000 == 0:
                counts = {p: len(phase_data[p]) for p in range(1, 7)}
                print(f"  Processed {total_records} records... {counts}")

            metrics = analyse_record(record)
            if metrics is None:
                skipped += 1
                continue

            phases = classify_phase(metrics)

            for phase in phases:
                phase_eligible[phase] += 1

                # Skip if we already have enough for this phase
                if len(phase_data[phase]) >= targets[phase]:
                    continue

                generator = PHASE_GENERATORS[phase]
                examples = generator(record, metrics, rng)
                for ex in examples:
                    if len(phase_data[phase]) < targets[phase]:
                        phase_data[phase].append(ex)

    # Write output files
    print()
    print("Writing phase files:")

    filenames = {
        1: "phase1_token_completion.jsonl",
        2: "phase2_statement_completion.jsonl",
        3: "phase3_function_completion.jsonl",
        4: "phase4_multi_function.jsonl",
        5: "phase5_multi_module.jsonl",
        6: "phase6_application.jsonl",
    }

    stats = {
        "corpus_file": corpus_path,
        "total_corpus_records": total_records,
        "skipped_records": skipped,
        "phases": {},
    }

    for phase in range(1, 7):
        fname = filenames[phase]
        fpath = outdir / fname
        data = phase_data[phase]

        # Shuffle for training
        rng.shuffle(data)

        with open(fpath, "w") as out:
            for item in data:
                out.write(json.dumps(item, ensure_ascii=False) + "\n")

        target = targets[phase]
        actual = len(data)
        eligible = phase_eligible[phase]
        pct = (actual / target * 100) if target > 0 else 0

        stats["phases"][str(phase)] = {
            "name": filenames[phase].replace(".jsonl", ""),
            "target": target,
            "actual": actual,
            "eligible_records": eligible,
            "fill_pct": round(pct, 1),
        }

        status = "OK" if actual >= target else f"GAP ({target - actual} short)"
        print(f"  Phase {phase}: {actual:>6} / {target:>6} ({pct:5.1f}%) — {fname} [{status}]")

    # Write stats
    stats_path = outdir / "stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\n  Stats written to: {stats_path}")

    # Summary
    total_examples = sum(len(phase_data[p]) for p in range(1, 7))
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"  Corpus records read:  {total_records}")
    print(f"  Records skipped:      {skipped}")
    print(f"  Total phase examples: {total_examples}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
