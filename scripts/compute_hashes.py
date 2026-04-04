#!/usr/bin/env python3
"""Compute SHA-256 hashes for all corpus files and build a Merkle tree.

Outputs:
  - hashes.json: {filename: sha256} for every corpus JSON file
  - Merkle tree root hash for efficient bulk verification

Usage::

    python scripts/compute_hashes.py
    python scripts/compute_hashes.py --corpus-dir corpus --output hashes.json

Story 10.7.7 -- Hash verification for D13=E publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_all_hashes(corpus_dir: Path) -> dict[str, str]:
    """Compute SHA-256 for every .json file under corpus_dir.

    Returns dict mapping relative path -> hex digest, sorted by key.
    """
    hashes: dict[str, str] = {}
    for path in sorted(corpus_dir.rglob("*.json")):
        rel = str(path.relative_to(corpus_dir))
        hashes[rel] = sha256_file(path)
    return hashes


def merkle_root(hashes: dict[str, str]) -> str:
    """Compute Merkle tree root from sorted hash values.

    Pairs adjacent leaf hashes, hashing upward until a single root
    remains.  Odd-count levels duplicate the last element.
    """
    if not hashes:
        return hashlib.sha256(b"").hexdigest()

    # Leaf layer: hash each (key + value) pair for binding
    leaves = [
        hashlib.sha256(f"{k}:{v}".encode()).hexdigest()
        for k, v in sorted(hashes.items())
    ]

    layer = leaves
    while len(layer) > 1:
        next_layer: list[str] = []
        for i in range(0, len(layer), 2):
            left = layer[i]
            right = layer[i + 1] if i + 1 < len(layer) else left
            combined = hashlib.sha256(f"{left}{right}".encode()).hexdigest()
            next_layer.append(combined)
        layer = next_layer

    return layer[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute corpus SHA-256 hashes")
    parser.add_argument("--corpus-dir", type=Path, default=Path("corpus"),
                        help="Root corpus directory (default: corpus/)")
    parser.add_argument("--output", type=Path, default=Path("hashes.json"),
                        help="Output file for hashes (default: hashes.json)")
    args = parser.parse_args()

    if not args.corpus_dir.is_dir():
        print(f"Error: {args.corpus_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {args.corpus_dir} ...")
    hashes = compute_all_hashes(args.corpus_dir)
    print(f"Hashed {len(hashes)} files")

    root = merkle_root(hashes)
    print(f"Merkle root: {root}")

    output = {
        "corpus_dir": str(args.corpus_dir),
        "file_count": len(hashes),
        "merkle_root": root,
        "files": hashes,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
