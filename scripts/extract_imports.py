#!/usr/bin/env python3
"""extract_imports.py — Story 10.8.4.

Walk the corpus, parse `i=alias:module;` declarations from each `tk_source`,
and write two fields in-place:

  imported_modules    — list of unique module names used (sorted).
  imports_unresolved  — subset that are NOT in the known stdlib module list.

Phase 2 import syntax:
    i=alias:dotted.module.name;

Examples from corpus:
    i=http:std.http;
    i=s:std.str;
    i=db:std.db;

Usage:
    python scripts/extract_imports.py [--corpus-dir PATH] [--dry-run]
                                      [--sample N]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"

# 15 canonical stdlib modules (from toke-spec/spec/stdlib-signatures.md).
KNOWN_STDLIB = frozenset({
    "std.str",
    "std.json",
    "std.toon",
    "std.yaml",
    "std.i18n",
    "std.http",
    "std.db",
    "std.file",
    "std.env",
    "std.process",
    "std.crypto",
    "std.time",
    "std.log",
    "std.test",
})

# Import declaration regex: `i=alias:module.path;`
# - alias: identifier (lowercase by Phase 2 post-autofix; but accept anything)
# - module: dotted name with letters/digits/underscore
RE_IMPORT = re.compile(
    r"\bi\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_.]*)\s*;"
)


def strip_strings(src: str) -> str:
    """Remove string literals from source so import scan doesn't match inside them."""
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == '"':
            i += 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def extract_imports(src: str) -> list[str]:
    """Return sorted unique list of module names imported by `src`."""
    if not isinstance(src, str) or not src:
        return []
    code = strip_strings(src)
    modules: set[str] = set()
    for m in RE_IMPORT.finditer(code):
        modules.add(m.group(2))
    return sorted(modules)


def iter_records(corpus_dir: Path, sample_per_cat: int | None = None):
    import random
    for cat_dir in sorted(corpus_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        files = [p for p in cat_dir.iterdir() if p.suffix == ".json"]
        if sample_per_cat is not None and len(files) > sample_per_cat:
            files = random.sample(files, sample_per_cat)
        for p in files:
            yield (p, cat_dir.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract imports from corpus records")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample", type=int, default=None)
    args = parser.parse_args()

    import random
    random.seed(42)

    scanned = 0
    with_imports = 0
    with_unresolved = 0
    module_counter: collections.Counter = collections.Counter()
    unresolved_counter: collections.Counter = collections.Counter()
    cat_stats: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )

    for path, cat in iter_records(args.corpus_dir, args.sample):
        try:
            with open(path) as f:
                record = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        src = record.get("tk_source", "")
        modules = extract_imports(src)
        unresolved = [m for m in modules if m not in KNOWN_STDLIB]

        record["imported_modules"] = modules
        record["imports_unresolved"] = unresolved

        if not args.dry_run:
            with open(path, "w") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        scanned += 1
        cat_stats[cat]["scanned"] += 1
        if modules:
            with_imports += 1
            cat_stats[cat]["with_imports"] += 1
        if unresolved:
            with_unresolved += 1
            cat_stats[cat]["with_unresolved"] += 1
        for m in modules:
            module_counter[m] += 1
        for m in unresolved:
            unresolved_counter[m] += 1

    # Summary
    print("=" * 70, file=sys.stderr)
    print("  Imports extraction summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  Scanned:          {scanned}", file=sys.stderr)
    print(f"  With imports:     {with_imports}", file=sys.stderr)
    print(f"  With unresolved:  {with_unresolved}", file=sys.stderr)

    print(f"\n  Stdlib usage histogram:", file=sys.stderr)
    for m, cnt in module_counter.most_common():
        marker = " " if m in KNOWN_STDLIB else "!"
        print(f"   {marker} {m:25s} {cnt:8d}", file=sys.stderr)

    if unresolved_counter:
        print(f"\n  Unresolved imports (top 30):", file=sys.stderr)
        for m, cnt in unresolved_counter.most_common(30):
            print(f"    {m:30s} {cnt:8d}", file=sys.stderr)

    print(f"\n  Top 20 categories by import usage:", file=sys.stderr)
    ranked = sorted(cat_stats.items(),
                    key=lambda kv: -kv[1].get("with_imports", 0))
    print(
        f"    {'category':35s} {'scanned':>8s} {'w/imp':>8s} {'w/unres':>8s}",
        file=sys.stderr,
    )
    for cat, cnts in ranked[:20]:
        print(
            f"    {cat:35s} "
            f"{cnts.get('scanned', 0):8d} "
            f"{cnts.get('with_imports', 0):8d} "
            f"{cnts.get('with_unresolved', 0):8d}",
            file=sys.stderr,
        )

    if args.dry_run:
        print("\n  [DRY RUN — no files modified]", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
