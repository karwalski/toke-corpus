#!/usr/bin/env python3
"""Epic 129 audit executor — independent re-audit of banked corpus records.

Subcommands:
  run     - sweep records: compile, build, execute tests (incl. driver-main
            synthesis for single_function), structural metrics, idiom score.
            Parallel (multiprocessing), resumable via an append-only audit
            ledger (same idempotency pattern as the shard ledger).
  report  - aggregate an audit ledger into failure buckets + metric
            distributions; writes audit_summary.json

Gates recorded per record (both old and tightened verdicts, nothing dropped):
  compile   tkc --check rc==0
  build     tkc -o rc==0 (when tests will run)
  tests_old the original run_shard.py gate (line match only)
  tests_new tightened: line match AND exit==0 AND no extra stdout lines
Structural/idiom flags are advisory here; thresholds live in quality_rubric.md
and are finalised from this sweep's distributions. Specs whose description
mandates a style (e.g. "Use nested if/el blocks") carry style_mandate so the
compaction pass can exempt them.
"""
import argparse, glob, json, multiprocessing, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import driver as drv                              # noqa: E402
import idiom_judge                                # noqa: E402
import metrics                                    # noqa: E402
from run_shard import render_expected             # noqa: E402
from validate import tkc_check                    # noqa: E402

TKC = "/Users/matthew.watt/tk/toke/tkc"
_MANDATE = re.compile(r"Variant \d+:\s*(.+)$")


def style_mandate(spec):
    m = _MANDATE.search(spec.get("description", "") or "")
    return m.group(1).strip() if m else None


_BASE = re.compile(r"^(A-[A-Z]+-\d+)v\d+$")


def load_specs(corpus_dir):
    specs = {}
    for sh in sorted(glob.glob(os.path.join(corpus_dir, "shards", "shard_*.jsonl"))):
        for line in open(sh):
            s = json.loads(line)
            specs[s["task_id"]] = s
    # 129.7: inject execution-verified A-category test cases (audit/a_tests/,
    # authored per base task) into variants whose input types match the base's
    a_dir = os.path.join(corpus_dir, "audit", "a_tests")
    if os.path.isdir(a_dir):
        banked = {}
        for fn in os.listdir(a_dir):
            if fn.endswith(".json"):
                banked[fn[:-5]] = json.load(open(os.path.join(a_dir, fn)))
        for tid, s in specs.items():
            # single_function only: the driver synthesizes their main; existing
            # A full_program mains never exercised these inputs (regeneration
            # scope — 129.8), so injecting tests there would just mass-fail
            if s.get("test_cases") or s.get("task_type") != "single_function":
                continue
            m = _BASE.match(tid)
            t = banked.get(m.group(1)) if m else None
            if t and (s.get("input_types_v03") or s.get("input_types")) == t["input_types"]:
                s["test_cases"] = t["test_cases"]
                s["_tests_from"] = "a_tests"
    return specs


def _run_bin(binpath, timeout=15):
    try:
        return subprocess.run([binpath], capture_output=True, text=True,
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _compare(got_lines, want_lines):
    """(old_gate_match, extra_lines). Old gate = per-line match ignoring extras."""
    if len(got_lines) < len(want_lines):
        return False, 0
    for g, w in zip(got_lines, want_lines):
        if g.strip() == w.strip():
            continue
        try:
            if abs(float(g) - float(w)) < 1e-6:
                continue
        except ValueError:
            pass
        return False, len(got_lines) - len(want_lines)
    return True, len(got_lines) - len(want_lines)


def _exec_tests(src, want_lines, tmpdir, tag):
    """Compile+build+run src, compare stdout to want_lines. Returns gate dict."""
    tkpath = os.path.join(tmpdir, tag + ".tk")
    binpath = tkpath + ".bin"
    with open(tkpath, "w") as f:
        f.write(src)
    try:
        b = subprocess.run([TKC, tkpath, "-o", binpath], capture_output=True,
                           text=True, errors="replace", timeout=90)
        if b.returncode != 0:
            return {"build": False, "tests_old": False, "tests_new": False,
                    "reason": "build failed"}
        r = _run_bin(binpath)
        if r is None:
            return {"build": True, "tests_old": False, "tests_new": False,
                    "reason": "timeout"}
        got = r.stdout.splitlines()
        old_ok, extra = _compare(got, want_lines)
        new_ok = old_ok and r.returncode == 0 and extra == 0
        reason = None
        if not old_ok:
            reason = "output mismatch"
        elif not new_ok:
            reason = ("exit=" + str(r.returncode) if r.returncode != 0 else "") + \
                     (" extra_lines=" + str(extra) if extra else "")
        return {"build": True, "tests_old": old_ok, "tests_new": new_ok,
                "exit": r.returncode, "extra_lines": extra,
                "reason": reason.strip() if reason else None}
    except subprocess.TimeoutExpired:
        return {"build": False, "tests_old": False, "tests_new": False,
                "reason": "build timeout"}
    finally:
        for p in (tkpath, binpath):
            if os.path.exists(p):
                os.unlink(p)


def audit_one(job):
    """One record end-to-end. Returns the audit row (never raises)."""
    task_id, rec_path, spec, tmpdir = job
    row = {"task_id": task_id, "ts": int(time.time())}
    try:
        rec = json.load(open(rec_path))
        src = rec["tk_source"]
        row.update({"category": spec.get("category"), "difficulty": spec.get("difficulty"),
                    "task_type": spec.get("task_type"),
                    "sha256": rec.get("regen", {}).get("source_sha256"),
                    "style_mandate": style_mandate(spec)})
        tkpath = os.path.join(tmpdir, task_id + ".tk")
        with open(tkpath, "w") as f:
            f.write(src)
        try:
            rc, codes, _ = tkc_check(tkpath)
            row["compile"] = rc == 0
            row["error_codes"] = codes if rc != 0 else []
            if rc == 0:
                m = metrics.analyse(tkpath)
                if m:
                    m.pop("functions", None)
                    m.pop("lint", None)
                row["metrics"] = m
                score, notes = idiom_judge.score(src)
                row["idiom"] = round(score, 2)
                row["idiom_notes"] = notes
        finally:
            if os.path.exists(tkpath):
                os.unlink(tkpath)
        # execution
        ttype = spec.get("task_type")
        tcs = spec.get("test_cases") or []
        if not row["compile"]:
            row["executed"] = False
        elif ttype == "full_program" and tcs:
            want = [render_expected(tc.get("expected")) for tc in tcs]
            row.update(_exec_tests(src, want, tmpdir, task_id + ".fp"))
            row["executed"] = True
            row["executed_via"] = "main"
        elif ttype == "single_function" and tcs:
            dsrc, err = drv.append_main(spec, src)
            if err:
                row["executed"] = False
                row["driver_fail"] = err
            else:
                # a build failure here is the RECORD's codegen failing (--check
                # passed but e.g. 127.6 E9003) — the driver adds only
                # probe-verified constructs, so it stays a record-level gate
                g = _exec_tests(dsrc, drv.expected_lines(spec), tmpdir, task_id + ".drv")
                row.update(g)
                row["executed"] = True
                row["executed_via"] = "driver"
        else:
            row["executed"] = False
    except Exception as e:  # defensive: one bad record must not kill the sweep
        row["audit_error"] = f"{type(e).__name__}: {e}"
    return row


def cmd_run(args):
    corpus = args.corpus_dir
    audit_dir = args.audit_dir
    os.makedirs(audit_dir, exist_ok=True)
    tmpdir = os.path.join(audit_dir, "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    ledger_path = os.path.join(audit_dir, "audit_corpus.jsonl")
    done = set()
    if os.path.exists(ledger_path):
        for line in open(ledger_path):
            done.add(json.loads(line)["task_id"])
    specs = load_specs(corpus)
    jobs = []
    for line in open(os.path.join(corpus, "MANIFEST.jsonl")):
        e = json.loads(line)
        tid = e["task_id"]
        if tid in done or tid not in specs:
            continue
        if args.category and e.get("category") != args.category:
            continue
        rec_path = os.path.join(corpus, e["category"], tid + ".json")
        if os.path.exists(rec_path):
            jobs.append((tid, rec_path, specs[tid], tmpdir))
        done.add(tid)  # MANIFEST can hold dup rows for retried tasks
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"auditing {len(jobs)} records ({len(done) - len(jobs)} already done), "
          f"{args.workers} workers", file=sys.stderr)
    n = ok_new = 0
    with multiprocessing.Pool(args.workers) as pool, open(ledger_path, "a") as led:
        for row in pool.imap_unordered(audit_one, jobs, chunksize=8):
            led.write(json.dumps(row) + "\n")
            led.flush()
            n += 1
            if row.get("compile") and (not row.get("executed") or row.get("tests_new")):
                ok_new += 1
            if n % 500 == 0:
                print(f"  {n}/{len(jobs)} ({ok_new} clean)", file=sys.stderr)
    print(json.dumps({"audited": n, "ledger": ledger_path}))


def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(p / 100 * len(sorted_vals)))]


def cmd_report(args):
    from collections import Counter, defaultdict
    rows = {}
    for line in open(os.path.join(args.audit_dir, "audit_corpus.jsonl")):
        r = json.loads(line)
        rows[r["task_id"]] = r          # last write wins
    rows = list(rows.values())
    buckets = Counter()
    sha = Counter()
    dist = defaultdict(list)
    by_cat = defaultdict(Counter)
    for r in rows:
        cat = r.get("category") or "?"
        if r.get("audit_error"):
            b = "audit_error"
        elif not r.get("compile"):
            b = "compile_fail"
        elif r.get("driver_fail"):
            b = "driver_fail"
        elif not r.get("executed"):
            b = "not_executable"
        elif not r.get("build"):
            b = "build_fail"
        elif not r.get("tests_old"):
            b = "test_fail"
        elif not r.get("tests_new"):
            b = "new_gate_only"
        else:
            b = "pass"
        buckets[b] += 1
        by_cat[cat][b] += 1
        if r.get("sha256"):
            sha[r["sha256"]] += 1
        m = r.get("metrics") or {}
        for k in ("max_depth", "max_func_bytes", "min_bytes", "func_count", "lint_warnings"):
            if m.get(k) is not None:
                dist[k].append(m[k])
        if r.get("idiom") is not None:
            dist["idiom"].append(r["idiom"])
    summary = {"total": len(rows), "buckets": dict(buckets),
               "dup_sources": sum(c - 1 for c in sha.values() if c > 1),
               "idiom_below_floor": sum(1 for v in dist["idiom"] if v < idiom_judge.IDIOM_FLOOR),
               "style_mandated": sum(1 for r in rows if r.get("style_mandate")),
               "distributions": {}, "by_category": {k: dict(v) for k, v in sorted(by_cat.items())}}
    for k, vals in dist.items():
        vals.sort()
        summary["distributions"][k] = {p: _pct(vals, p) for p in (50, 90, 95, 99)}
    out = os.path.join(args.audit_dir, "audit_summary.json")
    with open(out, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--corpus-dir", default="/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04")
    ap.add_argument("--audit-dir", default="/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04/audit")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--category")
    args = ap.parse_args()
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
