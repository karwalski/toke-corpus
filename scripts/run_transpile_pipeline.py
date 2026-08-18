#!/usr/bin/env python3
"""Batch runner for the transpile pipeline (Story 9.2.2).

Generates task specs from CurriculumGeneratorV2, writes LLM prompt files,
and optionally processes pre-generated Python responses through the
transpile+validate pipeline to produce a JSONL corpus file.

Usage::

    cd /Users/matthew.watt/tk/toke-corpus

    # Dry-run: generate prompts only (no LLM calls)
    .venv/bin/python scripts/run_transpile_pipeline.py --dry-run -n 100

    # Process pre-generated Python responses
    .venv/bin/python scripts/run_transpile_pipeline.py \\
        --from-responses data/responses/ -n 100
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

# Ensure repo root is on sys.path so packages are importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from generator.curriculum_v2 import CurriculumGeneratorV2, TaskSpecV2
from pipeline.transpile_pipeline import TranspilePipeline

DEFAULT_PROMPT_DIR = REPO_ROOT / "data" / "prompts"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "corpus_transpile_pipeline.jsonl"


# ---------------------------------------------------------------------------
# Worker function for multiprocessing (must be top-level for pickling)
# ---------------------------------------------------------------------------


def _process_one(args: tuple[dict, str, str]) -> dict | None:
    """Process a single (task_dict, python_response, tkc_path) tuple.

    Reconstructs TaskSpecV2 from dict and runs the pipeline.
    """
    task_dict, python_response, tkc_path = args

    from generator.curriculum_v2 import TaskSpecV2, TestCase
    from pipeline.transpile_pipeline import TranspilePipeline

    # Reconstruct TaskSpecV2 from serialised dict
    test_cases = [TestCase(**tc) for tc in task_dict.get("test_cases", [])]
    task = TaskSpecV2(
        task_id=task_dict["task_id"],
        category=task_dict["category"],
        description=task_dict["description"],
        input_types=task_dict.get("input_types", []),
        output_type=task_dict.get("output_type", "Str"),
        test_cases=test_cases,
        difficulty=task_dict.get("difficulty", 1),
        domain_context=task_dict.get("domain_context", ""),
    )

    pipeline = TranspilePipeline(tkc_path=Path(tkc_path))
    return pipeline.process_task(task, python_response)


def _task_to_dict(task: TaskSpecV2) -> dict:
    """Serialise a TaskSpecV2 to a plain dict for multiprocessing."""
    return {
        "task_id": task.task_id,
        "category": task.category,
        "description": task.description,
        "input_types": list(task.input_types),
        "output_type": task.output_type,
        "test_cases": [
            {"inputs": list(tc.inputs), "expected": tc.expected}
            for tc in task.test_cases
        ],
        "difficulty": task.difficulty,
        "domain_context": task.domain_context,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch transpile pipeline: task -> Python prompt -> toke corpus"
    )
    parser.add_argument(
        "-n", "--count", type=int, default=100,
        help="Number of tasks to generate (default: 100)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for curriculum generator (default: 42)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate prompts only, do not process responses",
    )
    parser.add_argument(
        "--from-responses", type=str, default=None, metavar="DIR",
        help="Directory containing pre-generated Python response files "
             "(named {task_id}.py)",
    )
    parser.add_argument(
        "--prompt-dir", type=str, default=str(DEFAULT_PROMPT_DIR),
        help=f"Directory to write prompt files (default: {DEFAULT_PROMPT_DIR})",
    )
    parser.add_argument(
        "--output", type=str, default=str(DEFAULT_OUTPUT),
        help=f"Output JSONL path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--tkc", type=str, default="/Users/matthew.watt/tk/toke/tkc",
        help="Path to tkc binary",
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Number of worker processes (default: cpu_count)",
    )
    args = parser.parse_args()

    prompt_dir = Path(args.prompt_dir)
    output_path = Path(args.output)
    tkc_path = args.tkc
    workers = args.workers or os.cpu_count() or 4

    # -----------------------------------------------------------------------
    # 1. Generate task specs
    # -----------------------------------------------------------------------
    print(f"Generating {args.count} task specs (seed={args.seed}) ...")
    gen = CurriculumGeneratorV2(seed=args.seed)
    all_tasks = gen.generate()
    tasks = all_tasks[: args.count]
    print(f"  Selected {len(tasks)} tasks from {len(all_tasks)} total")

    # -----------------------------------------------------------------------
    # 2. Write prompts
    # -----------------------------------------------------------------------
    prompt_dir.mkdir(parents=True, exist_ok=True)
    pipeline = TranspilePipeline(tkc_path=Path(tkc_path))

    print(f"Writing prompts to {prompt_dir}/ ...")
    for task in tasks:
        prompt = pipeline.generate_prompt(task)
        prompt_file = prompt_dir / f"{task.task_id}.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

    print(f"  Wrote {len(tasks)} prompt files")

    if args.dry_run:
        print("Dry-run complete. Prompts written; no transpilation performed.")
        return

    # -----------------------------------------------------------------------
    # 3. Load responses
    # -----------------------------------------------------------------------
    if args.from_responses is None:
        print(
            "ERROR: --from-responses DIR is required when not in --dry-run mode.",
            file=sys.stderr,
        )
        sys.exit(1)

    resp_dir = Path(args.from_responses)
    if not resp_dir.is_dir():
        print(f"ERROR: Response directory not found: {resp_dir}", file=sys.stderr)
        sys.exit(1)

    work_items: list[tuple[dict, str, str]] = []
    skipped = 0
    for task in tasks:
        resp_file = resp_dir / f"{task.task_id}.py"
        if not resp_file.is_file():
            skipped += 1
            continue
        python_response = resp_file.read_text(encoding="utf-8")
        work_items.append((_task_to_dict(task), python_response, tkc_path))

    print(
        f"Loaded {len(work_items)} responses "
        f"(skipped {skipped} tasks without response files)"
    )

    if not work_items:
        print("No work items to process.")
        return

    # -----------------------------------------------------------------------
    # 4. Process with multiprocessing
    # -----------------------------------------------------------------------
    print(f"Processing with {workers} workers ...")
    start = time.time()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    failed = 0

    with open(output_path, "w", encoding="utf-8") as out_f:
        with Pool(processes=workers) as pool:
            for i, result in enumerate(
                pool.imap(_process_one, work_items, chunksize=50)
            ):
                if (i + 1) % 100 == 0:
                    elapsed = time.time() - start
                    rate = (i + 1) / elapsed if elapsed > 0 else 0
                    print(
                        f"  Progress: {i + 1}/{len(work_items)} "
                        f"({rate:.0f}/s) written={written} failed={failed}"
                    )

                if result is None:
                    failed += 1
                    continue

                out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
                written += 1

    elapsed = time.time() - start

    print()
    print("=" * 60)
    print("  Transpile Pipeline — Results")
    print("=" * 60)
    print(f"  Tasks processed:  {len(work_items):>8,}")
    print(f"  Corpus entries:   {written:>8,}")
    print(f"  Failed:           {failed:>8,}")
    print(f"  Elapsed:          {elapsed:>8.1f}s")
    print(f"  Output: {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
