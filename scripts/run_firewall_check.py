#!/usr/bin/env python3
"""CLI tool for running contamination firewall checks on a JSONL corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from registry.corpus_tagger import CorpusTagger
from registry.firewall import ContaminationFirewall
from registry.source_registry import REGISTRY
from registry.split_protocol import HoldoutManifest, SplitProtocol


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run contamination firewall checks on a JSONL corpus."
    )
    parser.add_argument(
        "--corpus", required=True, type=Path, help="Input JSONL corpus to check."
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Output tagged JSONL (passed entries only)."
    )
    parser.add_argument(
        "--rejected", required=True, type=Path, help="Output rejected entries JSONL."
    )
    parser.add_argument(
        "--eval-descriptions",
        type=Path,
        default=None,
        help="File with evaluation task descriptions (one per line).",
    )
    parser.add_argument(
        "--holdout-manifest",
        type=Path,
        default=None,
        help="Holdout manifest JSON file.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Similarity threshold (default 0.85).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Load holdout manifest if provided
    manifest: HoldoutManifest | None = None
    if args.holdout_manifest is not None:
        manifest = SplitProtocol.load_manifest(str(args.holdout_manifest))

    # Build firewall
    firewall = ContaminationFirewall(
        registry=REGISTRY,
        holdout_manifest=manifest,
        similarity_threshold=args.threshold,
    )

    # Load evaluation descriptions if provided
    if args.eval_descriptions is not None:
        descriptions = [
            line.strip()
            for line in args.eval_descriptions.read_text().splitlines()
            if line.strip()
        ]
        firewall.add_evaluation_descriptions(descriptions)
        print(f"Loaded {len(descriptions)} evaluation descriptions.")

    # Run tagger
    tagger = CorpusTagger(firewall)
    report = tagger.process_corpus(args.corpus, args.output, args.rejected)

    print()
    print("=== Firewall Check Report ===")
    print(report.summary())
    print()
    print(f"Output:   {args.output}")
    print(f"Rejected: {args.rejected}")


if __name__ == "__main__":
    main()
