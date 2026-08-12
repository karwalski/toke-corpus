#!/usr/bin/env python3
"""Epic 129.3 — audit sweep over the library + example assets.

Assets:
  library   results/library/*.json manifests (all 1583 incl. gazeta): compile
            (toke --allow-all --out), run every test_case (stdin -> stdout
            exact after trailing-ws norm, fixtures materialised in a temp cwd),
            old gate (verify-one.py semantics: crash/output only) + tightened
            gate (exit==0, no extra trailing output beyond norm), plus
            structural metrics (tkc) + idiom score.
  orphans   solution.tk files not in any manifest (~597): compile + metrics +
            idiom only (no test metadata exists).
  examples  toke/examples/*.tk + results/library-examples/*/source.tk:
            compile + metrics + idiom.

Ledger: corpus/regen_v04/audit/audit_library.jsonl (append-only, resumable).
Report merged by audit_library.py report.
"""
import argparse, glob, json, multiprocessing, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import idiom_judge                                # noqa: E402
import metrics                                    # noqa: E402

ROOT = os.path.expanduser("~/tk/toke-test-programs")
TOKE = os.path.expanduser("~/tk/toke/toke")


def norm(s):
    return "\n".join(l.rstrip() for l in s.strip().splitlines())


def load_manifests():
    progs = []
    for cf in sorted(glob.glob(ROOT + "/results/library/*.json")):
        if cf.endswith("index.json"):
            continue
        d = json.load(open(cf))
        for p in d["programs"]:
            progs.append((d["category"], p))
    return progs


def static_checks(row, sol_path, src):
    m = metrics.analyse(sol_path)
    if m:
        m.pop("functions", None)
        m.pop("lint", None)
    row["metrics"] = m
    score, notes = idiom_judge.score(src)
    row["idiom"] = round(score, 2)
    row["idiom_notes"] = notes


def audit_library_one(job):
    cat, p = job
    pid = p["id"]
    row = {"id": pid, "asset": "library", "category": cat,
           "difficulty": p.get("difficulty"), "ts": int(time.time())}
    try:
        sol = f"{ROOT}/results/solutions/{cat}/{pid}/solution.tk"
        if not os.path.exists(sol):
            row["compile"] = False
            row["reason"] = "no solution.tk"
            return row
        src = open(sol, errors="replace").read()
        with tempfile.TemporaryDirectory() as td:
            b = td + "/b"
            r = subprocess.run([TOKE, sol, "--allow-all", "--out", b],
                               capture_output=True, text=True, errors="replace", timeout=90)
            row["compile"] = r.returncode == 0 and os.path.exists(b)
            if not row["compile"]:
                code = next((l.split('"error_code":"')[1].split('"')[0]
                             for l in (r.stdout + r.stderr).splitlines()
                             if '"error_code"' in l), "")
                row["error_codes"] = [code] if code else []
                return row
            static_checks(row, sol, src)
            tests_old = tests_new = True
            reason = None
            executed = False
            for i, tc in enumerate(p.get("test_cases") or []):
                executed = True
                cwd = os.path.join(td, f"t{i}")
                os.makedirs(cwd, exist_ok=True)
                fx = tc.get("fixtures") or {}
                for dd in fx.get("dirs", []):
                    os.makedirs(os.path.join(cwd, dd), exist_ok=True)
                for fpath, content in (fx.get("files") or {}).items():
                    ap = os.path.join(cwd, fpath)
                    os.makedirs(os.path.dirname(ap), exist_ok=True)
                    open(ap, "wb").write(content.encode("latin-1"))
                try:
                    rr = subprocess.run([b], input=tc.get("input", "") or "",
                                        capture_output=True, text=True,
                                        errors="replace", timeout=10, cwd=cwd)
                except subprocess.TimeoutExpired:
                    tests_old = tests_new = False
                    reason = f"timeout (test {i})"
                    break
                if rr.returncode < 0 or rr.returncode >= 128:
                    tests_old = tests_new = False
                    reason = f"crash sig {abs(rr.returncode)} (test {i})"
                    break
                if norm(rr.stdout) != norm(tc.get("expected_output", "")):
                    tests_old = tests_new = False
                    reason = f"wrong output (test {i})"
                    break
                if rr.returncode != 0:
                    tests_new = False
                    reason = reason or f"exit={rr.returncode} (test {i})"
            row["executed"] = executed
            if executed:
                row["tests_old"] = tests_old
                row["tests_new"] = tests_new
                row["reason"] = reason
    except Exception as e:
        row["audit_error"] = f"{type(e).__name__}: {e}"
    return row


def audit_static_one(job):
    asset, path = job
    row = {"id": path, "asset": asset, "ts": int(time.time())}
    try:
        src = open(path, errors="replace").read()
        r = subprocess.run([metrics.TKC, path, "--check"], capture_output=True,
                           text=True, errors="replace", timeout=30)
        row["compile"] = r.returncode == 0
        if row["compile"]:
            static_checks(row, path, src)
        row["executed"] = False
    except Exception as e:
        row["audit_error"] = f"{type(e).__name__}: {e}"
    return row


def cmd_run(args):
    os.makedirs(args.audit_dir, exist_ok=True)
    ledger_path = os.path.join(args.audit_dir, "audit_library.jsonl")
    done = set()
    if os.path.exists(ledger_path):
        for line in open(ledger_path):
            done.add(json.loads(line)["id"])
    manifests = load_manifests()
    manifest_ids = {p["id"] for _, p in manifests}
    lib_jobs = [(c, p) for c, p in manifests if p["id"] not in done]
    static_jobs = []
    for sol in glob.glob(ROOT + "/results/solutions/*/*/solution.tk"):
        pid = os.path.basename(os.path.dirname(sol))
        if pid not in manifest_ids and sol not in done:
            static_jobs.append(("orphan", sol))
    for ex in sorted(glob.glob(os.path.expanduser("~/tk/toke/examples/**/*.tk"),
                               recursive=True)):
        if ex not in done:
            static_jobs.append(("toke-example", ex))
    for ex in sorted(glob.glob(ROOT + "/results/library-examples/*/source.tk")):
        if ex not in done:
            static_jobs.append(("doc-example", ex))
    print(f"library={len(lib_jobs)} static={len(static_jobs)} "
          f"(skipping {len(done)} done)", file=sys.stderr)
    n = 0
    with multiprocessing.Pool(args.workers) as pool, open(ledger_path, "a") as led:
        for fn, jobs in ((audit_library_one, lib_jobs), (audit_static_one, static_jobs)):
            if args.limit:
                jobs = jobs[:args.limit]
            for row in pool.imap_unordered(fn, jobs, chunksize=4):
                led.write(json.dumps(row) + "\n")
                led.flush()
                n += 1
                if n % 200 == 0:
                    print(f"  {n}", file=sys.stderr)
    print(json.dumps({"audited": n, "ledger": ledger_path}))


def cmd_report(args):
    from collections import Counter, defaultdict
    rows = {}
    for line in open(os.path.join(args.audit_dir, "audit_library.jsonl")):
        r = json.loads(line)
        rows[r["id"]] = r
    rows = list(rows.values())
    buckets = defaultdict(Counter)
    dist = defaultdict(list)
    for r in rows:
        a = r.get("asset")
        if r.get("audit_error"):
            b = "audit_error"
        elif not r.get("compile"):
            b = "compile_fail"
        elif not r.get("executed"):
            b = "static_only_ok"
        elif not r.get("tests_old"):
            b = "test_fail"
        elif not r.get("tests_new"):
            b = "new_gate_only"
        else:
            b = "pass"
        buckets[a][b] += 1
        m = r.get("metrics") or {}
        for k in ("max_depth", "max_func_bytes", "min_bytes", "func_count", "lint_warnings"):
            if m.get(k) is not None:
                dist[k].append(m[k])
        if r.get("idiom") is not None:
            dist["idiom"].append(r["idiom"])
    summary = {"total": len(rows),
               "buckets": {k: dict(v) for k, v in sorted(buckets.items())},
               "idiom_below_floor": sum(1 for v in dist["idiom"]
                                        if v < idiom_judge.IDIOM_FLOOR),
               "distributions": {}}
    for k, vals in dist.items():
        vals.sort()
        summary["distributions"][k] = {p: vals[min(len(vals) - 1, int(p / 100 * len(vals)))]
                                       for p in (50, 90, 95, 99)} if vals else {}
    out = os.path.join(args.audit_dir, "audit_library_summary.json")
    with open(out, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--audit-dir",
                    default="/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04/audit")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
