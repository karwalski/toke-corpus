#!/usr/bin/env python3
"""131.47 — after the re-author wave is BANKED, re-run audit.audit_one over
every single_function record whose base was re-authored (the 54 packed-arity
bases: every variant of each base, 804 records) so the new a_tests are
exercised for real. Reads only; banks nothing; the pattern is
u64_recheck_131.py / recheck_131_44.py. Writes
  corpus/regen_v04/audit/recheck_<story>.jsonl   one row per record
  regen/freeze/recheck_<story>.json              tracked summary (per base)

The bases come from the wave workdir's batch manifests (--workdir, default
work/atest_131) unless --bases is given. Each row carries the record's prior
verdict from the 131.40/131.44 rechecks (when it had one) so the summary
can name what RESOLVED: expected = the 9 A-ARR-0048/0055 compile-only
records (no tests at arity 1) + A-ERR-0005v24 (output mismatch on the
packed cases).

    python3 regen/recheck_after_atest.py                # 8 workers
    python3 regen/recheck_after_atest.py --workers 12 --story 131.47
"""
import argparse, glob, json, multiprocessing, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                    # noqa: E402
import idiom_judge, metrics, validate, tkc_pin  # noqa: E402
import u64_recheck_131 as u64                   # noqa: E402

CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
DEFAULT_WD = os.path.join(CORPUS, "work", "atest_131")
PRIOR = [os.path.join(CORPUS, "audit", "recheck_131.44.jsonl"),
         os.path.join(CORPUS, "audit", "u64_recheck_131.40.jsonl")]
_BASE = re.compile(r"^(A-[A-Z]+-\d+)v\d+$")


def base_of(tid):
    m = _BASE.match(tid)
    return m.group(1) if m else tid


def bases_from_workdir(workdir):
    out = set()
    for fn in sorted(glob.glob(os.path.join(workdir, "batches", "batch_*.json"))):
        out |= set(json.load(open(fn)).get("base_ids") or [])
    return sorted(out)


def prior_verdicts(ids):
    """task_id -> prior verdict string (131.44 recheck first, else 131.40)."""
    want = set(ids)
    out = {}
    for path in PRIOR:
        if not os.path.exists(path):
            continue
        for line in open(path):
            r = json.loads(line)
            tid = r.get("task_id")
            if tid not in want or tid in out:
                continue
            v = r.get("verdict")
            if v is None:  # 131.44 rows carry the gates, not a verdict string
                if not r.get("compile"):
                    v = "record does not compile"
                elif not r.get("executed"):
                    v = "compile-only: " + (r.get("driver_fail") or r.get("tests_source") or "no tests")
                else:
                    v = "pass" if r.get("tests_new") else "fail: " + (r.get("reason") or "?")
            out[tid] = v
    return out


def classify(row):
    if row.get("audit_error"):
        return "audit_error"
    if not row.get("compile"):
        return "compile_fail"
    if row.get("driver_fail"):
        return "driver_fail"
    if not row.get("executed"):
        return "no_tests"
    return "pass" if row.get("tests_new") else "fail"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=DEFAULT_WD)
    ap.add_argument("--bases", nargs="*", help="override: base ids to recheck")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--story", default="131.47")
    args = ap.parse_args()
    bases = set(args.bases or bases_from_workdir(args.workdir))
    if not bases:
        sys.exit("no bases (no batches/ in the workdir and no --bases)")
    specs = audit.load_specs(CORPUS)          # injects the freshly banked a_tests
    man = {}
    for line in open(os.path.join(CORPUS, "MANIFEST.jsonl")):
        e = json.loads(line)
        man[e["task_id"]] = e["category"]
    ids = sorted(t for t, s in specs.items()
                 if t in man and s.get("task_type") == "single_function" and base_of(t) in bases)
    a_dir = os.path.join(CORPUS, "audit", "a_tests")
    banked = {fn[:-5]: json.load(open(os.path.join(a_dir, fn)))
              for fn in os.listdir(a_dir) if fn.endswith(".json")}
    src = {}
    for tid in ids:
        s = specs[tid]
        src[tid] = (s.get("_tests_from") or "spec") if s.get("test_cases") \
            else u64.inject_extra(s, banked)
    prior = prior_verdicts(ids)
    tmpdir = os.path.join(CORPUS, "audit", f"tmp_{args.story}")
    os.makedirs(tmpdir, exist_ok=True)
    jobs = [(tid, os.path.join(CORPUS, man[tid], tid + ".json"), specs[tid], tmpdir) for tid in ids]
    pinned = tkc_pin.pin().install(audit, validate, metrics, idiom_judge)
    print(f"{len(jobs)} records over {len(bases)} bases, tkc {pinned.version} "
          f"sha {pinned.sha256[:12]}", file=sys.stderr)
    rows = {}
    t0 = time.time()
    with pinned, multiprocessing.Pool(args.workers) as pool:
        for row in pool.imap_unordered(audit.audit_one, jobs, chunksize=2):
            rows[row["task_id"]] = row
    print(f"  {len(rows)} in {time.time() - t0:.0f}s", file=sys.stderr)
    out_jsonl = os.path.join(CORPUS, "audit", f"recheck_{args.story}.jsonl")
    out_summary = os.path.join(HERE, "freeze", f"recheck_{args.story}.json")
    per_base, totals = {}, {}
    resolved, still_failing, regressed = [], [], []
    with open(out_jsonl, "w") as f:
        for tid in ids:
            r = rows[tid]
            c = classify(r)
            totals[c] = totals.get(c, 0) + 1
            pb = per_base.setdefault(base_of(tid), {"records": 0, "provenance": banked.get(
                base_of(tid), {}).get("provenance"), "tests_source": {}, "verdicts": {},
                "failing_task_ids": []})
            pb["records"] += 1
            pb["tests_source"][src[tid]] = pb["tests_source"].get(src[tid], 0) + 1
            pb["verdicts"][c] = pb["verdicts"].get(c, 0) + 1
            if c in ("fail", "driver_fail", "compile_fail", "audit_error"):
                pb["failing_task_ids"].append(tid)
            pv = prior.get(tid)
            if pv and c == "pass" and not pv.startswith("pass"):
                resolved.append(tid)
            elif pv and pv.startswith("pass") and c != "pass":
                regressed.append(tid)
            elif c in ("fail", "driver_fail"):
                still_failing.append(tid)
            f.write(json.dumps({"task_id": tid, "base": base_of(tid), "tests_source": src[tid],
                                "prior_verdict": pv, "verdict": c, **u64._gates(r),
                                "tkc_bin_sha": pinned.sha256}) + "\n")
    summary = {"story": args.story, "date": time.strftime("%Y-%m-%d"), "tkc": pinned.stamp(),
               "workdir": os.path.relpath(args.workdir, os.path.dirname(HERE)),
               "bases": len(bases), "records": len(ids), "verdicts": totals,
               "resolved_task_ids": sorted(resolved), "regressed_task_ids": sorted(regressed),
               "failing_task_ids": sorted(still_failing),
               "bases_not_rebanked": sorted(b for b in bases if not str(
                   banked.get(b, {}).get("provenance", "")).startswith(args.story)),
               "per_base": per_base,
               "jsonl": os.path.relpath(out_jsonl, os.path.dirname(HERE))}
    with open(out_summary, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({k: summary[k] for k in ("records", "verdicts", "resolved_task_ids",
                                                "regressed_task_ids", "failing_task_ids",
                                                "bases_not_rebanked")}, indent=1))


if __name__ == "__main__":
    main()
