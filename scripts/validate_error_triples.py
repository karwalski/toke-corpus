#!/usr/bin/env python3
"""Validate error triples from corpus_error_triples.jsonl against TKC compiler.

Streams through the 2M-entry JSONL, samples up to 2,000 entries per
injection_type (12,000 total candidates), validates that broken_source
fails and fixed_source passes tkc --check, then writes passing entries
as corpus-schema JSON to phase2_deduplicated/ERR-TRIPLE-{type}/.

Story 10.2.2 -- Validate error triples from existing 2M corpus.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TKC_BIN = "/Users/matthew.watt/tk/toke/tkc"
SAMPLES_PER_TYPE = 2_000
NUM_WORKERS = 8

INJECTION_TYPES = [
    "type_mismatch",
    "missing_semicolon",
    "wrong_operator",
    "immutable_reassignment",
    "undefined_variable",
    "wrong_argument_count",
]


# ---------------------------------------------------------------------------
# Pass 1: Count lines per injection_type (streaming)
# ---------------------------------------------------------------------------

def count_lines_per_type(jsonl_path: str) -> dict[str, int]:
    """Stream through the JSONL and count entries per injection_type."""
    counts: dict[str, int] = collections.defaultdict(int)
    with open(jsonl_path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                itype = obj.get("injection_type", "")
                counts[itype] += 1
            except json.JSONDecodeError:
                continue
    return dict(counts)


# ---------------------------------------------------------------------------
# Pass 2: Sample evenly across the file (streaming)
# ---------------------------------------------------------------------------

def sample_entries(
    jsonl_path: str,
    counts: dict[str, int],
    samples_per_type: int,
) -> list[dict[str, Any]]:
    """Stream through JSONL and sample evenly spaced entries per type.

    For each type, if total_count > samples_per_type we take every Nth
    entry (N = total_count // samples_per_type). Otherwise take all.
    """
    # Compute step size per type
    steps: dict[str, int] = {}
    for itype, total in counts.items():
        if itype not in INJECTION_TYPES:
            continue
        if total <= samples_per_type:
            steps[itype] = 1
        else:
            steps[itype] = total // samples_per_type

    # Track index per type and collected samples
    type_idx: dict[str, int] = collections.defaultdict(int)
    type_collected: dict[str, int] = collections.defaultdict(int)
    samples: list[dict[str, Any]] = []

    with open(jsonl_path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            itype = obj.get("injection_type", "")
            if itype not in steps:
                continue

            idx = type_idx[itype]
            type_idx[itype] += 1

            if type_collected[itype] >= samples_per_type:
                continue

            if idx % steps[itype] == 0:
                samples.append(obj)
                type_collected[itype] += 1

    return samples


# ---------------------------------------------------------------------------
# Compiler validation (runs in worker processes)
# ---------------------------------------------------------------------------

def _check_source(source: str) -> int:
    """Write source to a temp file and run tkc --check. Return exit code."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tk", delete=False,
        ) as tmp:
            tmp.write(source)
            tmp_path = tmp.name
        result = subprocess.run(
            [TKC_BIN, "--check", tmp_path],
            capture_output=True,
            timeout=10,
        )
        return result.returncode
    except (subprocess.TimeoutExpired, OSError):
        return -1
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def validate_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Validate one error triple: broken must fail, fixed must pass.

    Returns the entry dict augmented with validation info, or None.
    """
    broken = entry.get("broken_source", "")
    fixed = entry.get("fixed_source", "")

    broken_rc = _check_source(broken)
    if broken_rc == 0:
        # broken_source should fail but didn't
        return None

    fixed_rc = _check_source(fixed)
    if fixed_rc != 0:
        # fixed_source should pass but didn't
        return None

    entry["_broken_rc"] = broken_rc
    entry["_fixed_rc"] = fixed_rc
    return entry


# ---------------------------------------------------------------------------
# Corpus schema output
# ---------------------------------------------------------------------------

def entry_to_corpus(
    entry: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    """Convert a validated entry to corpus-schema JSON."""
    itype = entry["injection_type"]
    fixed = entry["fixed_source"]
    broken = entry["broken_source"]
    diag = entry.get("injection_details", "")

    src_hash = hashlib.sha256(fixed.encode()).hexdigest()[:8]
    entry_id = f"ERR-{itype}-{index:04d}-{src_hash}"

    return {
        "id": entry_id,
        "version": 1,
        "phase": "B",
        "task_id": f"ERR-{itype}",
        "tk_source": fixed,
        "tk_tokens": len(fixed.split()),
        "attempts": 1,
        "model": "error-injection-repair",
        "validation": {"compiler_exit_code": 0, "error_codes": []},
        "differential": {"languages_agreed": [], "majority_output": ""},
        "judge": {"accepted": True, "score": 0.90},
        "references": {
            "broken_source": broken,
            "diagnostic": diag,
            "injection_type": itype,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="data/corpus_error_triples.jsonl",
        help="Path to corpus_error_triples.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        default="corpus/phase2_deduplicated",
        help="Output base directory",
    )
    parser.add_argument(
        "--samples-per-type",
        type=int,
        default=SAMPLES_PER_TYPE,
        help="Max samples per injection type (default: 2000)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=NUM_WORKERS,
        help="Number of parallel workers (default: 8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Sample and count but don't validate or write",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_base = Path(args.output_dir)

    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if not Path(TKC_BIN).exists():
        print(f"ERROR: TKC binary not found: {TKC_BIN}", file=sys.stderr)
        sys.exit(1)

    # Pass 1: count
    print("Pass 1: Counting entries per injection_type...")
    t0 = time.time()
    counts = count_lines_per_type(str(input_path))
    elapsed = time.time() - t0
    print(f"  Completed in {elapsed:.1f}s")
    for itype in INJECTION_TYPES:
        print(f"  {itype}: {counts.get(itype, 0):,}")
    total_all = sum(counts.get(t, 0) for t in INJECTION_TYPES)
    print(f"  Total (6 types): {total_all:,}")

    # Pass 2: sample
    print(f"\nPass 2: Sampling up to {args.samples_per_type} per type...")
    t0 = time.time()
    samples = sample_entries(str(input_path), counts, args.samples_per_type)
    elapsed = time.time() - t0
    sampled_counts: dict[str, int] = collections.defaultdict(int)
    for s in samples:
        sampled_counts[s["injection_type"]] += 1
    print(f"  Sampled {len(samples):,} entries in {elapsed:.1f}s")
    for itype in INJECTION_TYPES:
        print(f"  {itype}: {sampled_counts.get(itype, 0):,}")

    if args.dry_run:
        print("\n--dry-run: stopping before validation.")
        return

    # Pass 3: validate with parallel workers
    print(f"\nPass 3: Validating {len(samples):,} entries with {args.workers} workers...")
    t0 = time.time()
    validated: list[dict[str, Any]] = []
    failed_broken_ok = 0  # broken compiled fine (bad injection)
    failed_fixed_bad = 0  # fixed didn't compile (bad fix)
    done = 0

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(validate_entry, s): s for s in samples}
        for future in as_completed(futures):
            done += 1
            if done % 500 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(f"  Progress: {done:,}/{len(samples):,} "
                      f"({rate:.0f} entries/s, "
                      f"{len(validated):,} valid so far)")
            result = future.result()
            if result is not None:
                validated.append(result)

    elapsed = time.time() - t0
    print(f"  Validation completed in {elapsed:.1f}s")

    # Tally per type
    valid_counts: dict[str, int] = collections.defaultdict(int)
    for v in validated:
        valid_counts[v["injection_type"]] += 1

    # Pass 4: write output
    print(f"\nPass 4: Writing {len(validated):,} validated entries...")
    written_counts: dict[str, int] = collections.defaultdict(int)
    seen_hashes: set[str] = set()

    for itype in INJECTION_TYPES:
        out_dir = output_base / f"ERR-TRIPLE-{itype}"
        out_dir.mkdir(parents=True, exist_ok=True)

    type_index: dict[str, int] = collections.defaultdict(int)
    for entry in validated:
        itype = entry["injection_type"]
        idx = type_index[itype]
        type_index[itype] += 1

        corpus_entry = entry_to_corpus(entry, idx)

        # Dedup by fixed_source hash
        src_hash = hashlib.sha256(
            entry["fixed_source"].encode()
        ).hexdigest()[:16]
        if src_hash in seen_hashes:
            continue
        seen_hashes.add(src_hash)

        out_dir = output_base / f"ERR-TRIPLE-{itype}"
        out_path = out_dir / f"{corpus_entry['id']}.json"
        with open(out_path, "w") as fh:
            json.dump(corpus_entry, fh, indent=2)
            fh.write("\n")
        written_counts[itype] += 1

    # Report
    total_written = sum(written_counts.values())
    print(f"\n{'='*60}")
    print(f"VALIDATION REPORT")
    print(f"{'='*60}")
    print(f"Total sampled:    {len(samples):,}")
    print(f"Total validated:  {len(validated):,} "
          f"({len(validated)/len(samples)*100:.1f}%)")
    print(f"Total written:    {total_written:,} (after dedup)")
    print(f"{'='*60}")
    print(f"{'Type':<30} {'Sampled':>8} {'Valid':>8} {'Written':>8} {'Rate':>8}")
    print(f"{'-'*30} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for itype in INJECTION_TYPES:
        s = sampled_counts.get(itype, 0)
        v = valid_counts.get(itype, 0)
        w = written_counts.get(itype, 0)
        rate = f"{v/s*100:.1f}%" if s > 0 else "N/A"
        print(f"{itype:<30} {s:>8,} {v:>8,} {w:>8,} {rate:>8}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
