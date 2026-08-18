#!/usr/bin/env python3
"""generate_manifest.py — Story 10.1.4.

Walk every record in `corpus/phase2_deduplicated/` and emit a per-entry
manifest row joining enrichment fields with the pre-computed quality and
complexity metrics.

Outputs:
  corpus/phase2_deduplicated/MANIFEST.jsonl
  docs/manifest_summary.md

Manifest row schema (JSONL):
  {
    "id": str,
    "category": str,
    "path": str,                       # relative to corpus_dir
    "source": str,                     # model / hand / seed / fuzz etc
    "tk_chars": int,
    "tk_tokens": int | null,
    "composite_score": float | null,
    "complexity_tier": str | null,
    "num_functions": int | null,
    "has_stdlib": bool | null,
    "has_error_handling": bool | null,
    "is_multi_function": bool | null,
    "is_fuzzed": bool | null,
    "phase2_conformant": bool,
    "compile_passed": bool,
    "runtime_ran": bool,
    "runtime_exit": int | null,
    "judge_correct": bool | null,
    "imports_resolved": int,
    "imports_unresolved": int,
  }
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"
QUALITY_FILE = ROOT / "data" / "corpus_quality_scores.jsonl"
COMPLEXITY_FILE = ROOT / "data" / "corpus_complexity.jsonl"
MANIFEST_OUT = DEFAULT_CORPUS / "MANIFEST.jsonl"
SUMMARY_OUT = ROOT / "docs" / "manifest_summary.md"


def load_sidecar(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = row.get("id")
            if rid:
                out[rid] = row
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate corpus manifest")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=MANIFEST_OUT)
    parser.add_argument("--summary", type=Path, default=SUMMARY_OUT)
    args = parser.parse_args()

    print(f"  loading quality scores…", file=sys.stderr)
    quality = load_sidecar(QUALITY_FILE)
    print(f"  loading complexity scores…", file=sys.stderr)
    complexity = load_sidecar(COMPLEXITY_FILE)
    print(f"  quality rows: {len(quality)}  complexity rows: {len(complexity)}",
          file=sys.stderr)

    total = 0
    rows: list[dict] = []
    by_cat: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    tier_counts: collections.Counter = collections.Counter()
    source_counts: collections.Counter = collections.Counter()

    cats = sorted(p for p in args.corpus_dir.iterdir() if p.is_dir())
    for cat_dir in cats:
        cat = cat_dir.name
        for p in cat_dir.iterdir():
            if p.suffix != ".json":
                continue
            try:
                rec = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            rid = rec.get("id") or p.stem
            q = quality.get(rid, {})
            c = complexity.get(rid, {})
            sa = rec.get("syntax_audit") or {}
            cc = rec.get("compile_check") or {}
            rc = rec.get("runtime_check") or {}
            jc = rec.get("judge_output_check") or {}
            imp_ok = rec.get("imported_modules") or []
            imp_bad = rec.get("imports_unresolved") or []
            row = {
                "id": rid,
                "category": cat,
                "path": f"{cat}/{p.name}",
                "source": rec.get("model") or q.get("model") or "unknown",
                "tk_chars": len(rec.get("tk_source") or ""),
                "tk_tokens": rec.get("tk_tokens"),
                "composite_score": q.get("composite_score"),
                "complexity_tier": c.get("tier"),
                "num_functions": c.get("num_functions"),
                "has_stdlib": q.get("has_stdlib"),
                "has_error_handling": q.get("has_error_handling"),
                "is_multi_function": q.get("is_multi_function"),
                "is_fuzzed": q.get("is_fuzzed"),
                "phase2_conformant": bool(sa.get("phase2_conformant")),
                "compile_passed": bool(cc.get("passed")),
                "runtime_ran": bool(rc.get("ran")),
                "runtime_exit": rc.get("exit_code"),
                "judge_correct": jc.get("correct") if jc.get("verified") else None,
                "imports_resolved": len(imp_ok),
                "imports_unresolved": len(imp_bad),
            }
            rows.append(row)
            total += 1
            by_cat[cat]["total"] += 1
            if row["phase2_conformant"]:
                by_cat[cat]["conformant"] += 1
            if row["compile_passed"]:
                by_cat[cat]["compiled"] += 1
            if row["runtime_ran"]:
                by_cat[cat]["ran"] += 1
            tier = row["complexity_tier"] or "unclassified"
            tier_counts[tier] += 1
            source_counts[row["source"]] += 1
            if total % 25000 == 0:
                print(f"    scanned {total}", file=sys.stderr)

    print(f"  writing {args.out}…", file=sys.stderr)
    with open(args.out, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Summary
    print(f"  writing {args.summary}…", file=sys.stderr)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with open(args.summary, "w") as f:
        f.write("# Corpus manifest summary\n\n")
        f.write(f"Total records: **{total}**  \n")
        conformant = sum(c.get("conformant", 0) for c in by_cat.values())
        compiled = sum(c.get("compiled", 0) for c in by_cat.values())
        ran = sum(c.get("ran", 0) for c in by_cat.values())
        f.write(f"Phase 2 conformant: **{conformant}** ({100*conformant/total:.1f}%)  \n")
        f.write(f"Compile pass: **{compiled}** ({100*compiled/total:.1f}%)  \n")
        f.write(f"Runtime ran: **{ran}** ({100*ran/total:.2f}%)  \n\n")

        f.write("## Complexity tiers\n\n")
        f.write("| Tier | Count | % |\n|------|-------|---|\n")
        for t, n in tier_counts.most_common():
            f.write(f"| {t} | {n} | {100*n/total:.1f}% |\n")
        f.write("\n")

        f.write("## Categories (top 30 by size)\n\n")
        f.write("| Category | Total | Conformant | Compiled | Ran |\n")
        f.write("|---|---|---|---|---|\n")
        ranked = sorted(by_cat.items(), key=lambda kv: -kv[1].get("total", 0))
        for cat, cnts in ranked[:30]:
            t = cnts["total"]
            f.write(
                f"| {cat} | {t} | "
                f"{cnts.get('conformant',0)} ({100*cnts.get('conformant',0)/t:.0f}%) | "
                f"{cnts.get('compiled',0)} ({100*cnts.get('compiled',0)/t:.0f}%) | "
                f"{cnts.get('ran',0)} |\n"
            )
        f.write("\n")

        f.write("## Top sources\n\n")
        f.write("| Source | Count |\n|---|---|\n")
        for src, n in source_counts.most_common(15):
            f.write(f"| `{src}` | {n} |\n")

    print(f"\n  manifest: {args.out}", file=sys.stderr)
    print(f"  summary:  {args.summary}", file=sys.stderr)
    print(f"  records:  {total}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
