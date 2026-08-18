#!/usr/bin/env python3
"""Batch runner for domain-stratified toke corpus prompt generation.

Generates N tasks per domain and writes prompts to data/domain_prompts/
organised by domain subdirectory.

Usage:
    python -m scripts.run_domain_gen                      # full run (500/domain)
    python -m scripts.run_domain_gen --dry-run             # prompts only, no files
    python -m scripts.run_domain_gen --stats               # distribution stats
    python -m scripts.run_domain_gen --per-domain 100      # custom count
    python -m scripts.run_domain_gen --seed 99             # custom seed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from multiprocessing import Pool, cpu_count
from pathlib import Path

# Ensure project root is on sys.path so imports work when invoked as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from prompts.domain.domain_generator import DomainStratifiedGenerator
from prompts.domain.domain_templates import DOMAINS


# ---------------------------------------------------------------------------
# Worker function for multiprocessing
# ---------------------------------------------------------------------------

def _generate_domain(args: tuple[str, int, int, Path | None]) -> list[dict]:
    """Generate tasks for one domain. Designed for multiprocessing.Pool."""
    domain, per_domain, seed, output_dir = args
    gen = DomainStratifiedGenerator()
    tasks = gen.generate(domain, per_domain, seed=seed)

    if output_dir is not None:
        domain_dir = output_dir / domain.lower()
        domain_dir.mkdir(parents=True, exist_ok=True)

        for task in tasks:
            prompt = gen.to_prompt(task)
            prompt_path = domain_dir / f"{task['task_id']}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")

            meta_path = domain_dir / f"{task['task_id']}.json"
            meta = {k: v for k, v in task.items() if k != "stdlib_context"}
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    return tasks


# ---------------------------------------------------------------------------
# Stats printer
# ---------------------------------------------------------------------------

def _print_stats(all_tasks: list[dict]) -> None:
    """Print distribution statistics for generated tasks."""
    domain_counts: Counter[str] = Counter()
    difficulty_counts: Counter[int] = Counter()
    template_counts: Counter[str] = Counter()

    for t in all_tasks:
        domain_counts[t["domain"]] += 1
        difficulty_counts[t["difficulty"]] += 1
        template_counts[t["template_id"]] += 1

    print(f"\n{'='*60}")
    print(f"  Domain-Stratified Generation Statistics")
    print(f"{'='*60}")
    print(f"  Total tasks: {len(all_tasks)}")
    print(f"\n  Tasks per domain:")
    for domain in sorted(domain_counts):
        print(f"    {domain:10s}: {domain_counts[domain]:6d}")

    print(f"\n  Tasks per difficulty:")
    for diff in sorted(difficulty_counts):
        print(f"    Level {diff}: {difficulty_counts[diff]:6d}")

    print(f"\n  Unique templates used: {len(template_counts)}")
    print(f"  Unique task IDs: {len(set(t['task_id'] for t in all_tasks))}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate domain-stratified toke corpus prompts."
    )
    parser.add_argument(
        "--per-domain", type=int, default=500,
        help="Number of tasks per domain (default: 500)"
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility (default: 42)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate tasks but do not write files"
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print distribution statistics"
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="Output directory (default: data/domain_prompts/)"
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Number of parallel workers (default: CPU count)"
    )
    args = parser.parse_args()

    output_dir: Path | None = None
    if not args.dry_run:
        output_dir = Path(args.output_dir) if args.output_dir else (
            _PROJECT_ROOT / "data" / "domain_prompts"
        )
        output_dir.mkdir(parents=True, exist_ok=True)

    workers = args.workers or min(cpu_count(), len(DOMAINS))

    # Build work items: (domain, count, seed, output_dir)
    work_items: list[tuple[str, int, int, Path | None]] = []
    for idx, domain in enumerate(DOMAINS):
        domain_seed = args.seed + idx * 100_000
        work_items.append((domain, args.per_domain, domain_seed,
                           output_dir if not args.dry_run else None))

    t0 = time.monotonic()

    if args.dry_run or args.stats:
        # For dry-run and stats, generate in-memory (still use multiprocessing)
        work_for_mem = [
            (d, c, s, None) for d, c, s, _ in work_items
        ]
        with Pool(processes=workers) as pool:
            results = pool.map(_generate_domain, work_for_mem)
        all_tasks = [t for batch in results for t in batch]
    else:
        with Pool(processes=workers) as pool:
            results = pool.map(_generate_domain, work_items)
        all_tasks = [t for batch in results for t in batch]

    elapsed = time.monotonic() - t0

    total = len(all_tasks)
    print(f"Generated {total} tasks across {len(DOMAINS)} domains "
          f"in {elapsed:.2f}s")

    if output_dir and not args.dry_run:
        print(f"Prompts written to: {output_dir}")

    if args.dry_run:
        # Print a few sample prompts
        gen = DomainStratifiedGenerator()
        for task in all_tasks[:3]:
            print(f"\n--- {task['task_id']} ---")
            prompt = gen.to_prompt(task)
            print(prompt[:400] + "...")

    if args.stats or True:
        _print_stats(all_tasks)


if __name__ == "__main__":
    main()
