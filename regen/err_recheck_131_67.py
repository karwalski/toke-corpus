#!/usr/bin/env python3
"""131.67 — re-verify the A-ERR category on the FIXED compiler (127.56/127.58).

Why. Until toke commit f55354c, a `T!$err` function written in the
**variant style** (`t=$e{$bad:$str}` returned as `<$bad("x")`) never set the
thread-local `@tk_current_error` flag on its error exits, so every caller's
`mt` took the `$ok` arm — at every `-O`.  1,521 of the 1,546 A-ERR records
declare variant-style error types, so essentially the whole category was
audited against a compiler that took the wrong branch.  A record that
"passed" therefore proves nothing: either its tests never reached the error
path, or the wrong branch happened to print what was expected.

What this does.  A/B differential over every A-ERR record (and any record
outside A-ERR whose spec return is an error union — currently none):

  * pass NEW — audit.audit_one on the fixed compiler (pinned, 131.39)
  * pass OLD — audit.audit_one on a pre-fix compiler built from f55354c^
                (a private worktree; --old-tkc points at its binary)

Both passes use today's machinery — the 131.44 `$err` expectation rendering
in validate.render_expected and the 131.40 driver fixes — so the ONLY thing
that differs between them is the compiler.  Records with no test cases
cannot be given a pass/fail at all (they were never verified); for those the
run still builds and executes the program under both compilers and records
whether the observable output changed, which is what tells us how much of
the category the bug actually touched.

A third pass re-runs every untested record under the FIXED compiler a second
time: a record whose two same-compiler runs disagree prints something
run-dependent (an unbound err payload prints as a pointer), so an OLD/NEW
difference proves nothing about it.  Those are reported separately rather
than counted as behaviour the fix changed.

Classification (see `classify`):
  pass_stable / now_fails / now_passes / fail_stable   (records with tests)
  and, for a record that still passes, whether its tests ever reached the
  error path at all (`never_reached_err_path`) or the fix left the output
  untouched because the record used a form that always worked.

Reads records; BANKS NOTHING (measurement story).  Writes
  corpus/regen_v04/audit/err_recheck_131.67.jsonl        one row per record
  corpus/regen_v04/audit/pattern_sweep.131.67.patch.jsonl  131.13 sweep schema
  regen/freeze/err_recheck_131.67.json                   tracked summary
  regen/ERR_RECHECK_131.67.md                            tracked note

Applying the patch (pattern_common.load_bucket: a later line for the same
task_id wins, so plain concatenation is the merge):
  cat corpus/regen_v04/audit/pattern_sweep.jsonl \\
      corpus/regen_v04/audit/pattern_sweep.131.42.patch.jsonl \\
      corpus/regen_v04/audit/pattern_sweep.131.67.patch.jsonl \\
      > corpus/regen_v04/audit/pattern_sweep.merged.jsonl
  python3 regen/pattern_prep.py --bucket corpus/regen_v04/audit/pattern_sweep.merged.jsonl
"""
import argparse, json, multiprocessing, os, re, subprocess, sys, time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                    # noqa: E402
import driver as drv                            # noqa: E402
import idiom_judge, metrics, validate, tkc_pin  # noqa: E402

STORY = "131.67"
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
OUT_JSONL = os.path.join(CORPUS, "audit", f"err_recheck_{STORY}.jsonl")
OUT_PATCH = os.path.join(CORPUS, "audit", f"pattern_sweep.{STORY}.patch.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", f"err_recheck_{STORY}.json")
SWEEP = os.path.join(CORPUS, "audit", "pattern_sweep.jsonl")
LEDGER = os.path.join(CORPUS, "audit", "audit_corpus.jsonl")
ERRCHECK = os.path.join(CORPUS, "audit", "err_union_check.jsonl")
DEFAULT_OLD_TKC = "/tmp/toke_prefix_131_67/tkc"   # f55354c^ worktree build
PATCH_PATTERNS = ("err-propagate", "err-default")
PATCH_RULE = "err-arm-unverified"
FIX_COMMIT = "f55354c"


# ------------------------------------------------------ execution capture ---
def _exec_tests_cap(src, want_lines, tmpdir, tag):
    """audit._exec_tests, plus the observed stdout/exit (the differential
    needs the bytes, not just the gate).  Gate logic is audit's own
    (_compare / _run_bin) so the two cannot drift on the verdict."""
    tkpath = os.path.join(tmpdir, tag + ".tk")
    binpath = tkpath + ".bin"
    with open(tkpath, "w") as f:
        f.write(src)
    base = {"want": list(want_lines)}
    try:
        b = subprocess.run([str(audit.TKC), tkpath, "-o", binpath], capture_output=True,
                           text=True, errors="replace", timeout=90)
        if b.returncode != 0:
            return {**base, "build": False, "tests_old": False, "tests_new": False,
                    "reason": "build failed", "got": None, "exit": None}
        r = audit._run_bin(binpath)
        if r is None:
            return {**base, "build": True, "tests_old": False, "tests_new": False,
                    "reason": "timeout", "got": None, "exit": None}
        got = r.stdout.splitlines()
        old_ok, extra = audit._compare(got, want_lines)
        new_ok = old_ok and r.returncode == 0 and extra == 0
        reason = None
        if not old_ok:
            reason = "output mismatch"
        elif not new_ok:
            reason = ("exit=" + str(r.returncode) if r.returncode != 0 else "") + \
                     (" extra_lines=" + str(extra) if extra else "")
        return {**base, "build": True, "tests_old": old_ok, "tests_new": new_ok,
                "exit": r.returncode, "extra_lines": extra, "got": got,
                "reason": reason.strip() if reason else None}
    except subprocess.TimeoutExpired:
        return {**base, "build": False, "tests_old": False, "tests_new": False,
                "reason": "build timeout", "got": None, "exit": None}
    finally:
        for p in (tkpath, binpath):
            if os.path.exists(p):
                os.unlink(p)


audit._exec_tests = _exec_tests_cap      # audit_one resolves it module-globally


def _bare_run(src, tmpdir, tag):
    """Build and run a record that has no test cases, purely to observe its
    output under each compiler.  No verdict is implied — a record with no
    tests has never been verified either way."""
    tkpath = os.path.join(tmpdir, tag + ".bare.tk")
    binpath = tkpath + ".bin"
    with open(tkpath, "w") as f:
        f.write(src)
    try:
        b = subprocess.run([str(audit.TKC), tkpath, "-o", binpath], capture_output=True,
                           text=True, errors="replace", timeout=90)
        if b.returncode != 0:
            return {"build": False}
        r = audit._run_bin(binpath)
        if r is None:
            return {"build": True, "timeout": True}
        return {"build": True, "exit": r.returncode, "got": r.stdout.splitlines()}
    except subprocess.TimeoutExpired:
        return {"build": False, "timeout": True}
    finally:
        for p in (tkpath, binpath):
            if os.path.exists(p):
                os.unlink(p)


def bare_job(job):
    """Determinism control: a second bare build+run of the same record on the
    same compiler.  Two disagreeing runs mean the output is run-dependent."""
    tid, rec_path, _spec, tmpdir = job
    try:
        rec = json.load(open(rec_path))
        return {"task_id": tid, "bare": _bare_run(rec["tk_source"], tmpdir, tid + ".c2")}
    except Exception as e:
        return {"task_id": tid, "bare": {"error": f"{type(e).__name__}: {e}"}}


def job_one(job):
    """audit.audit_one, plus a bare build+run for records it cannot execute."""
    row = audit.audit_one(job)
    if row.get("compile") and not row.get("executed"):
        try:
            rec = json.load(open(job[1]))
            row["bare"] = _bare_run(rec["tk_source"], job[3], job[0])
        except Exception as e:
            row["bare"] = {"error": f"{type(e).__name__}: {e}"}
    return row


# --------------------------------------------------------------- statics ---
_TDECL = re.compile(r"t=\$([a-z0-9]+)\{([^}]*)\}")
# the two error-return forms.  127.56 broke ONLY the constructor call form;
# the literal form built the tagged box correctly all along.
_CTOR = re.compile(r"<\s*\$[a-z][a-z0-9]*\(")      # `<$notfound("x")`  — was broken
_LIT = re.compile(r"<\s*\$[a-z][a-z0-9]*\{")       # `<$e{$notfound:"x"}` — always worked
_PROP = re.compile(r"!\$[a-z]")                    # `expr!$e`          — 127.58


def err_return_forms(src):
    """Which error-return forms the record's source uses."""
    return ([f for f, rx in (("ctor", _CTOR), ("literal", _LIT), ("propagate", _PROP))
             if rx.search(src)] or ["none"])


def err_style(src, ret):
    """`variant` when the record's error type declares `$tag:T` members
    (the form 127.56 broke), `struct` when it declares plain `name:T`
    fields (always worked), `none` when no error type is declared."""
    if not ret or "!" not in ret:
        names = [m.group(1) for m in _TDECL.finditer(src)]
        if not names:
            return "none"
        body = _TDECL.search(src).group(2)
        return "variant" if "$" in body else "struct"
    errtype = ret.split("!", 1)[1].lstrip("$").lower()
    for m in _TDECL.finditer(src):
        if m.group(1).lower() == errtype:
            return "variant" if "$" in m.group(2) else "struct"
    return "none"


def expects_err(spec):
    """True when at least one banked test case expects an err result — i.e.
    the test actually reaches the error path."""
    for tc in spec.get("test_cases") or []:
        if drv.err_name(tc.get("expected")) is not None:
            return True
    return False


def gates(row):
    """Compact gate view of an audit row."""
    return {"compile": row.get("compile"), "executed": bool(row.get("executed")),
            "build": row.get("build"), "tests_old": row.get("tests_old"),
            "tests_new": row.get("tests_new"), "reason": row.get("reason"),
            "driver_fail": row.get("driver_fail"), "exit": row.get("exit"),
            "got": row.get("got"), "want": row.get("want"),
            "bare": row.get("bare")}


def _out(g):
    """Observable output of a run (test run or bare run), or None."""
    if g.get("got") is not None:
        return (tuple(g["got"]), g.get("exit"))
    b = g.get("bare") or {}
    if b.get("got") is not None:
        return (tuple(b["got"]), b.get("exit"))
    return None


def classify(old, new, has_tests, err_expect, deterministic=True):
    """(verdict, why) for one record from its OLD/NEW gate views.
    `deterministic` is False when two runs of the SAME (fixed) compiler
    disagree — then an OLD/NEW difference is not attributable to the fix."""
    if not new["compile"]:
        return "compile_fail", "record does not compile on the fixed compiler"
    o, n = _out(old), _out(new)
    changed = (o is not None and n is not None and o != n)
    if not has_tests:
        if new.get("driver_fail"):
            return "unverified_no_driver", new["driver_fail"]
        if deterministic is False:
            return "unverified_nondeterministic", ("no test cases; two runs of the fixed "
                                                   "compiler disagree (run-dependent output)")
        if changed:
            return "unverified_behaviour_changed", "no test cases; output changed under the fix"
        return "unverified_behaviour_same", "no test cases; output unchanged under the fix"
    if new.get("driver_fail") or old.get("driver_fail"):
        return "driver_fail", new.get("driver_fail") or old.get("driver_fail")
    op, np_ = bool(old.get("tests_new")), bool(new.get("tests_new"))
    if op and np_:
        if changed:
            return "pass_stable", "output changed under the fix but still matches"
        if not err_expect:
            return "pass_never_reached_err", "passes, but no test case expects an err result"
        return "pass_err_verified", "passes with an err-expecting test case, output unaffected"
    if op and not np_:
        return "now_fails", ("wrong branch coincidentally matched: "
                             + (new.get("reason") or "?"))
    if not op and np_:
        return "now_passes", "the fix repaired this record"
    return "fail_stable", new.get("reason") or "?"


# ---------------------------------------------------------------- routing ---
def patch_row(sweep_row, tid, verdict, ts=None):
    row = dict(sweep_row or {"task_id": tid, "category": "-".join(tid.split("-")[:2]),
                             "task_type": "single_function", "source": "regen"})
    row.update({
        "task_id": tid, "ts": int(ts or time.time()), "story": STORY,
        "violations": [{"rule": PATCH_RULE, "severity": "error", "pattern_id": p}
                       for p in PATCH_PATTERNS],
        "pattern_ids": list(PATCH_PATTERNS),
        "bucket": "AGENT",
        "bucket_reason": (f"{STORY} {verdict}: the record fails its banked test cases on the "
                          f"{FIX_COMMIT} compiler AND on the pre-fix compiler — its 131.42 "
                          f"'correct' classification was a return-type verdict, never an "
                          f"execution one (regen/freeze/err_recheck_{STORY}.json)"),
        "would_bucket": (sweep_row or {}).get("bucket"),
        "patch_of": "131.13 pattern_sweep row" if sweep_row else None,
    })
    return row


# ------------------------------------------------------------------- run ---
def scope(specs, man):
    """A-ERR records, plus any record whose spec return is an error union."""
    ids = []
    for tid, cat in man.items():
        s = specs.get(tid)
        if not s:
            continue
        if cat == "A-ERR" or validate.is_error_union(validate.spec_return_type(s) or ""):
            ids.append(tid)
    return sorted(ids)


def sweep_pass(jobs, pinned, workers, label, fn=job_one):
    print(f"  pass {label}: tkc {pinned.version} sha {pinned.sha256[:12]} "
          f"({pinned.resolved})", file=sys.stderr)
    rows, t0 = {}, time.time()
    with multiprocessing.Pool(workers) as pool:
        for row in pool.imap_unordered(fn, jobs, chunksize=4):
            rows[row["task_id"]] = row
            if len(rows) % 250 == 0:
                print(f"    {len(rows)}/{len(jobs)}", file=sys.stderr)
    print(f"  pass {label}: {len(rows)} rows in {time.time() - t0:.0f}s", file=sys.stderr)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--old-tkc", default=DEFAULT_OLD_TKC,
                    help="pre-fix binary (build f55354c^ in a worktree); "
                         "omit with --no-old to skip the differential")
    ap.add_argument("--no-old", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reuse", action="store_true",
                    help="reuse the OLD/NEW gate rows already in the jsonl (they carry "
                         "the observed stdout) and re-run only the determinism pass")
    a = ap.parse_args(argv)

    specs = audit.load_specs(CORPUS)
    man = {}
    for line in open(os.path.join(CORPUS, "MANIFEST.jsonl")):
        e = json.loads(line)
        man[e["task_id"]] = e["category"]
    ids = scope(specs, man)
    if a.limit:
        ids = ids[:a.limit]
    tmpdir = os.path.join(CORPUS, "audit", "tmp_131.67")
    os.makedirs(tmpdir, exist_ok=True)
    jobs = [(t, os.path.join(CORPUS, man[t], t + ".json"), specs[t], tmpdir) for t in ids]
    print(f"{len(jobs)} records in scope", file=sys.stderr)

    prev = {}
    if a.reuse:
        for line in open(OUT_JSONL):
            r = json.loads(line)
            prev[r["task_id"]] = r
        stamp_new = json.load(open(OUT_SUMMARY))["tkc_new"]
        stamp_old = json.load(open(OUT_SUMMARY))["tkc_old"]
        new_rows = {t: dict(r["new"], task_id=t) for t, r in prev.items()}
        old_rows = {t: dict(r["old"], task_id=t) for t, r in prev.items()}
    else:
        # --- NEW (fixed) compiler ----------------------------------------
        os.environ.pop(tkc_pin.ENV_PIN, None)
        pin_new = tkc_pin.pin()
        pin_new.install(audit, validate, metrics, idiom_judge)
        with pin_new:
            new_rows = sweep_pass(jobs, pin_new, a.workers, "NEW")
        stamp_new = pin_new.stamp()

        # --- OLD (pre-fix) compiler --------------------------------------
        old_rows, stamp_old = {}, None
        if not a.no_old:
            os.environ.pop(tkc_pin.ENV_PIN, None)
            pin_old = tkc_pin.pin(toke_repo=os.path.dirname(a.old_tkc), tkc=a.old_tkc)
            if pin_old.sha256 == stamp_new["tkc_bin_sha"]:
                raise SystemExit("--old-tkc is the same binary as the pinned tkc; "
                                 "build f55354c^ in a worktree first")
            pin_old.install(audit, validate, metrics, idiom_judge)
            with pin_old:
                old_rows = sweep_pass(jobs, pin_old, a.workers, "OLD")
            stamp_old = pin_old.stamp()
            os.environ.pop(tkc_pin.ENV_PIN, None)

    # --- determinism control: a SECOND run on the fixed compiler ----------
    # A record whose two same-compiler runs disagree prints something
    # run-dependent; an OLD/NEW difference then says nothing about the fix.
    determ = {t: r["deterministic"] for t, r in prev.items()
              if r.get("deterministic") is not None}
    cand = [j for j in jobs
            if (new_rows[j[0]].get("bare") or {}).get("got") is not None
            and j[0] not in determ]
    if cand:
        os.environ.pop(tkc_pin.ENV_PIN, None)
        pin_c = tkc_pin.pin()
        pin_c.install(audit, validate, metrics, idiom_judge)
        if pin_c.sha256 != stamp_new["tkc_bin_sha"]:
            raise SystemExit("the tkc binary changed since the NEW pass — rerun from scratch")
        with pin_c:
            conf = sweep_pass(cand, pin_c, a.workers, "CONFIRM(new x2)", fn=bare_job)
        os.environ.pop(tkc_pin.ENV_PIN, None)
        for tid, r in conf.items():
            b1 = new_rows[tid]["bare"]
            b2 = r["bare"] or {}
            determ[tid] = (b2.get("got") is not None
                           and (b1.get("got"), b1.get("exit")) == (b2.get("got"), b2.get("exit")))

    # --- prior (banked) verdicts + 131.42 buckets -------------------------
    banked = {}
    for line in open(LEDGER):
        r = json.loads(line)
        if r["task_id"] in specs:
            banked[r["task_id"]] = r
    errv = {}
    for line in open(ERRCHECK):
        r = json.loads(line)
        errv[r["task_id"]] = r["verdict"]
    sweep = {}
    for line in open(SWEEP):
        r = json.loads(line)
        sweep[r["task_id"]] = r

    # --- rows -------------------------------------------------------------
    out, ts = [], int(time.time())
    for tid in ids:
        spec = specs[tid]
        src = json.load(open(os.path.join(CORPUS, man[tid], tid + ".json")))["tk_source"]
        ret = validate.spec_return_type(spec) or ""
        gn = gates(new_rows[tid])
        go = gates(old_rows[tid]) if old_rows else dict(gn, compile=None)
        has_tests = bool(spec.get("test_cases"))
        ee = expects_err(spec)
        det = determ.get(tid)
        verdict, why = (classify(go, gn, has_tests, ee, det if det is not None else True)
                        if old_rows else ("no_differential", ""))
        b = banked.get(tid, {})
        rt_ok, rt_detail, _, rt_declared = validate.return_type_conforms(spec, src)
        out.append({
            "task_id": tid, "category": man[tid], "task_type": spec.get("task_type"),
            "spec_return": ret, "err_style": err_style(src, ret),
            "err_return_forms": err_return_forms(src),
            "bucket_131_42": errv.get(tid, "not_checked"),
            "group": "gamed" if errv.get(tid) == "gamed_err_marker" else "non_gamed",
            "has_tests": has_tests, "tests_from": spec.get("_tests_from"),
            "expects_err_case": ee,
            "banked": {"compile": b.get("compile"), "executed": bool(b.get("executed")),
                       "tests_new": b.get("tests_new"), "driver_fail": b.get("driver_fail")},
            "old": go, "new": gn, "deterministic": det,
            "output_changed": (_out(go) != _out(gn)) if (_out(go) is not None and _out(gn) is not None) else None,
            "verdict": verdict, "why": why,
            "return_type_gate_ok": rt_ok, "return_type_detail": rt_detail,
            "declared_return": rt_declared,
            "tkc_new_sha": stamp_new["tkc_bin_sha"],
            "tkc_old_sha": (stamp_old or {}).get("tkc_bin_sha"),
        })
    with open(OUT_JSONL, "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")

    # --- bucket patch: non-gamed records that fail on the fixed compiler ---
    REBUCKET = ("now_fails", "fail_stable", "driver_fail", "compile_fail")
    patch_ids = [r["task_id"] for r in out
                 if r["group"] == "non_gamed" and r["verdict"] in REBUCKET]
    with open(OUT_PATCH, "w") as f:
        for r in out:
            if r["task_id"] in set(patch_ids):
                f.write(json.dumps(patch_row(sweep.get(r["task_id"]), r["task_id"],
                                             r["verdict"], ts)) + "\n")

    # --- summary ----------------------------------------------------------
    def tally(rows):
        c = {"records": len(rows), "verdicts": dict(Counter(r["verdict"] for r in rows)),
             "err_style": dict(Counter(r["err_style"] for r in rows)),
             "err_return_forms": dict(Counter("+".join(r["err_return_forms"]) for r in rows)),
             "task_type": dict(Counter(r["task_type"] for r in rows)),
             "with_tests": sum(1 for r in rows if r["has_tests"]),
             "expects_err_case": sum(1 for r in rows if r["expects_err_case"]),
             "output_changed": sum(1 for r in rows if r["output_changed"]),
             "nondeterministic": sum(1 for r in rows if r.get("deterministic") is False),
             "old_pass": sum(1 for r in rows if r["old"].get("tests_new")),
             "new_pass": sum(1 for r in rows if r["new"].get("tests_new")),
             "banked_pass": sum(1 for r in rows if r["banked"].get("tests_new")),
             "banked_not_executed": sum(1 for r in rows if not r["banked"].get("executed")),
             "return_type_gate_fail": sum(1 for r in rows if not r["return_type_gate_ok"]),
             }
        c["fail_reasons"] = dict(Counter(
            r["new"].get("reason") or "?" for r in rows
            if r["verdict"] in ("now_fails", "fail_stable")).most_common(12))
        return c

    gamed = [r for r in out if r["group"] == "gamed"]
    nong = [r for r in out if r["group"] == "non_gamed"]
    by_base = defaultdict(Counter)
    for r in nong:
        by_base[re.sub(r"v\d+$", "", r["task_id"])][r["verdict"]] += 1
    summary = {
        "story": STORY, "date": time.strftime("%Y-%m-%d"),
        "fix_commit": FIX_COMMIT,
        "tkc_new": stamp_new, "tkc_old": stamp_old,
        "scope": "every A-ERR record + any record whose spec return is an error union",
        "records": len(out),
        "all": tally(out), "non_gamed": tally(nong), "gamed": tally(gamed),
        "non_gamed_by_base": {k: dict(v) for k, v in sorted(by_base.items())
                              if set(v) - {"pass_err_verified", "pass_never_reached_err",
                                           "unverified_behaviour_same"}},
        "patch": {"path": os.path.relpath(OUT_PATCH, os.path.dirname(HERE)),
                  "rows": len(patch_ids), "rule": PATCH_RULE,
                  "with_sweep_row": sum(1 for t in patch_ids if t in sweep)},
        "jsonl": os.path.relpath(OUT_JSONL, os.path.dirname(HERE)),
        "banks_nothing": True,
    }
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({k: summary[k] for k in ("records", "all", "non_gamed", "gamed", "patch")},
                     indent=1))


if __name__ == "__main__":
    main()
