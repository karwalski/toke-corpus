#!/usr/bin/env python3
"""127.381 -- repair MIG-P2-B-D-NET-0009v329-08e7c1a7, a banked record that indexes a str with .get(i).

What the row said (Z167, the toke 127.371 --check sweep over 24965 regen_v04
records): once 127.371 types `split` results, the record is E4031 ("cannot
index into 'str'; expected array type") at line 11 -- `let first=parts.get(0);
... let ch=first.get(i);` -- the 127.370 defect class. On the corpus pin and
toke main it checks clean and is wrong: `"127".get(1) as i64` is 1, not the
byte 50, so `solve("127.0.0.1")` returns false.

The task (its legacy_source, a migrate_fix): true when the address has at least
four dot-separated parts and the first part is a non-empty run of decimal
digits whose value is 127 (an IPv4 loopback test); false otherwise.

The repair (the smallest edit that says what the task meant; nothing else moves).
  `let ch=first.get(i);` -> `let ch=first.charat(i);`
`str.charat(s;i):i64` is the byte at i (std.str, 127.38), the spelling the
127.370 repair (repair_131_120_127_370.py) and 67 other banked records use.

Gates (every one must pass before anything is banked).
  stale     the record's tk_source sha256 is the one this repair was written
            against (MANIFEST source_sha256 on 2026-11-13); the edit matches once
  check     `--check` reports no error on EVERY tkc given (--tkc, repeatable):
            the 127.371 integration build, toke main and the corpus pin
  probe     differential against a Python reference of the task: the repaired
            function, driven by a probe main over hand-picked and generated
            addresses, must print exactly the reference's lines, on every tkc
The record has no main and no test cases (shards: test_cases absent;
differential languages_agreed []), so the probe is the differential.

Bank (the 131.78 / 136.72 / 131.120 convention; the record's file is still its
freeze-129 bytes, so it goes on ledger/rewrite_131.jsonl, wave `check-repair`,
and reconcile_rewrite_131.py keeps one sha chain per record):
  original archived -> corpus/regen_v04/audit/replaced/131/<task_id>.tk (first touch only)
  record            -> tk_source replaced; regen.source_sha256 and metrics
                       (min_bytes, proxy_tokens, max_depth, lint_pattern_violations,
                       measured with the FIRST --tkc) recomputed; regen.rewrite131
                       stamped {wave: "check-repair", story: "127.381", ...}
  manifest          -> re-stamped (131.35)
  ledger            -> corpus/regen_v04/ledger/rewrite_131.jsonl, one line
Summary: regen/freeze/repair_127_381.json.

Usage (toke-corpus root or a worktree; corpus/ is read from the main checkout
unless $TOKE_CORPUS_V04 is set):
  python3 regen/freeze/repair_127_381.py --tkc <int>/tkc --tkc ~/tk/toke/tkc \\
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
SUMMARY = os.path.join(HERE, "repair_127_381.json")
STORY = "127.381"
sha_text, tilde = base.sha_text, base.tilde

TID = "MIG-P2-B-D-NET-0009v329-08e7c1a7"
SPEC = {
    "story": STORY, "path": f"D-NET/{TID}.json",
    "prev": "d3ab449442c2da2e2b98d2ae11a21794cd2edc2c1151edf251e24903d0c8ed46",
    "edits": [("let ch=first.get(i);", "let ch=first.charat(i);")],
    "defect": ("str indexed with `.get(i)`: E4031 once toke 127.371 types split results; "
               "built on the corpus pin / toke main `\"127\".get(1)` is 1, not 50, so "
               "solve(\"127.0.0.1\") returned false"),
    "stdout": None, "output_check": "repaired",
}


def _write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def _load(path):
    with open(path) as fh:
        return json.load(fh)


# ---------------------------------------------------------------- probe
def loopback(n):
    """Python reference of the task (legacy_source): >= 4 dot parts, first part all digits == 127."""
    if n == "":
        return False
    parts = n.split(".")
    if len(parts) < 4 or parts[0] == "":
        return False
    num = 0
    for ch in parts[0]:
        d = ord(ch) - 48
        if d < 0 or d >= 10:
            return False
        num = num * 10 + d
    return num == 127


def inputs():
    fixed = ["127.0.0.1", "127.255.255.255", "127.1.2.3.4", "0127.0.0.1", "000000000000127.9.9.9",
             "127...", "127.0.0", "127", "", "10.0.0.1", "128.0.0.1", "12.7.0.1", "1270.0.0.1",
             "-127.0.0.1", " 127.0.0.1", "127 .0.0.1", "127a.0.0.1", ".127.0.0.1", "a.b.c.d",
             "127.x.y.z", "1.2.3", "localhost", "127.0.0.1:80", "::1"]
    rnd = random.Random(170)
    al = "0123456789....a -"
    gen = []
    for _ in range(26):
        head = rnd.choice(["127", "127", "12", "0127", "1", "", "1x7", str(rnd.randint(0, 999))])
        tail = "".join(rnd.choice(al) for _ in range(rnd.randint(0, 12)))
        gen.append(head + tail)
    return fixed + gen


def probe():
    calls, exp = [], []
    for s in inputs():
        calls.append(f'  io.println("[\\(solve("{s}"))]");')
        exp.append("[true]" if loopback(s) else "[false]")
    return "f=main():i64{\n" + "\n".join(calls) + "\n  <0\n};\n", exp


def evaluate(rec, tkcs, work):
    src = rec.get("tk_source") or ""
    r = {"task_id": TID, "story": STORY, "path": SPEC["path"],
         "prev_source_sha256": sha_text(src), "defect": SPEC["defect"]}
    if r["prev_source_sha256"] != SPEC["prev"]:
        r.update(status="skipped", reason="stale: tk_source is not the one this repair was written against")
        return r
    fixed = src
    for old, new in SPEC["edits"]:
        if fixed.count(old) != 1:
            r.update(status="failed", reason=f"edit does not match exactly once: {old!r}")
            return r
        fixed = fixed.replace(old, new)
    r.update(fixed=fixed, new_source_sha256=sha_text(fixed), edits=len(SPEC["edits"]),
             gates={}, output_check=SPEC["output_check"])
    f = os.path.join(work, TID + ".orig.tk")
    _write(f, src)
    r["errors_before"] = {tilde(t): base.errors(t, f) for t in tkcs}
    main, exp = probe()
    # the original, probed on each tkc that still builds it: the defect, shown
    r["probe_before"] = {}
    for t in tkcs:
        x, err = base.build_run(t, base.with_main(src, main), work, TID + ".orig.probe")
        if not err:
            got = x.stdout.splitlines()
            r["probe_before"][tilde(t)] = {
                "exit": x.returncode,
                "mismatch": sum(a != b for a, b in zip(got, exp)) + abs(len(got) - len(exp))}
    fails = []
    for t in tkcs:
        g = {}
        f = os.path.join(work, TID + ".tk")
        _write(f, fixed)
        g["check"] = base.errors(t, f)
        if g["check"]:
            fails.append(f"{tilde(t)}: --check {g['check']}")
        x, err = base.build_run(t, base.with_main(fixed, main), work, TID + ".probe")
        if err:
            fails.append(f"{tilde(t)}: probe {err}")
        else:
            got = x.stdout.splitlines()
            bad = [i for i, (a, b) in enumerate(zip(got, exp)) if a != b]
            g["probe"] = {"cases": len(exp), "mismatch": len(bad) + abs(len(got) - len(exp)),
                          "exit": x.returncode}
            if x.returncode != 0 or g["probe"]["mismatch"]:
                fails.append(f"{tilde(t)}: probe exit {x.returncode}, {g['probe']['mismatch']}/"
                             f"{len(exp)} lines differ from the reference")
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
    path = os.path.join(C, SPEC["path"])
    with tempfile.TemporaryDirectory(prefix="repair127381_") as work:
        r = evaluate(_load(path), tkcs, work)
        ok = r["status"] == "rewritten"
        if args.bank and ok:
            os.makedirs(REPLACED, exist_ok=True)
            man = manifest_tool.Manifest(MANIFEST)
            prev_rec_sha = manifest_tool.sha256_file(path)
            rec = _load(path)
            arch = os.path.join(REPLACED, TID + ".tk")
            if not os.path.exists(arch):
                _write(arch, rec["tk_source"])
            rec["tk_source"] = r["fixed"]
            rg = rec.setdefault("regen", {})
            mb_before = rg.get("min_bytes")
            rg["source_sha256"] = r["new_source_sha256"]
            p = os.path.join(work, TID + ".bank.tk")
            _write(p, r["fixed"])
            m = metrics.analyse(p, src=r["fixed"], tkc=tkcs[0]) or {}
            for k in ("min_bytes", "proxy_tokens", "max_depth", "lint_pattern_violations"):
                if m.get(k) is not None:
                    rg[k] = m[k]
            rg["rewrite131"] = {
                "wave": WAVE, "story": STORY, "ts": now, "tkc_bin_sha": stamps[0]["sha256"],
                "tkcs": stamps, "defect": r["defect"],
                "repairs": [list(e) for e in SPEC["edits"]],
                "prev_sha256": r["prev_source_sha256"], "output_check": r["output_check"],
                "output_after": None,
                "probe": r["gates"][tilde(tkcs[0])].get("probe"),
                "min_bytes_before": mb_before, "min_bytes_after": m.get("min_bytes"),
                "archived": f"audit/replaced/131/{TID}.tk", "attempts": 1,
            }
            with open(path, "w") as fh:
                json.dump(rec, fh)
            man.stamp(TID, path, save=False)
            man.save()
            with open(LEDGER, "a") as fh:
                fh.write(json.dumps({"task_id": TID, "wave": WAVE, "story": STORY,
                                     "status": "rewritten", "reason": "repaired:" + r["output_check"],
                                     "attempts": 1, "prev_sha256": prev_rec_sha,
                                     "new_sha256": manifest_tool.sha256_file(path), "ts": now,
                                     "tkc_bin_sha": stamps[0]["sha256"]}) + "\n")
    summary = {
        "stories": [STORY], "generated": now, "banked": bool(args.bank and ok), "tkcs": stamps,
        "by_status": {r["status"]: 1},
        "records": [{k: v for k, v in r.items() if k != "fixed"}],
    }
    with open(SUMMARY, "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps(summary["by_status"]), "BANKED" if summary["banked"] else
          ("DRY RUN" if not args.bank else "NOT BANKED (a gate failed)"))
    if not ok:
        print(TID, r["status"], r.get("reason"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
