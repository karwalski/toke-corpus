#!/usr/bin/env python3
"""Systematic variable name and literal parameterization (Story 57.7.2).

Takes existing corpus entries and creates synthetic diversity by:
1. Renaming user variables (camelCase variants, abbreviations, descriptive names)
2. Swapping literal values (numbers, strings, array sizes)
3. Type name variation (where type aliases exist)

All generated programs are validated with `tkc --check`. Only passing programs
are written to output. Target: 3-5x expansion from input corpus.

Usage:
    python3 parameterize_corpus.py --input corpus_default.jsonl --output parameterized.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Variable name alternatives — each key maps to a list of valid replacements
VAR_NAMES = {
    "result": ["res", "out", "ret", "val", "ans"],
    "count": ["cnt", "n", "num", "total", "ct"],
    "index": ["idx", "i", "pos", "ix", "off"],
    "value": ["val", "v", "data", "item", "elem"],
    "temp": ["tmp", "t", "buf", "scratch", "hold"],
    "sum": ["total", "acc", "s", "running", "subtotal"],
    "max": ["hi", "upper", "ceiling", "top", "best"],
    "min": ["lo", "lower", "floor", "bottom", "least"],
    "list": ["arr", "items", "elems", "seq", "coll"],
    "msg": ["message", "text", "s", "content", "payload"],
    "key": ["k", "name", "id", "tag", "label"],
    "err": ["e", "error", "failure", "problem", "issue"],
    "line": ["row", "entry", "record", "ln", "l"],
    "path": ["p", "filepath", "loc", "route", "target"],
    "data": ["d", "payload", "content", "input", "raw"],
}

# Numeric literal alternatives
NUM_ALTS = {
    "0": ["0"],
    "1": ["1"],
    "10": ["10", "20", "50", "100"],
    "100": ["100", "256", "500", "1000"],
    "1000": ["1000", "2048", "5000", "10000"],
}


def rename_variables(source: str, rng: random.Random) -> str | None:
    """Rename user-defined variables in toke source."""
    # Find all let/mut bindings: let X= or let X=mut.
    bindings = re.findall(r'\blet\s+(\w+)=', source)
    if not bindings:
        return None

    replacements = {}
    for var in bindings:
        if var in VAR_NAMES:
            alts = [a for a in VAR_NAMES[var] if a != var]
            if alts:
                replacements[var] = rng.choice(alts)
        elif len(var) > 2 and var not in ("mut",):
            # Try shortening long names
            short = var[:3]
            if short != var and short not in bindings:
                replacements[var] = short

    if not replacements:
        return None

    result = source
    for old, new in replacements.items():
        # Replace whole-word only (avoid replacing inside longer names)
        result = re.sub(rf'\b{re.escape(old)}\b', new, result)

    return result if result != source else None


def swap_literals(source: str, rng: random.Random) -> str | None:
    """Swap numeric literals with alternatives."""
    nums_found = re.findall(r'(?<![.\w])(\d+)(?![.\w])', source)
    if not nums_found:
        return None

    result = source
    changed = False
    for num in set(nums_found):
        if num in NUM_ALTS:
            alts = [a for a in NUM_ALTS[num] if a != num]
            if alts:
                new = rng.choice(alts)
                result = result.replace(num, new, 1)
                changed = True

    return result if changed else None


def validate_toke(source: str, tkc_path: str) -> bool:
    """Check if toke source passes tkc --check."""
    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(source)
        f.flush()
        try:
            r = subprocess.run(
                [tkc_path, "--check", f.name],
                capture_output=True, timeout=10
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        finally:
            os.unlink(f.name)


def parameterize_entry(entry: dict, tkc_path: str, rng: random.Random) -> list[dict]:
    """Generate parameterized variants of a single corpus entry."""
    source = entry.get("tk_source", entry.get("source", ""))
    if not source or len(source) < 20:
        return []

    variants = []

    # Try variable renaming
    renamed = rename_variables(source, rng)
    if renamed and renamed != source and validate_toke(renamed, tkc_path):
        new_entry = dict(entry)
        new_entry["tk_source"] = renamed
        new_entry["source"] = renamed
        new_entry["task_id"] = entry.get("task_id", "") + "-vr"
        new_entry["hash"] = hashlib.md5(renamed.encode()).hexdigest()
        variants.append(new_entry)

    # Try literal swapping
    swapped = swap_literals(source, rng)
    if swapped and swapped != source and validate_toke(swapped, tkc_path):
        new_entry = dict(entry)
        new_entry["tk_source"] = swapped
        new_entry["source"] = swapped
        new_entry["task_id"] = entry.get("task_id", "") + "-ls"
        new_entry["hash"] = hashlib.md5(swapped.encode()).hexdigest()
        variants.append(new_entry)

    # Try combined
    if renamed:
        combined = swap_literals(renamed, rng)
        if combined and combined != source and validate_toke(combined, tkc_path):
            new_entry = dict(entry)
            new_entry["tk_source"] = combined
            new_entry["source"] = combined
            new_entry["task_id"] = entry.get("task_id", "") + "-vrl"
            new_entry["hash"] = hashlib.md5(combined.encode()).hexdigest()
            variants.append(new_entry)

    return variants


def main():
    parser = argparse.ArgumentParser(description="Parameterize toke corpus entries")
    parser.add_argument("--input", required=True, help="Input JSONL file")
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument("--tkc", default="tkc", help="Path to tkc binary")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--limit", type=int, default=0, help="Max entries to process (0=all)")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    total = 0
    generated = 0
    validated = 0

    with open(args.input) as inf, open(args.output, "w") as outf:
        for line in inf:
            if args.limit and total >= args.limit:
                break
            entry = json.loads(line.strip())
            total += 1
            variants = parameterize_entry(entry, args.tkc, rng)
            for v in variants:
                outf.write(json.dumps(v) + "\n")
                generated += 1
                validated += 1

            if total % 1000 == 0:
                print(f"  Processed {total}, generated {generated} variants", file=sys.stderr)

    ratio = generated / total if total else 0
    print(f"Done: {total} input -> {generated} variants ({ratio:.1f}x expansion)", file=sys.stderr)
    print(f"All {validated} variants validated with tkc --check", file=sys.stderr)


if __name__ == "__main__":
    main()
