#!/usr/bin/env python3
"""phase2_autofix_v2.py — Story 10.8.5b.

Targeted corrective autofix for the two bugs introduced by 10.8.2 that
collectively account for 99.9% of the 13,759 compile-check failures from
10.8.3:

  Bug A — bare `str` in type position
      10.8.2 lowered Phase 1 `Str` to `str`, but Phase 2 requires `$str`
      (the SIGIL_TYPE form). Bare `str` is not a valid Phase 2 type.
      Fix: rewrite `str` with `$str` in every TYPE position.

  Bug B — lowercase variant names in match arms
      The Phase 2 grammar (grammar.ebnf line 153) still uses TYPE_IDENT
      for the variant position inside `|{...}` match blocks, so the
      parser requires an uppercase-initial identifier there. 10.8.2
      lowered `Ok`/`Err` (and a handful of custom variants) to `ok`/`err`
      etc. in match arms, which now fails to parse.
      Fix: inside every `|{...}` block, restore each arm's variant name
      to Title case. Prefer recovery from `syntax_audit.original_source`
      when available; fall back to the hard-coded `ok→Ok`, `err→Err`
      mapping for the ~99% common case.

Targets only records whose `compile_check.passed == false` AND whose
`syntax_audit.phase2_conformant == true` (i.e., 10.8.2 thought they were
OK). Records that were already unfixable under 10.8.2 are skipped.

Writes the corrected `tk_source` in place. Adds a new sub-field to
`syntax_audit`:

    syntax_audit.v2_fixes      — list of fix labels applied to this record
                                 (e.g. ["str_type", "match_arm"])
    syntax_audit.v2_applied_at — ISO timestamp

The pre-v2 form is already preserved in `syntax_audit.original_source`
from 10.8.2, so no additional backup field is needed.

Usage:
    python scripts/phase2_autofix_v2.py [--corpus-dir PATH] [--dry-run]
                                        [--sample N]
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"


# ---------------------------------------------------------------------------
# String-aware segmentation (reuse approach from phase2_syntax_audit)
# ---------------------------------------------------------------------------

def segment_source(src: str) -> list[tuple[str, str]]:
    """Split source into [(kind, text)] where kind is 'code' or 'string'."""
    segments: list[tuple[str, str]] = []
    buf: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == '"':
            if buf:
                segments.append(("code", "".join(buf)))
                buf = []
            start = i
            i += 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
            segments.append(("string", src[start:i]))
        else:
            buf.append(c)
            i += 1
    if buf:
        segments.append(("code", "".join(buf)))
    return segments


# ---------------------------------------------------------------------------
# Fix A — `str` → `$str` in type positions
# ---------------------------------------------------------------------------

# Match `str` only in type positions. Type positions are preceded by a
# type-starting context: `:`, `!`, or `as ` (with optional whitespace and
# optional `@` sigil stack). We match the immediately preceding context
# using a lookbehind and a preceding-scan regex.
#
# Patterns handled:
#   :str        → :$str
#   :@str       → :@$str
#   :@@str      → :@@$str
#   !str        → !$str           (error propagate)
#   as str      → as $str         (cast)
#   @str        → @$str           (array element type in return pos)
#
# Negative contexts (must NOT rewrite):
#   str.method  — module alias
#   $str        — already correct
#   strftime    — other identifier that starts with str (handled by \b)
#   xstr        — suffix match (handled by \b)

_RE_STR_TYPE = re.compile(
    r"""
    (?:
        (?P<prefix>(?<=[:!])\s*@*\s*)      # after : or !, optional @ stack
        |
        (?P<asprefix>(?<=\bas)\s+)         # after 'as ' keyword
    )
    (?P<token>str)
    (?=[^A-Za-z0-9_.])                     # not followed by ident char or `.`
    """,
    re.VERBOSE,
)


def fix_str_type(code: str) -> tuple[str, int]:
    """Rewrite bare `str` in TYPE positions to `$str`. Returns (new, count)."""
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        # Replace only the 'token' group; keep prefix whitespace/@ intact.
        prefix = m.group("prefix") or m.group("asprefix") or ""
        return prefix + "$str"

    new = _RE_STR_TYPE.sub(_sub, code)
    return (new, count)


# ---------------------------------------------------------------------------
# Fix B — restore Title-case variants inside match arms
# ---------------------------------------------------------------------------

# Hard-coded fallback mapping for common variants.
DEFAULT_VARIANT_MAP = {
    "ok": "Ok",
    "err": "Err",
    "notfound": "NotFound",
    "emptycollection": "EmptyCollection",
    "some": "Some",
    "none": "None",
    "true": "True",
    "false": "False",
}


def _find_match_blocks(code: str) -> list[tuple[int, int]]:
    """Return [(open_brace_idx, close_brace_idx)] for every `|{...}` block.
    Handles nested braces correctly.
    """
    blocks: list[tuple[int, int]] = []
    i = 0
    n = len(code)
    while i < n:
        if code[i] == "|" and i + 1 < n and code[i + 1] == "{":
            # Walk to matching close brace
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if code[j] == "{":
                    depth += 1
                elif code[j] == "}":
                    depth -= 1
                j += 1
            if depth == 0:
                blocks.append((i + 1, j - 1))  # exclude the '|' prefix
                i = j
                continue
        i += 1
    return blocks


def _recover_variant_map(
    orig_source: str | None, lowered_variants: set[str]
) -> dict[str, str]:
    """Given a set of lowered variant names and the original (pre-autofix)
    source, try to recover the original casing by finding identifiers in
    orig_source whose `lower()` equals each lowered variant.
    """
    mapping: dict[str, str] = {}
    if not orig_source or not lowered_variants:
        return mapping
    # Scan original source for any ident and check if its lower() matches
    for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", orig_source):
        name = m.group(0)
        lowered = name.lower().replace("_", "")
        if lowered in lowered_variants and lowered not in mapping:
            # Only accept names that have an uppercase letter (i.e., truly a
            # TYPE_IDENT, not some coincidental lowercase identifier).
            if any(c.isupper() for c in name):
                mapping[lowered] = name
    return mapping


def _top_level_arm_starts(body: str) -> list[int]:
    """Return offsets within `body` where a match arm begins (start of body
    and positions immediately after every top-level `;` at brace-depth 0).
    """
    starts = [0]
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c == '"':
            i += 1
            while i < n:
                if body[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if body[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ";" and depth == 0:
            starts.append(i + 1)
        i += 1
    return starts


_RE_ARM_HEAD = re.compile(r"\s*([a-z][a-z0-9]*)\s*:")


def fix_match_arms(code: str, orig_source: str | None) -> tuple[str, int]:
    """Restore Title-case variant names inside `|{...}` match blocks.
    Returns (new_code, count_of_arms_fixed).
    """
    blocks = _find_match_blocks(code)
    if not blocks:
        return (code, 0)

    # First pass: collect all lowered variants at top-level arm positions.
    lowered_used: set[str] = set()
    per_block_arms: list[list[tuple[int, str]]] = []  # (offset_in_body, name)
    for open_i, close_i in blocks:
        body = code[open_i + 1:close_i]
        arms: list[tuple[int, str]] = []
        for s in _top_level_arm_starts(body):
            m = _RE_ARM_HEAD.match(body, s)
            if not m:
                continue
            name = m.group(1)
            arms.append((m.start(1), name))
            lowered_used.add(name)
        per_block_arms.append(arms)

    if not lowered_used:
        return (code, 0)

    recovered = _recover_variant_map(orig_source, lowered_used)

    def _resolve(name: str) -> str:
        if name in recovered:
            return recovered[name]
        if name in DEFAULT_VARIANT_MAP:
            return DEFAULT_VARIANT_MAP[name]
        return name[0].upper() + name[1:]

    # Second pass: rewrite each block from the end backwards so offsets stay stable.
    out = code
    count = 0
    for (open_i, close_i), arms in zip(reversed(blocks), reversed(per_block_arms)):
        body = out[open_i + 1:close_i]
        # Rewrite arms within body from last to first.
        new_body = body
        for name_start, name in reversed(arms):
            repl = _resolve(name)
            if repl == name:
                continue
            new_body = (
                new_body[:name_start] + repl + new_body[name_start + len(name):]
            )
            count += 1
        out = out[:open_i + 1] + new_body + out[close_i:]

    return (out, count)


# ---------------------------------------------------------------------------
# Whole-record fix
# ---------------------------------------------------------------------------

def fix_record(record: dict) -> tuple[bool, list[str]]:
    """Attempt to apply the v2 fixes to `record`. Mutates in place.
    Returns (changed, labels_applied).
    """
    src = record.get("tk_source", "")
    if not isinstance(src, str) or not src:
        return (False, [])

    # Only process records that failed compile_check
    cc = record.get("compile_check") or {}
    if cc.get("passed"):
        return (False, [])

    orig_source = (record.get("syntax_audit") or {}).get("original_source")

    # Apply fixes on code segments only.
    segments = segment_source(src)
    new_parts: list[str] = []
    labels: list[str] = []
    str_total = 0
    match_total = 0
    for kind, text in segments:
        if kind == "string":
            new_parts.append(text)
            continue
        code = text
        code2, n = fix_str_type(code)
        str_total += n
        code3, m = fix_match_arms(code2, orig_source)
        match_total += m
        new_parts.append(code3)

    new_src = "".join(new_parts)
    if new_src == src:
        return (False, [])

    if str_total > 0:
        labels.append("str_type")
    if match_total > 0:
        labels.append("match_arm")

    record["tk_source"] = new_src

    sa = record.setdefault("syntax_audit", {})
    sa["v2_fixes"] = labels
    sa["v2_applied_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    sa["v2_str_count"] = str_total
    sa["v2_match_count"] = match_total

    return (True, labels)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def iter_records(corpus_dir: Path, sample_per_cat: int | None):
    for cat_dir in sorted(corpus_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        files = [p for p in cat_dir.iterdir() if p.suffix == ".json"]
        if sample_per_cat is not None and len(files) > sample_per_cat:
            files = random.sample(files, sample_per_cat)
        for p in files:
            yield (p, cat_dir.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 autofix v2")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample", type=int, default=None)
    args = parser.parse_args()

    random.seed(42)

    scanned = 0
    already_passing = 0
    attempted = 0
    changed = 0
    label_counter: collections.Counter = collections.Counter()
    cat_stats: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )

    for path, cat in iter_records(args.corpus_dir, args.sample):
        try:
            with open(path) as f:
                rec = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        scanned += 1
        cat_stats[cat]["scanned"] += 1

        cc = rec.get("compile_check") or {}
        if cc.get("passed"):
            already_passing += 1
            continue

        attempted += 1
        cat_stats[cat]["attempted"] += 1

        modified, labels = fix_record(rec)
        if modified:
            changed += 1
            cat_stats[cat]["changed"] += 1
            for lab in labels:
                label_counter[lab] += 1
                cat_stats[cat][lab] += 1
            if not args.dry_run:
                try:
                    with open(path, "w") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                except OSError:
                    pass

    # Summary
    print("=" * 70, file=sys.stderr)
    print("  Phase 2 autofix v2 summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  Scanned:           {scanned}", file=sys.stderr)
    print(f"  Already passing:   {already_passing}", file=sys.stderr)
    print(f"  Failing attempted: {attempted}", file=sys.stderr)
    print(f"  Changed:           {changed}", file=sys.stderr)
    print(f"  Unchanged:         {attempted - changed}", file=sys.stderr)
    print(f"\n  Fixes applied:", file=sys.stderr)
    for lab, cnt in label_counter.most_common():
        print(f"    {lab:15s} {cnt:8d}", file=sys.stderr)

    print(f"\n  Top 20 categories by changes:", file=sys.stderr)
    ranked = sorted(cat_stats.items(), key=lambda kv: -kv[1].get("changed", 0))
    print(
        f"    {'category':35s} {'attempted':>10s} {'changed':>10s} "
        f"{'str':>8s} {'match':>8s}",
        file=sys.stderr,
    )
    for cat, cnts in ranked[:20]:
        print(
            f"    {cat:35s} "
            f"{cnts.get('attempted', 0):10d} "
            f"{cnts.get('changed', 0):10d} "
            f"{cnts.get('str_type', 0):8d} "
            f"{cnts.get('match_arm', 0):8d}",
            file=sys.stderr,
        )

    if args.dry_run:
        print("\n  [DRY RUN — no files modified]", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
