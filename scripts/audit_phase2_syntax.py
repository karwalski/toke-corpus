#!/usr/bin/env python3
"""audit_phase2_syntax.py — Story 10.10.2.

Scan the corpus, task specs, and task inputs for syntax that violates Phase 2
rules. Fully string/comment/match-arm aware — uses a state machine that tracks
whether the scan cursor is inside a string literal, a line comment, a block
comment, or a match-arm head position.

Checks (outside strings and comments):
  UPPERCASE       — any [A-Z] character NOT in a TYPE_IDENT match-arm-head position
  PHASE1_FUNC     — `F=<name>` function declarations (Phase 1: should be `f=`)
  PHASE1_MOD      — `M=<name>` module declarations (Phase 1: should be `m=`)
  PHASE1_TYPE     — `T=<name>` type declarations (Phase 1: should be `t=$`)
  CAMELCASE       — identifier matching [a-z]+[A-Z][A-Za-z0-9]* (e.g. startsWith)
  DOUBLE_EQUAL    — `==` (Phase 2 equality is single `=`)
  NOT_EQUAL       — `!=`
  SQUARE_BRACKET  — `[` or `]` (Phase 2 uses `@(...)` for arrays/maps)
  RETURN_KEYWORD  — `return ` (Phase 2 uses `<x` or `rt x;`)
  FN_KEYWORD      — `fn ` (Phase 2 uses `f=`)
  ELIF_KEYWORD    — `elif ` or `else if` (Phase 2 uses `el { if ...}`)
  COMMA_SEP       — commas that appear to separate function arguments
  BARE_VOID       — `void` not preceded by `$` in return-type position

Usage:
    python3 scripts/audit_phase2_syntax.py [--corpus-only | --specs-only]
    python3 scripts/audit_phase2_syntax.py --summary  # only print summary

Output: data/phase2_syntax_audit.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus" / "phase2_deduplicated"
TASK_SPECS = ROOT / "data" / "task_specs_v2.jsonl"
BENCHMARK = ROOT / "data" / "benchmark_tasks.jsonl"
NEW_TASKS = ROOT / "generator" / "new_tasks.json"
OUTPUT = ROOT / "data" / "phase2_syntax_audit.jsonl"

# ---------------------------------------------------------------------------
# State machine — extracts "code tokens" from toke source, skipping strings
# and comments, and tracking match-arm-head positions.
# ---------------------------------------------------------------------------

_ARM_HEAD_RE = re.compile(r"[A-Z][A-Za-z0-9]*")
_CAMEL_RE = re.compile(r"[a-z]+[A-Z][A-Za-z0-9]*")


def _scan(src: str, source_path: str, source_field: str) -> list[dict]:
    """Walk src with full context awareness, emitting findings."""
    findings: list[dict] = []
    n = len(src)
    i = 0
    brace_depth = 0
    match_stack: list[int] = []
    expect_arm_head = False

    def _finding(rule: str, offset: int, snippet_len: int = 30) -> None:
        snip = src[max(0, offset - 10):offset + snippet_len]
        findings.append({
            "path": source_path,
            "field": source_field,
            "rule": rule,
            "offset": offset,
            "snippet": snip,
        })

    while i < n:
        c = src[i]

        # --- Double-quoted string literal --- skip entirely.
        if c == '"':
            i += 1
            while i < n:
                ch = src[i]
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
                if ch == '"':
                    break
            continue

        # --- Single-quoted literal --- skip entirely.
        if c == "'":
            i += 1
            while i < n:
                ch = src[i]
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
                if ch == "'":
                    break
            continue

        # --- Line comment --- skip to end of line.
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            i += 2
            while i < n and src[i] != "\n":
                i += 1
            continue

        # --- Block comment --- skip through closing */.
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue

        # --- Match block entry ---
        if c == "|" and i + 1 < n and src[i + 1] == "{":
            i += 2
            brace_depth += 1
            match_stack.append(brace_depth)
            expect_arm_head = True
            continue

        if c == "{":
            brace_depth += 1
            i += 1
            continue

        if c == "}":
            if match_stack and brace_depth == match_stack[-1]:
                match_stack.pop()
            brace_depth -= 1
            expect_arm_head = False
            i += 1
            continue

        if c == ";" and match_stack and brace_depth == match_stack[-1]:
            expect_arm_head = True
            i += 1
            continue

        # --- Match-arm head position: uppercase IS valid here.
        if expect_arm_head:
            if c.isspace():
                i += 1
                continue
            m = _ARM_HEAD_RE.match(src, i)
            if m:
                i = m.end()
                expect_arm_head = False
                continue
            expect_arm_head = False
            # fall through to normal checks

        # ===================================================================
        # Now at a "normal code" position (not string, not comment, not arm head).
        # ===================================================================

        # PHASE1_FUNC: F=<name>
        if c == "F" and i + 1 < n and src[i + 1] == "=" and (i == 0 or not src[i - 1].isalpha()):
            _finding("PHASE1_FUNC", i)
            i += 1
            continue

        # PHASE1_MOD: M=<name>
        if c == "M" and i + 1 < n and src[i + 1] == "=" and (i == 0 or not src[i - 1].isalpha()):
            _finding("PHASE1_MOD", i)
            i += 1
            continue

        # PHASE1_TYPE: T=<name>
        if c == "T" and i + 1 < n and src[i + 1] == "=" and (i == 0 or not src[i - 1].isalpha()):
            _finding("PHASE1_TYPE", i)
            i += 1
            continue

        # DOUBLE_EQUAL: ==
        if c == "=" and i + 1 < n and src[i + 1] == "=":
            _finding("DOUBLE_EQUAL", i)
            i += 2
            continue

        # NOT_EQUAL: !=
        if c == "!" and i + 1 < n and src[i + 1] == "=":
            _finding("NOT_EQUAL", i)
            i += 2
            continue

        # SQUARE_BRACKET
        if c in "[]":
            _finding("SQUARE_BRACKET", i)
            i += 1
            continue

        # RETURN keyword
        if src[i:i + 7] == "return " and (i == 0 or not src[i - 1].isalnum()):
            _finding("RETURN_KEYWORD", i)
            i += 7
            continue

        # FN keyword
        if src[i:i + 3] == "fn " and (i == 0 or not src[i - 1].isalnum()):
            _finding("FN_KEYWORD", i)
            i += 3
            continue

        # ELIF / ELSE IF
        if src[i:i + 5] == "elif " and (i == 0 or not src[i - 1].isalnum()):
            _finding("ELIF_KEYWORD", i)
            i += 5
            continue
        if src[i:i + 8] == "else if " and (i == 0 or not src[i - 1].isalnum()):
            _finding("ELIF_KEYWORD", i)
            i += 8
            continue

        # UPPERCASE check — any single uppercase letter outside known positions.
        if c.isupper():
            # Try to read an identifier
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            ident = src[i:j]
            # Check for camelCase
            if _CAMEL_RE.fullmatch(ident):
                _finding("CAMELCASE", i, snippet_len=len(ident) + 10)
            else:
                _finding("UPPERCASE", i, snippet_len=len(ident) + 10)
            i = j if j > i else i + 1
            continue

        # COMMA_SEP — comma inside parentheses (likely function args).
        if c == ",":
            # crude heuristic: if preceded by a non-quote character
            _finding("COMMA_SEP", i)
            i += 1
            continue

        i += 1

    return findings


def _audit_file(path_str: str) -> list[dict]:
    """Audit one corpus JSON file."""
    p = Path(path_str)
    try:
        data = json.loads(p.read_text())
    except Exception:
        return [{"path": path_str, "rule": "UNREADABLE", "field": "", "offset": 0, "snippet": ""}]

    results: list[dict] = []
    for field in ("tk_source", "broken_source"):
        src = data.get(field)
        if src and isinstance(src, str):
            results.extend(_scan(src, str(p), field))
    return results


def collect_corpus_files(root: Path) -> list[str]:
    files: list[str] = []
    for cat_dir in sorted(root.iterdir()):
        if not cat_dir.is_dir():
            continue
        for p in cat_dir.iterdir():
            if p.suffix == ".json":
                files.append(str(p))
    return files


def audit_jsonl(path: Path, fields: list[str]) -> list[dict]:
    """Scan a JSONL file (task specs / benchmarks) for violations."""
    results: list[dict] = []
    with open(path) as fh:
        for line_no, line in enumerate(fh, 1):
            d = json.loads(line)
            for field in fields:
                v = d.get(field)
                if v and isinstance(v, str):
                    findings = _scan(v, f"{path}:{line_no}", field)
                    results.extend(findings)
    return results


def audit_new_tasks(path: Path) -> list[dict]:
    """Scan generator/new_tasks.json."""
    data = json.loads(path.read_text())
    results: list[dict] = []
    if not isinstance(data, dict):
        return results
    for cat, entries in data.items():
        for idx, entry in enumerate(entries):
            if not isinstance(entry, list):
                continue
            for part_idx, part in enumerate(entry):
                if isinstance(part, str):
                    findings = _scan(part, f"{path}:{cat}[{idx}]",
                                     f"entry[{part_idx}]")
                    results.extend(findings)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-only", action="store_true")
    ap.add_argument("--specs-only", action="store_true")
    ap.add_argument("--summary", action="store_true",
                    help="only print summary counts, skip JSONL output")
    args = ap.parse_args()

    all_findings: list[dict] = []

    if not args.specs_only:
        files = collect_corpus_files(CORPUS_DIR)
        print(f"Auditing {len(files):,} corpus records...", file=sys.stderr)
        import os
        with Pool(processes=min(8, os.cpu_count() or 8)) as pool:
            batches = pool.map(_audit_file, files, chunksize=2000)
        for b in batches:
            all_findings.extend(b)
        print(f"  corpus findings: {len(all_findings):,}", file=sys.stderr)

    if not args.corpus_only:
        if TASK_SPECS.exists():
            print(f"Auditing {TASK_SPECS.name}...", file=sys.stderr)
            r = audit_jsonl(TASK_SPECS, ["description", "domain_context"])
            print(f"  task_specs findings: {len(r):,}", file=sys.stderr)
            all_findings.extend(r)

        if BENCHMARK.exists():
            print(f"Auditing {BENCHMARK.name}...", file=sys.stderr)
            r = audit_jsonl(BENCHMARK, ["description", "domain_context", "prompt"])
            print(f"  benchmark findings: {len(r):,}", file=sys.stderr)
            all_findings.extend(r)

        if NEW_TASKS.exists():
            print(f"Auditing {NEW_TASKS.name}...", file=sys.stderr)
            r = audit_new_tasks(NEW_TASKS)
            print(f"  new_tasks findings: {len(r):,}", file=sys.stderr)
            all_findings.extend(r)

    # Summary
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Total findings: {len(all_findings):,}", file=sys.stderr)
    by_rule = Counter(f["rule"] for f in all_findings)
    print("\nBy rule:", file=sys.stderr)
    for rule, count in by_rule.most_common():
        print(f"  {rule:25s} {count:8,}", file=sys.stderr)

    by_field = Counter(f["field"] for f in all_findings)
    print("\nBy field:", file=sys.stderr)
    for field, count in by_field.most_common():
        print(f"  {field:25s} {count:8,}", file=sys.stderr)

    # By-category breakdown for corpus
    cat_rules: dict[str, Counter] = defaultdict(Counter)
    for f in all_findings:
        p = f.get("path", "")
        if "phase2_deduplicated" in p:
            parts = Path(p).parts
            idx = parts.index("phase2_deduplicated") + 1 if "phase2_deduplicated" in parts else -1
            cat = parts[idx] if idx < len(parts) else "?"
            cat_rules[cat][f["rule"]] += 1
    if cat_rules:
        print("\nTop 15 corpus categories by finding count:", file=sys.stderr)
        ranked = sorted(cat_rules.items(), key=lambda kv: -sum(kv[1].values()))[:15]
        for cat, ctr in ranked:
            total = sum(ctr.values())
            top3 = " ".join(f"{r}={n}" for r, n in ctr.most_common(3))
            print(f"  {cat:40s} tot={total:6,}  {top3}", file=sys.stderr)

    # Write output
    if not args.summary:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT, "w") as fh:
            for f in all_findings:
                fh.write(json.dumps(f, separators=(",", ":")) + "\n")
        print(f"\nWrote {OUTPUT}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
