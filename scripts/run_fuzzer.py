#!/usr/bin/env python3
"""Generate random toke programs with the grammar fuzzer, validate with tkc --check,
and write passing programs to data/corpus_fuzzed.jsonl."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Ensure fuzz/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fuzz.grammar_fuzz import GrammarFuzzer

TKC = Path("/Users/matthew.watt/tk/toke/tkc")
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "corpus_fuzzed.jsonl"
TOTAL = 50_000
SEED = 42
TIMEOUT = 5  # seconds per tkc call


def validate_program(item: tuple[int, str]) -> dict | None:
    """Write program to temp file, run tkc --check, return record if it passes."""
    idx, source = item
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tk", delete=False
        ) as tmp:
            tmp.write(source)
            tmp_path = tmp.name
        result = subprocess.run(
            [str(TKC), "--check", tmp_path],
            capture_output=True,
            timeout=TIMEOUT,
        )
        os.unlink(tmp_path)
        if result.returncode == 0:
            return {
                "id": f"fuzz_{idx:06d}",
                "tk_source": source,
                "tk_tokens": len(source.split()),
                "generation_method": "grammar_fuzz",
                "validation": {"tkc_check": "pass"},
            }
    except subprocess.TimeoutExpired:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return None


def main() -> None:
    print(f"Generating {TOTAL:,} programs (seed={SEED}) ...")
    t0 = time.time()
    fuzzer = GrammarFuzzer()
    programs = fuzzer.generate_batch(TOTAL, seed=SEED)
    gen_time = time.time() - t0
    print(f"Generation done in {gen_time:.1f}s")

    print(f"Validating with tkc ({os.cpu_count()} workers) ...")
    t1 = time.time()

    passed: list[dict] = []
    total_done = 0
    # Size buckets: token count ranges
    bucket_counts: dict[str, list[int]] = {}  # bucket -> [total, passed]

    def bucket_label(tokens: int) -> str:
        if tokens <= 10:
            return "1-10"
        if tokens <= 25:
            return "11-25"
        if tokens <= 50:
            return "26-50"
        if tokens <= 100:
            return "51-100"
        return "101+"

    items = list(enumerate(programs))

    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = {executor.submit(validate_program, item): item for item in items}
        for future in as_completed(futures):
            total_done += 1
            item = futures[future]
            idx, source = item
            tokens = len(source.split())
            bl = bucket_label(tokens)
            if bl not in bucket_counts:
                bucket_counts[bl] = [0, 0]
            bucket_counts[bl][0] += 1

            record = future.result()
            if record is not None:
                passed.append(record)
                bucket_counts[bl][1] += 1

            if total_done % 5000 == 0:
                rate = len(passed) / total_done * 100 if total_done else 0
                elapsed = time.time() - t1
                print(
                    f"  [{total_done:>6,}/{TOTAL:,}] "
                    f"passing={len(passed):,}  rate={rate:.1f}%  "
                    f"elapsed={elapsed:.1f}s"
                )

    val_time = time.time() - t1

    # Sort by id for deterministic output
    passed.sort(key=lambda r: r["id"])

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        for record in passed:
            f.write(json.dumps(record) + "\n")

    # -- Stats --
    total_pass = len(passed)
    rate = total_pass / TOTAL * 100
    print()
    print("=" * 60)
    print(f"Total generated : {TOTAL:,}")
    print(f"Total passing   : {total_pass:,}")
    print(f"Pass rate       : {rate:.2f}%")
    print(f"Validation time : {val_time:.1f}s")
    print(f"Output          : {OUTPUT}")
    print()
    print("Pass rate by token-count bucket:")
    for bl in sorted(bucket_counts.keys(), key=lambda x: int(x.split("-")[0])):
        tot, pas = bucket_counts[bl]
        br = pas / tot * 100 if tot else 0
        print(f"  {bl:>6s} tokens : {pas:,}/{tot:,}  ({br:.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    main()
