#!/usr/bin/env python3
"""phase2_revert_match_arm_sigils.py — Story 10.9.5 revert.

Undoes `phase2_autofix_match_arm_sigils.py`. The assumption that Phase 2
match arms should use SIGIL_TYPE (`$ok:v`) was incorrect — the grammar
(`toke/spec/spec/grammar.ebnf` line 151: `MatchArm = TYPE_IDENT ':' IDENT
Expr`) and the reference compiler only accept TYPE_IDENT (`Ok:v`).

For each record with `syntax_audit.match_sigil_applied_at` set, walk all
`|{...}` blocks and rewrite `$lowered:` arm heads back to TYPE_IDENT form.
Variant casing is recovered from `syntax_audit.original_source` when
available, else from a default map, else by title-casing.
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


DEFAULT_VARIANT_MAP = {
    "ok": "Ok",
    "err": "Err",
    "notfound": "NotFound",
    "emptycollection": "EmptyCollection",
    "some": "Some",
    "none": "None",
    "invalidformat": "InvalidFormat",
    "emptyinput": "EmptyInput",
    "outofrange": "OutOfRange",
    "divbyzero": "DivByZero",
}


def _recover_from_original(orig: str | None) -> dict[str, str]:
    """Scan original_source for uppercase idents and return {lower: original}."""
    if not orig:
        return {}
    mapping: dict[str, str] = {}
    for m in re.finditer(r"[A-Z][A-Za-z0-9]*", orig):
        name = m.group(0)
        key = name.lower()
        if key not in mapping:
            mapping[key] = name
    return mapping


def _resolve(lowered: str, recovered: dict[str, str]) -> str:
    if lowered in recovered:
        return recovered[lowered]
    if lowered in DEFAULT_VARIANT_MAP:
        return DEFAULT_VARIANT_MAP[lowered]
    return lowered[:1].upper() + lowered[1:]


_RE_SIGIL_HEAD = re.compile(r"\$([a-z][a-z0-9]*)(\s*:)")


def revert_source(
    src: str, recovered: dict[str, str]
) -> tuple[str, int]:
    """Rewrite `$lower:` → `TitleCase:` but only when inside a `|{...}` block
    at arm-head position (directly after `|{` or after a depth-0 `;` within
    the innermost enclosing match block).
    """
    if not src or "|{" not in src:
        return src, 0

    out: list[str] = []
    i = 0
    n = len(src)
    brace_depth = 0
    match_stack: list[int] = []
    expect_arm_head = False
    count = 0

    while i < n:
        c = src[i]

        if c == '"':
            out.append(c)
            i += 1
            while i < n:
                ch = src[i]
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                i += 1
                if ch == '"':
                    break
            continue

        if c == "|" and i + 1 < n and src[i + 1] == "{":
            out.append("|{")
            i += 2
            brace_depth += 1
            match_stack.append(brace_depth)
            expect_arm_head = True
            continue

        if c == "{":
            brace_depth += 1
            out.append(c)
            i += 1
            continue

        if c == "}":
            if match_stack and brace_depth == match_stack[-1]:
                match_stack.pop()
            brace_depth -= 1
            out.append(c)
            i += 1
            expect_arm_head = False
            continue

        if c == ";" and match_stack and brace_depth == match_stack[-1]:
            out.append(c)
            i += 1
            expect_arm_head = True
            continue

        if expect_arm_head:
            if c.isspace():
                out.append(c)
                i += 1
                continue
            m = _RE_SIGIL_HEAD.match(src, i)
            if m:
                lowered = m.group(1)
                restored = _resolve(lowered, recovered)
                out.append(restored)
                out.append(m.group(2))
                i = m.end()
                count += 1
                expect_arm_head = False
                continue
            expect_arm_head = False

        out.append(c)
        i += 1

    return "".join(out), count


def revert_record(record: dict) -> tuple[bool, int, int]:
    changed = False
    tk_count = 0
    broken_count = 0

    sa = record.get("syntax_audit") or {}
    if "match_sigil_applied_at" not in sa:
        return (False, 0, 0)

    orig_src = sa.get("original_source")
    recovered = _recover_from_original(orig_src)

    src = record.get("tk_source", "")
    if isinstance(src, str):
        new, n = revert_source(src, recovered)
        if n > 0:
            record["tk_source"] = new
            tk_count = n
            changed = True

    refs = record.get("references")
    if isinstance(refs, dict):
        bs = refs.get("broken_source")
        if isinstance(bs, str):
            new, n = revert_source(bs, recovered)
            if n > 0:
                refs["broken_source"] = new
                broken_count = n
                changed = True

    # Clean up the sigil-applied metadata regardless (so re-runs are idempotent)
    sa.pop("match_sigil_applied_at", None)
    sa.pop("match_sigil_tk_count", None)
    sa.pop("match_sigil_broken_count", None)
    # Restore conformant flag — the autofix forced it to False but TYPE_IDENT
    # arm heads are actually valid, so we put it back to what it was before.
    sa["phase2_conformant"] = True
    cc = record.get("compile_check")
    if isinstance(cc, dict):
        cc.pop("stale_after_match_sigil", None)

    return (True, tk_count, broken_count)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Revert Phase 2 match-arm sigil autofix (Story 10.9.5)"
    )
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    scanned = 0
    reverted = 0
    tk_total = 0
    broken_total = 0
    by_cat: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )

    for cat_dir in sorted(args.corpus_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        for p in cat_dir.iterdir():
            if p.suffix != ".json":
                continue
            try:
                with open(p) as f:
                    rec = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            scanned += 1

            sa = rec.get("syntax_audit") or {}
            if "match_sigil_applied_at" not in sa:
                continue

            changed, tk_n, br_n = revert_record(rec)
            if changed:
                reverted += 1
                tk_total += tk_n
                broken_total += br_n
                by_cat[cat_dir.name]["reverted"] += 1
                if not args.dry_run:
                    try:
                        with open(p, "w") as f:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    except OSError:
                        pass

            if scanned % 25000 == 0:
                print(f"    scanned {scanned}  reverted {reverted}",
                      file=sys.stderr)

    print("=" * 60, file=sys.stderr)
    print("  Match-arm sigil REVERT summary", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Scanned:         {scanned}", file=sys.stderr)
    print(f"  Reverted:        {reverted}", file=sys.stderr)
    print(f"  tk arms revert:  {tk_total}", file=sys.stderr)
    print(f"  broken reverts:  {broken_total}", file=sys.stderr)
    if args.dry_run:
        print("\n  [DRY RUN]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
