#!/usr/bin/env python3
"""127.460 -- repair the four banked records whose output came from array `==` (138.20 follow-on).

What the row said (Z209, the toke 138.20 --check sweep over the 12,969 regen_v04
records that contain ==/!=): four banked records compare two arrays with `==`,
the shape `if(val==other){..}`. Before toke 138.20 that lowered to an address or
C-string compare of the array blocks -- a silent wrong answer -- and from 138.20
on it is E4031 with no fix (owner decision 2026-11-15, register 1.2 (a): refuse;
route these records through correctness repair, not a mechanical rewrite).
  A-ARR-0033v2                     arrconcat @f64, full_program: on the corpus pin
                                   `@(1.0;2.0)==@(3.0;4.0)` is true, so main printed
                                   `1 2` (the early return), not `1 2 3 4`
  A-ARR-0090v2                     difference @str, single_function
  MIG-P2-A-A-ARR-0010v2-32c4964b   arrlen @str, migrate_fix
  MIG-P2-A-A-ARR-0027v2-803f9d89   arrreverse @i64, migrate_fix:
                                   `let val=mut.arr;let other=mut.arr;if(val==other){<val}`
                                   compares the input with itself, so it returned
                                   its input unreversed

The task (the reference each repair is held to).
  0033v2 / 0090v2 (shards: description, test_cases []): concat = all of a then
    all of b; difference = the elements of a not in b, in order; "Variant 2: use
    parameter names val and other. Handle the edge case where the inputs are
    equal by returning early." Equal inputs = same length and equal elements.
    The early return gives the same answer as the general case: val+val for
    concat (the form the sibling A-ARR-0032v50 banks), @() for difference.
  0010v2 (migrate_fix; legacy_source `if(val=other){<0};<val.len as i64`): 0
    when the two arrays are equal (length, then element by element), else
    val.len -- the legacy program with real equality.
  0027v2 (migrate_fix; legacy `arrreverse`): the array reversed. The legacy
    guard compares the input with a copy of itself, so under any equality it
    is always taken and the function would never reverse; the guard is dropped
    (it has no inputs to tell apart), leaving the reversal the name and body say.

The repair (the smallest edit that says what the task meant; nothing else moves).
  0033v2 / 0090v2 / 0010v2: a helper `f=arreq(val:@T;other:@T):bool` (length,
    then element by element -- the spelling banked records A-ARR-0086v2 and
    A-ARR-0032v50 already use) is inserted before the function, and
    `if(val==other)` becomes `if(arreq(val;other))`; 0033v2's early return
    becomes `<val+val`.
  0027v2: `let val=mut.arr;let other=mut.arr;if(val==other){<val};` is removed.

Gates (every one must pass, for every record, before anything is banked).
  stale     the record's tk_source sha256 is the one this repair was written
            against (MANIFEST source_sha256 on 2026-11-23); each edit matches once
  check     `--check` reports no error on EVERY tkc given (--tkc, repeatable):
            the integration build, toke main and the corpus pin
  build+run full_program (0033v2): builds and exits 0 printing exactly
            the reference's lines (1 2 3 4) on every tkc
  probe     differential against a Python reference of the task: the repaired
            function, driven by a probe main over hand-picked inputs (equal,
            empty, prefix, permuted, duplicate) and 20 generated ones (about a
            third equal pairs), must print exactly the reference's lines, on
            every tkc. None of the four has test cases (shards: test_cases []
            or absent; differential languages_agreed []), so the probe is the
            differential, as in the 131.120 / 127.381 repairs.
The original is probed too, on every tkc that still builds it, to show the defect.

Bank: the check-repair convention (repair_131_120_127_370.py, shared code):
  original archived -> corpus/regen_v04/audit/replaced/131/<task_id>.tk (first touch only)
  record            -> tk_source replaced; regen.source_sha256 and metrics
                       recomputed with the FIRST --tkc; regen.rewrite131 stamped
                       {wave: "check-repair", story: "127.460", ...}
  manifest          -> re-stamped (131.35)
  ledger            -> corpus/regen_v04/ledger/rewrite_131.jsonl, one line per record
Summary: regen/freeze/repair_127_460.json. Task IDs are unchanged, so the
benchmark task-ID dedup has nothing new to see (the route has no dedup step);
no judge/model call (the route has none: the gates above are its judge).

Usage (toke-corpus root or a worktree; corpus/ is read from the main checkout
unless $TOKE_CORPUS_V04 is set):
  python3 regen/freeze/repair_127_460.py --tkc <int>/tkc --tkc ~/tk/toke/tkc \\
      --tkc ~/tk/archive/pins/tkc-131.103/tkc [--bank]
(without --bank: dry run, writes the summary only)
"""
import argparse
import json
import os
import random
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import repair_131_120_127_370 as base  # shared gates and bank layout

manifest_tool, metrics, tkc_pin = base.manifest_tool, base.metrics, base.tkc_pin
C, MANIFEST, REPLACED, LEDGER, WAVE = base.C, base.MANIFEST, base.REPLACED, base.LEDGER, base.WAVE
SUMMARY = os.path.join(HERE, "repair_127_460.json")
STORY = "127.460"
sha_text, tilde = base.sha_text, base.tilde


def arreq(t):
    return ("f=arreq(val:@%s;other:@%s):bool{\n"
            "  if(val.len!=other.len){<false};\n"
            "  lp(let i=0;i<val.len;i=i+1){\n"
            "    if(val.get(i)!=other.get(i)){<false}\n"
            "  };\n"
            "  <true\n"
            "};\n" % (t, t))


DEFECT = ("array `==` (`if(val==other)`): E4031 since toke 138.20; before it, an address / "
          "C-string compare of the array blocks")

REPAIRS = {
    "A-ARR-0033v2": {
        "path": "A-ARR/A-ARR-0033v2.json",
        "prev": "38ea2f5d73654a4242bcb0d448db9233a584494b3dd6f44bca415045be64ccf0",
        "edits": [("f=arrconcat(val:@f64;other:@f64):@f64{\n",
                   arreq("f64") + "f=arrconcat(val:@f64;other:@f64):@f64{\n"),
                  ("if(val==other){<val};", "if(arreq(val;other)){<val+val};")],
        "defect": DEFECT + "; on the corpus pin @(1.0;2.0)==@(3.0;4.0) was true, so main printed 1 2",
        "stdout": "1\n2\n3\n4\n", "output_check": "repaired", "probe": "concat",
    },
    "A-ARR-0090v2": {
        "path": "A-ARR/A-ARR-0090v2.json",
        "prev": "670618569b9517567c236d0e9dccc23a5f1b0a21bd93367e0f0592ab3277460d",
        "edits": [("f=difference(val:@str;other:@str):@str{\n",
                   arreq("str") + "f=difference(val:@str;other:@str):@str{\n"),
                  ("if(val==other){<@()};", "if(arreq(val;other)){<@()};")],
        "defect": DEFECT,
        "stdout": None, "output_check": "repaired", "probe": "difference",
    },
    "MIG-P2-A-A-ARR-0010v2-32c4964b": {
        "path": "A-ARR/MIG-P2-A-A-ARR-0010v2-32c4964b.json",
        "prev": "f32da8bade4f0c8ab4ef55dfbe2a99f20e0778c00e080a0f147739bc73b12264",
        "edits": [("f=arrlen(val:@str;other:@str):i64{\n",
                   arreq("str") + "f=arrlen(val:@str;other:@str):i64{\n"),
                  ("if(val==other){<0};", "if(arreq(val;other)){<0};")],
        "defect": DEFECT,
        "stdout": None, "output_check": "repaired", "probe": "arrlen",
    },
    "MIG-P2-A-A-ARR-0027v2-803f9d89": {
        "path": "A-ARR/MIG-P2-A-A-ARR-0027v2-803f9d89.json",
        "prev": "dcc06cd848cbfd544cbff1b7d49dce28fe9e4f9ce846cc4d4f51de29e14325ab",
        "edits": [("let val=mut.arr;let other=mut.arr;if(val==other){<val};", "")],
        "defect": (DEFECT + "; the guard compared the input with a copy of itself, so it was "
                   "always taken and the function returned its input unreversed"),
        "stdout": None, "output_check": "repaired", "probe": "reverse",
    },
}


def _write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def _load(path):
    with open(path) as fh:
        return json.load(fh)


# ---------------------------------------------------------------- probes
def _f64(x):
    return "%g" % x


def _lit(t, xs):
    if t == "f64":
        el = ["0.0-%r" % -x if x < 0 else "%r" % x for x in xs]
    elif t == "i64":
        el = ["0-%d" % -x if x < 0 else "%d" % x for x in xs]
    else:
        el = ['"%s"' % x for x in xs]
    return "@(%s)" % ";".join(el)


def _pairs(t):
    rnd = random.Random(460)
    if t == "f64":
        pool = [0.0, 1.0, 2.0, 2.5, 0.1, -3.25, 100.0, 7.5]
    elif t == "i64":
        pool = [0, 1, 2, 3, -5, 42, 1000, -1]
    else:
        pool = ["a", "b", "c", "ab", "", "x y", "B"]
    fixed = [([], []), ([], [pool[1]]), ([pool[1]], []), (pool[1:3], pool[1:3]),
             (pool[1:3], pool[2:0:-1]), (pool[1:2], pool[1:3]), (pool[1:3], pool[1:2]),
             (pool[:4], pool[:4]), ([pool[1], pool[1]], [pool[1]]), ([pool[1]], [pool[1], pool[1]]),
             (pool[1:4] + pool[1:2], pool[2:3]), (pool[:5], pool[3:6])]
    gen = []
    for _ in range(20):
        a = [rnd.choice(pool) for _ in range(rnd.randint(0, 6))]
        if rnd.random() < .35:
            b = list(a)
        else:
            b = [rnd.choice(pool) for _ in range(rnd.randint(0, 6))]
        gen.append((a, b))
    return fixed + gen


def _show(t):
    el = '"\\(a.get(i))"' if t != "str" else "a.get(i)"
    return ("f=show(a:@%s):str{\n  let s=mut.\"[\";\n"
            "  lp(let i=0;i<a.len;i=i+1){\n"
            "    if(i>0){s=s.concat(\",\")};\n    s=s.concat(%s)\n  };\n"
            "  <s.concat(\"]\")\n};\n" % (t, el))


def probe(kind):
    """(main source appended to the repaired module minus its main, expected lines)."""
    t = {"concat": "f64", "difference": "str", "arrlen": "str", "reverse": "i64"}[kind]
    fmt = _f64 if t == "f64" else str
    body, exp = [], []
    for n, (a, b) in enumerate(_pairs(t)):
        body.append("  let a%d:@%s=%s;" % (n, t, _lit(t, a)))
        if kind == "reverse":
            body.append("  io.println(show(arrreverse(a%d)));" % n)
            exp.append("[%s]" % ",".join(fmt(x) for x in reversed(a)))
            continue
        body.append("  let b%d:@%s=%s;" % (n, t, _lit(t, b)))
        if kind == "concat":
            body.append("  io.println(show(arrconcat(a%d;b%d)));" % (n, n))
            exp.append("[%s]" % ",".join(fmt(x) for x in a + b))
        elif kind == "difference":
            body.append("  io.println(show(difference(a%d;b%d)));" % (n, n))
            exp.append("[%s]" % ",".join(x for x in a if x not in b))
        else:
            body.append('  io.println("\\(arrlen(a%d;b%d))");' % (n, n))
            exp.append(str(0 if a == b else len(a)))
    return _show(t) + "f=main():i64{\n" + "\n".join(body) + "\n  <0\n};\n", exp


# ---------------------------------------------------------------- gates
def evaluate(tid, spec, rec, tkcs, work):
    src = rec.get("tk_source") or ""
    r = {"task_id": tid, "story": STORY, "path": spec["path"],
         "prev_source_sha256": sha_text(src), "defect": spec["defect"]}
    if r["prev_source_sha256"] != spec["prev"]:
        r.update(status="skipped", reason="stale: tk_source is not the one this repair was written against")
        return r
    fixed = src
    for old, new in spec["edits"]:
        if fixed.count(old) != 1:
            r.update(status="failed", reason="edit does not match exactly once: %r" % old)
            return r
        fixed = fixed.replace(old, new)
    r.update(fixed=fixed, new_source_sha256=sha_text(fixed), edits=len(spec["edits"]),
             gates={}, output_check=spec["output_check"])
    f = os.path.join(work, tid + ".orig.tk")
    _write(f, src)
    r["errors_before"] = {tilde(t): base.errors(t, f) for t in tkcs}
    main, exp = probe(spec["probe"])
    r["probe_before"] = {}
    for t in tkcs:
        x, err = base.build_run(t, base.with_main(src, main), work, tid + ".orig.probe")
        if not err:
            got = x.stdout.splitlines()
            r["probe_before"][tilde(t)] = {
                "exit": x.returncode,
                "mismatch": sum(a != b for a, b in zip(got, exp)) + abs(len(got) - len(exp))}
        if spec["stdout"] is not None:
            x, err = base.build_run(t, src, work, tid + ".orig")
            if not err:
                r["probe_before"][tilde(t)]["stdout"] = x.stdout
    fails = []
    for t in tkcs:
        g = {}
        f = os.path.join(work, tid + ".tk")
        _write(f, fixed)
        g["check"] = base.errors(t, f)
        if g["check"]:
            fails.append("%s: --check %s" % (tilde(t), g["check"]))
        if spec["stdout"] is not None:
            x, err = base.build_run(t, fixed, work, tid)
            if err:
                fails.append("%s: %s" % (tilde(t), err))
            else:
                g["stdout"] = x.stdout
                if x.returncode != 0 or x.stdout != spec["stdout"]:
                    fails.append("%s: main exit %d stdout %r != %r"
                                 % (tilde(t), x.returncode, x.stdout, spec["stdout"]))
        x, err = base.build_run(t, base.with_main(fixed, main), work, tid + ".probe")
        if err:
            fails.append("%s: probe %s" % (tilde(t), err))
        else:
            got = x.stdout.splitlines()
            bad = [i for i, (a, b) in enumerate(zip(got, exp)) if a != b]
            g["probe"] = {"cases": len(exp), "mismatch": len(bad) + abs(len(got) - len(exp)),
                          "exit": x.returncode}
            if x.returncode != 0 or g["probe"]["mismatch"]:
                fails.append("%s: probe exit %d, %d/%d lines differ from the reference"
                             % (tilde(t), x.returncode, g["probe"]["mismatch"], len(exp)))
        r["gates"][tilde(t)] = g
    r["status"] = "failed" if fails else "rewritten"
    if fails:
        r["reason"] = "; ".join(fails)
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tkc", action="append", required=True,
                    help="compiler every gate runs on (repeatable); the first also measures metrics")
    ap.add_argument("--bank", action="store_true")
    args = ap.parse_args()
    tkcs = [os.path.realpath(os.path.expanduser(t)) for t in args.tkc]
    stamps = [{"tkc": tilde(t), "sha256": tkc_pin.sha256_file(t), "version": tkc_pin._version(t)}
              for t in tkcs]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = []
    with tempfile.TemporaryDirectory(prefix="repair127460_") as work:
        for tid, spec in REPAIRS.items():
            results.append(evaluate(tid, spec, _load(os.path.join(C, spec["path"])), tkcs, work))
        ok = all(r["status"] == "rewritten" for r in results)
        if args.bank and ok:
            os.makedirs(REPLACED, exist_ok=True)
            man = manifest_tool.Manifest(MANIFEST)
            led = []
            for r in results:
                tid = r["task_id"]
                spec = REPAIRS[tid]
                path = os.path.join(C, spec["path"])
                prev_rec_sha = manifest_tool.sha256_file(path)
                rec = _load(path)
                arch = os.path.join(REPLACED, tid + ".tk")
                if not os.path.exists(arch):
                    _write(arch, rec["tk_source"])
                rec["tk_source"] = r["fixed"]
                rg = rec.setdefault("regen", {})
                mb_before = rg.get("min_bytes")
                rg["source_sha256"] = r["new_source_sha256"]
                p = os.path.join(work, tid + ".bank.tk")
                _write(p, r["fixed"])
                m = metrics.analyse(p, src=r["fixed"], tkc=tkcs[0]) or {}
                for k in ("min_bytes", "proxy_tokens", "max_depth", "lint_pattern_violations"):
                    if m.get(k) is not None:
                        rg[k] = m[k]
                rg["rewrite131"] = {
                    "wave": WAVE, "story": STORY, "ts": now, "tkc_bin_sha": stamps[0]["sha256"],
                    "tkcs": stamps, "defect": r["defect"],
                    "repairs": [list(e) for e in spec["edits"]],
                    "prev_sha256": r["prev_source_sha256"], "output_check": r["output_check"],
                    "output_after": r["gates"][tilde(tkcs[0])].get("stdout"),
                    "probe": r["gates"][tilde(tkcs[0])].get("probe"),
                    "min_bytes_before": mb_before, "min_bytes_after": m.get("min_bytes"),
                    "archived": "audit/replaced/131/%s.tk" % tid, "attempts": 1,
                }
                with open(path, "w") as fh:
                    json.dump(rec, fh)
                man.stamp(tid, path, save=False)
                led.append({"task_id": tid, "wave": WAVE, "story": STORY,
                            "status": "rewritten", "reason": "repaired:" + r["output_check"],
                            "attempts": 1, "prev_sha256": prev_rec_sha,
                            "new_sha256": manifest_tool.sha256_file(path), "ts": now,
                            "tkc_bin_sha": stamps[0]["sha256"]})
            man.save()
            with open(LEDGER, "a") as fh:
                for e in led:
                    fh.write(json.dumps(e) + "\n")
    summary = {
        "stories": [STORY], "generated": now, "banked": bool(args.bank and ok), "tkcs": stamps,
        "by_status": {s: sum(1 for r in results if r["status"] == s)
                      for s in sorted({r["status"] for r in results})},
        "records": [{k: v for k, v in r.items() if k != "fixed"} for r in results],
    }
    with open(SUMMARY, "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps(summary["by_status"]), "BANKED" if summary["banked"] else
          ("DRY RUN" if not args.bank else "NOT BANKED (a gate failed)"))
    for r in results:
        if r["status"] != "rewritten":
            print(r["task_id"], r["status"], r.get("reason"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
