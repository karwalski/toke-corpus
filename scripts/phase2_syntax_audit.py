#!/usr/bin/env python3
"""phase2_syntax_audit.py — Story 10.8.2.

Scan every corpus record for Phase 2 syntax violations and apply mechanical
autofixes where safe. Writes results in place to the `syntax_audit` field
and (if autofix succeeded) rewrites `tk_source`.

Phase 2 syntax rules (per toke-spec/spec/phase2-profile.md):

  uppercase_letter    — `[A-Z]` in identifiers. Autofix: lowercase all.
  underscore          — `_` in identifiers.   Autofix: strip underscores.
  double_equals       — `==` outside strings. Autofix: `=` (repeat to fixpoint).
  comma_separator     — `,` outside strings. Autofix: `;`.
  square_bracket      — `[` or `]` outside strings. Detected, NOT autofixed
                        (Phase 1 array syntax — semantically different).

Ignored (tkc accepts them, spec does not strictly require flagging here):

  - `.len()` vs `.len` — both accepted
  - `:void` vs `:$void` — both accepted; `void` is the spec keyword

Collision detection: after lowering identifiers, if two distinct original
identifiers in the same record collapse to the same lowered form, the record
is flagged with `collisions` and autofix is NOT applied.

BIFI/ERR-TRIPLE records: only `tk_source` (the FIXED side) is audited.
`references.broken_source` is left untouched — its intentional brokenness must
survive unmodified.

Usage:
    python scripts/phase2_syntax_audit.py [--corpus-dir PATH] [--dry-run]
                                          [--sample N] [--no-autofix]

Flags:
    --dry-run      : report without modifying any files
    --sample N     : only audit N random records per category
    --no-autofix   : detect violations but do not apply autofix
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
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"

# ---------------------------------------------------------------------------
# Phase 2 rule constants
# ---------------------------------------------------------------------------

# Phase 2 keywords and reserved — do not rename even if they matched.
PHASE2_KEYWORDS = frozenset({
    "m", "f", "i", "t",
    "let", "mut", "if", "el", "lp", "br", "as", "rt",
    "true", "false",
})

# Phase 2 primitive types — also do not rename.
PHASE2_PRIMITIVE_TYPES = frozenset({
    "bool", "void",
    "i8", "i16", "i32", "i64",
    "u8", "u16", "u32", "u64",
    "f32", "f64",
})

# Special identifiers reserved by spec §9.
PHASE2_SPECIAL = frozenset({"arena", "len", "get"})

DO_NOT_RENAME = PHASE2_KEYWORDS | PHASE2_PRIMITIVE_TYPES | PHASE2_SPECIAL

# Regex: identifier-like token candidate (pre-lowering).
# Starts with letter or underscore, continues with letters/digits/underscore.
RE_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

# Final Phase 2 identifier shape.
RE_PHASE2_IDENT = re.compile(r'^[a-z][a-z0-9]*$')


# ---------------------------------------------------------------------------
# String-aware segmentation
# ---------------------------------------------------------------------------

def segment_source(src: str) -> list[tuple[str, str]]:
    """Split source into [(kind, text)] where kind is 'code' or 'string'.
    'string' segments include their surrounding quotes.
    """
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
            # Consume the string literal
            start = i
            i += 1
            while i < n:
                if src[i] == '\\' and i + 1 < n:
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
# Detection
# ---------------------------------------------------------------------------

def detect_violations(src: str) -> list[dict]:
    """Scan a source string for Phase 2 violations, string-aware.
    Returns a list of violation dicts.
    Line/col are 1-indexed, computed on the *original* source.
    """
    violations: list[dict] = []

    # Build line/col lookup
    line_starts = [0]
    for idx, ch in enumerate(src):
        if ch == '\n':
            line_starts.append(idx + 1)

    def pos_to_linecol(offset: int) -> tuple[int, int]:
        # Binary search for the line containing offset.
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return (lo + 1, offset - line_starts[lo] + 1)

    segments = segment_source(src)
    offset = 0
    for kind, text in segments:
        if kind == "string":
            offset += len(text)
            continue

        # Identifiers with uppercase or underscore
        for m in RE_IDENT.finditer(text):
            name = m.group(0)
            if name in DO_NOT_RENAME:
                continue
            abs_pos = offset + m.start()
            line, col = pos_to_linecol(abs_pos)
            if any(c.isupper() for c in name):
                violations.append({
                    "rule": "uppercase_letter",
                    "name": name,
                    "line": line, "col": col,
                    "fixable": True,
                })
            if "_" in name:
                violations.append({
                    "rule": "underscore",
                    "name": name,
                    "line": line, "col": col,
                    "fixable": True,
                })

        # == (skip ::= etc.)
        for m in re.finditer(r'==', text):
            abs_pos = offset + m.start()
            line, col = pos_to_linecol(abs_pos)
            violations.append({
                "rule": "double_equals",
                "line": line, "col": col,
                "fixable": True,
            })

        # Comma (outside strings — guaranteed by segmentation)
        for m in re.finditer(r',', text):
            abs_pos = offset + m.start()
            line, col = pos_to_linecol(abs_pos)
            violations.append({
                "rule": "comma_separator",
                "line": line, "col": col,
                "fixable": True,
            })

        # Square brackets (Phase 1 array syntax — not fixable mechanically)
        for m in re.finditer(r'[\[\]]', text):
            abs_pos = offset + m.start()
            line, col = pos_to_linecol(abs_pos)
            violations.append({
                "rule": "square_bracket",
                "char": text[m.start()],
                "line": line, "col": col,
                "fixable": False,
            })

        offset += len(text)

    return violations


# ---------------------------------------------------------------------------
# Autofix
# ---------------------------------------------------------------------------

def lower_identifier(name: str) -> str:
    """Lower an identifier to Phase 2 form: lowercase + strip underscores."""
    return name.lower().replace("_", "")


def autofix_source(src: str) -> tuple[str, list[str], bool]:
    """Apply autofix rules to `src`. Returns (fixed_src, collisions, fixable).

    fixed_src   — the rewritten source (or the original if not fixable).
    collisions  — list of identifier collision strings ("orig1,orig2 → lowered").
    fixable     — True if the entire record is fully autofixable:
                  * no identifier collisions
                  * no unfixable violations (square brackets) remain
    """
    segments = segment_source(src)

    # First pass: build identifier mapping and detect collisions globally.
    # We scan code segments to find all identifier candidates, compute their
    # lowered form, and ensure no two distinct originals collapse to the
    # same lowered value.
    ident_map: dict[str, str] = {}
    reverse: dict[str, set[str]] = collections.defaultdict(set)
    for kind, text in segments:
        if kind != "code":
            continue
        for m in RE_IDENT.finditer(text):
            name = m.group(0)
            if name in DO_NOT_RENAME:
                continue
            if not (any(c.isupper() for c in name) or "_" in name):
                continue
            lowered = lower_identifier(name)
            # Lowered must be a valid Phase 2 identifier shape, otherwise
            # we can't fix it (e.g., starts with digit after stripping).
            if not RE_PHASE2_IDENT.match(lowered):
                # Record collision-like unfixable — store original as its
                # own mapping so later collision detection marks it.
                ident_map[name] = name
                reverse[name].add(name)
                continue
            ident_map[name] = lowered
            reverse[lowered].add(name)

    collisions: list[str] = []
    # A collision exists if:
    #  (a) multiple distinct originals map to the same lowered form, OR
    #  (b) a lowered form collides with an unrelated EXISTING identifier
    #      (one that is already valid Phase 2 and didn't need renaming).
    existing_lowered: set[str] = set()
    for kind, text in segments:
        if kind != "code":
            continue
        for m in RE_IDENT.finditer(text):
            name = m.group(0)
            if name in DO_NOT_RENAME:
                continue
            if RE_PHASE2_IDENT.match(name):
                existing_lowered.add(name)

    for lowered, origs in reverse.items():
        if len(origs) > 1:
            collisions.append(f"{sorted(origs)} -> {lowered}")
        elif lowered in existing_lowered and lowered not in origs:
            collisions.append(f"{list(origs)[0]} -> {lowered} (collides with existing identifier)")

    # Check for unfixable square brackets
    has_square = False
    for kind, text in segments:
        if kind == "code" and ("[" in text or "]" in text):
            has_square = True
            break

    fixable = not collisions and not has_square

    if not fixable:
        return (src, collisions, False)

    # Second pass: actually rewrite code segments.
    new_parts: list[str] = []
    for kind, text in segments:
        if kind == "string":
            new_parts.append(text)
            continue
        # Identifier rewrite — longest-match left-to-right.
        def _sub(m: re.Match) -> str:
            n = m.group(0)
            return ident_map.get(n, n)
        rewritten = RE_IDENT.sub(_sub, text)
        # == → = (repeat to fixpoint for sequences like ====)
        while "==" in rewritten:
            rewritten = rewritten.replace("==", "=")
        # , → ;
        rewritten = rewritten.replace(",", ";")
        new_parts.append(rewritten)

    return ("".join(new_parts), [], True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def iter_records(corpus_dir: Path, sample_per_cat: int | None = None
                 ) -> Iterable[tuple[Path, dict, str]]:
    for cat_dir in sorted(corpus_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        files = [p for p in cat_dir.iterdir() if p.suffix == ".json"]
        if sample_per_cat is not None and len(files) > sample_per_cat:
            files = random.sample(files, sample_per_cat)
        for path in files:
            try:
                with open(path) as f:
                    record = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            yield (path, record, cat_dir.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 syntax audit + autofix")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report findings without rewriting any files")
    parser.add_argument("--sample", type=int, default=None,
                        help="Only audit N random records per category")
    parser.add_argument("--no-autofix", action="store_true",
                        help="Detect violations but do not apply autofix")
    args = parser.parse_args()

    random.seed(42)
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    scanned = 0
    had_violations = 0
    fully_fixed = 0
    fixable_but_skipped = 0
    unfixable = 0
    rule_counter: collections.Counter = collections.Counter()
    cat_stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    for path, record, cat in iter_records(args.corpus_dir, args.sample):
        src = record.get("tk_source", "")
        if not isinstance(src, str) or not src:
            continue
        scanned += 1
        cat_stats[cat]["scanned"] += 1

        violations = detect_violations(src)
        for v in violations:
            rule_counter[v["rule"]] += 1
            cat_stats[cat][v["rule"]] += 1

        if not violations:
            audit = {
                "violations": [],
                "autofix_applied": False,
                "phase2_conformant": True,
                "collisions": [],
                "checked_at": now_iso,
            }
            record["syntax_audit"] = audit
            if not args.dry_run:
                with open(path, "w") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            continue

        had_violations += 1
        cat_stats[cat]["had_violations"] += 1

        fixed_src, collisions, fixable = autofix_source(src)

        if args.no_autofix or not fixable:
            audit = {
                "violations": violations,
                "autofix_applied": False,
                "phase2_conformant": False,
                "collisions": collisions,
                "checked_at": now_iso,
            }
            if not fixable:
                unfixable += 1
                cat_stats[cat]["unfixable"] += 1
            else:
                fixable_but_skipped += 1
            record["syntax_audit"] = audit
            if not args.dry_run:
                with open(path, "w") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            continue

        # Apply autofix
        audit = {
            "violations": violations,
            "autofix_applied": True,
            "original_source": src,
            "phase2_conformant": True,
            "collisions": [],
            "checked_at": now_iso,
        }
        record["tk_source"] = fixed_src
        record["syntax_audit"] = audit
        fully_fixed += 1
        cat_stats[cat]["fully_fixed"] += 1

        if not args.dry_run:
            with open(path, "w") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Summary
    print("=" * 70, file=sys.stderr)
    print("  Phase 2 syntax audit summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  Scanned records:      {scanned}", file=sys.stderr)
    print(f"  With violations:      {had_violations}", file=sys.stderr)
    print(f"  Fully autofixed:      {fully_fixed}", file=sys.stderr)
    print(f"  Unfixable:            {unfixable}", file=sys.stderr)
    print(f"  Fixable but skipped:  {fixable_but_skipped}", file=sys.stderr)

    print(f"\n  Total violations by rule:", file=sys.stderr)
    for rule, cnt in rule_counter.most_common():
        print(f"    {rule:25s} {cnt:10d}", file=sys.stderr)

    print(f"\n  Per category (top 20 by violations):", file=sys.stderr)
    ranked = sorted(cat_stats.items(), key=lambda kv: -kv[1].get("had_violations", 0))
    print(f"    {'category':35s} {'scanned':>8s} {'w/viol':>8s} {'fixed':>8s} {'unfix':>8s}", file=sys.stderr)
    for cat, cnts in ranked[:20]:
        print(
            f"    {cat:35s} "
            f"{cnts.get('scanned', 0):8d} "
            f"{cnts.get('had_violations', 0):8d} "
            f"{cnts.get('fully_fixed', 0):8d} "
            f"{cnts.get('unfixable', 0):8d}",
            file=sys.stderr,
        )

    if args.dry_run:
        print("\n  [DRY RUN — no files modified]", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
