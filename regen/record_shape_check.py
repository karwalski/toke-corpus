#!/usr/bin/env python3
"""131.44 (b) — corpus-wide shape check: does every single_function record
DECLARE its target function?

131.40 reported 6 `A-ARR-0077` variants as "contain no function at all"
(driver_fail: no target function found). They do declare
`f=flatten(p:@(@u64)):@u64` — the driver's `[^)]*` regex could not parse the
nested-paren parameter, a false negative fixed in driver.function_decls.
This script re-counts with the paren-aware predicate validate.has_target_function
and, for the record, the legacy regex, so both numbers are on file.

Reads only; banks nothing. Writes
  corpus/regen_v04/audit/record_shape_131.44.jsonl   one row per FLAGGED record
  regen/freeze/record_shape_131.44.json               tracked summary
"""
import argparse, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                   # noqa: E402
import driver as drv                           # noqa: E402
from validate import has_target_function, target_name  # noqa: E402

CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
OUT_JSONL = os.path.join(CORPUS, "audit", "record_shape_131.44.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", "record_shape_131.44.json")
_LEGACY_FN = re.compile(r"f=([a-z0-9]+)\(([^)]*)\):(\S+?)\{")   # pre-131.44 driver._FN


def classify(src, spec):
    """Shape verdict for one record: {ok, detail, legacy_ok, driver_target,
    nested_paren_params}. `ok` is the has_target_function predicate;
    `legacy_ok` is what the pre-131.44 regex would have said (main/stubs
    excluded); `driver_target` is drv.find_target's pick (None = driver_fail)."""
    ok, detail = has_target_function(src, spec)
    stubs = drv._stub_names(spec)
    legacy = [m.group(1) for m in _LEGACY_FN.finditer(src)
              if m.group(1) != "main" and m.group(1) not in stubs]
    want = target_name(spec)
    legacy_ok = (want in legacy) if want else bool(legacy)
    target = drv.find_target(spec, src)
    d = drv._last_decl(src, target) if target else None
    nested = bool(d and any("@(" in p for p in d["params"]))
    return {"ok": ok, "detail": detail, "legacy_ok": legacy_ok,
            "driver_target": target, "nested_paren_params": nested}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-dir", default=CORPUS)
    ap.add_argument("--out", default=OUT_JSONL)
    ap.add_argument("--summary", default=OUT_SUMMARY)
    args = ap.parse_args()
    specs = audit.load_specs(args.corpus_dir)
    man = {}
    for line in open(os.path.join(args.corpus_dir, "MANIFEST.jsonl")):
        e = json.loads(line)
        man[e["task_id"]] = e
    n = flagged = legacy_flagged = driver_none = nested = 0
    rows = []
    for tid, e in sorted(man.items()):
        s = specs.get(tid)
        if not s or s.get("task_type") != "single_function":
            continue
        path = os.path.join(args.corpus_dir, e["category"], tid + ".json")
        if not os.path.exists(path):
            continue
        n += 1
        src = json.load(open(path))["tk_source"]
        c = classify(src, s)
        flagged += not c["ok"]
        legacy_flagged += not c["legacy_ok"]
        driver_none += c["driver_target"] is None
        nested += c["nested_paren_params"]
        if not c["ok"] or not c["legacy_ok"] or c["driver_target"] is None:
            rows.append({"task_id": tid, "category": e["category"], **c})
    with open(args.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    summary = {"story": "131.44", "date": time.strftime("%Y-%m-%d"),
               "single_function_records": n,
               "no_target_function": flagged,
               "legacy_regex_false_negatives": legacy_flagged,
               "driver_find_target_none": driver_none,
               "nested_paren_param_targets": nested,
               "flagged_task_ids": sorted(r["task_id"] for r in rows if not r["ok"]),
               "legacy_only_task_ids": sorted(r["task_id"] for r in rows
                                              if r["ok"] and not r["legacy_ok"]),
               "jsonl": os.path.relpath(args.out, os.path.dirname(HERE))}
    with open(args.summary, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
