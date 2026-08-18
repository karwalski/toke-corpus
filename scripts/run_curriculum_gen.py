#!/usr/bin/env python3
"""Batch curriculum generation runner (Story 9.2.1).

Generates the full V2 curriculum (Phase A + Domain categories) and writes
task specifications to data/task_specs_v2.jsonl.

Usage::

    cd /Users/matthew.watt/tk/toke-corpus
    .venv/bin/python scripts/run_curriculum_gen.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

# Ensure repo root is on sys.path so packages are importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from generator.curriculum_v2 import CurriculumGeneratorV2

OUTPUT_PATH = REPO_ROOT / "data" / "task_specs_v2.jsonl"


def main() -> None:
    print("Generating V2 curriculum ...")
    t0 = time.time()

    gen = CurriculumGeneratorV2()
    tasks = gen.generate()

    elapsed = time.time() - t0
    print(f"Generated {len(tasks)} tasks in {elapsed:.1f}s")

    # Write JSONL
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cat_counts: Counter[str] = Counter()

    with open(OUTPUT_PATH, "w", encoding="utf-8") as out:
        for task in tasks:
            record = {
                "task_id": task.task_id,
                "category": task.category,
                "description": task.description,
                "input_types": task.input_types,
                "output_type": task.output_type,
                "test_cases": [
                    {"inputs": tc.inputs, "expected": tc.expected}
                    for tc in task.test_cases
                ],
                "difficulty": task.difficulty,
                "domain_context": task.domain_context,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            cat_counts[task.category] += 1

    print(f"\nOutput: {OUTPUT_PATH}")
    print(f"\nTasks per category:")
    for cat in sorted(cat_counts):
        print(f"  {cat:10s}  {cat_counts[cat]:>6d}")
    print(f"  {'TOTAL':10s}  {len(tasks):>6d}")


if __name__ == "__main__":
    main()
