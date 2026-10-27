#!/usr/bin/env python3
"""131.60 — a test lock for migrate_fix records (route (a)).

A `migrate_fix` record is a phase-2 program migrated to current toke. Most
have no main() and the spec carries no test cases, so `diff_check` returned
`unverifiable: no main()` and no wave could bank or verify them (210 sat in the
131.14 AUTO bucket alone). They are the largest block of never-executed
training data.

Route (b) — diff against the stored PRE-migration source — is not available:
every pre-form was selected precisely because it FAILS the current compiler
(sample_tasks.mine_migrate_tasks keeps only sources `tkc --check` rejects), so
there is no baseline binary to diff against. Route (a) is: the phase-2 task id
inside the MIG id (`MIG-P2-A-A-ARR-0004v7-<hash>` -> `A-ARR-0004v7`) names the
same base task the A-side a_tests bank (129.7, execution-verified against a
Python reference) was authored for. When the migrated source declares a
function whose signature matches that a_tests file — same arity, every
parameter type conforming (regen_validate.param_type_conforms) and the same
normalised return type — and the shared driver would call exactly that
function, the record gets a synthesised single_function spec whose test cases
are the base's a_tests cases, so the driver (and diff_check's 20 generated
inputs and python_ref cross-check) can execute it.

No lock (and an explicit reason) when: the record has its own main() (it runs
as a full program already), the id carries no A-side base, the base has no
a_tests file, no function matches the a_tests signature, or the driver's
target would be a different function (ambiguous). D-side (phase-2 group B)
records have no a_tests bank at all — they stay compile-only and AUDIT_131
must say so (route (c), explicitly), never imply they executed.

Library: legacy_task_id, base_id, lock_spec, drive_spec. CLI (--census):
every migrate_fix record -> lock or reason, and for each lock the ORIGINAL run
against it (pass / fail / build fail), so the freeze can state exactly how
many migrate_fix records are execution-verified.

Outputs (--census)
  corpus/regen_v04/audit/migrate_lock_131.60.jsonl
  regen/freeze/migrate_lock_131.60.json
"""
import argparse
import collections
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import driver as drv                            # noqa: E402
import regen_validate as rv                     # noqa: E402

STORY = "131.60"
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
OUT_JSONL = os.path.join(CORPUS, "audit", f"migrate_lock_{STORY}.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", f"migrate_lock_{STORY}.json")
_MIG = re.compile(r"^MIG-P2-([A-Z])-(.+)-[0-9a-f]{8}$")
_TASK = re.compile(r"^([A-Z]-[A-Z]{3}-\d{4})(v\d+)?$")


def legacy_task_id(tid):
    """Phase-2 task id inside a MIG id (`A-ARR-0004v7`), or None."""
    m = _MIG.match(tid or "")
    return m.group(2) if m else None


def base_id(tid):
    """A-side base id (`A-ARR-0004`) of a MIG id, or None (D-side, N-series,
    unparseable)."""
    t = legacy_task_id(tid)
    m = _TASK.match(t or "")
    if not m or not m.group(1).startswith("A-"):
        return None
    return m.group(1)


def load_a_test(base, corpus=CORPUS):
    path = os.path.join(corpus, "audit", "a_tests", f"{base}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _sig_text(name, types, ret):
    return f"f={name}(" + ";".join(f"p{i}:{t}" for i, t in enumerate(types)) + f"):{ret}"


def lock_spec(spec, src, corpus=CORPUS, a_test=None):
    """(lock spec | None, info). info: {"lock": "a_tests:<base>" | None,
    "reason", "base", "target", "a_test"}. The lock spec is a single_function
    spec (task_type, description naming the matched function, input/output
    types and test cases from the a_tests file); the record itself is not
    touched."""
    info = {"lock": None, "reason": None, "base": None, "target": None, "a_test": None}
    if spec.get("task_type") != "migrate_fix":
        info["reason"] = "not migrate_fix"
        return None, info
    if "f=main(" in src:
        info["reason"] = "has main(): runs as a full program"
        return None, info
    base = base_id(spec.get("task_id"))
    info["base"] = base
    if not base:
        info["reason"] = "no A-side base task in the id (D-side / N-series records have no a_tests bank)"
        return None, info
    at = a_test or load_a_test(base, corpus)
    if not at or not at.get("test_cases"):
        info["reason"] = f"no a_tests for {base}"
        return None, info
    in_types = drv.norm_types(at.get("input_types"))
    out_type = drv._norm_type(at.get("output_type") or "")
    if not in_types or not out_type:
        info["reason"] = f"a_tests {base} carries no signature"
        return None, info
    decls = [d for d in drv.function_decls(src) if d["ret"] is not None and d["name"] != "main"]
    matches = []
    for d in decls:
        if len(d["params"]) != len(in_types) or any(":" not in p for p in d["params"]):
            continue
        got = [p.split(":", 1)[1].strip() for p in d["params"]]
        if all(rv.param_type_conforms(w, g) for w, g in zip(in_types, got)) and \
                rv.norm_type(d["ret"]) == rv.norm_type(out_type):
            matches.append(d["name"])
    sig = _sig_text("?", in_types, out_type)
    if not matches:
        info["reason"] = f"no function matches the a_tests signature {sig}"
        return None, info
    lspec = {"task_id": spec.get("task_id"), "category": spec.get("category"),
             "task_type": "single_function", "difficulty": spec.get("difficulty"),
             "description": _sig_text(matches[-1], in_types, out_type),
             "input_types": in_types, "output_type": out_type,
             "domain_context_v03": "", "test_cases": at["test_cases"],
             "_tests_from": f"a_tests:{base}", "_lock_story": STORY}
    target = drv.find_target(lspec, src)
    if target not in matches:
        info["reason"] = (f"ambiguous: the driver would call `{target}`, which does not match "
                          f"{sig} (matching: {', '.join(matches)})")
        return None, info
    lspec["description"] = _sig_text(target, in_types, out_type)
    info.update({"lock": f"a_tests:{base}", "reason": "ok", "target": target, "a_test": at})
    return lspec, info


def drive_spec(spec, src, a_test=None, corpus=CORPUS):
    """For the diff gate: (spec to drive with, a_test, lock info or None).
    A migrate_fix record with a lock is driven as single_function against it
    (and its a_tests python_ref feeds the cross-check); everything else is
    returned unchanged."""
    if spec.get("task_type") != "migrate_fix":
        return spec, a_test, None
    lspec, info = lock_spec(spec, src, corpus, a_test)
    public = {k: v for k, v in info.items() if k != "a_test"}
    if lspec is None:
        return spec, a_test, public
    return lspec, a_test or info["a_test"], public


# ---------------------------------------------------------------- census ---
def census_one(args):
    """One record: lock (or reason) and, when locked, the ORIGINAL run
    against it. Never raises."""
    tid, path, spec, corpus = args
    import diff_check as dc
    row = {"task_id": tid, "category": spec.get("category"), "legacy_task_id": legacy_task_id(tid)}
    try:
        src = json.load(open(path))["tk_source"]
        row["has_main"] = "f=main(" in src
        lspec, info = lock_spec(spec, src, corpus)
        row.update({k: v for k, v in info.items() if k != "a_test"})
        if lspec is None:
            row["verdict"] = "no_lock"
            return row
        with tempfile.TemporaryDirectory(prefix="mlock_") as td:
            prog = dc.prepare_program(src, lspec, tid, td, "orig")
            try:
                if not prog.test_bin:
                    row.update({"verdict": "driver_fail", "detail": prog.reason})
                    return row
                runs = dc.run_spec_cases(prog, td)
                base = dc.baseline_verdict(prog, lspec, runs)
                row.update({"verdict": {"pass": "lock_pass", "fail": "lock_fail"}.get(base["verdict"], "unknown"),
                            "detail": base["detail"],
                            "got": dc._stdout_lines(runs[0][1]["stdout"])[:12] if runs else None,
                            "want": drv.expected_lines(lspec)[:12]})
            finally:
                prog.cleanup()
        return row
    except Exception as e:                       # noqa: BLE001 — a census never crashes the sweep
        row.update({"verdict": "error", "detail": f"{type(e).__name__}: {e}"})
        return row


def _reason_class(r):
    why = r.get("reason") or ""
    for key, label in (("has main", "has_main"), ("no A-side base", "no_a_side_base"),
                       ("no a_tests", "no_a_tests_for_base"), ("no function matches", "signature_mismatch"),
                       ("ambiguous", "ambiguous_target")):
        if key in why:
            return label
    return why or None


_BOOL_TEXT = {"true": "1", "false": "0"}


def bool_render_only(r):
    """131.102: a lock_fail whose ONLY difference is bool spelling — tkc >= 3.0
    interpolates bools as true/false (127.15/127.94) while driver.render_out
    still expects 1/0. Judged only when got/want are complete (<12 lines)."""
    got, want = r.get("got") or [], r.get("want") or []
    if r.get("verdict") != "lock_fail" or len(want) >= 12 or len(got) >= 12:
        return False
    return got != want and [_BOOL_TEXT.get(x, x) for x in got] == want


def summarise(rows, stamp, auto):
    for r in rows:
        r["auto_bucket"] = r["task_id"] in auto
        r["reason_class"] = _reason_class(r) if r["verdict"] == "no_lock" else None
        r["bool_render_only"] = bool_render_only(r)
        r.setdefault("tkc_bin_sha", stamp["tkc_bin_sha"])

    def tally(rs):
        return {"records": len(rs), "verdicts": dict(collections.Counter(r["verdict"] for r in rs)),
                "lock_fail_bool_render_only": sum(1 for r in rs if r["bool_render_only"]),
                "no_lock_reasons": dict(collections.Counter(r["reason_class"] for r in rs if r["verdict"] == "no_lock"))}

    def ids(pred):
        return sorted(r["task_id"] for r in rows if pred(r))

    def group(r):
        return (_MIG.match(r["task_id"]) or [None, "?"])[1]
    summary = {
        "story": STORY, "date": time.strftime("%Y-%m-%d"), "tkc": stamp,
        "route": "(a) a_tests test lock for A-side bases; (c) compile-only, stated explicitly, for the rest",
        "route_b_unavailable": "every pre-migration source fails the current compiler by construction "
                               "(sample_tasks.mine_migrate_tasks keeps only sources tkc --check rejects)",
        "all": tally(rows),
        "auto_bucket_131_14": tally([r for r in rows if r["auto_bucket"]]),
        "by_group": {g: tally([r for r in rows if group(r) == g]) for g in sorted({group(r) for r in rows})},
        "lock_pass_task_ids": ids(lambda r: r["verdict"] == "lock_pass"),
        "lock_fail_bool_render_only_task_ids": ids(lambda r: r["bool_render_only"]),
        "lock_fail_task_ids": ids(lambda r: r["verdict"] == "lock_fail" and not r["bool_render_only"]),
        "driver_fail_task_ids": ids(lambda r: r["verdict"] == "driver_fail"),
        "has_main_task_ids": ids(lambda r: r["verdict"] == "no_lock" and r.get("has_main")),
        "compile_only_task_ids": ids(lambda r: r["verdict"] == "no_lock" and not r.get("has_main")),
        "jsonl": os.path.relpath(OUT_JSONL, os.path.dirname(HERE)),
    }
    summary["compile_only"] = len(summary["compile_only_task_ids"])
    summary["lock_fail_genuine"] = len(summary["lock_fail_task_ids"])
    return summary


def main(argv=None):
    import audit
    import diff_check as dc
    import tkc_pin
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--census", action="store_true", required=True,
                    help="lock + original verdict for every migrate_fix record")
    ap.add_argument("--rerun-verdict", default=None,
                    help="re-run only the existing jsonl rows with this verdict (e.g. driver_fail after a "
                         "driver fix), merge them in and rebuild the summary")
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args(argv)
    specs = audit.load_specs(a.corpus)
    man = [json.loads(line) for line in open(os.path.join(a.corpus, "MANIFEST.jsonl")) if line.strip()]
    mig = [e for e in man if e.get("task_type") == "migrate_fix" and e["task_id"] in specs]
    keep = {}
    if a.rerun_verdict:
        with open(OUT_JSONL) as f:
            keep = {r["task_id"]: r for r in (json.loads(x) for x in f if x.strip())}
        mig = [e for e in mig if (keep.get(e["task_id"]) or {}).get("verdict") == a.rerun_verdict]
    auto_path = os.path.join(a.corpus, "audit", "buckets", "auto.txt")
    auto = {x.strip() for x in open(auto_path)} if os.path.exists(auto_path) else set()
    with tkc_pin.pin().install(sys.modules[__name__], dc, rv) as pinned:
        jobs = [(e["task_id"], os.path.join(a.corpus, e["path"]), specs[e["task_id"]], a.corpus) for e in mig]
        with ProcessPoolExecutor(a.jobs) as ex:
            fresh = list(ex.map(census_one, jobs, chunksize=8))
        stamp = pinned.stamp()
    for r in fresh:
        r["tkc_bin_sha"] = stamp["tkc_bin_sha"]
        if a.rerun_verdict:
            r["rerun_from"] = a.rerun_verdict
    keep.update({r["task_id"]: r for r in fresh})
    rows = sorted(keep.values(), key=lambda r: r["task_id"])
    summary = summarise(rows, stamp, auto)
    if a.rerun_verdict:
        summary["rerun"] = {"verdict": a.rerun_verdict, "records": len(fresh),
                            "now": dict(collections.Counter(r["verdict"] for r in fresh))}
    with open(OUT_JSONL, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    print(json.dumps({k: summary[k] for k in ("all", "auto_bucket_131_14", "compile_only", "lock_fail_genuine")
                      + (("rerun",) if "rerun" in summary else ())}, indent=1))
    return summary


if __name__ == "__main__":
    main()
