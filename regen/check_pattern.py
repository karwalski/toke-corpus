#!/usr/bin/env python3
"""Worker self-check for the 131.15 agent pattern-rewrite wave.

Usage: check_pattern.py --task-id <id> [--workdir .../work/pattern_131]
Reads gen/pat_<task_id>.tk and runs the worker-visible gates on it:
  shape (same module shape as the current source) -> tkc --check -> build +
  run the test cases (single_function via the driver main, full_program one
  line per case, stdin_program one run per case with the input on stdin) ->
  idiom floor + structure -> tkc --lint --diag-json pattern rules: 0
  errors/warnings net of the task's exemptions (hints listed, not gated) ->
  non-pattern lint warnings <= before -> tkc --min bytes <= before ->
  proxy tokens <= before.
Prints PASS or FAIL <reason> plus the diagnostics the worker needs to act on.
Exit 0/1. Writes nothing outside a temp dir. The main-thread bank
(bank_pattern.py) re-runs all of this plus the differential + perf gates —
workers are never trusted for acceptance.
"""
import argparse, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pattern_common as pc                       # noqa: E402
from validate import tkc_check                    # noqa: E402

WORKER_GATES = ("shape", "compile", "signature", "build", "tests", "idiom", "structure",
                "pattern", "lint_other", "min_bytes", "proxy_tokens")


def run_worker_gates(paths, tid, tmp):
    """The static + test + size gates over gen/pat_<tid>.tk. Returns
    (res, before, spec, meta) — res is None when spec/candidate are missing."""
    spec_path = paths.sub("specs", tid + ".json")
    meta_path = paths.sub("meta", tid + ".json")
    cand_path = paths.sub("gen", pc.GEN_PREFIX + tid + ".tk")
    if not os.path.exists(spec_path):
        return None, None, None, f"no spec at {spec_path} (run pattern_prep.py first)"
    if not os.path.exists(cand_path):
        return None, None, None, f"no candidate at {cand_path}"
    spec = json.load(open(spec_path))
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    cand = open(cand_path, encoding="utf-8", errors="replace").read()
    before = meta.get("before")
    if before is None:                            # prep without meta: measure the record live
        rec_path = paths.record(spec.get("category"), tid)
        if os.path.exists(rec_path):
            before = pc.before_metrics(json.load(open(rec_path)), rec_path, tmp)
            before.pop("struct", None)
    res = pc.static_and_test_gates(spec, cand, tmp)
    res["candidate"] = cand
    if res["record"] is not None:
        pc.apply_exemptions(res, meta.get("exempt") or [])
        pc.lint_other_gate(res, before)
        pc.size_gates(res, before)
    elif res["gates"].get("compile") is False:
        raw, _ = pc.normalise_candidate(spec, cand)
        p = os.path.join(tmp, "diag.tk")
        with open(p, "w") as f:
            f.write(res["tk_source"] if res["tk_source"] else (raw or ""))
        _, _, diag = tkc_check(p)
        res["compile_diag"] = diag
    return res, before, spec, meta


def report_lines(res, before):
    """Printable report for a run_worker_gates result."""
    out = []
    failed = [k for k in WORKER_GATES if res["gates"].get(k) is False]
    ok = not failed
    b = before or {}
    if ok:
        out.append(f"PASS min_bytes={res.get('min_bytes')} (before {b.get('min_bytes')}) "
                   f"proxy={res.get('proxy_tokens')} (before {b.get('proxy_tokens')}) "
                   f"idiom={res.get('idiom')} depth={res.get('max_depth')}")
    else:
        out.append(f"FAIL {res.get('reason') or ', '.join(failed)}")
    out.append("gates: " + pc.summary_line(res))
    if res.get("compile_diag"):
        out.append("compiler: " + res["compile_diag"].strip()[:1500])
    rt = res.get("runtime")
    if rt and not rt.get("match", True):
        out.append(f"tests: {rt.get('reason')}" + (f" stdout={rt.get('stdout')!r}" if rt.get("stdout") else ""))
        for c in (rt.get("cases") or []):
            if not c.get("match"):
                out.append(f"  case {c.get('case')}: {c.get('reason')} stdout={c.get('stdout')!r}")
    for d in res.get("pattern_hits") or []:
        out.append(f"pattern {d.get('rule')} [{d.get('severity')}] line {d.get('line')}: MUST fix")
    for d in res.get("hints") or []:
        out.append(f"hint {d.get('rule')} line {d.get('line')}: fix unless the task mandates it")
    if res.get("lint_exempt"):
        out.append("exempt (task mandate / sweep): " + ", ".join(res["lint_exempt"]))
    if res["gates"].get("lint_other") is False:
        out.append(f"lint: {res.get('lint_other_warnings')} non-pattern warnings vs "
                   f"{b.get('lint_other_warnings')} before — do not add unused-let/unused-import etc.")
    if res["gates"].get("idiom") is False:
        out.append(f"idiom {res.get('idiom')} < {pc.idiom_judge.IDIOM_FLOOR}")
    if res["gates"].get("min_bytes") is False or res["gates"].get("proxy_tokens") is False:
        out.append(f"size: --min {res.get('min_bytes')} vs before {b.get('min_bytes')}; "
                   f"proxy {res.get('proxy_tokens')} vs before {b.get('proxy_tokens')} "
                   "— the rewrite must not grow")
    return ok, out


def check(paths, tid):
    """Returns (ok, lines) — lines is the printable report."""
    with tempfile.TemporaryDirectory(prefix="check_pattern_") as tmp:
        res, before, _spec, err = run_worker_gates(paths, tid, tmp)
        if res is None:
            return False, ["FAIL " + err]
        return report_lines(res, before)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--corpus", default=pc.CORPUS)
    args = ap.parse_args(argv)
    paths = pc.Paths(corpus=args.corpus, workdir=args.workdir)
    ok, lines = check(paths, args.task_id)
    print("\n".join(lines))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
