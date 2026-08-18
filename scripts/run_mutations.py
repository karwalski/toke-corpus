"""Run the mutation engine over the Phase A corpus.

Reads data/corpus_default.jsonl, applies MutationEngine to each entry,
writes results to data/corpus_mutations.jsonl with multiprocessing.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from multiprocessing import Pool

# Ensure the repo root is on sys.path so we can import mutate.mutations
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from mutate.mutations import MutationEngine

INPUT_PATH = os.path.join(REPO_ROOT, "data", "corpus_default.jsonl")
OUTPUT_PATH = os.path.join(REPO_ROOT, "data", "corpus_mutations.jsonl")
SEED = 42
PROGRESS_INTERVAL = 5000


def process_entry(entry_json: str) -> list[str] | None:
    """Process a single JSONL line, return list of output JSONL lines."""
    try:
        entry = json.loads(entry_json)
        source_id = entry.get("id", entry.get("source_id", "unknown"))
        source = entry.get("tk_source", entry.get("source", ""))
        if not source:
            return None

        engine = MutationEngine(seed=SEED)
        mutations = engine.mutate(source)

        results = []
        for mutated_source, mutation_type, mutation_details in mutations:
            record = {
                "source_id": source_id,
                "original_source": source,
                "mutated_source": mutated_source,
                "mutation_type": mutation_type,
                "mutation_details": mutation_details,
            }
            results.append(json.dumps(record))
        return results
    except Exception:
        return None


def main() -> None:
    print(f"Reading corpus from {INPUT_PATH}")
    with open(INPUT_PATH) as f:
        lines = [line.strip() for line in f if line.strip()]
    total_entries = len(lines)
    print(f"Loaded {total_entries:,} entries")

    num_workers = os.cpu_count() or 4
    print(f"Using {num_workers} workers")

    t0 = time.time()
    type_counts: Counter[str] = Counter()
    total_mutations = 0
    errors = 0
    processed = 0

    with open(OUTPUT_PATH, "w") as out, Pool(processes=num_workers) as pool:
        # Use imap_unordered with a reasonable chunksize for throughput
        chunksize = max(1, total_entries // (num_workers * 10))
        for result in pool.imap_unordered(process_entry, lines, chunksize=chunksize):
            processed += 1
            if result is None:
                errors += 1
            else:
                for line in result:
                    out.write(line + "\n")
                    # Parse mutation_type for stats
                    rec = json.loads(line)
                    type_counts[rec["mutation_type"]] += 1
                    total_mutations += 1

            if processed % PROGRESS_INTERVAL == 0:
                elapsed = time.time() - t0
                rate = processed / elapsed if elapsed > 0 else 0
                print(
                    f"  Progress: {processed:,}/{total_entries:,} entries "
                    f"({total_mutations:,} mutations, {rate:.0f} entries/s)"
                )

    elapsed = time.time() - t0

    print()
    print("=" * 60)
    print("MUTATION STATS")
    print("=" * 60)
    print(f"Total entries processed: {processed:,}")
    print(f"Errors (skipped):        {errors:,}")
    print(f"Total mutations:         {total_mutations:,}")
    print(f"Avg mutations/entry:     {total_mutations / max(1, processed - errors):.2f}")
    print()
    print("Mutations by type:")
    for mtype, count in type_counts.most_common():
        print(f"  {mtype:30s} {count:>8,}")
    print()
    print(f"Output written to: {OUTPUT_PATH}")
    print(f"Time elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
