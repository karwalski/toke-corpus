#!/usr/bin/env python3
"""131.40 — re-execute every single_function record whose signature carries an
unsigned type (`u64` / `@u64` / `@@u64`) BEFORE and AFTER the driver.lit fix
(`(N as u64)` literals), so the AUDIT_129 "silently unverified" set is
quantified. Reads only; banks nothing; writes
  corpus/regen_v04/audit/u64_recheck_131.40.jsonl   one row per record
  regen/freeze/u64_recheck_131.40.json             tracked summary

Test injection mirrors audit.load_specs (a_tests per base, exact input_types
match) and adds two clearly-labelled extra sources for records that today get
NO tests because the 129.7 a_tests carry the sampler-mangled spelling:
  a_tests_norm     `@(u64` normalised to `@u64` (diff_check.ref_signature_ok rule)
  a_tests_unpacked base authored against a mangled 1-param spec with the real
                   args packed into one list: unpacked only when the list has
                   exactly the spec's arity and every element's Python type
                   matches the spec type (list for @T, int for integers)
BEFORE = driver.lit as of 6bb2554 (bare ints); AFTER = current driver.lit.
"""
import json, multiprocessing, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                   # noqa: E402
import driver as drv                           # noqa: E402
import idiom_judge, metrics, validate, tkc_pin  # noqa: E402

CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
OUT_JSONL = os.path.join(CORPUS, "audit", "u64_recheck_131.40.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", "u64_recheck_131.40.json")
UNSIGNED = ("u64", "u32")
_BASE = re.compile(r"^(A-[A-Z]+-\d+)v\d+$")
_FN = re.compile(r"f=([a-z0-9]+)\(([^)]*)\):(\S+?)\{")


def _legacy_lit(v, t):
    """driver.lit before 131.40 (toke-corpus 6bb2554) — bare integer literals."""
    if t.startswith("@"):
        if not isinstance(v, list):
            raise ValueError(f"array type {t} but non-list input {v!r}")
        return "@(" + ";".join(_legacy_lit(x, t[1:]) for x in v) + ")"
    if t == "str":
        return '"' + drv._esc(str(v)) + '"'
    if t == "bool":
        return "true" if v else "false"
    if t in ("f64", "f32"):
        s = repr(float(v))
        return s if "." in s or "e" in s else s + ".0"
    return str(int(v))


def _norm_a_types(ts):
    return [t[:-1] if t.endswith("(") else t.replace("@(", "@") for t in ts]


def _py_ok(v, t):
    if t.startswith("@"):
        return isinstance(v, list) and all(_py_ok(x, t[1:]) for x in v)
    if t in ("i64", "u64", "i32", "u32"):
        return isinstance(v, int) and not isinstance(v, bool)
    if t in ("f64", "f32"):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t in ("str", "$str"):
        return isinstance(v, str)
    if t == "bool":
        return isinstance(v, bool)
    return False


def inject_extra(spec, banked):
    """Attach a_tests to a spec audit.load_specs left without tests. Returns the
    source label or a reason string starting with 'no tests:'."""
    tid = spec["task_id"]
    m = _BASE.match(tid)
    b = banked.get(m.group(1) if m else tid)
    if not b:
        return "no tests: no banked a_tests base"
    et = drv.effective_input_types(spec)
    if et == _norm_a_types(b["input_types"]):
        spec["test_cases"] = b["test_cases"]
        return "a_tests_norm"
    if len(b["input_types"]) == 1 and len(et) > 1:
        cases = []
        for tc in b["test_cases"]:
            ins = tc.get("inputs") or []
            if len(ins) == 1 and isinstance(ins[0], list) and len(ins[0]) == len(et) \
                    and all(_py_ok(v, t) for v, t in zip(ins[0], et)):
                cases.append({"inputs": ins[0], "expected": tc.get("expected")})
            else:
                return (f"no tests: a_tests arity mismatch (base authored against "
                        f"{b['input_types']}, spec {et}; packed case not unpackable)")
        spec["test_cases"] = cases
        return "a_tests_unpacked"
    return f"no tests: a_tests input_types {b['input_types']} != spec {et}"


def affected_specs():
    specs = audit.load_specs(CORPUS)
    a_dir = os.path.join(CORPUS, "audit", "a_tests")
    banked = {fn[:-5]: json.load(open(os.path.join(a_dir, fn)))
              for fn in os.listdir(a_dir) if fn.endswith(".json")}
    out = {}
    for tid, s in specs.items():
        if s.get("task_type") != "single_function":
            continue
        et = drv.effective_input_types(s)
        ret = s.get("output_type_v03") or s.get("output_type") or ""
        in_hit = any(u in t for t in et for u in UNSIGNED)
        if not in_hit and not any(u in ret for u in UNSIGNED):
            continue
        if s.get("test_cases"):
            src = "spec" if not s.get("_tests_from") else s["_tests_from"]
        else:
            src = inject_extra(s, banked)
        out[tid] = (s, {"input_types": et, "output_type": ret, "input_affected": in_hit,
                        "tests_source": src, "err_expectation": _err_expectation(s)})
    return out


def _init(mode):
    if mode == "before":
        drv.lit = _legacy_lit


def _gates(row):
    return {k: row.get(k) for k in ("compile", "executed", "driver_fail", "build",
                                    "tests_old", "tests_new", "reason", "error_codes")}


def _record_sig_has_unsigned(rec_path, n_inputs):
    src = json.load(open(rec_path))["tk_source"]
    decls = [(m.group(1), m.group(2)) for m in _FN.finditer(src) if m.group(1) != "main"]
    match = [p for _, p in decls if len([x for x in p.split(";") if x.strip()]) == n_inputs]
    params = match[-1] if match else (decls[-1][1] if decls else "")
    return any(u in params for u in UNSIGNED)


def _err_expectation(spec):
    """A `T!Err` return whose a_tests expect an err marker the driver's
    interpolation cannot render (it prints the payload) — a driver limit,
    not evidence the record is wrong."""
    ret = spec.get("output_type_v03") or spec.get("output_type") or ""
    return "!" in ret and any(isinstance(tc.get("expected"), dict)
                              for tc in spec.get("test_cases") or [])


def verdict(meta, before, after):
    if not after.get("compile"):
        return "record does not compile"
    if not after.get("executed"):
        return "compile-only: " + (after.get("driver_fail") or meta["tests_source"])
    b_ok, a_ok = bool(before.get("tests_new")), bool(after.get("tests_new"))
    if a_ok:
        return "pass (unchanged)" if b_ok else "newly executes + passes"
    if b_ok:
        return "REGRESSION"
    if meta.get("err_expectation"):
        return "executes; FAILS only on a T!Err err-case (driver cannot render err — unproven)"
    if before.get("executed") and before.get("build"):
        return "fail (unchanged, real failure)"
    return "newly executes + FAILS"


def err_marker_hardcoded(man):
    """Seen while classifying the T!Err rows: single_function records with a
    `T!Err` spec return whose source contains the literal text `{'err'` — the
    record returns the a_tests err marker as a STRING (return type drifted to
    `str`) so the driver comparison passes. Counted for the report only."""
    specs = audit.load_specs(CORPUS)
    ids = []
    for tid, s in specs.items():
        ret = s.get("output_type_v03") or s.get("output_type") or ""
        if s.get("task_type") != "single_function" or "!" not in ret or tid not in man:
            continue
        src = json.load(open(os.path.join(CORPUS, man[tid], tid + ".json")))["tk_source"]
        if "{'err'" in src:
            ids.append(tid)
    return {"records": len(ids), "bases": len({(_BASE.match(t) or [None, t])[1] for t in ids}),
            "task_ids": sorted(ids)}


def main(workers=8):
    aff = affected_specs()
    man = {}
    for line in open(os.path.join(CORPUS, "MANIFEST.jsonl")):
        e = json.loads(line)
        man[e["task_id"]] = e["category"]
    ledger = {}
    for line in open(os.path.join(CORPUS, "audit", "audit_corpus.jsonl")):
        r = json.loads(line)
        ledger[r["task_id"]] = r
    tmpdir = os.path.join(CORPUS, "audit", "tmp_u64_131.40")
    os.makedirs(tmpdir, exist_ok=True)
    jobs = [(tid, os.path.join(CORPUS, man[tid], tid + ".json"), s, tmpdir)
            for tid, (s, _) in sorted(aff.items()) if tid in man]
    pinned = tkc_pin.pin().install(audit, validate, metrics, idiom_judge)
    print(f"{len(jobs)} affected records, tkc {pinned.version} sha {pinned.sha256[:12]}",
          file=sys.stderr)
    results = {}
    with pinned:
        for mode in ("before", "after"):
            t0 = time.time()
            with multiprocessing.Pool(workers, initializer=_init, initargs=(mode,)) as pool:
                for row in pool.imap_unordered(audit.audit_one, jobs, chunksize=4):
                    results.setdefault(row["task_id"], {})[mode] = _gates(row)
            print(f"  {mode}: {len(jobs)} in {time.time() - t0:.0f}s", file=sys.stderr)
    rows, counts, newly_fail, newly_pass, drift = [], {}, [], [], []
    for tid, _, s, _ in jobs:
        meta = aff[tid][1]
        before, after = results[tid]["before"], results[tid]["after"]
        rec_u = _record_sig_has_unsigned(os.path.join(CORPUS, man[tid], tid + ".json"),
                                         len(meta["input_types"]))
        v = verdict(meta, before, after)
        old = ledger.get(tid, {})
        row = {"task_id": tid, "base": (_BASE.match(tid) or [None, tid])[1],
               "category": man[tid], **meta, "record_sig_unsigned": rec_u,
               "audit_129": {"executed": old.get("executed"), "tests_new": old.get("tests_new"),
                             "driver_fail": old.get("driver_fail")},
               "before": before, "after": after, "verdict": v,
               "tkc_bin_sha": pinned.sha256}
        rows.append(row)
        counts[v] = counts.get(v, 0) + 1
        if v == "newly executes + FAILS":
            newly_fail.append(tid)
        elif v == "newly executes + passes":
            newly_pass.append(tid)
        if meta["input_affected"] and not rec_u:
            drift.append(tid)
    with open(OUT_JSONL, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    by_src = {}
    for r in rows:
        by_src.setdefault(r["tests_source"], {}).setdefault(r["verdict"], 0)
        by_src[r["tests_source"]][r["verdict"]] += 1
    summary = {"story": "131.40", "date": time.strftime("%Y-%m-%d"),
               "related_finding_err_marker_hardcoded": err_marker_hardcoded(man),
               "tkc": pinned.stamp(), "affected_records": len(rows),
               "input_affected": sum(1 for r in rows if r["input_affected"]),
               "record_sig_unsigned": sum(1 for r in rows if r["record_sig_unsigned"]),
               "signature_drift_i64_for_u64": len(drift),
               "audit_129_never_executed": sum(1 for r in rows if not r["audit_129"]["executed"]),
               "verdicts": counts, "verdicts_by_tests_source": by_src,
               "newly_failing_task_ids": sorted(newly_fail),
               "newly_passing_task_ids": sorted(newly_pass),
               "compile_only_reasons": sorted({r["verdict"] for r in rows
                                               if r["verdict"].startswith("compile-only")}),
               "a_tests_bases_still_unmatched": sorted({r["base"] for r in rows
                                                        if r["tests_source"].startswith("no tests")}),
               "jsonl": os.path.relpath(OUT_JSONL, os.path.dirname(HERE))}
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({k: summary[k] for k in ("affected_records", "verdicts",
                                                "newly_failing_task_ids")}, indent=1))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
