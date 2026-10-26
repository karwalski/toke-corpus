#!/usr/bin/env python3
"""131.87: re-judge banked records with the stricter Phase A differential test.

A measurement, not a bank operation: corpus records are only read. For every
record that carries Phase A provenance (a non-empty
``differential.languages_agreed`` and all three ``references.*_source``
fields) it re-runs ``validate.diff_test.DifferentialTester`` -- reference
majority agreement, then build and run the toke program -- and reports how
many the stricter test would reject, and why.

Records do not store the test inputs they were generated with (only the
majority output), so each record is re-run on one empty-stdin input, which is
what the reference harnesses print from.

Modes:
  (default)     full stricter Phase A (references, then toke)
  --refs-only   only step (a): reference majority agreement; toke not built
  --toke-shape  no execution at all: count records whose toke source defines
                no ``main`` (they cannot be run, so step (b) rejects them)

Outputs: one JSONL row per checked record (--out) and a JSON summary on
stdout (and --summary when given).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from validate.compiler import CompilerValidator  # noqa: E402
from validate.diff_test import (  # noqa: E402
    DifferentialTester,
    canonical_output,
    reference_majority,
)

REF_KEYS = ("python_source", "c_source", "java_source")
OUTPUT_KEEP = 400          # chars of each reference output kept in a refs-only row
MAIN_RE = re.compile(r"(?:^|[;\s{}])[fF]=main\s*\(")


def is_phase_a(rec: dict) -> bool:
    """True when the record was accepted by the Phase A differential test."""
    agreed = (rec.get("differential") or {}).get("languages_agreed") or []
    refs = rec.get("references") or {}
    return bool(agreed) and all(refs.get(k) for k in REF_KEYS)


def has_main(src: str) -> bool:
    return bool(MAIN_RE.search(src or ""))


def iter_records(corpus: Path) -> Iterator[tuple[str, dict]]:
    for dp, _dn, fn in os.walk(corpus):
        for f in sorted(fn):
            if not f.endswith(".json"):
                continue
            path = os.path.join(dp, f)
            try:
                with open(path, encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict):
                yield os.path.relpath(path, corpus), rec


def min_refs_for(rec: dict) -> int:
    """main.py's threshold: A-ERR relaxes to one reference."""
    cat = rec.get("category") or (rec.get("task_id") or "")[:5]
    return 1 if str(cat).startswith("A-ERR") else 2


def strict_disagrees(outs: dict[str, str], min_refs: int) -> bool:
    """Would byte-exact comparison (no canonical form) find no majority?"""
    produced = [o for o in outs.values() if o]
    if len(produced) < min_refs:
        return False
    counts = Counter(produced).most_common()
    return counts[0][1] < min_refs or (len(counts) > 1 and counts[1][1] == counts[0][1])


async def check_one(tester: DifferentialTester, rec: dict, refs_only: bool) -> dict:
    refs = rec["references"]
    m = min_refs_for(rec)
    row: dict = {"id": rec.get("id"), "task_id": rec.get("task_id"),
                 "category": rec.get("category"), "has_main": has_main(rec.get("tk_source", ""))}
    if refs_only:
        c = tester.compiler
        rs = await asyncio.gather(c.run_python(refs["python_source"]),
                                  c.run_c(refs["c_source"]), c.run_java(refs["java_source"]))
        outs = {lang: (r.stdout.strip() if r.success else "")
                for lang, r in zip(("python", "c", "java"), rs)}
        maj, agreed, reason = reference_majority(outs, m)
        stored = (rec.get("differential") or {}).get("majority_output", "").strip()
        row.update(passed=reason is None, reason=reason, languages_agreed=agreed,
                   strict_disagrees=strict_disagrees(outs, m),
                   matches_stored_majority=(canonical_output(maj) == canonical_output(stored))
                   if maj is not None else None,
                   outputs={k: v[:OUTPUT_KEEP] for k, v in outs.items()})
        return row
    r = await tester.test(rec.get("tk_source", ""), refs["python_source"], refs["c_source"],
                          refs["java_source"], [], min_refs=m)
    row.update(passed=r.passed, reason=r.discarded_reason, languages_agreed=r.languages_agreed)
    return row


async def run(rows: list[dict], tester: DifferentialTester, refs_only: bool,
              workers: int, out) -> list[dict]:
    sem = asyncio.Semaphore(workers)
    results: list[dict] = []

    async def one(rec: dict) -> None:
        async with sem:
            res = await check_one(tester, rec, refs_only)
        results.append(res)
        if out is not None:
            out.write(json.dumps(res) + "\n")
            out.flush()

    await asyncio.gather(*(one(r) for r in rows))
    return results


def summarise(results: list[dict], population: int, mode: str) -> dict:
    n = len(results)
    rejected = [r for r in results if not r["passed"]]
    by_cat: Counter = Counter(r["category"] for r in rejected)
    return {"story": "131.87", "mode": mode, "phase_a_population": population,
            "checked": n, "rejected": len(rejected), "accepted": n - len(rejected),
            "reject_rate": round(len(rejected) / n, 4) if n else None,
            "reasons": dict(Counter(r["reason"] for r in rejected)),
            "rejected_by_category": dict(sorted(by_cat.items()))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--corpus", type=Path, default=ROOT / "corpus" / "phase2_deduplicated")
    ap.add_argument("--out", type=Path, default=None, help="per-record JSONL")
    ap.add_argument("--summary", type=Path, default=None, help="also write the summary here")
    ap.add_argument("--tkc", default=os.environ.get("TKC", "tkc"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=20, help="per subprocess, seconds")
    ap.add_argument("--sample", type=int, default=None,
                    help="check a seeded random sample of N Phase A records")
    ap.add_argument("--seed", type=int, default=13187)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--refs-only", action="store_true")
    mode.add_argument("--toke-shape", action="store_true")
    a = ap.parse_args(argv)

    pop = [rec for _p, rec in iter_records(a.corpus) if is_phase_a(rec)]
    if a.toke_shape:
        no_main = [r for r in pop if not has_main(r.get("tk_source", ""))]
        s = {"story": "131.87", "mode": "toke-shape", "phase_a_population": len(pop),
             "no_main": len(no_main),
             "no_main_by_category": dict(sorted(Counter(r.get("category") for r in no_main).items()))}
    else:
        rows = pop
        if a.sample is not None and a.sample < len(pop):
            rows = random.Random(a.seed).sample(pop, a.sample)
        tester = DifferentialTester(CompilerValidator(tkc_path=a.tkc, timeout_s=a.timeout))
        out = open(a.out, "w", encoding="utf-8") if a.out else None
        try:
            res = asyncio.run(run(rows, tester, a.refs_only, a.workers, out))
        finally:
            if out is not None:
                out.close()
        s = summarise(res, len(pop), "refs-only" if a.refs_only else "full")
        if a.refs_only:
            s["strict_compare_disagree"] = sum(1 for r in res if r.get("strict_disagrees"))
        if a.sample is not None:
            s["sample"] = {"n": len(rows), "seed": a.seed}
    text = json.dumps(s, indent=1)
    print(text)
    if a.summary:
        a.summary.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
