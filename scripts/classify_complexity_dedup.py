#!/usr/bin/env python3
"""classify_complexity_dedup.py — Story 10.4.6.

Walk `corpus/phase2_deduplicated/` and ensure every record is classified
into a complexity tier, writing results to
`data/corpus_complexity_dedup.jsonl`. Reuses `analyse_entry()` from
`classify_complexity.py`.

The existing `data/corpus_complexity.jsonl` was computed against
`corpus/phase2_combined/` — a pre-BIFI snapshot — and so omits 91,009
records (BIFI-*, ERR-TRIPLE-*, COMPOSE-NEW, etc.). This script re-runs
the classifier against the current deduplicated corpus so that
`prepare_training_data.py` can fully stratify by tier.
"""
from __future__ import annotations

import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

# Reuse the analyser from the existing script
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_complexity import analyse_entry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus" / "phase2_deduplicated"
OUTPUT = ROOT / "data" / "corpus_complexity_dedup.jsonl"


def collect_files(root: Path) -> list[str]:
    files: list[str] = []
    for cat_dir in sorted(root.iterdir()):
        if not cat_dir.is_dir():
            continue
        for p in cat_dir.iterdir():
            if p.suffix == ".json":
                files.append(str(p))
    return files


def main() -> int:
    print(f"Scanning {CORPUS_DIR}", file=sys.stderr)
    files = collect_files(CORPUS_DIR)
    print(f"Found {len(files):,} files", file=sys.stderr)

    print(f"Analysing with {os.cpu_count() or 8} workers...", file=sys.stderr)
    with Pool(processes=min(8, os.cpu_count() or 8)) as pool:
        raw = pool.map(analyse_entry, files, chunksize=2000)

    results = [r for r in raw if r is not None]
    print(f"Analysed {len(results):,}  failed {len(files) - len(results)}",
          file=sys.stderr)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        for r in results:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")

    # Tier summary
    from collections import Counter
    tiers = Counter(r["tier"] for r in results)
    print("\nTier distribution (dedup corpus):", file=sys.stderr)
    total = len(results)
    for t in ["simple", "medium", "complex", "application"]:
        n = tiers.get(t, 0)
        pct = 100 * n / total if total else 0
        print(f"  {t:12s} {n:8d}  ({pct:5.1f}%)", file=sys.stderr)

    # Per-top-category tiers
    from collections import defaultdict
    cat_tiers: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        cat_tiers[r["category"]][r["tier"]] += 1
    print("\nTop 15 categories by size:", file=sys.stderr)
    ranked = sorted(cat_tiers.items(),
                    key=lambda kv: -sum(kv[1].values()))[:15]
    for cat, c in ranked:
        tot = sum(c.values())
        row = " ".join(
            f"{t[0]}={c.get(t, 0):>5d}"
            for t in ["simple", "medium", "complex", "application"]
        )
        print(f"  {cat:40s} tot={tot:6d}  {row}", file=sys.stderr)

    print(f"\nWrote {OUTPUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
