#!/usr/bin/env python3
"""
Story 10.4.1: Classify all corpus entries into complexity tiers.

Reads every JSON file from corpus/phase2_combined/, computes complexity
metrics, classifies into simple/medium/complex/application tiers, and
produces:
  - data/corpus_complexity.jsonl   (per-entry classification)
  - docs/complexity_report.md      (aggregate analysis)

ANALYSIS ONLY — no corpus files are modified.
"""

import json
import os
import re
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus" / "phase2_combined"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# Research-recommended distribution targets
TARGET_DIST = {
    "simple": (0.40, 0.50),
    "medium": (0.25, 0.30),
    "complex": (0.10, 0.15),
    "application": (0.05, 0.10),
}


def estimate_nesting_depth(source: str) -> int:
    """Estimate max brace nesting depth."""
    depth = 0
    max_depth = 0
    for ch in source:
        if ch == '{':
            depth += 1
            if depth > max_depth:
                max_depth = depth
        elif ch == '}':
            depth -= 1
    return max_depth


def count_pattern(source: str, pattern: str) -> int:
    """Count non-overlapping occurrences of a literal pattern."""
    return source.count(pattern)


def analyse_entry(filepath: str) -> dict | None:
    """Analyse a single corpus JSON file and return metrics + tier."""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    entry_id = data.get("id", Path(filepath).stem)
    tk_source = data.get("tk_source", "")
    tk_tokens = data.get("tk_tokens")

    # Derive category from the file path (parent directory name)
    category = Path(filepath).parent.name

    # --- Metrics ---
    num_functions = count_pattern(tk_source, "f=")
    num_loops = count_pattern(tk_source, "lp(")
    num_conditionals = count_pattern(tk_source, "if(")
    num_let_bindings = count_pattern(tk_source, "let ")
    num_imports = count_pattern(tk_source, "i=")
    num_types = count_pattern(tk_source, "t=")
    has_error_handling = "!$" in tk_source
    nesting_depth = estimate_nesting_depth(tk_source)

    # Token count: prefer tk_tokens field, fallback to whitespace split
    if tk_tokens is not None and isinstance(tk_tokens, (int, float)):
        token_count = int(tk_tokens)
    else:
        token_count = len(tk_source.split())

    loops_plus_conds = num_loops + num_conditionals

    # --- Classification ---
    # Evaluate from most complex to least; first match wins.
    if (num_functions >= 4
            or (num_imports >= 1 and num_functions >= 3)
            or token_count > 300):
        tier = "application"
    elif (2 <= num_functions <= 3
          or token_count > 150):
        tier = "complex"
    elif (num_functions <= 1
          and (loops_plus_conds > 2
               or 50 <= token_count <= 150
               or has_error_handling)):
        tier = "medium"
    else:
        # 1 function, <=2 loops+conds, <50 tokens
        tier = "simple"

    return {
        "id": entry_id,
        "category": category,
        "tier": tier,
        "num_functions": num_functions,
        "num_loops": num_loops,
        "num_conditionals": num_conditionals,
        "num_let_bindings": num_let_bindings,
        "num_imports": num_imports,
        "num_types": num_types,
        "token_count": token_count,
        "has_error_handling": has_error_handling,
        "nesting_depth": nesting_depth,
    }


def collect_files() -> list[str]:
    """Walk the corpus directory and return all .json file paths."""
    files = []
    for root, _dirs, filenames in os.walk(CORPUS_DIR):
        for fn in filenames:
            if fn.endswith(".json"):
                files.append(os.path.join(root, fn))
    return sorted(files)


def write_jsonl(results: list[dict], path: Path):
    """Write results as newline-delimited JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        for r in results:
            f.write(json.dumps(r, separators=(',', ':')) + '\n')


def generate_report(results: list[dict], path: Path):
    """Generate the complexity_report.md analysis document."""
    total = len(results)

    # --- Tier distribution ---
    tier_counts = defaultdict(int)
    tier_metrics = defaultdict(lambda: defaultdict(list))
    cat_tier_counts = defaultdict(lambda: defaultdict(int))

    metric_keys = [
        "token_count", "num_functions", "num_loops",
        "num_conditionals", "num_let_bindings", "num_imports",
        "num_types", "nesting_depth",
    ]

    for r in results:
        tier = r["tier"]
        cat = r["category"]
        tier_counts[tier] += 1
        cat_tier_counts[cat][tier] += 1
        for k in metric_keys:
            tier_metrics[tier][k].append(r[k])

    tier_order = ["simple", "medium", "complex", "application"]
    categories = sorted(cat_tier_counts.keys())

    lines = []
    lines.append("# Corpus Complexity Report")
    lines.append("")
    lines.append(f"**Total entries analysed:** {total:,}")
    lines.append(f"**Generated:** analysis only (no files modified)")
    lines.append("")

    # --- Overall tier distribution ---
    lines.append("## Tier Distribution")
    lines.append("")
    lines.append("| Tier | Count | % | Target % |")
    lines.append("|------|------:|--:|----------|")
    for t in tier_order:
        c = tier_counts[t]
        pct = 100.0 * c / total if total else 0
        lo, hi = TARGET_DIST[t]
        lines.append(f"| {t} | {c:,} | {pct:.1f}% | {lo*100:.0f}-{hi*100:.0f}% |")
    lines.append("")

    # --- Gap analysis ---
    lines.append("## Gap Analysis")
    lines.append("")
    lines.append("Entries needed to reach the **midpoint** of each target range,")
    lines.append("assuming the total corpus size stays at {:,}.".format(total))
    lines.append("")
    lines.append("| Tier | Current | Target (mid) | Delta |")
    lines.append("|------|--------:|-------------:|------:|")
    for t in tier_order:
        current = tier_counts[t]
        lo, hi = TARGET_DIST[t]
        mid_target = int(total * (lo + hi) / 2)
        delta = mid_target - current
        sign = "+" if delta > 0 else ""
        lines.append(f"| {t} | {current:,} | {mid_target:,} | {sign}{delta:,} |")
    lines.append("")

    # --- Average metrics per tier ---
    lines.append("## Average Metrics per Tier")
    lines.append("")
    header = "| Tier | " + " | ".join(k.replace("_", " ") for k in metric_keys) + " |"
    sep = "|------|" + "|".join(["-----:" for _ in metric_keys]) + "|"
    lines.append(header)
    lines.append(sep)
    for t in tier_order:
        vals = []
        for k in metric_keys:
            arr = tier_metrics[t][k]
            avg = sum(arr) / len(arr) if arr else 0
            vals.append(f"{avg:.1f}")
        lines.append(f"| {t} | " + " | ".join(vals) + " |")
    lines.append("")

    # --- Per-category tier breakdown ---
    lines.append("## Tier Distribution by Category")
    lines.append("")
    header = "| Category | " + " | ".join(tier_order) + " | Total |"
    sep = "|----------|" + "|".join(["-----:" for _ in tier_order]) + "|------:|"
    lines.append(header)
    lines.append(sep)
    for cat in categories:
        cat_total = sum(cat_tier_counts[cat].values())
        parts = []
        for t in tier_order:
            c = cat_tier_counts[cat][t]
            pct = 100.0 * c / cat_total if cat_total else 0
            parts.append(f"{c:,} ({pct:.0f}%)")
        lines.append(f"| {cat} | " + " | ".join(parts) + f" | {cat_total:,} |")
    lines.append("")

    # --- Top 20 most complex ---
    lines.append("## Top 20 Most Complex Programs")
    lines.append("")
    # Score: weighted sum of functions, loops, conditionals, tokens, nesting
    def complexity_score(r):
        return (
            r["num_functions"] * 10
            + r["num_loops"] * 5
            + r["num_conditionals"] * 5
            + r["token_count"] * 0.1
            + r["nesting_depth"] * 3
            + r["num_imports"] * 8
            + (5 if r["has_error_handling"] else 0)
        )

    scored = sorted(results, key=complexity_score, reverse=True)[:20]
    lines.append("| # | ID | Category | Tier | Funcs | Loops | Conds | Tokens | Nesting | Score |")
    lines.append("|---|-----|----------|------|------:|------:|------:|-------:|--------:|------:|")
    for i, r in enumerate(scored, 1):
        sc = complexity_score(r)
        lines.append(
            f"| {i} | {r['id']} | {r['category']} | {r['tier']} "
            f"| {r['num_functions']} | {r['num_loops']} | {r['num_conditionals']} "
            f"| {r['token_count']} | {r['nesting_depth']} | {sc:.1f} |"
        )
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    print("Collecting corpus files...")
    files = collect_files()
    print(f"Found {len(files):,} JSON files")

    print("Analysing with 8 workers...")
    with Pool(processes=8) as pool:
        raw_results = pool.map(analyse_entry, files, chunksize=2000)

    results = [r for r in raw_results if r is not None]
    failed = len(files) - len(results)
    print(f"Analysed {len(results):,} entries ({failed} failures)")

    print("Writing data/corpus_complexity.jsonl ...")
    write_jsonl(results, DATA_DIR / "corpus_complexity.jsonl")

    print("Generating docs/complexity_report.md ...")
    generate_report(results, DOCS_DIR / "complexity_report.md")

    # Quick summary
    tier_counts = defaultdict(int)
    for r in results:
        tier_counts[r["tier"]] += 1
    print("\n=== Tier Summary ===")
    for t in ["simple", "medium", "complex", "application"]:
        c = tier_counts[t]
        pct = 100.0 * c / len(results) if results else 0
        print(f"  {t:12s}: {c:>8,}  ({pct:5.1f}%)")
    print(f"  {'TOTAL':12s}: {len(results):>8,}")
    print("\nDone.")


if __name__ == "__main__":
    main()
