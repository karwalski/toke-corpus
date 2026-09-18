#!/usr/bin/env python3
"""131.44 — re-execute the records 131.40 left behind, through audit.audit_one
on the pinned compiler, after the (a)/(b)/(d) fixes:
  (a) 76 records whose a_tests carry the mangled `@(u64` spelling
      (u64_recheck tests_source == a_tests_norm) — now injected by load_specs
  (b) 6 A-ARR-0077 variants the driver reported as function-less
      (verdict compile-only: no target function found) — paren-aware find_target
  (d) 15 records failing only on a `T!Err` err-case — driver renders `err:<variant>`
Records with no tests after load_specs (the 4 A-ERR-0005 packed-arity ones)
get u64_recheck_131.inject_extra's unpacking, labelled. Reads only; banks
nothing. Writes
  corpus/regen_v04/audit/recheck_131.44.jsonl   one row per record
  regen/freeze/recheck_131.44.json              tracked summary
"""
import json, multiprocessing, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                    # noqa: E402
import idiom_judge, metrics, validate, tkc_pin  # noqa: E402
import u64_recheck_131 as u64                   # noqa: E402

CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
PRIOR = os.path.join(CORPUS, "audit", "u64_recheck_131.40.jsonl")
OUT_JSONL = os.path.join(CORPUS, "audit", "recheck_131.44.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", "recheck_131.44.json")


def groups():
    g = {"a_norm76": [], "b_no_target6": [], "d_err15": []}
    for line in open(PRIOR):
        r = json.loads(line)
        if r["tests_source"] == "a_tests_norm":
            g["a_norm76"].append(r["task_id"])
        if r["verdict"].startswith("compile-only: no target"):
            g["b_no_target6"].append(r["task_id"])
        if r["verdict"].startswith("executes; FAILS only"):
            g["d_err15"].append(r["task_id"])
    return {k: sorted(v) for k, v in g.items()}


def main(workers=8):
    g = groups()
    ids = sorted(set(sum(g.values(), [])))
    specs = audit.load_specs(CORPUS)
    a_dir = os.path.join(CORPUS, "audit", "a_tests")
    banked = {fn[:-5]: json.load(open(os.path.join(a_dir, fn)))
              for fn in os.listdir(a_dir) if fn.endswith(".json")}
    man = {}
    for line in open(os.path.join(CORPUS, "MANIFEST.jsonl")):
        e = json.loads(line)
        man[e["task_id"]] = e["category"]
    src = {}
    for tid in ids:
        s = specs[tid]
        if s.get("test_cases"):
            src[tid] = s.get("_tests_from") or "spec"
        else:
            src[tid] = u64.inject_extra(s, banked)
    tmpdir = os.path.join(CORPUS, "audit", "tmp_131.44")
    os.makedirs(tmpdir, exist_ok=True)
    jobs = [(tid, os.path.join(CORPUS, man[tid], tid + ".json"), specs[tid], tmpdir) for tid in ids]
    pinned = tkc_pin.pin().install(audit, validate, metrics, idiom_judge)
    print(f"{len(jobs)} records, tkc {pinned.version} sha {pinned.sha256[:12]}", file=sys.stderr)
    rows = {}
    t0 = time.time()
    with pinned, multiprocessing.Pool(workers) as pool:
        for row in pool.imap_unordered(audit.audit_one, jobs, chunksize=2):
            rows[row["task_id"]] = row
    print(f"  {len(rows)} in {time.time() - t0:.0f}s", file=sys.stderr)
    out = []
    summary = {"story": "131.44", "date": time.strftime("%Y-%m-%d"), "tkc": pinned.stamp(),
               "records": len(ids), "groups": {}, "jsonl": os.path.relpath(OUT_JSONL, os.path.dirname(HERE))}
    for name, tids in g.items():
        c = {"records": len(tids), "executed": 0, "pass": 0, "fail": 0, "driver_fail": 0,
             "compile_fail": 0, "fail_reasons": {}, "failing_task_ids": [], "tests_source": {}}
        for tid in tids:
            r = rows[tid]
            c["tests_source"][src[tid]] = c["tests_source"].get(src[tid], 0) + 1
            if not r.get("compile"):
                c["compile_fail"] += 1
            elif r.get("driver_fail"):
                c["driver_fail"] += 1
                c["fail_reasons"]["driver: " + r["driver_fail"]] = \
                    c["fail_reasons"].get("driver: " + r["driver_fail"], 0) + 1
                c["failing_task_ids"].append(tid)
            elif r.get("executed"):
                c["executed"] += 1
                if r.get("tests_new"):
                    c["pass"] += 1
                else:
                    c["fail"] += 1
                    why = r.get("reason") or "?"
                    c["fail_reasons"][why] = c["fail_reasons"].get(why, 0) + 1
                    c["failing_task_ids"].append(tid)
        summary["groups"][name] = c
    for tid in ids:
        r = rows[tid]
        out.append({"task_id": tid, "groups": [k for k, v in g.items() if tid in v],
                    "tests_source": src[tid], **u64._gates(r), "tkc_bin_sha": pinned.sha256})
    with open(OUT_JSONL, "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary["groups"], indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
