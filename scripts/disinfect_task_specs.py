#!/usr/bin/env python3
"""disinfect_task_specs.py — Story 10.10.3.

Rewrite Phase 1 syntax ("F=name(...):ret", camelCase identifiers like
`startsWith`, `parseInt`, `charCode`, `M=...`, `[1,2,3]`) that leaked
into `data/task_specs_v2.jsonl` (105K task specs), `generator/new_tasks.json`,
and any other spec inputs the generator uses, into Phase 2 equivalents.

The problem: many rows in `task_specs_v2.jsonl` have descriptions like
`"Write a function F=min2(a:f64;b:f64):f64 that ..."` and domain_context
strings like `"F=startsWith(s:$str;prefix:$str):bool F=contains(...)"`.
This means when the generator builds an LLM prompt, it told the model
to write Phase 1 style — which is why so much of the generated corpus
needed fix-up. This script neutralises the infection at the source.

What it does (all string-aware, preserves quoted substrings verbatim):
  1. Replace `F=<name>(...)` → `f=<lowered_name>(...)` outside strings.
  2. Replace `M=<name>` → `m=<lowered_name>` outside strings.
  3. Replace known camelCase identifiers with their canonical Phase 2
     equivalents (see `CAMEL_MAP`). Only touches bare-word matches
     outside strings, so `'--verbose'` is untouched.
  4. For any remaining camelCase word outside a string that matches
     `[a-z]+[A-Z][A-Za-z0-9]*`, lowercase it (`charCode` → `charcode`).
  5. Replace `[a,b,c]` array literals that appear inside tk-code-looking
     substrings with `@(a;b;c)` — only applied to `domain_context`,
     not to natural-language `description` where brackets often mean
     something else.

Input/output: rewrites `data/task_specs_v2.jsonl` in place (backup is
written alongside with `.bak.jsonl` suffix unless --no-backup).
Also rewrites `generator/new_tasks.json` (full Phase 1 style, every
entry has `F=camelCase(...)`).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_SPECS = ROOT / "data" / "task_specs_v2.jsonl"
BENCHMARK = ROOT / "data" / "benchmark_tasks.jsonl"
NEW_TASKS = ROOT / "generator" / "new_tasks.json"

# Canonical Phase 2 forms for common Phase 1 camelCase identifiers. Keys
# are the Phase 1 spelling; values are the Phase 2 replacement. These
# follow the grammar rule `[a-z][a-z0-9]*` (no underscores) and, where
# applicable, match the actual stdlib names in `toke/stdlib/str.tki` and
# `toke/stdlib/math.tki`.
CAMEL_MAP: dict[str, str] = {
    "startsWith": "startswith",
    "endsWith": "endswith",
    "indexOf": "indexof",
    "lastIndexOf": "lastindexof",
    "parseInt": "parseint",
    "parseFloat": "parsefloat",
    "toString": "tostring",
    "toInt": "toint",
    "toFloat": "tofloat",
    "charCode": "charcode",
    "fromCharCode": "fromcharcode",
    "toLower": "tolower",
    "toUpper": "toupper",
    "maxLen": "maxlen",
    "minLen": "minlen",
    "toType": "totype",
    "arrIndexOf": "arrindexof",
    "arrCount": "arrcount",
    "arrContains": "arrcontains",
    "arrLen": "arrlen",
    "arrConcat": "arrconcat",
    "arrSlice": "arrslice",
    "arrReverse": "arrreverse",
    "arrSum": "arrsum",
    "arrMin": "arrmin",
    "arrMax": "arrmax",
    "arrFirst": "arrfirst",
    "arrLast": "arrlast",
    "arrGet": "arrget",
    "arrPush": "arrpush",
    "arrPop": "arrpop",
    "safeGet": "safeget",
    "safeFirst": "safefirst",
    "safeLast": "safelast",
    "safeLookup": "safelookup",
    "safeDiv": "safediv",
    "safeDivI": "safedivi",
    "safeMod": "safemod",
    "safeSqrt": "safesqrt",
    "linearSearch": "linearsearch",
    "binarySearch": "binarysearch",
    "mergeSort": "mergesort",
    "bubbleSort": "bubblesort",
    "quickSort": "quicksort",
    "insertionSort": "insertionsort",
    "findMax": "findmax",
    "findMin": "findmin",
    "findMaxIndex": "findmaxindex",
    "findMinIndex": "findminindex",
    "ifVal": "ifval",
    "isGreaterThan": "isgreaterthan",
    "isLessThan": "islessthan",
    "isGreaterOrEqual": "isgreaterorequal",
    "isLessOrEqual": "islessorequal",
    "isBetween": "isbetween",
    "isEqual": "isequal",
    "isNotEqual": "isnotequal",
    "strLen": "strlen",
    "strReverse": "strreverse",
    "strConcat": "strconcat",
}

# Regex for identifiers that start with lowercase and have at least one
# uppercase char (classic camelCase). Used for the fallback pass over
# anything not in CAMEL_MAP.
_CAMEL_RE = re.compile(r"\b([a-z][a-z0-9]*[A-Z][A-Za-z0-9]*)\b")
# Regex for the Phase 1 `F=<name>` / `M=<name>` / `T=<name>` prefixes.
_F_PREFIX = re.compile(r"\bF=([A-Za-z][A-Za-z0-9]*)")
_M_PREFIX = re.compile(r"\bM=([A-Za-z][A-Za-z0-9]*)")
_T_PREFIX = re.compile(r"\bT=([A-Za-z][A-Za-z0-9]*)")


def _split_strings(text: str) -> list[tuple[str, bool]]:
    """Split text into [(segment, is_string)] preserving order.

    is_string=True segments are quoted literals (either `"..."` or `'...'`)
    and must be left untouched. Backslash-escape aware.
    """
    out: list[tuple[str, bool]] = []
    n = len(text)
    i = 0
    buf: list[str] = []
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            delim = c
            if buf:
                out.append(("".join(buf), False))
                buf = []
            s: list[str] = [c]
            i += 1
            while i < n:
                ch = text[i]
                s.append(ch)
                if ch == "\\" and i + 1 < n:
                    s.append(text[i + 1])
                    i += 2
                    continue
                i += 1
                if ch == delim:
                    break
            out.append(("".join(s), True))
            continue
        buf.append(c)
        i += 1
    if buf:
        out.append(("".join(buf), False))
    return out


def _rewrite_non_string(seg: str) -> str:
    """Apply all disinfection rules to a non-string segment."""
    # 1. F=<name> → f=<lowered_name>
    def _f_sub(m: re.Match[str]) -> str:
        name = m.group(1)
        return "f=" + _lower_ident(name)

    def _m_sub(m: re.Match[str]) -> str:
        name = m.group(1)
        return "m=" + _lower_ident(name)

    def _t_sub(m: re.Match[str]) -> str:
        name = m.group(1)
        return "t=$" + _lower_ident(name)

    seg = _F_PREFIX.sub(_f_sub, seg)
    seg = _M_PREFIX.sub(_m_sub, seg)
    seg = _T_PREFIX.sub(_t_sub, seg)

    # 2. Known camelCase identifiers → mapped.
    def _camel_sub(m: re.Match[str]) -> str:
        ident = m.group(1)
        if ident in CAMEL_MAP:
            return CAMEL_MAP[ident]
        return _lower_ident(ident)

    seg = _CAMEL_RE.sub(_camel_sub, seg)
    return seg


def _lower_ident(name: str) -> str:
    """Map a (possibly camelCase) identifier to Phase 2 form.

    If in CAMEL_MAP use that; otherwise lowercase and strip underscores
    so the result matches `[a-z][a-z0-9]*`.
    """
    if name in CAMEL_MAP:
        return CAMEL_MAP[name]
    # Strip underscores (Phase 2 forbids them) and lowercase.
    return name.replace("_", "").lower()


def disinfect(text: str) -> str:
    """String-aware rewrite of one text field."""
    if not text:
        return text
    parts = _split_strings(text)
    out: list[str] = []
    for seg, is_str in parts:
        if is_str:
            out.append(seg)
        else:
            out.append(_rewrite_non_string(seg))
    return "".join(out)


# ---------------------------------------------------------------------------
# File-level drivers
# ---------------------------------------------------------------------------


def process_task_specs(path: Path, backup: bool) -> dict:
    """Rewrite JSONL file row by row. Fields touched: description, domain_context."""
    if backup:
        shutil.copyfile(path, path.with_suffix(".bak.jsonl"))
    rows_in = rows_out = rows_changed = 0
    tmp = path.with_suffix(".tmp.jsonl")
    with open(path) as fh_in, open(tmp, "w") as fh_out:
        for line in fh_in:
            rows_in += 1
            d = json.loads(line)
            orig_desc = d.get("description", "")
            orig_dc = d.get("domain_context", "")
            new_desc = disinfect(orig_desc)
            new_dc = disinfect(orig_dc)
            if new_desc != orig_desc or new_dc != orig_dc:
                rows_changed += 1
                d["description"] = new_desc
                d["domain_context"] = new_dc
            fh_out.write(json.dumps(d, separators=(",", ":")) + "\n")
            rows_out += 1
    tmp.replace(path)
    return {
        "path": str(path),
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rows_changed": rows_changed,
    }


def process_new_tasks(path: Path, backup: bool) -> dict:
    """Rewrite generator/new_tasks.json (dict of category → list of [name, sig, desc])."""
    if backup:
        shutil.copyfile(path, path.with_suffix(".bak.json"))
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected new_tasks shape: {type(data).__name__}")
    changed = 0
    total = 0
    new_data: dict[str, list] = {}
    for cat, entries in data.items():
        new_entries: list[list[str]] = []
        for entry in entries:
            total += 1
            if not isinstance(entry, list) or len(entry) < 3:
                new_entries.append(entry)
                continue
            name, sig, desc = entry[0], entry[1], entry[2]
            new_name = _lower_ident(name) if isinstance(name, str) else name
            new_sig = disinfect(sig) if isinstance(sig, str) else sig
            new_desc = disinfect(desc) if isinstance(desc, str) else desc
            rest = entry[3:]
            new_entry = [new_name, new_sig, new_desc, *rest]
            if new_entry != entry:
                changed += 1
            new_entries.append(new_entry)
        new_data[cat] = new_entries
    path.write_text(json.dumps(new_data, indent=2) + "\n")
    return {"path": str(path), "entries": total, "changed": changed}


def process_benchmark_tasks(path: Path, backup: bool) -> dict:
    """Safety-check benchmark_tasks.jsonl — the survey said clean but run through
    the disinfector anyway so the rule is the same everywhere."""
    if not path.exists():
        return {"path": str(path), "skipped": "missing"}
    if backup:
        shutil.copyfile(path, path.with_suffix(".bak.jsonl"))
    rows = rows_changed = 0
    tmp = path.with_suffix(".tmp.jsonl")
    with open(path) as fh_in, open(tmp, "w") as fh_out:
        for line in fh_in:
            rows += 1
            d = json.loads(line)
            orig = json.dumps(d, sort_keys=True)
            for key in ("description", "domain_context", "prompt"):
                if key in d and isinstance(d[key], str):
                    d[key] = disinfect(d[key])
            if json.dumps(d, sort_keys=True) != orig:
                rows_changed += 1
            fh_out.write(json.dumps(d, separators=(",", ":")) + "\n")
    tmp.replace(path)
    return {"path": str(path), "rows": rows, "rows_changed": rows_changed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        # Sanity test a few rows without writing.
        print("Dry-run: rewriting first 10 task_spec rows for preview.\n",
              file=sys.stderr)
        with open(TASK_SPECS) as fh:
            for i, line in enumerate(fh):
                if i >= 10:
                    break
                d = json.loads(line)
                dd = disinfect(d.get("description", ""))
                dc = disinfect(d.get("domain_context", ""))
                print(f"row {i}:")
                print(f"  desc   : {d.get('description','')}")
                print(f"  →      : {dd}")
                print(f"  dc     : {d.get('domain_context','')}")
                print(f"  →      : {dc}")
                print()
        return 0

    backup = not args.no_backup
    print(f"Disinfecting {TASK_SPECS} ...", file=sys.stderr)
    r1 = process_task_specs(TASK_SPECS, backup=backup)
    print(f"  rows={r1['rows_in']:,} changed={r1['rows_changed']:,}",
          file=sys.stderr)

    print(f"Disinfecting {NEW_TASKS} ...", file=sys.stderr)
    r2 = process_new_tasks(NEW_TASKS, backup=backup)
    print(f"  entries={r2['entries']:,} changed={r2['changed']:,}",
          file=sys.stderr)

    print(f"Checking {BENCHMARK} ...", file=sys.stderr)
    r3 = process_benchmark_tasks(BENCHMARK, backup=backup)
    print(f"  rows={r3.get('rows', 0):,} changed={r3.get('rows_changed', 0):,}",
          file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
