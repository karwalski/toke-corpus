#!/usr/bin/env python3
"""Worker self-check for the 129.4/129.5 repair wave.

Usage: check_repair.py --task-id <id> [--workdir .../work/repair_129]
Reads gen/fix_<task_id>.tk, runs the FULL audit gate set on it (compile, build,
tests via driver/main with tightened gates, idiom floor, structural rubric)
and prints PASS or FAIL <reason> with diagnostics. Exit 0/1. Writes nothing.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit import audit_one                       # noqa: E402

CORPUS = "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--workdir", default=os.path.join(CORPUS, "work", "repair_129"))
    args = ap.parse_args()
    spec = json.load(open(os.path.join(args.workdir, "specs", args.task_id + ".json")))
    fix = os.path.join(args.workdir, "gen", "fix_" + args.task_id + ".tk")
    if not os.path.exists(fix):
        print("FAIL no candidate at " + fix)
        sys.exit(1)
    # audit_one reads a record JSON; wrap the candidate source as one
    tmp = fix + ".check.rec.json"
    with open(tmp, "w") as f:
        json.dump({"tk_source": open(fix, errors="replace").read(), "regen": {}}, f)
    try:
        row = audit_one((args.task_id, tmp, spec, args.workdir))
    finally:
        os.unlink(tmp)
    failures = []
    if not row.get("compile"):
        failures.append("compile: " + ",".join(row.get("error_codes") or []))
    elif row.get("executed"):
        if not row.get("build"):
            failures.append("build failed (codegen)")
        elif not row.get("tests_new"):
            failures.append("tests: " + str(row.get("reason")))
    if row.get("driver_fail"):
        failures.append("driver: " + row["driver_fail"])
    if (row.get("idiom") or 1.0) < 0.6:
        failures.append(f"idiom {row['idiom']} < 0.6: {row.get('idiom_notes')}")
    m = row.get("metrics") or {}
    if m.get("max_depth", 0) > 4:
        failures.append(f"nesting depth {m['max_depth']} > 4")
    if m.get("max_func_bytes", 0) > 600:
        failures.append(f"function {m['max_func_bytes']} bytes > 600")
    if failures:
        print("FAIL " + "; ".join(failures))
        print(json.dumps(row, indent=1)[:1500])
        sys.exit(1)
    print(f"PASS depth={m.get('max_depth')} fn_bytes={m.get('max_func_bytes')} "
          f"idiom={row.get('idiom')}")


if __name__ == "__main__":
    main()
