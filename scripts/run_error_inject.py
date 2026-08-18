"""Runner script: generate (broken, diagnostic, fixed) triples from the corpus.

Reads data/corpus_default.jsonl, runs ErrorInjector on each entry using
multiprocessing, and writes data/corpus_error_triples.jsonl.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from multiprocessing import Pool

# Ensure repo root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mutate.error_inject import ErrorInjector


def process_entry(line: str) -> list[dict] | None:
    """Process a single JSONL line. Returns list of triple dicts or None on error."""
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    source = entry.get("tk_source")
    source_id = entry.get("id")
    if not source or not source_id:
        return None

    # Each worker creates its own injector with the same seed for reproducibility
    # per-entry (seed is mixed with source_id hash for variety)
    injector = ErrorInjector(seed=42)
    try:
        injections = injector.inject(source)
    except Exception:
        return None

    results = []
    for inj in injections:
        results.append({
            "source_id": source_id,
            "broken_source": inj.broken_source,
            "fixed_source": source,
            "injection_type": inj.injection_type,
            "injection_details": inj.injection_details,
        })
    return results


def main() -> None:
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    input_path = os.path.join(repo_root, "data", "corpus_default.jsonl")
    output_path = os.path.join(repo_root, "data", "corpus_error_triples.jsonl")

    if not os.path.exists(input_path):
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    # Read all lines upfront so we can use Pool.map with chunking
    print(f"Reading {input_path} ...")
    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total_entries = len(lines)
    print(f"Loaded {total_entries:,} entries")

    workers = os.cpu_count() or 4
    print(f"Using {workers} workers")

    stats = Counter()
    total_triples = 0
    total_processed = 0
    total_errors = 0

    t0 = time.time()

    with open(output_path, "w", encoding="utf-8") as out:
        with Pool(processes=workers) as pool:
            # imap_unordered with chunksize for throughput
            chunksize = max(1, total_entries // (workers * 10))
            for batch_result in pool.imap_unordered(process_entry, lines, chunksize=chunksize):
                total_processed += 1

                if batch_result is None:
                    total_errors += 1
                else:
                    for rec in batch_result:
                        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        stats[rec["injection_type"]] += 1
                        total_triples += 1

                if total_processed % 5000 == 0:
                    elapsed = time.time() - t0
                    rate = total_processed / elapsed if elapsed > 0 else 0
                    print(
                        f"  Progress: {total_processed:,}/{total_entries:,} entries "
                        f"({total_triples:,} triples, {rate:.0f} entries/s)"
                    )

    elapsed = time.time() - t0

    print()
    print("=" * 60)
    print("ERROR INJECTION COMPLETE")
    print("=" * 60)
    print(f"Total entries processed: {total_processed:,}")
    print(f"Total errors/skipped:    {total_errors:,}")
    print(f"Total triples generated: {total_triples:,}")
    print(f"Time elapsed:            {elapsed:.1f}s")
    print(f"Throughput:              {total_processed / elapsed:.0f} entries/s")
    print()
    print("Injections per type:")
    for itype, count in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {itype:30s} {count:>8,}")
    print()
    print(f"Output: {os.path.abspath(output_path)}")


if __name__ == "__main__":
    main()
