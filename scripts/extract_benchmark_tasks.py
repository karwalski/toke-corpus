#!/usr/bin/env python3
"""Extract benchmark task descriptions and test cases from cached datasets.

Story 9.2.6 (Part 3).

Usage::

    python3 scripts/extract_benchmark_tasks.py [--cache-dir data/benchmarks]

Outputs:
    data/eval_descriptions.txt   — one description per line (firewall input)
    data/benchmark_tasks.jsonl   — full extracted tasks
    data/holdout_manifest.json   — source-level holdout manifest for split sources
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from registry.source_registry import get_split_sources
from registry.task_extractor import TaskExtractor


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract benchmark tasks.")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/benchmarks"),
        help="Directory containing downloaded benchmark data.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="Directory for output files.",
    )
    args = parser.parse_args()

    extractor = TaskExtractor(cache_dir=args.cache_dir)

    # Extract from all cached sources.
    all_tasks = extractor.extract_all()

    if not all_tasks:
        print("No cached datasets found.  Run scripts/setup_benchmarks.sh first.")
        sys.exit(1)

    # Flatten.
    flat: list = []
    for source_name, tasks in sorted(all_tasks.items()):
        flat.extend(tasks)

    # Write evaluation descriptions (for contamination firewall).
    desc_path = args.output_dir / "eval_descriptions.txt"
    extractor.write_descriptions(flat, desc_path)
    print(f"Wrote {sum(1 for _ in open(desc_path))} descriptions -> {desc_path}")

    # Write full JSONL.
    jsonl_path = args.output_dir / "benchmark_tasks.jsonl"
    extractor.write_tasks_jsonl(flat, jsonl_path)
    print(f"Wrote {len(flat)} tasks -> {jsonl_path}")

    # Generate holdout manifest for split sources.
    split_names = {s.name for s in get_split_sources()}
    manifest: dict[str, dict] = {}
    for source_name, tasks in sorted(all_tasks.items()):
        if source_name in split_names:
            manifest[source_name] = {
                "total_tasks": len(tasks),
                "holdout_ids": [t.task_id for t in tasks],
            }

    manifest_path = args.output_dir / "holdout_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote holdout manifest ({len(manifest)} sources) -> {manifest_path}")

    # Summary.
    print("\n=== Per-source counts ===")
    for source_name, tasks in sorted(all_tasks.items()):
        print(f"  {source_name:20s}  {len(tasks):>6d} tasks")
    print(f"  {'TOTAL':20s}  {len(flat):>6d} tasks")


if __name__ == "__main__":
    main()
