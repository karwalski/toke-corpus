#!/usr/bin/env python3
"""Main-thread banking for 129.7: independently re-verify each authored test
file (re-executes the Python reference — verify_a_tests.verify) and, on pass,
install it as audit/a_tests/<base>.json. Variant propagation happens at audit
time (audit.py injects a base's cases into variants with matching input
types). Processed files archived .done.

131.35 note: this path writes audit/a_tests/<base>.json only — it never
rewrites a corpus record file, so there is nothing to re-stamp in
MANIFEST.jsonl. If it ever starts touching <CATEGORY>/<task_id>.json, call
manifest_tool.Manifest(...).stamp(task_id, rec_path) right after the write
(see bank_repairs.py / run_shard.cmd_validate)."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from verify_a_tests import verify, WD, CORPUS
from driver import effective_input_types     # noqa: E402


def main():
    gen = os.path.join(WD, "gen")
    outdir = os.path.join(CORPUS, "audit", "a_tests")
    os.makedirs(outdir, exist_ok=True)
    banked = rejected = 0
    reasons = []
    for fn in sorted(os.listdir(gen)):
        if not fn.startswith("tests_") or not fn.endswith(".json"):
            continue
        base = fn[6:-5]
        path = os.path.join(gen, fn)
        spec_path = os.path.join(WD, "specs", base + ".json")
        errs = ["invalid JSON"]
        doc = None
        spec = None
        if os.path.exists(spec_path):
            spec = json.load(open(spec_path))
            try:
                doc = json.load(open(path))
                errs = verify(base, doc, spec)
            except json.JSONDecodeError:
                pass
        else:
            errs = ["no spec"]
        if not errs:
            with open(os.path.join(outdir, base + ".json"), "w") as f:
                json.dump({"base": base, "python_ref": doc.get("python_ref"),
                           "test_cases": doc["test_cases"],
                           "input_types": effective_input_types(spec),
                           "output_type": spec.get("output_type_v03") or spec.get("output_type"),
                           "provenance": "129.7-agent+ref-verified"}, f)
            banked += 1
        else:
            rejected += 1
            reasons.append({base: errs[:2]})
        os.rename(path, path + ".done")
    print(json.dumps({"banked": banked, "rejected": rejected,
                      "sample_rejects": reasons[:10]}))


if __name__ == "__main__":
    main()
