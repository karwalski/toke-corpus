#!/usr/bin/env python3
"""
Story 10.1.2 / 10.1.3 — Score corpus quality and flag fuzzed programs for removal.

Reads all JSON files from corpus/phase2_combined/, computes quality metrics per entry,
writes per-entry scores to data/corpus_quality_scores.jsonl, and generates
docs/quality_report.md with distribution analysis.

Analysis only — no corpus files are modified or deleted.
"""

import json
import os
import re
import sys
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus" / "phase2_combined"
OUTPUT_JSONL = ROOT / "data" / "corpus_quality_scores.jsonl"
REPORT_MD = ROOT / "docs" / "quality_report.md"

# ---------------------------------------------------------------------------
# Identifier analysis helpers
# ---------------------------------------------------------------------------

# Patterns that indicate generic/random identifiers from the grammar fuzzer
GENERIC_IDENT = re.compile(
    r"^(fn\d+|v\d+|p\d+|i\d+|mod\d+|x\d*|y\d*|z\d*|a\d*|b\d*|n\d*|m\d*|t\d*|_)$",
    re.IGNORECASE,
)

# Extract identifiers from toke source: module name, function names, let bindings, params
IDENT_MODULE = re.compile(r"m=(\w+)")
IDENT_FUNC = re.compile(r"f=(\w+)\(")
IDENT_LET = re.compile(r"let\s+(\w+)")
IDENT_PARAM = re.compile(r"[\(;](\w+):")

MEANINGFUL_WORD = re.compile(r"[a-z]{3,}", re.IGNORECASE)


def _extract_identifiers(src: str) -> list[str]:
    """Pull all user-defined identifiers from toke source."""
    ids = []
    for pat in (IDENT_MODULE, IDENT_FUNC, IDENT_LET, IDENT_PARAM):
        ids.extend(pat.findall(src))
    # Deduplicate but keep order
    seen = set()
    out = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def semantic_coherence(src: str) -> float:
    """Score 0-1 based on how meaningful the identifiers are."""
    idents = _extract_identifiers(src)
    if not idents:
        return 0.0
    meaningful = 0
    generic = 0
    for ident in idents:
        if GENERIC_IDENT.match(ident):
            generic += 1
        elif MEANINGFUL_WORD.search(ident):
            meaningful += 1
        else:
            # Short but not matching generic pattern — treat as half
            meaningful += 0.5
    total = len(idents)
    ratio = meaningful / total
    if ratio >= 0.8:
        return 1.0
    elif ratio >= 0.4:
        return 0.5
    else:
        return 0.0


def complexity_score(src: str) -> int:
    """Count complexity indicators: functions, loops, conditionals, let bindings, imports."""
    funcs = len(re.findall(r"f=\w+\(", src))
    loops = src.count("lp(")
    conds = src.count("if(") + src.count("if (")
    lets = len(re.findall(r"\blet\s+", src))
    imports = len(re.findall(r"\bi=", src))
    return funcs + loops + conds + lets + imports


def has_stdlib(src: str) -> bool:
    return bool(re.search(r"\bi=", src))


def has_error_handling(src: str) -> bool:
    return "!$err" in src or "$err" in src or "result" in src.lower()


def is_multi_function(src: str) -> bool:
    return len(re.findall(r"f=\w+\(", src)) > 1


# ---------------------------------------------------------------------------
# Per-file scoring
# ---------------------------------------------------------------------------

def score_file(path: str) -> dict | None:
    """Read one JSON file, compute all metrics, return a score dict."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    src = data.get("tk_source", "")
    if not src:
        return None

    entry_id = data.get("id", Path(path).stem)
    # Category = parent directory name
    category = Path(path).parent.name
    model = data.get("model", "")
    token_count = data.get("tk_tokens", 0)

    fuzzed = category == "FUZZ" or model == "grammar-fuzzer"

    sc = semantic_coherence(src)
    cx = complexity_score(src)
    stdlib = has_stdlib(src)
    errh = has_error_handling(src)
    multif = is_multi_function(src)

    composite = (
        sc * 0.4
        + min(cx / 10.0, 1.0) * 0.3
        + (1.0 if stdlib else 0.0) * 0.15
        + (1.0 if multif else 0.0) * 0.15
    )

    return {
        "id": entry_id,
        "category": category,
        "model": model,
        "token_count": token_count,
        "semantic_coherence": sc,
        "complexity": cx,
        "has_stdlib": stdlib,
        "has_error_handling": errh,
        "is_multi_function": multif,
        "is_fuzzed": fuzzed,
        "composite_score": round(composite, 4),
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(scores: list[dict]) -> str:
    """Build the quality_report.md content."""
    total = len(scores)
    fuzzed = [s for s in scores if s["is_fuzzed"]]
    non_fuzzed = [s for s in scores if not s["is_fuzzed"]]

    # Histogram buckets
    buckets = defaultdict(int)
    for s in scores:
        bucket = min(int(s["composite_score"] * 10), 9)  # 0-9
        buckets[bucket] += 1

    # Per-category stats
    cat_scores = defaultdict(list)
    for s in scores:
        cat_scores[s["category"]].append(s["composite_score"])
    cat_avg = {
        cat: round(sum(v) / len(v), 4) for cat, v in sorted(cat_scores.items())
    }

    # Bottom 10%
    sorted_scores = sorted(scores, key=lambda s: s["composite_score"])
    bottom_n = max(1, total // 10)
    bottom_10 = sorted_scores[:bottom_n]
    bottom_threshold = bottom_10[-1]["composite_score"] if bottom_10 else 0

    # Error handling and stdlib counts
    err_entries = [s for s in scores if s["has_error_handling"]]
    stdlib_entries = [s for s in scores if s["has_stdlib"]]

    # Non-fuzzed stats
    nf_composites = [s["composite_score"] for s in non_fuzzed]
    nf_mean = sum(nf_composites) / len(nf_composites) if nf_composites else 0

    lines = []
    lines.append("# Corpus Quality Report")
    lines.append("")
    lines.append(f"**Generated**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Total entries scored**: {total:,}")
    lines.append(f"**Fuzzed entries flagged for removal**: {len(fuzzed):,}")
    lines.append(f"**Non-fuzzed entries**: {len(non_fuzzed):,}")
    lines.append(f"**Non-fuzzed mean composite score**: {nf_mean:.4f}")
    lines.append("")

    # Histogram
    lines.append("## Composite Score Distribution")
    lines.append("")
    lines.append("| Range | Count | % |")
    lines.append("|-------|------:|--:|")
    for b in range(10):
        lo = b / 10
        hi = (b + 1) / 10
        count = buckets[b]
        pct = count / total * 100 if total else 0
        bar = "#" * max(1, int(pct / 2))
        lines.append(f"| {lo:.1f}–{hi:.1f} | {count:,} | {pct:.1f}% {bar} |")
    lines.append("")

    # Per-category
    lines.append("## Per-Category Average Scores")
    lines.append("")
    lines.append("| Category | Count | Avg Score |")
    lines.append("|----------|------:|----------:|")
    for cat, avg in cat_avg.items():
        cnt = len(cat_scores[cat])
        lines.append(f"| {cat} | {cnt:,} | {avg:.4f} |")
    lines.append("")

    # Bottom 10%
    lines.append("## Bottom 10% — Candidates for Removal")
    lines.append("")
    lines.append(f"Threshold composite score: **{bottom_threshold:.4f}**")
    lines.append(f"Entries at or below threshold: **{bottom_n:,}**")
    lines.append("")

    # Breakdown of bottom 10% by category
    bottom_cats = defaultdict(int)
    for s in bottom_10:
        bottom_cats[s["category"]] += 1
    lines.append("| Category | Count in bottom 10% |")
    lines.append("|----------|--------------------:|")
    for cat, cnt in sorted(bottom_cats.items(), key=lambda x: -x[1]):
        lines.append(f"| {cat} | {cnt:,} |")
    lines.append("")

    # Fuzzed summary
    lines.append("## Fuzzed Programs (flagged for removal)")
    lines.append("")
    lines.append(f"- **Count**: {len(fuzzed):,}")
    if fuzzed:
        fuzz_composites = [s["composite_score"] for s in fuzzed]
        lines.append(f"- **Mean composite**: {sum(fuzz_composites)/len(fuzz_composites):.4f}")
        lines.append(f"- **Min composite**: {min(fuzz_composites):.4f}")
        lines.append(f"- **Max composite**: {max(fuzz_composites):.4f}")
    lines.append("")

    # Error handling (for 10.2 planning)
    lines.append("## Entries with Error Handling (for story 10.2)")
    lines.append("")
    lines.append(f"- **Count**: {len(err_entries):,} ({len(err_entries)/total*100:.1f}%)")
    err_cats = defaultdict(int)
    for s in err_entries:
        err_cats[s["category"]] += 1
    if err_cats:
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|----------|------:|")
        for cat, cnt in sorted(err_cats.items(), key=lambda x: -x[1]):
            lines.append(f"| {cat} | {cnt:,} |")
    lines.append("")

    # Stdlib usage (for 10.3 planning)
    lines.append("## Entries with Stdlib Usage (for story 10.3)")
    lines.append("")
    lines.append(f"- **Count**: {len(stdlib_entries):,} ({len(stdlib_entries)/total*100:.1f}%)")
    std_cats = defaultdict(int)
    for s in stdlib_entries:
        std_cats[s["category"]] += 1
    if std_cats:
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|----------|------:|")
        for cat, cnt in sorted(std_cats.items(), key=lambda x: -x[1]):
            lines.append(f"| {cat} | {cnt:,} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Scanning {CORPUS_DIR} ...")
    json_files = []
    for dirpath, _dirs, filenames in os.walk(CORPUS_DIR):
        for fn in filenames:
            if fn.endswith(".json"):
                json_files.append(os.path.join(dirpath, fn))

    print(f"Found {len(json_files):,} JSON files. Scoring with 8 workers ...")
    t0 = time.time()

    with Pool(processes=8) as pool:
        results = pool.map(score_file, json_files, chunksize=512)

    scores = [r for r in results if r is not None]
    elapsed = time.time() - t0
    print(f"Scored {len(scores):,} entries in {elapsed:.1f}s")

    # Write JSONL
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSONL, "w") as f:
        for s in scores:
            f.write(json.dumps(s) + "\n")
    print(f"Wrote {OUTPUT_JSONL}")

    # Generate report
    report = generate_report(scores)
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_MD, "w") as f:
        f.write(report)
    print(f"Wrote {REPORT_MD}")

    # Quick summary
    fuzzed = sum(1 for s in scores if s["is_fuzzed"])
    non_fuzzed = len(scores) - fuzzed
    composites = [s["composite_score"] for s in scores if not s["is_fuzzed"]]
    mean_c = sum(composites) / len(composites) if composites else 0
    print(f"\nSummary: {non_fuzzed:,} non-fuzzed (mean={mean_c:.4f}), {fuzzed:,} fuzzed flagged for removal")


if __name__ == "__main__":
    main()
