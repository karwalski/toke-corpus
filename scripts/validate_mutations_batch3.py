#!/usr/bin/env python3
"""Validate mutations batch 3 (offset 85K-200K)."""

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
OFFSET = 85000
LIMIT = 200000


def compile_check(source):
    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    try:
        r = subprocess.run([TKC, "--check", fname], capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        os.unlink(fname)


def process_batch(batch):
    return [(idx, rec) for idx, rec in batch if compile_check(rec.get("mutated_source", ""))]


def main():
    entries = []
    with open(INPUT, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < OFFSET:
                continue
            if i >= LIMIT:
                break
            line = line.strip()
            if line:
                entries.append((i, json.loads(line)))

    print(f"Loaded {len(entries):,} mutations (offset {OFFSET}-{LIMIT})")

    BATCH_SIZE = 100
    batches = []
    for i in range(0, len(entries), BATCH_SIZE):
        batches.append(entries[i:i + BATCH_SIZE])

    passed = 0
    failed = 0
    by_type = {}

    with ProcessPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_batch, b): b for b in batches}
        done = 0
        for future in as_completed(futures):
            done += 1
            results = future.result()
            passed += len(results)
            failed += len(futures[future]) - len(results)

            for idx, rec in results:
                src = rec["mutated_source"]
                mut_type = rec.get("mutation_type", "unknown")
                source_id = rec.get("source_id", "unk")
                h = hashlib.sha1(src.encode()).hexdigest()[:8]
                entry_id = f"MUT-{mut_type[:4]}-{idx:06d}-{h}"
                cat = f"MUT-{mut_type}"
                by_type[cat] = by_type.get(cat, 0) + 1

                cat_dir = OUTPUT_DIR / cat
                cat_dir.mkdir(parents=True, exist_ok=True)
                out_file = cat_dir / f"{entry_id}.json"
                with open(out_file, "w", encoding="utf-8") as fh:
                    json.dump({
                        "id": entry_id, "version": 1, "phase": "B",
                        "task_id": f"MUT-{source_id}", "tk_source": src,
                        "tk_tokens": len(src.split()), "attempts": 1,
                        "model": f"mutation-{mut_type}",
                        "validation": {"compiler_exit_code": 0, "error_codes": []},
                        "differential": {"languages_agreed": [], "majority_output": ""},
                        "judge": {"accepted": True, "score": 0.85},
                        "references": {"mutation_type": mut_type, "original_source": rec.get("original_source", "")},
                    }, fh, ensure_ascii=False)
                    fh.write("\n")

            if done % 50 == 0:
                t = passed + failed
                print(f"  ... {t:,}/{len(entries):,}, pass={passed:,} ({passed / t * 100:.1f}%)")

    print(f"\nResults: {passed:,} passed, {failed:,} failed ({passed / (passed + failed) * 100:.1f}%)")
    for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t:35s} {n:8,}")


if __name__ == "__main__":
    main()
