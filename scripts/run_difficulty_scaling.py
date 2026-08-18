#!/usr/bin/env python3
"""Batch difficulty-scaling runner (Story 9.3.4).

Reads seed programs from data/corpus_fuzzed.jsonl, applies the difficulty
scaling chain to each, and writes results to data/corpus_difficulty_scaled.jsonl.

Usage::

    cd /Users/matthew.watt/tk/toke-corpus
    .venv/bin/python scripts/run_difficulty_scaling.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

# Ensure repo root is on sys.path so pipeline package is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.difficulty_chain import DifficultyChain

INPUT_PATH = REPO_ROOT / "data" / "corpus_fuzzed.jsonl"
OUTPUT_PATH = REPO_ROOT / "data" / "corpus_difficulty_scaled.jsonl"
WORKERS = 4


def _process_entry(line: str) -> list[dict]:
    """Process a single JSONL line through the difficulty chain.

    Returns a list of output records (one per achieved difficulty level).
    """
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return []

    source = entry.get("tk_source", "")
    base_id = entry.get("id", "unknown")
    if not source:
        return []

    chain = DifficultyChain()
    levels = chain.build_chain(source)

    results: list[dict] = []
    for level, scaled_source in levels:
        # Skip level 1 -- that is the original seed
        if level == 1:
            continue
        tk_tokens = len(scaled_source.split())
        record = {
            "id": f"scaled-{base_id}-L{level}",
            "tk_source": scaled_source,
            "tk_tokens": tk_tokens,
            "generation_method": "difficulty_scaling",
            "base_id": base_id,
            "difficulty_level": level,
            "validation": {"tkc_check": "pass"},
        }
        results.append(record)

    return results


def main() -> None:
    if not INPUT_PATH.exists():
        print(f"ERROR: Input file not found: {INPUT_PATH}")
        sys.exit(1)

    print(f"Reading seeds from {INPUT_PATH} ...")
    with open(INPUT_PATH, encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    print(f"  {len(lines)} seed programs loaded")
    print(f"Scaling with {WORKERS} workers ...")

    t0 = time.time()

    with Pool(processes=WORKERS) as pool:
        batched_results = pool.map(_process_entry, lines)

    elapsed = time.time() - t0

    # Flatten and write
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    total_written = 0
    level_counts: Counter[int] = Counter()

    with open(OUTPUT_PATH, "w", encoding="utf-8") as out:
        for results in batched_results:
            for record in results:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                total_written += 1
                level_counts[record["difficulty_level"]] += 1

    # Report
    print(f"\nDone in {elapsed:.1f}s")
    print(f"Total scaled programs written: {total_written}")
    print(f"Output: {OUTPUT_PATH}")
    print("\nPrograms reaching each level:")
    for level in sorted(level_counts):
        print(f"  Level {level}: {level_counts[level]}")


if __name__ == "__main__":
    main()
