#!/usr/bin/env python3
"""Dynamic packing utility for toke training data.

Concatenates training samples to fill context windows efficiently using
first-fit decreasing bin packing.  Samples within a bin are joined with
a configurable separator token and padding metrics are reported.

Usage:
    python scripts/dynamic_packing.py \
        --input data/parallel_corpus.jsonl \
        --output-dir data \
        --context-window 2048 \
        --separator '<|sep|>' \
        --tokenizer cl100k_base
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Tokenization helpers
# ---------------------------------------------------------------------------

def _make_token_counter(tokenizer_name: str, dry_run: bool):
    """Return a callable(text) -> int for counting tokens.

    In dry-run mode (or when tiktoken is unavailable) falls back to
    character-count as a proxy.
    """
    if dry_run:
        return len  # char count proxy

    try:
        import tiktoken
        enc = tiktoken.get_encoding(tokenizer_name)
        return lambda text: len(enc.encode(text))
    except (ImportError, Exception):
        print("[warn] tiktoken unavailable — falling back to char-count proxy",
              file=sys.stderr)
        return len


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_samples(path: Path, count_tokens) -> list[dict[str, Any]]:
    """Load JSONL and annotate each record with its token count.

    Token count is computed over the concatenation of all string-valued
    fields so the packing budget is conservative.
    """
    samples: list[dict[str, Any]] = []
    with open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[warn] skipping line {lineno}: {exc}", file=sys.stderr)
                continue
            text = " ".join(
                str(v) for v in rec.values() if isinstance(v, str)
            )
            rec["_token_count"] = count_tokens(text)
            samples.append(rec)
    return samples


# ---------------------------------------------------------------------------
# Bin packing — first-fit decreasing
# ---------------------------------------------------------------------------

def _pack(samples: list[dict], context_window: int, separator: str,
          count_tokens) -> tuple[list[list[dict]], list[int]]:
    """First-fit decreasing bin packing.

    Returns (bins, padding_per_bin) where each bin is a list of sample
    dicts and padding_per_bin[i] is the unused tokens in bin i.
    """
    sep_tokens = count_tokens(separator)

    # Sort descending by token count (first-fit *decreasing*).
    ordered = sorted(samples, key=lambda s: s["_token_count"], reverse=True)

    bins: list[list[dict]] = []
    remaining: list[int] = []       # remaining capacity per bin

    for sample in ordered:
        size = sample["_token_count"]
        placed = False
        for i, cap in enumerate(remaining):
            # Cost to add this sample: its tokens + separator if not first.
            cost = size + (sep_tokens if bins[i] else 0)
            if cost <= cap:
                bins[i].append(sample)
                remaining[i] -= cost
                placed = True
                break
        if not placed:
            bins.append([sample])
            remaining.append(context_window - size)

    padding = list(remaining)
    return bins, padding


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _build_packed_record(bin_samples: list[dict], separator: str) -> dict:
    """Produce a single JSONL record for one packed bin."""
    task_ids = [s["task_id"] for s in bin_samples if "task_id" in s]

    # Concatenate the primary text fields with separator.
    def _concat(field: str) -> str:
        parts = [s[field] for s in bin_samples if field in s]
        return separator.join(parts)

    rec: dict[str, Any] = {
        "task_ids": task_ids,
        "packed_count": len(bin_samples),
    }

    # Preserve every string field present in the first sample.
    string_fields = [
        k for k in bin_samples[0]
        if isinstance(bin_samples[0][k], str) and not k.startswith("_")
    ]
    for field in string_fields:
        rec[field] = _concat(field)

    return rec


def _compute_stats(bins: list[list[dict]], padding: list[int],
                   context_window: int, total_samples: int) -> dict:
    total_capacity = context_window * len(bins)
    total_padding = sum(padding)
    padding_ratio = total_padding / total_capacity if total_capacity else 0.0

    samples_per_bin = [len(b) for b in bins]
    dist: dict[int, int] = {}
    for n in samples_per_bin:
        dist[n] = dist.get(n, 0) + 1

    return {
        "context_window": context_window,
        "original_samples": total_samples,
        "bin_count": len(bins),
        "total_padding_tokens": total_padding,
        "total_capacity_tokens": total_capacity,
        "padding_ratio": round(padding_ratio, 6),
        "padding_ratio_pct": round(padding_ratio * 100, 3),
        "samples_per_bin_distribution": {str(k): v for k, v in sorted(dist.items())},
        "per_bin_padding": padding,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(bins: list[list[dict]], original_ids: set[str],
              context_window: int, count_tokens, separator: str) -> list[str]:
    """Return a list of error strings (empty = all OK)."""
    errors: list[str] = []

    # 1. No sample lost.
    packed_ids: set[str] = set()
    for b in bins:
        for s in b:
            if "task_id" in s:
                packed_ids.add(s["task_id"])
    missing = original_ids - packed_ids
    if missing:
        errors.append(f"{len(missing)} task_ids missing after packing")

    extra = packed_ids - original_ids
    if extra:
        errors.append(f"{len(extra)} unexpected task_ids appeared")

    # 2. No bin exceeds context window.
    sep_tokens = count_tokens(separator)
    for i, b in enumerate(bins):
        total = sum(s["_token_count"] for s in b)
        total += sep_tokens * max(0, len(b) - 1)
        if total > context_window:
            errors.append(f"bin {i} exceeds context window: {total} > {context_window}")

    # 3. Round-trip: unpack and verify task_ids match.
    #    (We already checked set equality above, which is the meaningful
    #    round-trip invariant for id-based unpacking.)

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dynamic packing for toke training data")
    parser.add_argument("--input", required=True,
                        help="Path to training JSONL file")
    parser.add_argument("--output-dir", required=True,
                        help="Directory for packed output files")
    parser.add_argument("--context-window", type=int, default=2048,
                        help="Target context window size in tokens (default: 2048)")
    parser.add_argument("--separator", default="<|sep|>",
                        help="Delimiter between packed samples (default: <|sep|>)")
    parser.add_argument("--tokenizer", default="cl100k_base",
                        help="Tiktoken encoding name (default: cl100k_base)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use char-count proxy instead of real tokenizer")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    count_tokens = _make_token_counter(args.tokenizer, args.dry_run)

    # Load and sort by token length (ascending for display, packing uses descending).
    print(f"Loading samples from {input_path} ...")
    samples = _load_samples(input_path, count_tokens)
    if not samples:
        print("No samples found — nothing to pack.", file=sys.stderr)
        return 1
    samples.sort(key=lambda s: s["_token_count"])
    print(f"  {len(samples)} samples loaded, token range "
          f"[{samples[0]['_token_count']} .. {samples[-1]['_token_count']}]")

    original_ids = {s["task_id"] for s in samples if "task_id" in s}

    # Pack.
    print(f"Packing into {args.context_window}-token bins ...")
    bins, padding = _pack(samples, args.context_window, args.separator,
                          count_tokens)

    # Validate.
    errors = _validate(bins, original_ids, args.context_window,
                       count_tokens, args.separator)
    if errors:
        for e in errors:
            print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    # Compute stats.
    stats = _compute_stats(bins, padding, args.context_window, len(samples))

    # Write packed JSONL.
    packed_path = output_dir / "packed_train.jsonl"
    with open(packed_path, "w") as fh:
        for b in bins:
            rec = _build_packed_record(b, args.separator)
            fh.write(json.dumps(rec) + "\n")
    print(f"  Wrote {packed_path}")

    # Write stats JSON.
    stats_path = output_dir / "packing_stats.json"
    with open(stats_path, "w") as fh:
        json.dump(stats, fh, indent=2)
        fh.write("\n")
    print(f"  Wrote {stats_path}")

    # Summary.
    print()
    print("=== Packing Summary ===")
    print(f"  Original samples : {stats['original_samples']}")
    print(f"  Bins produced    : {stats['bin_count']}")
    print(f"  Padding ratio    : {stats['padding_ratio_pct']:.3f}%")
    print(f"  Total padding    : {stats['total_padding_tokens']} tokens")
    target = "PASS" if stats["padding_ratio"] < 0.05 else "FAIL"
    print(f"  <5% target       : {target}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
