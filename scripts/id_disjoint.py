#!/usr/bin/env python3
"""id_disjoint.py -- a disjointness check that can tell PASS from VACUOUS (131.22a).

The tokenizer holdout used to assert ``holdout_ids & benchmark_ids == {}``
where the holdout held corpus record ids (``A-MTH-0016v40``) and the benchmark
file held 974 bare integers and 164 ``HumanEval/N`` ids.  Those two sets cannot
intersect by construction, so the assertion passed every time and verified
nothing.  This module makes the difference visible.

Every id has a *shape*: each run of uppercase letters becomes ``A``, each run of
lowercase letters ``a``, each run of digits ``9``; every other character is
kept.  ``A-MTH-0016v40`` -> ``A-A-9a9``, ``HumanEval/7`` -> ``AaAa/9``,
``974`` -> ``9``.  The shape is a function of the string, so two ids with
different shapes can never be equal.  If no shape occurs on both sides, the
intersection is empty *by construction* and the check reports ``VACUOUS``: it
could not have failed, so it is not evidence of anything.

Statuses
  FAIL      the sets intersect (the overlap is listed)
  VACUOUS   an operand is empty, or the two sides share no id shape
  PASS      the sets are disjoint and at least one shape is shared -- the check
            could have failed and did not.  ``left_comparable`` /
            ``right_comparable`` say how many ids on each side were actually
            comparable; ids outside the shared shapes are counted, not hidden.

CLI (negative controls, ad-hoc audits)
  python3 scripts/id_disjoint.py LEFT_FILE RIGHT_FILE [--strict]
  Files hold one id per line (first whitespace/tab field; ``#`` comments).
  Exit 1 on FAIL, and on VACUOUS with --strict; 0 otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PASS, FAIL, VACUOUS = "PASS", "FAIL", "VACUOUS"

_RUNS = re.compile(r"[A-Z]+|[a-z]+|[0-9]+")


def id_shape(s: str) -> str:
    """Structural shape of an id: A = uppercase run, a = lowercase run, 9 = digit run."""
    def cls(m: re.Match[str]) -> str:
        c = m.group(0)[0]
        return "A" if c.isupper() else ("a" if c.islower() else "9")
    return _RUNS.sub(cls, s)


def check_disjoint(name: str, left: Iterable[str], right: Iterable[str],
                   max_examples: int = 5) -> dict[str, Any]:
    """Compare two id sets; return a JSON-serialisable result with a status."""
    lset, rset = set(left), set(right)
    # sorted input: most_common() breaks ties by insertion order, so this keeps manifests byte-stable
    lshapes = Counter(id_shape(x) for x in sorted(lset))
    rshapes = Counter(id_shape(x) for x in sorted(rset))
    shared = sorted(set(lshapes) & set(rshapes))
    overlap = sorted(lset & rset)
    res: dict[str, Any] = {
        "check": name,
        "left_n": len(lset),
        "right_n": len(rset),
        "left_shapes": dict(lshapes.most_common(8)),
        "right_shapes": dict(rshapes.most_common(8)),
        "shared_shapes": shared,
        "left_comparable": sum(lshapes[s] for s in shared),
        "right_comparable": sum(rshapes[s] for s in shared),
        "overlap_n": len(overlap),
        "overlap_examples": overlap[:max_examples],
    }
    if overlap:
        res["status"] = FAIL
        res["reason"] = f"{len(overlap)} id(s) on both sides"
    elif not lset or not rset:
        res["status"] = VACUOUS
        res["reason"] = "an operand is empty"
    elif not shared:
        res["status"] = VACUOUS
        res["reason"] = ("the two sides share no id shape, so they cannot intersect by construction "
                         f"(left {sorted(lshapes)[:4]} vs right {sorted(rshapes)[:4]})")
    else:
        res["status"] = PASS
        res["reason"] = (f"disjoint over {res['left_comparable']}/{len(lset)} left and "
                         f"{res['right_comparable']}/{len(rset)} right comparable ids")
    return res


def summary_line(res: dict[str, Any]) -> str:
    return f"{res['status']:<7} {res['check']}: {res['reason']}"


def read_ids(p: Path) -> set[str]:
    ids: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            ids.add(s.split("\t")[0].split()[0])
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("left", type=Path)
    ap.add_argument("right", type=Path)
    ap.add_argument("--name", default="left vs right")
    ap.add_argument("--strict", action="store_true", help="exit 1 on VACUOUS as well as FAIL")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    a = ap.parse_args(argv)
    res = check_disjoint(a.name, read_ids(a.left), read_ids(a.right))
    print(json.dumps(res, indent=1) if a.json else summary_line(res))
    if res["status"] == FAIL or (a.strict and res["status"] == VACUOUS):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
