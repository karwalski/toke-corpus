#!/usr/bin/env python3
"""restamp_tokenizer_disjointness.py -- re-stamp a frozen tokenizer manifest's
holdout disjointness claims with PASS / FAIL / VACUOUS (131.92 slice c).

The pre131 manifest (data/tokenizer_manifest_pre131.json, 131.22) was written
before 131.22a, so it records `disjoint_benchmark: true`. That claim compared
corpus record ids against data/holdout_task_ids.txt, which holds bare integers
and HumanEval/N: the two sets cannot intersect, so the check could not have
failed (owner decision 4.12: re-stamp it so no committed file claims a
disjointness nobody checked).

This re-runs the same four checks curate_tokenizer_set.py runs after the carve
(holdout_disjointness, scripts/id_disjoint.py), from the manifest's own record
list and the id files it names, and replaces the three booleans with the
statuses plus the full per-check results. Nothing else in the manifest moves:
the training/holdout text, the carve and every SHA are unchanged. A
`disjointness_restamp` block records the story, the prior values and the
sha256 of each id file used.

The curated report (docs/tokenizer_data_report_<label>.md) carries the same
claim in its "asserted:" line; --report rewrites that line to the statuses.

Usage (toke-corpus root):
  python3 scripts/restamp_tokenizer_disjointness.py data/tokenizer_manifest_pre131.json \\
      --report docs/tokenizer_data_report_pre131.md [--check]

--check exits 1 when the files on disk differ from the re-stamp (nothing is written).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from curate_tokenizer_set import holdout_disjointness, read_id_file, worst_status
from id_disjoint import summary_line

STORY = "131.92"
DATE = "2026-11-08"
ASSERTED_PREFIX = "- asserted: disjoint from benchmark ids"


def _sha256(p: Path | None) -> str | None:
    if p is None or not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def restamp(man: dict, bench_p: Path | None, base_p: Path | None) -> dict:
    """The manifest with its disjointness fields recomputed from its records."""
    recs = [r for r in man["records"] if r.get("source") != "stdlib"]   # the stdlib doc is added after the carve
    hold_ids = {r["task_id"] for r in recs if r["split"] == "holdout"}
    hold_bases = {r["base"] for r in recs if r["split"] == "holdout"}
    train_bases = {r["base"] for r in recs if r["split"] == "train"}
    bench, baseline = read_id_file(bench_p), read_id_file(base_p)
    h = man["holdout"]
    if len(bench) != h.get("benchmark_ids_n") or len(baseline) != h.get("baseline_ids_n"):
        raise SystemExit(f"id files changed since the run: benchmark {len(bench)} vs {h.get('benchmark_ids_n')}, "
                         f"baseline {len(baseline)} vs {h.get('baseline_ids_n')}")
    if len(hold_bases) != h.get("families_holdout"):
        raise SystemExit(f"holdout families {len(hold_bases)} != manifest {h.get('families_holdout')}")
    checks = holdout_disjointness(hold_ids, hold_bases, train_bases, bench, baseline)
    by = {r["check"]: r for r in checks}
    keys = ("disjoint_benchmark", "disjoint_baseline", "family_disjoint_train")
    prior = (man.get("disjointness_restamp") or {}).get("prior") or {k: h.get(k) for k in keys}
    out = dict(man)
    hold = dict(h)
    hold["disjoint_benchmark"] = worst_status(by["holdout_records_vs_benchmark"], by["holdout_families_vs_benchmark"])
    hold["disjoint_baseline"] = by["holdout_records_vs_baseline"]["status"]
    hold["family_disjoint_train"] = by["holdout_families_vs_train_families"]["status"]
    hold["disjointness_checks"] = checks
    out["holdout"] = hold
    rebuilt = {}
    for k, v in out.items():                         # keep key order; the restamp block goes after `holdout`
        if k == "disjointness_restamp":
            continue
        rebuilt[k] = v
        if k == "holdout":
            rebuilt["disjointness_restamp"] = {
                "story": STORY, "date": DATE,
                "why": ("written before 131.22a: the booleans below were asserted, not checked. The benchmark "
                        "ids (bare integers, HumanEval/N) share no id shape with corpus ids, so that check is "
                        "VACUOUS -- it could not have failed. Owner decision 4.12 (re-stamp the pre131 "
                        "manifest). Re-stamped by scripts/restamp_tokenizer_disjointness.py from this "
                        "manifest's records; nothing else changed. Real benchmark disjointness waits on "
                        "BENCH-128's id list (128.1a) and the v04 --strict-disjoint run (131.22)."),
                "prior": prior,
                "benchmark_ids_sha256": _sha256(bench_p),
                "baseline_ids_sha256": _sha256(base_p),
                "checks": [summary_line(r) for r in checks],
            }
    return rebuilt


def report_line(hold: dict) -> str:
    return (f"{ASSERTED_PREFIX} = {hold['disjoint_benchmark']}, disjoint from baseline ids = "
            f"{hold['disjoint_baseline']}, no family split across train/holdout = {hold['family_disjoint_train']} "
            f"(re-stamped {DATE}, {STORY}: VACUOUS = the operands cannot intersect, so the check proves nothing; "
            "the original run asserted True for all three)")


def restamp_report(text: str, hold: dict) -> str:
    lines = text.split("\n")
    hits = [i for i, x in enumerate(lines) if x.startswith(ASSERTED_PREFIX)]
    if len(hits) != 1:
        raise SystemExit(f"report: expected one '{ASSERTED_PREFIX}' line, found {len(hits)}")
    lines[hits[0]] = report_line(hold)
    return "\n".join(lines)


def dump(man: dict) -> str:
    return json.dumps(man, indent=1, ensure_ascii=False) + "\n"     # curate_tokenizer_set's format


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--benchmark-ids", type=Path, default=None, help="default: the file the manifest names")
    ap.add_argument("--baseline-ids", type=Path, default=None, help="default: the file the manifest names")
    ap.add_argument("--check", action="store_true", help="exit 1 if the files differ from the re-stamp")
    a = ap.parse_args(argv)
    man = json.loads(a.manifest.read_text(encoding="utf-8"))
    h = man["holdout"]
    bench_p = a.benchmark_ids or (Path(h["benchmark_ids_file"]) if h.get("benchmark_ids_file") else None)
    base_p = a.baseline_ids or (Path(h["baseline_ids_file"]) if h.get("baseline_ids_file") else None)
    new = restamp(man, bench_p, base_p)
    outs = [(a.manifest, dump(new))]
    if a.report:
        outs.append((a.report, restamp_report(a.report.read_text(encoding="utf-8"), new["holdout"])))
    differs = [str(p) for p, text in outs if p.read_text(encoding="utf-8") != text]
    if a.check:
        print(json.dumps({"differs": differs}))
        return 1 if differs else 0
    for p, text in outs:
        p.write_text(text, encoding="utf-8")
    hh = new["holdout"]
    print(json.dumps({"written": [str(p) for p, _ in outs],
                      **{k: hh[k] for k in ("disjoint_benchmark", "disjoint_baseline", "family_disjoint_train")}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
