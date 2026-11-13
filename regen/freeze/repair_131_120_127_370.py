#!/usr/bin/env python3
"""131.120 + 127.370 -- repair five banked records that a newer checker rejects.

What the rows said.
  131.120 (Z154, the toke 127.365 sweep): A-ERR-0087v19 declares
    `t=$lookuperr{notfound:str;emptycollection:bool}` -- a RECORD -- and builds
    it as a sum (`$lookuperr{$emptycollection:true}`); A-ERR-0093v53 does the
    same with `$matherr{divbyzero:bool;overflow:str}`. On toke main they check
    clean and build a record with one slot written; with 127.365 every such
    literal is E4077 ("leaves out field ...; a sum was meant"). A-ERR-0093v53
    also prints its @i64 result with `"\\(r)"`, E4032 since toke 3.1.0 (131.106
    routed it for that; it printed a pointer, `4343077896`, when it built).
  127.370 (Z156, the toke 127.364 sweep): three records index a str with
    `.get(i)` and are E4031 once 127.364 types the receiver: A-STR-0037v9
    (`a.get(i) as i64` on `n.lower()`; built on main it exits RT003 before
    printing anything), MIG-P2-B-D-CRY-0015v280-52b9c01c
    (`p0.slice(i;i+1).get(0) as i64`) and MIG-P2-B-D-WEB-0024v247-1525170f
    (`url.get(j as u64)` on `left.concat(right)`; built on main it never finds
    the `:`, so it returns "" for every input).

The repair (the smallest edit that says what the task meant; nothing else moves).
  A-ERR-0087v19    the type's variants get their `$`: `t=$lookuperr{$notfound:str;$emptycollection:bool}`
                   (the task: "Return NotFound error ... Return EmptyCollection")
  A-ERR-0093v53    `t=$matherr{$divbyzero:bool;$overflow:str}`, and main prints
                   the doubled array one element per line (the form its siblings
                   A-ERR-0093v12/v87 use) instead of interpolating the array
  A-STR-0037v9     `a.get(i) as i64` / `b.get(i) as i64` -> `a.charat(i)` / `b.charat(i)`
  MIG ...CRY...    `p0.slice(i;i+1).get(0)as i64` -> `p0.charat(i)`
  MIG ...WEB...    `url.get(j as u64)==58` -> `url.charat(j)==58`
`str.charat(s;i):i64` is the byte at i (std.str, 127.38), the spelling 67
banked records already use.

Gates (every one must pass, for every record, before anything is banked).
  stale     the record's tk_source sha256 is the one this repair was written
            against (MANIFEST source_sha256 on 2026-11-13); each edit matches once
  check     `--check` reports no error on EVERY tkc given (--tkc, repeatable):
            the 127.364/127.365 integration build, toke main and the corpus pin
  build+run full_program: builds and exits 0 printing exactly the expected
            lines on every tkc. A-ERR-0087v19's `3` is its pre-repair output on
            toke main (identical); the other two never printed a correct line
            (pointer / RT003), so theirs is the task's answer
  probe     differential against a Python reference of the task: the repaired
            function, driven by a probe main over spec-derived inputs (the error
            paths for the two A-ERR records; 25-28 generated inputs for the three
            str records), must print exactly the reference's lines, on every tkc
None of the five has test cases (shards: test_cases []), so the probe is the
differential; the record's own main and function are unchanged apart from the
edits above.

Bank (the 131.78 / 136.72 convention; all five records are already in, or
chain from freeze-129 into, ledger/rewrite_131.jsonl, so they stay on that
ledger and reconcile_rewrite_131.py keeps one sha chain per record):
  original archived -> corpus/regen_v04/audit/replaced/131/<task_id>.tk (first touch only)
  record            -> tk_source replaced; regen.source_sha256 and metrics
                       (min_bytes, proxy_tokens, max_depth, lint_pattern_violations,
                       measured with the FIRST --tkc) recomputed; regen.rewrite131
                       stamped {wave: "check-repair", story, ...}
  manifest          -> re-stamped (131.35)
  ledger            -> corpus/regen_v04/ledger/rewrite_131.jsonl, one line per record
Summary: regen/freeze/repair_131_120_127_370.json.

Usage (toke-corpus root or a worktree; corpus/ is read from the main checkout
unless $TOKE_CORPUS_V04 is set):
  python3 regen/freeze/repair_131_120_127_370.py --tkc <int>/tkc --tkc ~/tk/toke/tkc \\
      --tkc ~/tk/archive/pins/tkc-131.103/tkc [--bank]
(without --bank: dry run, writes the summary only)
"""
import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
sys.path.insert(0, REGEN)
import manifest_tool  # noqa: E402
import metrics        # noqa: E402
import tkc_pin        # noqa: E402

C = os.environ.get("TOKE_CORPUS_V04") or os.path.join(
    os.path.expanduser("~"), "tk", "toke-corpus", "corpus", "regen_v04")
MANIFEST = os.path.join(C, "MANIFEST.jsonl")
REPLACED = os.path.join(C, "audit", "replaced", "131")
LEDGER = os.path.join(C, "ledger", "rewrite_131.jsonl")
SUMMARY = os.path.join(HERE, "repair_131_120_127_370.json")
WAVE = "check-repair"

sha_text = lambda s: hashlib.sha256(s.encode()).hexdigest()
tilde = lambda p: p.replace(os.path.expanduser("~"), "~", 1)   # no home paths in the summary

PRINT_ARRAY = ('  lp(let i=0;i<r.len;i=i+1){\n'
               '    io.println("\\(r.get(i))");\n'
               '  };\n')

# task_id -> story, path, prev source sha, edits (old, new: each old occurs once), defect,
#            expected stdout of the record's own main, probe
REPAIRS = {
    "A-ERR-0087v19": {
        "story": "131.120", "path": "A-ERR/A-ERR-0087v19.json",
        "prev": "153b60147c95481ae5cba925b68843f2ef391ca141da588ad7e681a3068fb1eb",
        "edits": [("t=$lookuperr{notfound:str;emptycollection:bool};",
                   "t=$lookuperr{$notfound:str;$emptycollection:bool};")],
        "defect": ("record error type built as a sum (`$lookuperr{$emptycollection:true}`): "
                   "E4077 since toke 127.365; on main it built a record with one slot written"),
        "stdout": "3\n", "output_check": "unchanged",
        "probe": "err87",
    },
    "A-ERR-0093v53": {
        "story": "131.120", "path": "A-ERR/A-ERR-0093v53.json",
        "prev": "70da833037234a105bbb0c8b9019571b91cc588a2c78dc103fa59728ee822c2d",
        "edits": [("t=$matherr{divbyzero:bool;overflow:str};",
                   "t=$matherr{$divbyzero:bool;$overflow:str};"),
                  ('  io.println("\\(r)");\n', PRINT_ARRAY)],
        "defect": ("record error type built as a sum (`$matherr{$overflow:...}`): E4077 since "
                   "toke 127.365; and an @i64 interpolated into a string, E4032 since toke 3.1.0 "
                   "(it printed a pointer when it built)"),
        "stdout": "2\n4\n6\n", "output_check": "repaired",
        "probe": "err93",
    },
    "A-STR-0037v9": {
        "story": "127.370", "path": "A-STR/A-STR-0037v9.json",
        "prev": "c216ed83362f60b2640d6424e06f82c7f5a450773f48dd44d6766209702f7fdb",
        "edits": [("let c=a.get(i) as i64;", "let c=a.charat(i);"),
                  ("let c=b.get(i) as i64;", "let c=b.charat(i);")],
        "defect": ("str indexed with `.get(i)`: E4031 once toke 127.364 types the receiver; "
                   "built on main it exits RT003 (index 121382024472940 out of bounds) before printing"),
        "stdout": "true\nfalse\ntrue\n", "output_check": "repaired",
        "probe": "anagram",
    },
    "MIG-P2-B-D-CRY-0015v280-52b9c01c": {
        "story": "127.370", "path": "D-CRY/MIG-P2-B-D-CRY-0015v280-52b9c01c.json",
        "prev": "36744ba2c315a9eb7d15a2c3fe960e65a1806d979d9cda8e58e533b6410aa6b5",
        "edits": [("let ch=p0.slice(i;i+1).get(0)as i64;", "let ch=p0.charat(i);")],
        "defect": "str indexed with `.get(0)`: E4031 once toke 127.364 types the receiver",
        "stdout": None, "output_check": "probe",
        "probe": "checksum",
    },
    "MIG-P2-B-D-WEB-0024v247-1525170f": {
        "story": "127.370", "path": "D-WEB/MIG-P2-B-D-WEB-0024v247-1525170f.json",
        "prev": "a6ffaada7a1628d24cb122ca0204f813eead107d94f843765bb4e24453c3a065",
        "edits": [("if(url.get(j as u64)==58)", "if(url.charat(j)==58)")],
        "defect": ("str indexed with `.get(j)`: E4031 once toke 127.364 types the receiver; "
                   "built on main it never found the `:` and returned \"\" for every input"),
        "stdout": None, "output_check": "repaired",
        "probe": "scheme",
    },
}


# ---------------------------------------------------------------- probes
def _lower(s):
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in s)


def _gen_inputs():
    rnd = random.Random(163)
    al = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 :/.-_?=&"
    rs = lambda n: "".join(rnd.choice(al) for _ in range(n))
    ana = [("listen", "silent"), ("hello", "world"), ("Dormitory", "dirtyroom"), ("", ""),
           ("a", "A"), ("ab", "abc"), ("Tom Marvolo", "Voldemort Mar"), ("aab", "abb")]
    for _ in range(20):
        a = rs(rnd.randint(0, 12))
        b = list(a)
        rnd.shuffle(b)
        b = "".join(b)
        if rnd.random() < .5:
            b = b.swapcase()
        if rnd.random() < .3 and b:
            b = b[:-1] + rnd.choice(al)
        ana.append((a, b))
    cry = [("",), ("a",), ("abc",), ("Hello, World",), ("zzzzzzzzzzzzzzzz",)] + \
        [(rs(rnd.randint(0, 40)),) for _ in range(20)]
    web = [("http", "://x.org"), ("", "abc"), (":", "x"), ("a:b", ":c"), ("https://", "h"),
           ("ftp", ""), ("mailto", ":me")] + \
        [(rs(rnd.randint(0, 8)), rs(rnd.randint(0, 8))) for _ in range(20)]
    return ana, cry, web


def _scheme(l, r):
    u = l + r
    i = u.find(":")
    return u[:i] if i > 0 else ""


def _str_probe(fn, inputs, ref, rt):
    calls, exp = [], []
    for args in inputs:
        call = "%s(%s)" % (fn, ";".join('"%s"' % a for a in args))
        calls.append('  io.println("[".concat(%s).concat("]"));' % call if rt == "str"
                     else '  io.println("[\\(%s)]");' % call)
        exp.append("[%s]" % ref(*args))
    return "f=main():i64{\n" + "\n".join(calls) + "\n  <0\n};\n", exp


def probe(kind):
    """(main source appended to the repaired module minus its main, expected lines)."""
    ana, cry, web = _gen_inputs()
    if kind == "anagram":
        return _str_probe("isanagram", ana,
                          lambda a, b: "true" if sorted(_lower(a)) == sorted(_lower(b)) else "false",
                          "bool")
    if kind == "checksum":
        return _str_probe("solve", cry, lambda s: str(sum(map(ord, s)) % 256), "i64")
    if kind == "scheme":
        return _str_probe("solve", web, _scheme, "str")
    if kind == "err87":   # mat[row][col]; NotFound when row/col out of bounds; EmptyCollection when empty
        main = ('f=show(m:@(@i64);r:i64;c:i64):str{\n'
                '  <mt safematget(m;r;c) {$ok:v "ok \\(v)";$err:e mt e {$notfound:s "notfound \\(s)";'
                '$emptycollection:b "empty"}}\n};\n'
                'f=main():i64{\n  let em:@(@i64)=@();\n'
                '  io.println(show(@(@(1;2);@(3;4));1;0));\n'
                '  io.println(show(@(@(1;2);@(3;4));0;1));\n'
                '  io.println(show(@(@(1;2);@(3;4));2;0));\n'
                '  io.println(show(@(@(1;2);@(3;4));0-1;0));\n'
                '  io.println(show(@(@(1;2);@(3;4));1;2));\n'
                '  io.println(show(em;0;0));\n  <0\n};\n')
        return main, ["ok 3", "ok 2", "notfound row out of bounds", "notfound row out of bounds",
                      "notfound col out of bounds", "empty"]
    if kind == "err93":   # doubles each element; Overflow when an element > 4611686018427387903
        main = ('f=total(v:@i64):i64{\n  let s=mut.0;\n'
                '  lp(let i=0;i<v.len;i=i+1){s=s+v.get(i)};\n  <s\n};\n'
                'f=show(a:@i64):str{\n'
                '  <mt safedoubleall(a) {$ok:v "ok \\(v.len) \\(total(v))";$err:e mt e '
                '{$overflow:s "overflow \\(s)";$divbyzero:b "div"}}\n};\n'
                'f=main():i64{\n'
                '  io.println(show(@(1;2;3)));\n  io.println(show(@(0-5;10)));\n'
                '  io.println(show(@(4611686018427387903)));\n'
                '  io.println(show(@(1;4611686018427387904)));\n  <0\n};\n')
        return main, ["ok 3 12", "ok 2 10", "ok 1 9223372036854775806",
                      "overflow doubled value overflows"]
    raise ValueError(kind)


# ---------------------------------------------------------------- gates
def run(argv, cwd, timeout=120):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def errors(tkc, path):
    p = run([tkc, "--check", path], os.path.dirname(path))
    out = []
    for line in (p.stdout + p.stderr).splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("severity") == "error":
            out.append("%s:L%s" % (d.get("error_code"), (d.get("pos") or {}).get("line")))
    if p.returncode != 0 and not out:
        out.append("rc=%d" % p.returncode)
    return out


def build_run(tkc, src, work, name):
    f = os.path.join(work, name + ".tk")
    open(f, "w").write(src)
    b = run([tkc, f, "-o", f[:-3]], work)
    if b.returncode != 0:
        return None, "build rc=%d %s" % (b.returncode, (b.stdout + b.stderr)[-300:])
    x = run([f[:-3]], work, timeout=30)
    return x, None


def with_main(src, main):
    body = src[:src.index("f=main()")] if "f=main()" in src else src
    if "i=io:std.io;" not in body:
        first = body.index(";") + 1
        body = body[:first] + "\ni=io:std.io;" + body[first:]
    return body + main


def evaluate(tid, spec, rec, tkcs, work):
    src = rec.get("tk_source") or ""
    r = {"task_id": tid, "story": spec["story"], "path": spec["path"],
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
    open(f, "w").write(src)
    r["errors_before"] = {tilde(t): errors(t, f) for t in tkcs}
    main, exp = probe(spec["probe"])
    fails = []
    for t in tkcs:
        g = {}
        f = os.path.join(work, tid + ".tk")
        open(f, "w").write(fixed)
        g["check"] = errors(t, f)
        if g["check"]:
            fails.append("%s: --check %s" % (tilde(t), g["check"]))
        if spec["stdout"] is not None:
            x, err = build_run(t, fixed, work, tid)
            if err:
                fails.append("%s: %s" % (tilde(t), err))
            else:
                g["stdout"] = x.stdout
                if x.returncode != 0 or x.stdout != spec["stdout"]:
                    fails.append("%s: main exit %d stdout %r != %r" % (tilde(t), x.returncode, x.stdout, spec["stdout"]))
        x, err = build_run(t, with_main(fixed, main), work, tid + ".probe")
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
    with tempfile.TemporaryDirectory(prefix="repair131120_") as work:
        for tid, spec in REPAIRS.items():
            path = os.path.join(C, spec["path"])
            results.append(evaluate(tid, spec, json.load(open(path)), tkcs, work))
        ok = all(r["status"] == "rewritten" for r in results)
        if args.bank and ok:
            os.makedirs(REPLACED, exist_ok=True)
            man = manifest_tool.Manifest(MANIFEST)
            led = []
            for r in results:
                spec = REPAIRS[r["task_id"]]
                path = os.path.join(C, spec["path"])
                prev_rec_sha = manifest_tool.sha256_file(path)
                rec = json.load(open(path))
                arch = os.path.join(REPLACED, r["task_id"] + ".tk")
                if not os.path.exists(arch):
                    open(arch, "w").write(rec["tk_source"])
                rec["tk_source"] = r["fixed"]
                rg = rec.setdefault("regen", {})
                mb_before = rg.get("min_bytes")
                rg["source_sha256"] = r["new_source_sha256"]
                p = os.path.join(work, r["task_id"] + ".bank.tk")
                open(p, "w").write(r["fixed"])
                m = metrics.analyse(p, src=r["fixed"], tkc=tkcs[0]) or {}
                for k in ("min_bytes", "proxy_tokens", "max_depth", "lint_pattern_violations"):
                    if m.get(k) is not None:
                        rg[k] = m[k]
                rg["rewrite131"] = {
                    "wave": WAVE, "story": r["story"], "ts": now, "tkc_bin_sha": stamps[0]["sha256"],
                    "tkcs": stamps, "defect": r["defect"],
                    "repairs": [list(e) for e in spec["edits"]],
                    "prev_sha256": r["prev_source_sha256"], "output_check": r["output_check"],
                    "output_after": r["gates"][tilde(tkcs[0])].get("stdout"),
                    "probe": r["gates"][tilde(tkcs[0])].get("probe"),
                    "min_bytes_before": mb_before, "min_bytes_after": m.get("min_bytes"),
                    "archived": "audit/replaced/131/%s.tk" % r["task_id"], "attempts": 1,
                }
                with open(path, "w") as fh:
                    json.dump(rec, fh)
                man.stamp(r["task_id"], path, save=False)
                led.append({"task_id": r["task_id"], "wave": WAVE, "story": r["story"],
                            "status": "rewritten", "reason": "repaired:" + r["output_check"],
                            "attempts": 1, "prev_sha256": prev_rec_sha,
                            "new_sha256": manifest_tool.sha256_file(path), "ts": now,
                            "tkc_bin_sha": stamps[0]["sha256"]})
            man.save()
            with open(LEDGER, "a") as fh:
                for e in led:
                    fh.write(json.dumps(e) + "\n")
    summary = {
        "stories": ["131.120", "127.370"], "generated": now, "banked": bool(args.bank and ok),
        "tkcs": stamps,
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
