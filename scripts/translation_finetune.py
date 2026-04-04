#!/usr/bin/env python3
"""Format parallel corpus into chat-format fine-tuning data for translation.

Reads the parallel corpus JSONL (task_id, python_source, toke_source) and
converts it into chat-format training data with train/val/test splits.
Optionally generates reverse (toke -> Python) pairs for bidirectional
capability.

Usage::

    python scripts/translation_finetune.py \\
        --parallel-corpus data/parallel_corpus.jsonl \\
        --output-dir data

Story 9.3.3 -- Python-to-toke translation fine-tuning stage.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_PY2TOKE = (
    "You are a Python-to-Toke translator. Translate the given Python function "
    "to equivalent Toke source code using Profile 1 syntax. "
    "Profile 1 uses M= (module), F= (function), T= (struct), I= (import) "
    "uppercase keywords. Use `let x=mut.0` for mutable bindings, `<value` for "
    "return, `lp(init;cond;step){body}` for loops, `;` as separator, and "
    "`[a;b;c]` for arrays."
)

SYSTEM_PROMPT_TOKE2PY = (
    "You are a Toke-to-Python translator. Translate the given Toke source code "
    "to equivalent Python using idiomatic style. "
    "Toke Profile 1 uses M= (module), F= (function), T= (struct), I= (import) "
    "uppercase keywords, `let x=mut.0` for mutable bindings, `<value` for "
    "return, `lp(init;cond;step){body}` for loops, `;` as separator, and "
    "`[a;b;c]` for arrays."
)


# ---------------------------------------------------------------------------
# Data formatting
# ---------------------------------------------------------------------------


def make_py2toke(record: dict) -> dict:
    """Create a Python->Toke chat-format training example."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_PY2TOKE},
            {
                "role": "user",
                "content": (
                    "Translate this Python to Toke:\n"
                    f"```python\n{record['python_source']}\n```"
                ),
            },
            {"role": "assistant", "content": record["toke_source"]},
        ]
    }


def make_toke2py(record: dict) -> dict:
    """Create a Toke->Python chat-format training example (reverse)."""
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT_TOKE2PY},
            {
                "role": "user",
                "content": (
                    "Translate this Toke to Python:\n"
                    f"```toke\n{record['toke_source']}\n```"
                ),
            },
            {"role": "assistant", "content": record["python_source"]},
        ]
    }


def assign_bucket(record: dict) -> str:
    """Assign a token-ratio bucket for stratified splitting."""
    ratio = record.get("token_ratio")
    if ratio is None:
        return "unknown"
    if ratio < 0.5:
        return "very_short"
    if ratio < 0.8:
        return "short"
    if ratio < 1.0:
        return "similar"
    if ratio < 1.3:
        return "longer"
    return "much_longer"


def stratified_split(
    records: list[dict],
    train_ratio: float,
    rng: random.Random,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split records into train/val/test with stratification by bucket.

    Validation and test each get (1 - train_ratio) / 2 of the data.
    """
    val_ratio = (1.0 - train_ratio) / 2.0

    # Group by bucket
    buckets: dict[str, list[dict]] = {}
    for rec in records:
        b = assign_bucket(rec)
        buckets.setdefault(b, []).append(rec)

    train, val, test = [], [], []

    for _bucket, items in sorted(buckets.items()):
        rng.shuffle(items)
        n = len(items)
        n_train = max(1, round(n * train_ratio))
        n_val = max(0, round(n * val_ratio))
        # Ensure at least 1 train item; remaining split between val and test
        if n_train + n_val >= n:
            n_val = max(0, n - n_train - 1)

        train.extend(items[:n_train])
        val.extend(items[n_train : n_train + n_val])
        test.extend(items[n_train + n_val :])

    # Final shuffle within each split
    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)

    return train, val, test


def build_examples(
    records: list[dict],
    reverse_ratio: float,
    rng: random.Random,
) -> list[dict]:
    """Build forward and reverse chat-format examples from records."""
    examples = []
    for rec in records:
        examples.append(make_py2toke(rec))

    # Add reverse pairs
    n_reverse = max(0, round(len(records) * reverse_ratio))
    if n_reverse > 0:
        reverse_pool = list(records)
        rng.shuffle(reverse_pool)
        for rec in reverse_pool[:n_reverse]:
            examples.append(make_toke2py(rec))

    rng.shuffle(examples)
    return examples


def write_jsonl(path: Path, data: list[dict]) -> None:
    """Write a list of dicts as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Format parallel corpus for translation fine-tuning."
    )
    parser.add_argument(
        "--parallel-corpus",
        type=Path,
        default=Path("data/parallel_corpus.jsonl"),
        help="Input parallel corpus JSONL file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="Output directory for split files",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Fraction of data for training (default: 0.8)",
    )
    parser.add_argument(
        "--reverse-ratio",
        type=float,
        default=0.2,
        help="Fraction of reverse (toke->Python) pairs to add (default: 0.2)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()

    corpus_path = args.parallel_corpus
    if not corpus_path.is_file():
        print(f"Error: parallel corpus not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    # Load records
    records = []
    with open(corpus_path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: skipping line {line_num}: {e}", file=sys.stderr)
                continue
            if "python_source" not in rec or "toke_source" not in rec:
                print(
                    f"Warning: skipping line {line_num}: missing required fields",
                    file=sys.stderr,
                )
                continue
            records.append(rec)

    if not records:
        print("Error: no valid records found in corpus.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(records)} parallel pairs.", file=sys.stderr)

    rng = random.Random(args.seed)

    # Stratified split
    train_recs, val_recs, test_recs = stratified_split(
        records, args.train_ratio, rng
    )

    print(
        f"Split: train={len(train_recs)}, val={len(val_recs)}, "
        f"test={len(test_recs)}",
        file=sys.stderr,
    )

    # Build chat-format examples (with reverse pairs for train/val only)
    train_examples = build_examples(train_recs, args.reverse_ratio, rng)
    val_examples = build_examples(val_recs, args.reverse_ratio, rng)
    # Test split: forward only (pure evaluation)
    test_examples = [make_py2toke(rec) for rec in test_recs]

    print(
        f"Examples: train={len(train_examples)}, val={len(val_examples)}, "
        f"test={len(test_examples)}",
        file=sys.stderr,
    )

    # Write output
    out = args.output_dir
    write_jsonl(out / "translation_train.jsonl", train_examples)
    write_jsonl(out / "translation_val.jsonl", val_examples)
    write_jsonl(out / "translation_test.jsonl", test_examples)

    print(f"\nWrote splits to {out}/", file=sys.stderr)
    print(f"  translation_train.jsonl  ({len(train_examples)} examples)")
    print(f"  translation_val.jsonl    ({len(val_examples)} examples)")
    print(f"  translation_test.jsonl   ({len(test_examples)} examples)")


if __name__ == "__main__":
    main()
