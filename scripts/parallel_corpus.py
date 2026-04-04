#!/usr/bin/env python3
"""Build semantically equivalent Python/toke translation pairs.

Reads toke solutions from the benchmark solutions directory and Python
reference solutions from the baselines file, pairs them by task_id, and
outputs JSONL with optional token counts (requires tiktoken).

Usage::

    python scripts/parallel_corpus.py \\
        --benchmark-dir /path/to/toke-benchmark \\
        --output data/parallel_corpus.jsonl

Story 9.1.2 -- Parallel corpus: Python <-> toke translation pairs.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import statistics
import sys
import textwrap
from pathlib import Path


def parse_python_solutions(solutions_path: Path) -> dict[str, str]:
    """Extract task_id -> source code from the Python solutions file.

    Each solution is a function decorated with @task("task-X-NNNN").
    We extract the full source text of each decorated function.
    """
    source = solutions_path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    tasks: dict[str, str] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            # Match @task("task-X-NNNN")
            if not isinstance(decorator, ast.Call):
                continue
            if not (isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "task"):
                continue
            if not decorator.args:
                continue
            arg = decorator.args[0]
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                continue
            task_id = arg.value

            # Extract source lines for this function (including decorator)
            start = node.decorator_list[0].lineno - 1  # 0-indexed
            end = node.end_lineno  # already 1-indexed, use as exclusive
            func_source = "".join(lines[start:end]).rstrip()

            # Dedent in case of indentation
            func_source = textwrap.dedent(func_source)
            tasks[task_id] = func_source

    return tasks


def load_toke_solutions(solutions_dir: Path) -> dict[str, str]:
    """Load task_id -> source from toke solution files.

    Files are named task-X-NNNN.toke; the task_id is the stem.
    """
    tasks: dict[str, str] = {}
    for path in sorted(solutions_dir.glob("*.toke")):
        task_id = path.stem  # e.g. "task-a-0001"
        tasks[task_id] = path.read_text().rstrip()
    return tasks


def count_tokens(text: str, encoder) -> int | None:
    """Count tokens using a tiktoken encoder, or return None."""
    if encoder is None:
        return None
    return len(encoder.encode(text))


def build_pairs(
    python_solutions: dict[str, str],
    toke_solutions: dict[str, str],
    encoder,
) -> list[dict]:
    """Build paired records for all tasks present in both sets."""
    common_ids = sorted(set(python_solutions) & set(toke_solutions))
    records = []

    for task_id in common_ids:
        py_src = python_solutions[task_id]
        tk_src = toke_solutions[task_id]

        py_tok = count_tokens(py_src, encoder)
        tk_tok = count_tokens(tk_src, encoder)

        ratio = None
        if py_tok and tk_tok:
            ratio = round(tk_tok / py_tok, 4)

        record: dict = {
            "task_id": task_id,
            "python_source": py_src,
            "toke_source": tk_src,
        }

        if py_tok is not None:
            record["python_tokens"] = py_tok
        if tk_tok is not None:
            record["toke_tokens"] = tk_tok
        if ratio is not None:
            record["token_ratio"] = ratio

        records.append(record)

    return records


def print_summary(records: list[dict], file=sys.stderr) -> None:
    """Print summary statistics to stderr."""
    total = len(records)
    print(f"Total pairs: {total}", file=file)

    ratios = [r["token_ratio"] for r in records if "token_ratio" in r]
    if not ratios:
        print("Token counting unavailable -- no ratio statistics.", file=file)
        return

    mean_r = round(statistics.mean(ratios), 4)
    median_r = round(statistics.median(ratios), 4)
    sorted_ratios = sorted(ratios)
    n = len(sorted_ratios)

    def percentile(data: list[float], p: float) -> float:
        k = (p / 100) * (len(data) - 1)
        f = int(k)
        c = f + 1 if f + 1 < len(data) else f
        return round(data[f] + (k - f) * (data[c] - data[f]), 4)

    p10 = percentile(sorted_ratios, 10)
    p90 = percentile(sorted_ratios, 90)
    shorter = sum(1 for r in ratios if r < 1.0)

    print(f"Mean token ratio (toke/python): {mean_r}", file=file)
    print(f"Median token ratio:             {median_r}", file=file)
    print(f"P10 token ratio:                {p10}", file=file)
    print(f"P90 token ratio:                {p90}", file=file)
    print(f"Tasks where toke is shorter:    {shorter}/{len(ratios)}", file=file)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Python/toke parallel corpus from benchmark solutions."
    )
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=Path("/Users/matthew.watt/tk/toke-benchmark"),
        help="Base directory containing solutions/ and baselines/python/",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/parallel_corpus.jsonl"),
        help="Output JSONL file path",
    )
    parser.add_argument(
        "--tokenizer",
        default="cl100k_base",
        help="Tokenizer encoding name (default: cl100k_base)",
    )
    args = parser.parse_args()

    # Resolve paths
    solutions_dir = args.benchmark_dir / "solutions"
    python_file = args.benchmark_dir / "baselines" / "python" / "solutions.py"

    if not solutions_dir.is_dir():
        print(f"Error: solutions directory not found: {solutions_dir}",
              file=sys.stderr)
        sys.exit(1)

    if not python_file.is_file():
        print(f"Error: Python solutions file not found: {python_file}",
              file=sys.stderr)
        sys.exit(1)

    # Load encoder (graceful degradation)
    encoder = None
    try:
        import tiktoken
        encoder = tiktoken.get_encoding(args.tokenizer)
        print(f"Using tokenizer: {args.tokenizer}", file=sys.stderr)
    except ImportError:
        print("tiktoken not installed -- skipping token counts.",
              file=sys.stderr)
    except Exception as e:
        print(f"Could not load tokenizer {args.tokenizer}: {e}",
              file=sys.stderr)

    # Load sources
    python_solutions = parse_python_solutions(python_file)
    toke_solutions = load_toke_solutions(solutions_dir)

    print(f"Python solutions loaded: {len(python_solutions)}", file=sys.stderr)
    print(f"Toke solutions loaded:   {len(toke_solutions)}", file=sys.stderr)

    # Build pairs
    records = build_pairs(python_solutions, toke_solutions, encoder)

    if not records:
        print("Warning: no matching pairs found.", file=sys.stderr)
        sys.exit(1)

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(records)} pairs to {args.output}", file=sys.stderr)
    print("---", file=sys.stderr)
    print_summary(records)


if __name__ == "__main__":
    main()
