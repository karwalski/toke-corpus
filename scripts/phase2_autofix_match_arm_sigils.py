#!/usr/bin/env python3
"""phase2_autofix_match_arm_sigils.py — Story 10.9.5.

Rewrite uppercase match-arm heads to SIGIL_TYPE form inside `|{...}` blocks.

Phase 2 spec requires SIGIL_TYPE in match-arm heads:

    result |{ $ok:v  body; $err:e  body }

tkc is lenient and also accepts the Phase 1 TYPE_IDENT form (`Ok:v`,
`Err:e`), which is why these records previously compiled and were marked
`phase2_conformant=true`. But for training purposes we want every row to
model the canonical Phase 2 surface syntax.

This script walks every record in the corpus and rewrites:

    |{ X:binding body; Y:binding body }      →      |{ $x:binding body; $y:binding body }

`Ok` → `$ok`, `Err` → `$err`, `NotFound` → `$notfound`,
`EmptyCollection` → `$emptycollection`, etc.

Affects both `tk_source` (assistant response) AND
`references.broken_source` (BIFI user prompt), since the broken program
is shown verbatim to the model.

The rewrite:
- only applies inside `|{...}` blocks (not struct literals, not general text)
- only rewrites arm heads at top-level depth-0 positions within each block
- is string-aware (skips inside `"..."` literals)
- preserves existing `$variant` heads untouched

After running, re-run `phase2_syntax_audit.py` + `compile_check_corpus.py`
to refresh the conformance flags, then regenerate training data.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"


# ---------------------------------------------------------------------------
# Single-pass rewrite
# ---------------------------------------------------------------------------
#
# We walk the source character-by-character, tracking:
#   - `brace_depth`: current depth of any `{`
#   - `match_stack`: a stack of brace_depth values at which each `|{` was
#     opened. When brace_depth returns to one of these depths and we see `}`,
#     we pop the stack.
#   - `expect_arm_head`: True immediately after `|{` or after a depth-0 `;`
#     relative to the innermost enclosing `|{...}`. When True, the next
#     non-whitespace ident is an arm head.
#
# String literals are skipped verbatim. This handles nested `|{...}` blocks
# correctly because we never skip past a block — we just keep walking.

_RE_UPPER_HEAD = re.compile(r"([A-Z][A-Za-z0-9]*)(\s*:)")


def rewrite_source(src: str) -> tuple[str, int]:
    """Rewrite uppercase match-arm heads to `$<lower>` sigil form.
    Returns (new_source, count_of_arms_rewritten).
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

        # Skip string literals verbatim
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

        # Detect `|{` (match block open)
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

        # Top-level `;` inside a match block starts a new arm
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
            # Try to match an uppercase ident arm head at this position
            m = _RE_UPPER_HEAD.match(src, i)
            if m:
                name = m.group(1)
                out.append("$" + name.lower())
                out.append(m.group(2))
                i = m.end()
                count += 1
                expect_arm_head = False
                continue
            # Either `$ident:` (already sigil), `_:` wildcard, or body began
            # — stop looking for arm head until next `;`.
            expect_arm_head = False

        out.append(c)
        i += 1

    return "".join(out), count


# ---------------------------------------------------------------------------
# Record-level
# ---------------------------------------------------------------------------

def fix_record(record: dict) -> tuple[bool, int, int]:
    """Apply the rewrite to tk_source and references.broken_source.
    Returns (changed, tk_count, broken_count).
    """
    changed = False
    tk_count = 0
    broken_count = 0

    src = record.get("tk_source", "")
    if isinstance(src, str):
        new, n = rewrite_source(src)
        if n > 0:
            record["tk_source"] = new
            tk_count = n
            changed = True

    refs = record.get("references")
    if isinstance(refs, dict):
        bs = refs.get("broken_source")
        if isinstance(bs, str):
            new, n = rewrite_source(bs)
            if n > 0:
                refs["broken_source"] = new
                broken_count = n
                changed = True

    if changed:
        sa = record.setdefault("syntax_audit", {})
        sa["match_sigil_applied_at"] = _dt.datetime.now(
            _dt.timezone.utc
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        sa["match_sigil_tk_count"] = tk_count
        sa["match_sigil_broken_count"] = broken_count
        # Invalidate prior audit/compile results so they get refreshed.
        sa["phase2_conformant"] = False
        cc = record.get("compile_check")
        if isinstance(cc, dict):
            cc["passed"] = False
            cc["stale_after_match_sigil"] = True

    return changed, tk_count, broken_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2 match-arm sigil autofix"
    )
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    scanned = 0
    changed = 0
    tk_changes = 0
    broken_changes = 0
    by_cat: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )

    for cat_dir in sorted(args.corpus_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        cat = cat_dir.name
        for p in cat_dir.iterdir():
            if p.suffix != ".json":
                continue
            try:
                with open(p) as f:
                    rec = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            scanned += 1
            by_cat[cat]["scanned"] += 1

            ch, tk_n, br_n = fix_record(rec)
            if ch:
                changed += 1
                tk_changes += tk_n
                broken_changes += br_n
                by_cat[cat]["changed"] += 1
                by_cat[cat]["tk"] += tk_n
                by_cat[cat]["broken"] += br_n
                if not args.dry_run:
                    try:
                        with open(p, "w") as f:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    except OSError:
                        pass

            if scanned % 25000 == 0:
                print(f"    scanned {scanned}  changed {changed}",
                      file=sys.stderr)

    print("=" * 70, file=sys.stderr)
    print("  Match-arm sigil autofix summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  Scanned:        {scanned}", file=sys.stderr)
    print(f"  Changed:        {changed}", file=sys.stderr)
    print(f"  tk arms fixed:  {tk_changes}", file=sys.stderr)
    print(f"  broken arms:    {broken_changes}", file=sys.stderr)
    print(f"\n  Top 20 categories by changes:", file=sys.stderr)
    ranked = sorted(by_cat.items(), key=lambda kv: -kv[1].get("changed", 0))
    print(
        f"    {'category':40s} {'scanned':>10s} {'changed':>10s} "
        f"{'tk':>8s} {'broken':>8s}",
        file=sys.stderr,
    )
    for cat, cnts in ranked[:20]:
        if not cnts.get("changed", 0):
            continue
        print(
            f"    {cat:40s} "
            f"{cnts.get('scanned', 0):10d} "
            f"{cnts.get('changed', 0):10d} "
            f"{cnts.get('tk', 0):8d} "
            f"{cnts.get('broken', 0):8d}",
            file=sys.stderr,
        )

    if args.dry_run:
        print("\n  [DRY RUN — no files modified]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
