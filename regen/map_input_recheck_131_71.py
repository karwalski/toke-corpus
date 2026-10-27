#!/usr/bin/env python3
"""131.71 — re-execute every record whose spec takes a map input.

`driver.lit` could not render an `@(str:i64)` input ("array type @str:i64 but
non-list input {...}"), so `driver.append_main` failed on test case 0 for the
29 A-ERR records 131.67 reported as `driver_fail`, and the worker `build` /
`tests` gates rejected the original and every candidate alike. lit now
renders map literals; this re-runs each in-scope record through the same
path the diff gate uses (diff_check.prepare_program -> run_spec_cases ->
baseline_verdict) and records what it actually prints.

Scope: every banked single_function record whose effective input types
include a map (driver.map_types), cross-checked against the 131.67
`driver_fail` rows. Reads only; banks nothing.

Outputs
  corpus/regen_v04/audit/map_input_recheck_131.71.jsonl
  regen/freeze/map_input_recheck_131.71.json
"""
import argparse
import collections
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                    # noqa: E402
import diff_check as dc                         # noqa: E402
import driver as drv                            # noqa: E402
import regen_validate                           # noqa: E402
import tkc_pin                                  # noqa: E402

STORY = "131.71"
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
OUT_JSONL = os.path.join(CORPUS, "audit", f"map_input_recheck_{STORY}.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", f"map_input_recheck_{STORY}.json")
PREV = os.path.join(CORPUS, "audit", "err_recheck_131.67.jsonl")


def in_scope(specs, man_ids):
    return sorted(t for t, s in specs.items()
                  if t in man_ids and s.get("task_type") == "single_function"
                  and any(drv.map_types(x) for x in drv.effective_input_types(s)))


def recheck(tid, spec, corpus):
    src = json.load(open(os.path.join(corpus, spec["category"], tid + ".json")))["tk_source"]
    row = {"task_id": tid, "category": spec.get("category"),
           "err_cases": sum(1 for tc in spec.get("test_cases") or [] if drv.err_name(tc.get("expected"))),
           "cases": len(spec.get("test_cases") or [])}
    with tempfile.TemporaryDirectory(prefix="map71_") as td:
        prog = dc.prepare_program(src, spec, tid, td, "orig")
        try:
            runs = dc.run_spec_cases(prog, td)
            base = dc.baseline_verdict(prog, spec, runs)
            row.update({"driver_built": bool(prog.test_bin), "reason": prog.reason,
                        "verdict": base["verdict"], "detail": base["detail"],
                        "got": dc._stdout_lines(runs[0][1]["stdout"]) if runs else None,
                        "want": drv.expected_lines(spec)})
        finally:
            prog.cleanup()
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=CORPUS)
    a = ap.parse_args(argv)
    specs = audit.load_specs(a.corpus)
    man_ids = {json.loads(line)["task_id"] for line in open(os.path.join(a.corpus, "MANIFEST.jsonl"))}
    ids = in_scope(specs, man_ids)
    prev = set()
    if os.path.exists(PREV):
        prev = {r["task_id"] for r in (json.loads(x) for x in open(PREV)) if r.get("verdict") == "driver_fail"}
    with tkc_pin.pin().install(sys.modules[__name__], dc, regen_validate) as pinned:
        rows = [recheck(t, specs[t], a.corpus) for t in ids]
        for r in rows:
            r["tkc_bin_sha"] = pinned.sha256
        stamp = pinned.stamp()
    with open(OUT_JSONL, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    v = collections.Counter(r["verdict"] for r in rows)
    summary = {
        "story": STORY, "date": time.strftime("%Y-%m-%d"), "tkc": stamp,
        "scope": "banked single_function records with a map-typed input (driver.map_types)",
        "records": len(rows),
        "same_as_131_67_driver_fail": sorted(prev) == ids,
        "only_in_131_67": sorted(prev - set(ids)), "only_in_scope": sorted(set(ids) - prev),
        "driver_built": sum(1 for r in rows if r["driver_built"]),
        "verdicts": dict(v),
        "pass_with_err_case": sum(1 for r in rows if r["verdict"] == "pass" and r["err_cases"]),
        "pass_task_ids": sorted(r["task_id"] for r in rows if r["verdict"] == "pass"),
        "fail": [{k: r[k] for k in ("task_id", "got", "want")} for r in rows if r["verdict"] != "pass"],
        "jsonl": os.path.relpath(OUT_JSONL, os.path.dirname(HERE)),
    }
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    print(json.dumps({k: summary[k] for k in ("records", "same_as_131_67_driver_fail", "driver_built",
                                              "verdicts", "pass_with_err_case")}, indent=1))
    return summary


if __name__ == "__main__":
    main()
