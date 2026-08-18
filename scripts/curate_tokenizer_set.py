#!/usr/bin/env python3
"""Curate the tokenizer training set from the deduplicated corpus.

Collects non-mutation programs from phase2_deduplicated/ plus the 330 original
DOC examples. Deduplicates by exact tk_source match, then writes:
  - data/tokenizer_training.txt   (one program per line, SentencePiece format)
  - data/tokenizer_training.jsonl (id, category, tk_source for traceability)
  - docs/tokenizer_data_report.md (statistics and coverage analysis)
"""

import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEDUP_DIR = ROOT / "corpus" / "phase2_deduplicated"
DOC_DIR = ROOT / "corpus" / "phase_b" / "DOC"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

# The 56 characters of the Phase 2 default syntax
PHASE2_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "=;{}()<>!.,:@$\"+-*/%&|^~#?\\'\n "
)
# Note: whitespace chars (space, newline) and backslash/quote are included above.
# The exact set is 56 visible chars + whitespace — we'll report what we find.


def load_programs():
    """Load all non-MUT programs from dedup + DOC originals."""
    programs = []  # list of (id, category, tk_source)

    # 1. Non-MUT categories from phase2_deduplicated
    for category_dir in sorted(DEDUP_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        cat_name = category_dir.name
        if cat_name.startswith("MUT-"):
            continue
        for fpath in sorted(category_dir.iterdir()):
            if not fpath.suffix == ".json":
                continue
            try:
                rec = json.loads(fpath.read_text())
                tk = rec.get("tk_source", "")
                if tk:
                    programs.append((rec.get("id", fpath.stem), cat_name, tk))
            except (json.JSONDecodeError, KeyError):
                pass

    # 2. Original DOC examples from phase_b/DOC
    for fpath in sorted(DOC_DIR.iterdir()):
        if not fpath.suffix == ".json":
            continue
        try:
            rec = json.loads(fpath.read_text())
            tk = rec.get("tk_source", "")
            if tk:
                programs.append((rec.get("id", fpath.stem), "DOC-ORIG", tk))
        except (json.JSONDecodeError, KeyError):
            pass

    return programs


def deduplicate(programs):
    """Deduplicate by exact tk_source. Keep first occurrence."""
    seen = set()
    unique = []
    for pid, cat, tk in programs:
        if tk not in seen:
            seen.add(tk)
            unique.append((pid, cat, tk))
    return unique


def compute_stats(unique):
    """Compute token/character statistics."""
    lengths = [len(tk) for _, _, tk in unique]
    token_counts = []
    char_freq = Counter()
    for _, _, tk in unique:
        # Approximate token count: split on whitespace and punctuation boundaries
        # Use character count as a proxy; actual BPE tokens computed later
        token_counts.append(len(tk))
        char_freq.update(tk)

    stats = {
        "total": len(unique),
        "char_min": min(lengths),
        "char_max": max(lengths),
        "char_mean": statistics.mean(lengths),
        "char_median": statistics.median(lengths),
        "char_p95": sorted(lengths)[int(0.95 * len(lengths))],
        "char_freq": char_freq,
    }

    # Per-category counts
    cat_counts = Counter()
    for _, cat, _ in unique:
        cat_counts[cat] += 1
    stats["categories"] = cat_counts

    return stats


def check_phase2_coverage(char_freq):
    """Check which Phase 2 characters are present."""
    found = set(char_freq.keys())
    # We define the expected printable chars (the 56-char set)
    # From the toke spec: letters, digits, and these symbols:
    expected_printable = set(
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789"
        "=;{}()<>!.,:@$\"+-*/%&|^~#?\\_' "
    )
    # Uppercase letters too
    expected_printable.update("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    # Add newline and tab as whitespace
    expected_printable.update("\n\t")

    present = found & expected_printable
    missing = expected_printable - found
    extra = found - expected_printable
    return present, missing, extra


def write_outputs(unique, stats):
    """Write training files and report."""
    DATA_DIR.mkdir(exist_ok=True)
    DOCS_DIR.mkdir(exist_ok=True)

    # 1. SentencePiece format: one program per line (newlines within escaped)
    txt_path = DATA_DIR / "tokenizer_training.txt"
    with open(txt_path, "w") as f:
        for _, _, tk in unique:
            # SentencePiece expects one sentence per line
            # Replace literal newlines with \n escape so each program is one line
            line = tk.replace("\n", "\\n")
            f.write(line + "\n")
    print(f"Wrote {txt_path} ({len(unique)} lines)")

    # 2. JSONL for traceability
    jsonl_path = DATA_DIR / "tokenizer_training.jsonl"
    with open(jsonl_path, "w") as f:
        for pid, cat, tk in unique:
            rec = {"id": pid, "category": cat, "tk_source": tk}
            f.write(json.dumps(rec) + "\n")
    print(f"Wrote {jsonl_path} ({len(unique)} records)")

    # 3. Report
    present, missing, extra = check_phase2_coverage(stats["char_freq"])
    report_path = DOCS_DIR / "tokenizer_data_report.md"

    lines = []
    lines.append("# Tokenizer Training Data Report\n")
    lines.append(f"## Summary\n")
    lines.append(f"- **Total unique programs**: {stats['total']:,}")
    lines.append(f"- **Character count**: min={stats['char_min']}, max={stats['char_max']}, "
                 f"mean={stats['char_mean']:.0f}, median={stats['char_median']:.0f}, "
                 f"p95={stats['char_p95']}")
    lines.append(f"- **Character coverage**: {len(present)} chars present "
                 f"out of expected set")
    if missing:
        lines.append(f"- **Missing chars**: {sorted(repr(c) for c in missing)}")
    lines.append("")

    lines.append("## Per-Category Contribution\n")
    lines.append("| Category | Count | % |")
    lines.append("|----------|------:|--:|")
    for cat, count in sorted(stats["categories"].items(), key=lambda x: -x[1]):
        pct = 100.0 * count / stats["total"]
        lines.append(f"| {cat} | {count:,} | {pct:.1f}% |")
    lines.append("")

    lines.append("## Character Frequency (top 40)\n")
    lines.append("| Char | Repr | Count |")
    lines.append("|------|------|------:|")
    for ch, cnt in stats["char_freq"].most_common(40):
        display = repr(ch)
        lines.append(f"| {ch if ch.isprintable() and ch != '|' else ''} | {display} | {cnt:,} |")
    lines.append("")

    if missing:
        lines.append("## Missing Phase 2 Characters\n")
        for ch in sorted(missing):
            lines.append(f"- `{repr(ch)}`")
        lines.append("")

    if extra:
        lines.append("## Extra Characters (outside expected set)\n")
        for ch in sorted(extra, key=lambda c: ord(c)):
            cnt = stats["char_freq"][ch]
            lines.append(f"- `{repr(ch)}` — {cnt:,} occurrences")
        lines.append("")

    lines.append("## Vocabulary Coverage Estimate\n")
    total_chars = sum(stats["char_freq"].values())
    unique_chars = len(stats["char_freq"])
    lines.append(f"- **Total characters**: {total_chars:,}")
    lines.append(f"- **Unique characters**: {unique_chars}")
    lines.append(f"- With `character_coverage=1.0`, SentencePiece will cover all "
                 f"{unique_chars} unique characters in the training data.")
    lines.append(f"- Recommended vocab size: 8,000–16,000 (standard for domain-specific BPE)")
    lines.append("")

    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Wrote {report_path}")


def main():
    print("Loading programs from corpus...")
    programs = load_programs()
    print(f"  Loaded {len(programs):,} programs (non-MUT + DOC originals)")

    print("Deduplicating by exact tk_source...")
    unique = deduplicate(programs)
    print(f"  {len(unique):,} unique programs after dedup")

    print("Computing statistics...")
    stats = compute_stats(unique)

    print("Writing outputs...")
    write_outputs(unique, stats)

    print(f"\nFinal count: {len(unique):,} unique programs for tokenizer training")


if __name__ == "__main__":
    main()
