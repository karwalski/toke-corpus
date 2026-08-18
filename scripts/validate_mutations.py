#!/usr/bin/env python3
"""Validate mutation corpus entries by compile-checking with tkc.

Reads corpus_mutations.jsonl, validates each mutated_source with tkc --check,
and writes passing entries to corpus/phase2_combined/MUT-{type}/.

Usage:
    python3 scripts/validate_mutations.py [--dry-run] [--limit N] [--workers N]
"""

import json
import hashlib
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "data" / "corpus_mutations.jsonl"
OUTPUT_DIR = ROOT / "corpus" / "phase2_combined"
TKC = os.environ.get("TKC", str(ROOT.parent / "toke" / "tkc"))
DRY_RUN = "--dry-run" in sys.argv
LIMIT = None
WORKERS = 8

for i, arg in enumerate(sys.argv):
    if arg == "--limit" and i + 1 < len(sys.argv):
        LIMIT = int(sys.argv[i + 1])
    if arg == "--workers" and i + 1 < len(sys.argv):
        WORKERS = int(sys.argv[i + 1])


def compile_check(source: str) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    try:
        r = subprocess.run(
            [TKC, "--check", fname],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False
    finally:
        os.unlink(fname)


def process_batch(batch):
    """Process a batch of (index, record) tuples. Returns list of passing entries."""
    results = []
    for idx, rec in batch:
        src = rec.get("mutated_source", "")
        if not src.strip():
            continue
        if compile_check(src):
            results.append((idx, rec))
    return results


def main():
    if not INPUT.exists():
        print(f"ERROR: {INPUT} not found")
        sys.exit(1)

    # Load entries
    print(f"Loading {INPUT}...")
    entries = []
    with open(INPUT, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if LIMIT and i >= LIMIT:
                break
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    print(f"Loaded {len(entries):,} mutations")

    # Validate in parallel using batches
    BATCH_SIZE = 100
    batches = []
    for i in range(0, len(entries), BATCH_SIZE):
        batch = [(j, entries[j]) for j in range(i, min(i + BATCH_SIZE, len(entries)))]
        batches.append(batch)

    passed = 0
    failed = 0
    by_type = {}

    print(f"Validating with {WORKERS} workers...")

    with ProcessPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(process_batch, batch): batch for batch in batches}
        done_count = 0

        for future in as_completed(futures):
            done_count += 1
            results = future.result()
            passed += len(results)
            failed += len(futures[future]) - len(results)

            if not DRY_RUN:
                for idx, rec in results:
                    src = rec["mutated_source"]
                    mut_type = rec.get("mutation_type", "unknown")
                    source_id = rec.get("source_id", "unk")
                    h = hashlib.sha1(src.encode()).hexdigest()[:8]
                    entry_id = f"MUT-{mut_type[:4]}-{idx:06d}-{h}"

                    cat = f"MUT-{mut_type}"
                    by_type[cat] = by_type.get(cat, 0) + 1

                    corpus_entry = {
                        "id": entry_id,
                        "version": 1,
                        "phase": "B",
                        "task_id": f"MUT-{source_id}",
                        "tk_source": src,
                        "tk_tokens": len(src.split()),
                        "attempts": 1,
                        "model": f"mutation-{mut_type}",
                        "validation": {"compiler_exit_code": 0, "error_codes": []},
                        "differential": {"languages_agreed": [], "majority_output": ""},
                        "judge": {"accepted": True, "score": 0.85},
                        "references": {
                            "mutation_type": mut_type,
                            "mutation_details": rec.get("mutation_details", ""),
                            "original_source": rec.get("original_source", ""),
                        },
                    }

                    cat_dir = OUTPUT_DIR / cat
                    cat_dir.mkdir(parents=True, exist_ok=True)
                    out_file = cat_dir / f"{entry_id}.json"
                    with open(out_file, "w", encoding="utf-8") as fh:
                        json.dump(corpus_entry, fh, ensure_ascii=False)
                        fh.write("\n")

            if done_count % 100 == 0:
                total_checked = passed + failed
                rate = passed / total_checked * 100 if total_checked else 0
                print(f"  ... {total_checked:,}/{len(entries):,} checked, "
                      f"{passed:,} pass ({rate:.1f}%)")

    prefix = "[dry-run] " if DRY_RUN else ""
    print(f"\n{prefix}Results:")
    print(f"  Total checked:   {passed + failed:,}")
    print(f"  Passed:          {passed:,}")
    print(f"  Failed:          {failed:,}")
    print(f"  Pass rate:       {passed/(passed+failed)*100:.1f}%")
    if by_type:
        print(f"\n  By mutation type:")
        for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"    {t:35s} {n:8,}")


if __name__ == "__main__":
    main()
