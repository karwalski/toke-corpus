#!/usr/bin/env python3
"""
Semantic deduplication of the toke mutation corpus.

Reads all JSON files from corpus/phase2_combined/, computes structural hashes
that normalize away variable names and numeric literals, groups duplicates,
and writes a deduplicated corpus to corpus/phase2_deduplicated/.

Story 10.1.1
"""

import json
import hashlib
import os
import re
import shutil
import sys
from collections import defaultdict, Counter
from multiprocessing import Pool
from pathlib import Path
from datetime import datetime

# ---------- configuration ----------
ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "corpus" / "phase2_combined"
DST_DIR = ROOT / "corpus" / "phase2_deduplicated"
REPORT_PATH = ROOT / "docs" / "dedup_report.md"
WORKERS = 8

# Toke keywords / built-in tokens that should NOT be normalised
TOKE_KEYWORDS = frozenset({
    "m", "f", "s", "e", "p", "let", "mut", "lp", "if", "elif", "else",
    "match", "impl", "true", "false", "nil", "self", "pub", "priv",
    "as", "in", "is", "or", "and", "not", "ret", "brk", "cont",
})

# Toke type names that should be kept as-is
TOKE_TYPES = frozenset({
    "i8", "i16", "i32", "i64",
    "u8", "u16", "u32", "u64",
    "f32", "f64",
    "bool", "str", "char", "void",
    "any", "Self",
})

# ---------- structural normalisation ----------

# Tokeniser: split toke source into atomic tokens
_TOKEN_RE = re.compile(
    r"""
      [a-zA-Z_]\w*      # identifiers / keywords
    | \d+(?:\.\d+)?      # numeric literals
    | [<>=!]=?           # comparison operators
    | [+\-*/%&|^~]       # arithmetic / bitwise operators
    | [{}()\[\];,.:@<>]  # delimiters
    | \S                 # anything else (single char)
    """,
    re.VERBOSE,
)

_IDENT_RE = re.compile(r'^[a-zA-Z_]\w*$')
_NUM_RE = re.compile(r'^\d+(?:\.\d+)?$')


def _tokenise(src: str) -> list[str]:
    """Split toke source into raw tokens."""
    return _TOKEN_RE.findall(src)


def structural_normalise(src: str) -> str:
    """
    Normalise toke source for structural hashing.

    - Variable / function / module names -> positional tokens (id0, id1, ...)
    - Numeric literals -> NUM
    - Keywords, type names, and operators kept as-is
    """
    tokens = _tokenise(src)
    ident_map: dict[str, str] = {}
    ident_counter = 0
    normalised: list[str] = []

    for tok in tokens:
        if _NUM_RE.match(tok):
            normalised.append("NUM")
        elif _IDENT_RE.match(tok):
            if tok in TOKE_KEYWORDS or tok in TOKE_TYPES:
                normalised.append(tok)
            else:
                if tok not in ident_map:
                    ident_map[tok] = f"id{ident_counter}"
                    ident_counter += 1
                normalised.append(ident_map[tok])
        else:
            normalised.append(tok)

    return " ".join(normalised)


def structural_hash(src: str) -> str:
    """Return hex digest of the structural normalisation."""
    norm = structural_normalise(src)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


# ---------- file loading (runs in worker pool) ----------

def load_entry(path_str: str) -> dict | None:
    """Load a single JSON corpus file and attach metadata."""
    try:
        with open(path_str, "r") as fh:
            data = json.load(fh)
        tk_src = data.get("tk_source", "")
        if not tk_src:
            return None

        subdir = Path(path_str).parent.name
        is_mutation = subdir.startswith("MUT-")
        mutation_type = None
        if is_mutation:
            refs = data.get("references", {})
            mutation_type = refs.get("mutation_type", subdir.replace("MUT-", ""))

        return {
            "path": path_str,
            "subdir": subdir,
            "id": data.get("id", ""),
            "tk_source": tk_src,
            "shash": structural_hash(tk_src),
            "is_mutation": is_mutation,
            "mutation_type": mutation_type,
            "score": data.get("judge", {}).get("score", 0),
        }
    except Exception as exc:
        print(f"  WARN: failed to load {path_str}: {exc}", file=sys.stderr)
        return None


# ---------- dedup selection logic ----------

def select_keepers(group: list[dict]) -> list[dict]:
    """
    From a group of structurally-identical entries, pick:
      - The original (non-MUT) seed if one exists
      - Up to 2 structurally-distinct variants (prefer different mutation types)
    Pure variable renames of an already-kept entry are skipped.
    """
    # Sort: originals first (higher score tiebreak), then mutations
    originals = [e for e in group if not e["is_mutation"]]
    mutations = [e for e in group if e["is_mutation"]]

    # Sort originals by score desc
    originals.sort(key=lambda e: -e["score"])
    # Sort mutations: prefer diverse mutation types
    mutations.sort(key=lambda e: (e["mutation_type"] or "", -e["score"]))

    kept: list[dict] = []

    # Keep best original
    if originals:
        kept.append(originals[0])

    # Now pick up to 2 additional variants with distinct mutation types
    # Skip variable_rename entirely (they hash the same because we normalise names)
    seen_mut_types: set[str] = set()
    for entry in mutations:
        if len(kept) >= 3:  # 1 original + 2 variants max
            break
        mt = entry["mutation_type"]
        if mt == "variable_rename":
            continue  # skip pure renames
        if mt in seen_mut_types:
            continue
        seen_mut_types.add(mt)
        kept.append(entry)

    # If no original was kept, ensure we keep at least one entry
    if not kept and mutations:
        # Pick the first non-variable-rename, or fall back to first entry
        for entry in mutations:
            if entry["mutation_type"] != "variable_rename":
                kept.append(entry)
                break
        if not kept:
            kept.append(mutations[0])

    return kept


# ---------- main ----------

def main():
    t0 = datetime.now()
    print(f"[dedup] Starting semantic deduplication at {t0:%H:%M:%S}")

    # 1. Discover all JSON files
    print("[dedup] Scanning corpus files ...")
    all_paths: list[str] = []
    for subdir in sorted(SRC_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        for f in subdir.iterdir():
            if f.suffix == ".json":
                all_paths.append(str(f))

    total_files = len(all_paths)
    print(f"[dedup] Found {total_files:,} JSON files across {len(list(SRC_DIR.iterdir()))} subdirectories")

    # 2. Load and hash all entries in parallel
    print(f"[dedup] Loading and hashing with {WORKERS} workers ...")
    with Pool(WORKERS) as pool:
        results = pool.map(load_entry, all_paths, chunksize=500)

    entries = [r for r in results if r is not None]
    print(f"[dedup] Successfully loaded {len(entries):,} entries")

    # 3. Group by structural hash
    groups: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        groups[entry["shash"]].append(entry)

    num_unique_hashes = len(groups)
    print(f"[dedup] Found {num_unique_hashes:,} unique structural hashes")

    # 4. Select keepers from each group
    print("[dedup] Selecting entries to keep ...")
    kept_entries: list[dict] = []
    group_stats: list[tuple[int, int, str]] = []  # (original_size, kept_size, hash)

    for shash, group in groups.items():
        keepers = select_keepers(group)
        kept_entries.extend(keepers)
        group_stats.append((len(group), len(keepers), shash))

    kept_entries.sort(key=lambda e: e["path"])
    print(f"[dedup] Keeping {len(kept_entries):,} entries (removed {len(entries) - len(kept_entries):,})")

    # 5. Write deduplicated corpus
    print("[dedup] Writing deduplicated corpus ...")
    if DST_DIR.exists():
        shutil.rmtree(DST_DIR)

    # Tally per-subdir
    before_counts: Counter = Counter()
    after_counts: Counter = Counter()
    for entry in entries:
        before_counts[entry["subdir"]] += 1
    for entry in kept_entries:
        after_counts[entry["subdir"]] += 1

    # Copy kept files preserving subdir structure
    for entry in kept_entries:
        src_path = Path(entry["path"])
        dst_subdir = DST_DIR / entry["subdir"]
        dst_subdir.mkdir(parents=True, exist_ok=True)
        dst_path = dst_subdir / src_path.name
        shutil.copy2(src_path, dst_path)

    # 6. Generate report
    print("[dedup] Generating dedup report ...")
    group_stats.sort(key=lambda x: -x[0])

    # Distribution of kept-per-group
    kept_dist: Counter = Counter()
    for _, kept_n, _ in group_stats:
        kept_dist[kept_n] += 1

    elapsed = (datetime.now() - t0).total_seconds()

    lines: list[str] = []
    lines.append("# Corpus Deduplication Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now():%Y-%m-%d %H:%M}")
    lines.append(f"**Runtime:** {elapsed:.1f}s")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total files before | {len(entries):,} |")
    lines.append(f"| Total files after  | {len(kept_entries):,} |")
    lines.append(f"| Files removed      | {len(entries) - len(kept_entries):,} |")
    lines.append(f"| Reduction          | {100*(1 - len(kept_entries)/len(entries)):.1f}% |")
    lines.append(f"| Unique structural hashes | {num_unique_hashes:,} |")
    lines.append("")
    lines.append("## Before/After Counts by Category")
    lines.append("")
    lines.append("| Category | Before | After | Removed | Reduction |")
    lines.append("|----------|--------|-------|---------|-----------|")
    all_subdirs = sorted(set(list(before_counts.keys()) + list(after_counts.keys())))
    for sd in all_subdirs:
        b = before_counts[sd]
        a = after_counts[sd]
        r = b - a
        pct = f"{100*r/b:.1f}%" if b > 0 else "N/A"
        lines.append(f"| {sd} | {b:,} | {a:,} | {r:,} | {pct} |")
    lines.append("")

    lines.append("## Largest Duplicate Groups (Top 20)")
    lines.append("")
    lines.append("| Rank | Hash | Group Size | Kept | Sample Source (first 80 chars) |")
    lines.append("|------|------|-----------|------|-------------------------------|")
    for i, (sz, kept_n, shash) in enumerate(group_stats[:20], 1):
        # Get a sample tk_source from this group
        sample = ""
        for entry in entries:
            if entry["shash"] == shash:
                sample = entry["tk_source"][:80].replace("|", "\\|")
                break
        lines.append(f"| {i} | `{shash}` | {sz:,} | {kept_n} | `{sample}` |")
    lines.append("")

    lines.append("## Distribution of Kept Variants per Structural Hash")
    lines.append("")
    lines.append("| Kept per group | Number of groups |")
    lines.append("|---------------|-----------------|")
    for k in sorted(kept_dist.keys()):
        lines.append(f"| {k} | {kept_dist[k]:,} |")
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"[dedup] Report written to {REPORT_PATH}")

    # Final summary
    print(f"\n{'='*60}")
    print(f"  DEDUPLICATION COMPLETE")
    print(f"  Before: {len(entries):,} files")
    print(f"  After:  {len(kept_entries):,} files")
    print(f"  Removed: {len(entries) - len(kept_entries):,} ({100*(1 - len(kept_entries)/len(entries)):.1f}%)")
    print(f"  Unique structural hashes: {num_unique_hashes:,}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
