#!/usr/bin/env python3
"""131.69 — which differential mode does each prepped 131.15 record land in?

The bank's diff gate is now baseline-aware (diff_check.baseline_verdict): a
record whose ORIGINAL passes its own test cases keeps byte-identity; one whose
original FAILS them, where some case expects an err marker, switches to
`repair` (candidate must PASS its cases; only err-marker lines may change).

This measures, per record in work/pattern_131/specs, without writing anything:

  driver_blocked   the driver/main cannot even be synthesised or built for the
                   ORIGINAL — the worker `build`/`tests` gate rejects every
                   candidate, so 131.69 cannot help (the 29 `driver_fail` rows)
  identity         the original passes its tests (or the question is not
                   answerable) — byte-identity still required
  repair_ok        the original fails, a case expects an err, and its
                   NON-error lines already match the expectation, so a correct
                   rewrite satisfies the repair rule and can bank
  repair_blocked   the original fails on a NON-error line too (or the line
                   count differs): the repair rule cannot accept any candidate,
                   because the candidate must pass while every non-error line
                   stays equal to a wrong one — these need regeneration/repair,
                   not a pattern rewrite

Usage: baseline_mode_scan_131_69.py [--prefix A-ERR] [--jobs 6] [--out x.json]
"""
import argparse, json, os, sys, tempfile
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pattern_common as pc                           # noqa: E402
import diff_check as dc                               # noqa: E402
import driver as drv                                  # noqa: E402


def classify(args):
    """One record. Returns a row dict; never raises."""
    tid, spec_path, corpus = args
    row = {"task_id": tid, "class": None, "detail": None, "baseline": None,
           "err_lines": 0, "task_type": None}
    try:
        spec = json.load(open(spec_path))
        row["task_type"] = spec.get("task_type")
        rec_path = os.path.join(corpus, spec.get("category") or tid.rsplit("-", 1)[0], tid + ".json")
        orig = json.load(open(rec_path))["tk_source"]
        with tempfile.TemporaryDirectory(prefix="bms_") as td:
            prog = dc.prepare_program(orig, spec, tid, td, "orig")
            if not prog.test_bin:
                row.update({"class": "driver_blocked", "detail": prog.reason})
                prog.cleanup()
                return row
            runs = dc.run_spec_cases(prog, td)
            base = dc.baseline_verdict(prog, spec, runs)
            prog.cleanup()
        row["baseline"] = base["verdict"]
        row["err_lines"] = base["err_lines"]
        if base["verdict"] != "fail" or base["err_lines"] == 0:
            row.update({"class": "identity", "detail": base["detail"]})
            return row
        # The candidate that banks must PASS its cases, so its lines are the
        # expected lines: the repair rule is satisfiable exactly when the
        # ORIGINAL's non-error lines already equal the expectation.
        exp = dc.expected_runs(prog, spec)
        for (want, flags), (lab, r) in zip(exp, runs):
            got = dc._stdout_lines(r["stdout"])
            if len(got) != len(want):
                row.update({"class": "repair_blocked",
                            "detail": f"{lab}: {len(got)} stdout lines vs {len(want)} expected"})
                return row
            for k, (g, w, f) in enumerate(zip(got, want, flags)):
                if f or dc.lines_equal([g], [w]):
                    continue
                row.update({"class": "repair_blocked",
                            "detail": f"{lab}: non-error line {k + 1} {g!r} != expected {w!r}"})
                return row
        row.update({"class": "repair_ok", "detail": base["detail"]})
        return row
    except Exception as e:                            # noqa: BLE001 — a scan never crashes the sweep
        row.update({"class": "error", "detail": f"{type(e).__name__}: {e}"})
        return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=pc.CORPUS)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--prefix", default=None, help="only task ids starting with this")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out", default=None, help="write the rows as JSON here")
    args = ap.parse_args(argv)
    with pc.pin_tkc(sys.modules[__name__], dc) as pinned:     # 131.39
        paths = pc.Paths(corpus=args.corpus, workdir=args.workdir)
        specs = sorted(os.listdir(paths.sub("specs")))
        tids = [s[:-5] for s in specs if s.endswith(".json")]
        if args.prefix:
            tids = [t for t in tids if t.startswith(args.prefix)]
        work = [(t, paths.sub("specs", t + ".json"), paths.corpus) for t in tids]
        print(f"baseline_mode_scan: {len(work)} records, tkc {pinned.sha256[:12]}", file=sys.stderr)
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            rows = list(ex.map(classify, work, chunksize=1))
        tkc_bin_sha = pinned.sha256            # inside the pin: the copy is gone on exit
    from collections import Counter
    counts = Counter(r["class"] for r in rows)
    out = {"story": "131.69", "records": len(rows), "counts": dict(counts),
           "tkc_bin_sha": tkc_bin_sha, "rows": rows}
    if args.out:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
