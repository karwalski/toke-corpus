#!/usr/bin/env python3
"""Story 131.105 — route the three 131.14-banked records whose single-use-let
inline changed their empty-input failure (RT003 -> RT002) to the 131.15 AGENT
wave, with the instruction to restore the bound `let`.

Z86 (131.59) measured: the 131.14 AUTO wave inlined `let n=<x>.len;` into
`<x.get(n-1)`. The `let` bound an i64; inlined, `<x>.len-1` is u64 arithmetic,
so on an empty array the record now fails `RT002: integer overflow in
subtraction` where the original failed `RT003: index -1 out of bounds`. No
test or generated input is empty, so diff_check rated all three identical.

Convention (why AGENT and not an in-place restore). 131.14 archived each
original on first touch to `audit/replaced/131/<task_id>.tk`; nothing in the
Epic 131 tooling restores an archive into a record — every change to a banked
record goes through a wave's bank path (archive -> record + rewrite131 block ->
ledger -> MANIFEST stamp), and this batch banks nothing. So the records are
routed to the AGENT wave via a route_patch row whose reason names the archived
original and the line to restore; the bank's differential (identity mode:
the originals pass their tests) then holds every other line.

Known obstacle: the faithful restore is +8 `--min` bytes on each record
(A-ARR-0017v41 77->85, D-CFG-0018v98 272->280, D-FIO-0002v99 237->245), and
the 131.15 size gates (`--min` and proxy tokens <= before) reject any growth.
Story 131.108 owns that; until it lands these three will be rejected.

Usage (toke-corpus root, after check_routing_131_106.py route):
  python3 regen/route_131_105.py && python3 regen/route_merge.py
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import route_merge
import route_patch

STORY = "131.105"
RULE = "inline-changed-empty-failure"
TS = 1794182400                                   # 2026-11-09T00:00:00Z (reproducible patch)
# task_id -> (binding restored, receiver)
RECORDS = {"A-ARR-0017v41": ("let n=arr.len;", "arr"),
           "D-CFG-0018v98": ("let n=parts.len;", "parts"),
           "D-FIO-0002v99": ("let n=parts.len;", "parts")}


def reason(tid, let, recv):
    return (f"the 131.14 auto wave inlined `{let}` into `<{recv}.get({recv}.len-1)`. The `let` bound an "
            f"i64, so on an EMPTY {recv} the original failed `RT003: index -1 out of bounds`; the inlined "
            "u64 subtraction now fails `RT002: integer overflow in subtraction` instead. Restore the "
            f"binding exactly as the archived original has it (audit/replaced/131/{tid}.tk): `{let}` then "
            f"`<{recv}.get(n-1)`; every other line stays as it is now (single-use-let does not apply here "
            "— the binding carries the u64->i64 coercion that 131.59 now withholds from the inline)")


def route_rows(cur, ts=TS):
    out = []
    for tid, (let, recv) in sorted(RECORDS.items()):
        base = cur.get(tid)
        if base is None:
            raise SystemExit(f"route_131_105: {tid} has no merged routing row")
        out.append(route_patch.merge_row(base, tid, STORY, RULE, "AGENT", reason(tid, let, recv), ts=ts,
                                         keep_buckets=("REGEN",)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=route_merge.CORPUS)
    a = ap.parse_args(argv)
    i = [s for s, _ in route_merge.PATCHES].index(STORY)
    _, cur, _, _ = route_merge.build(a.corpus, patches=route_merge.PATCHES[:i])
    rows = route_rows(cur)
    path = route_patch.write_patch(a.corpus, STORY, rows)
    print(json.dumps({"patch": path, "rows": {r["task_id"]: [r["would_bucket"], r["bucket"]] for r in rows}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
