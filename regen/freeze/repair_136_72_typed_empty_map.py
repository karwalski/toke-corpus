#!/usr/bin/env python3
"""136.72 (3) -- repair records that spell an empty typed map `@(str:i64)`.

What the row said.  A-ERR-0030v31/v39/v77 "pass --check and fail the build"
(E9003 `%i64` for an empty `@(str:i64)` literal; E9005).

What current toke says (toke 6396596, 2026-09-27).  No disagreement is left:
`@(str:i64)` in VALUE position is a type used as a value, E4033 at --check
and at build alike (127.226 / C171 made `@($str:$i64)` the typed empty map
and C024 rejects a bare type name as a value).  The three records are simply
invalid toke now.  A scan of every MANIFEST record for a builtin type name
opening a map/array literal in value position found six; the pinned compiler
confirms E4033 on four (the three above and A-STR-0034v18, which writes
`@(str:i64)()`); the other two are type-position `as @(str:i64)` casts and
check clean.

The repair.  Mechanical: `@(K:V)` / `@(K:V)()` in value position, K and V
builtin scalar names, becomes `@($K:$V)`.  Every repaired record must then
--check clean and build and run under the pinned compiler.  None of the four
has test cases (has_tests false in err_recheck_131.67) and none ever built
before (the E9003/E9005 above), so there is no earlier output to compare
against: the post-repair stdout is recorded as the record's first observed
behaviour, and the stamp says so (output_check "no_baseline").

A record that checks clean after the rewrite but does not build is left
unbanked and flagged: A-ERR-0030v39 is one -- behind its E4033 sat the real
check/build disagreement the row describes, E9005 at codegen for a match that
lists the error type's variants as flat arms (`mt lookup(..) {$ok:v v;
$notfound:e ..;$emptycollection:e 0}`), which --check accepts.  That is a
compiler defect (reported for Batch GB), not a corpus one.

Follows the 131.78 bank convention: original archived under
corpus/regen_v04/audit/replaced/136/<task_id>.tk (first touch only);
tk_source replaced; regen.source_sha256 and metrics recomputed; regen.repair136
stamped; MANIFEST re-stamped; one ledger line per record in
corpus/regen_v04/ledger/repair_136.jsonl.  Summary: regen/freeze/repair_136.72.json.

Usage:  python3 regen/freeze/repair_136_72_typed_empty_map.py [--bank]
        (without --bank: dry run, writes the summary only)
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
CORP = os.path.dirname(REGEN)
sys.path.insert(0, REGEN)
import manifest_tool  # noqa: E402
import metrics        # noqa: E402
import tkc_pin        # noqa: E402

# corpus/ is untracked (local data): a worktree has none, so read the main
# checkout's unless told otherwise.
C = os.environ.get("TOKE_CORPUS_V04") or os.path.join(
    os.path.expanduser("~"), "tk", "toke-corpus", "corpus", "regen_v04")
MANIFEST = os.path.join(C, "MANIFEST.jsonl")
REPLACED = os.path.join(C, "audit", "replaced", "136")
LEDGER = os.path.join(C, "ledger", "repair_136.jsonl")
SUMMARY = os.path.join(HERE, "repair_136.72.json")
STORY, WAVE = "136.72", "typed-empty-map"

SCALARS = r"(?:str|i8|i16|i32|i64|u8|u16|u32|u64|f32|f64|bool|byte)"
# a builtin type name opening a literal, preceded by something that is not a
# type-position introducer (`:` annotation, `!` error type, `as` cast)
CANDIDATE = re.compile(r"([^\s:!])(\s*)@\((" + SCALARS + r")\s*[:)]")
# the repairable shape: value-position `@(K:V)` with an optional `()` call
REWRITE = re.compile(r"(=|\(|;|,|\.|\s)(mut\.)?@\((" + SCALARS + r"):(" + SCALARS + r")\)(\(\))?")

sha_text = lambda s: hashlib.sha256(s.encode()).hexdigest()


def run(argv, cwd, timeout=60):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def codes(tkc, path):
    p = run([tkc, "--check", path], os.path.dirname(path))
    out = []   # the ERROR codes, in order
    for line in (p.stdout + p.stderr).splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("severity") == "error":   # a W-code warning does not block
            out.append(d.get("error_code"))
    return out


def rewrite(src):
    def sub(m):
        lead, mut, k, v = m.group(1), m.group(2) or "", m.group(3), m.group(4)
        return "%s%s@($%s:$%s)" % (lead, mut, k, v)
    # never touch a type position: `x:@(str:i64)`, `as @(str:i64)`
    out, n = [], 0
    last = 0
    for m in REWRITE.finditer(src):
        before = src[:m.start(1) + len(m.group(1))].rstrip()
        if before.endswith(":") or before.endswith("!") or re.search(r"\bas$", before):
            continue
        out.append(src[last:m.start()])
        out.append(sub(m))
        last = m.end()
        n += 1
    out.append(src[last:])
    return "".join(out), n


def main(bank):
    pin = tkc_pin.pin()
    tkc = pin.argv0
    rows = [json.loads(l) for l in open(MANIFEST) if l.strip()]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results, scanned = [], 0
    work = tempfile.mkdtemp(prefix="repair136_")
    for row in rows:
        scanned += 1
        path = os.path.join(C, row["path"])
        rec = json.load(open(path))
        src = rec.get("tk_source") or ""
        if not CANDIDATE.search(src):
            continue
        tid = row["task_id"]
        f = os.path.join(work, tid + ".tk")
        open(f, "w").write(src)
        before = codes(tkc, f)
        r = {"task_id": tid, "path": row["path"], "task_type": row.get("task_type"),
             "codes_before": before}
        if "E4033" not in before:
            r["status"] = "not_in_class"
            results.append(r)
            continue
        fixed, n = rewrite(src)
        open(f, "w").write(fixed)
        after = codes(tkc, f)
        r.update({"rewrites": n, "codes_after": after})
        if after:
            r["status"] = "flagged"
            r["reason"] = "still fails --check after the mechanical rewrite"
            results.append(r)
            continue
        binp = os.path.join(work, tid)
        b = run([tkc, f, "-o", binp], work, timeout=120)
        if b.returncode != 0:
            r.update(status="flagged", reason="checks clean, does not build",
                     build_tail=(b.stdout + b.stderr)[-400:])
            results.append(r)
            continue
        if r["task_type"] == "full_program":
            x = run([binp], work, timeout=30)
            r.update(exit=x.returncode, stdout=x.stdout, stderr_tail=x.stderr[-200:])
            if x.returncode != 0 or re.search(r"^RT\d{3}:", x.stderr, re.M):
                r.update(status="flagged", reason="builds, exits %d" % x.returncode)
                results.append(r)
                continue
        r.update(status="rewritten", fixed=fixed, prev_source_sha256=sha_text(src),
                 new_source_sha256=sha_text(fixed), output_check="no_baseline")
        results.append(r)

    if bank:
        os.makedirs(REPLACED, exist_ok=True)
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        man = manifest_tool.Manifest(MANIFEST)
        led = []
        for r in results:
            if r["status"] != "rewritten":
                continue
            path = os.path.join(C, r["path"])
            prev_rec_sha = manifest_tool.sha256_file(path)
            rec = json.load(open(path))
            arch = os.path.join(REPLACED, r["task_id"] + ".tk")
            if not os.path.exists(arch):
                open(arch, "w").write(rec["tk_source"])
            rec["tk_source"] = r["fixed"]
            rg = rec.setdefault("regen", {})
            mb_before = rg.get("min_bytes")
            rg["source_sha256"] = r["new_source_sha256"]
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, r["task_id"] + ".tk")
                open(p, "w").write(r["fixed"])
                m = metrics.analyse(p, src=r["fixed"], tkc=tkc) or {}
            for k in ("min_bytes", "proxy_tokens", "max_depth", "lint_pattern_violations"):
                if m.get(k) is not None:
                    rg[k] = m[k]
            rg["repair136"] = {
                "wave": WAVE, "story": STORY, "ts": now, "tkc_bin_sha": pin.sha256,
                "toke_head": pin.toke_head,
                "defect": ("`@(K:V)` written as a value: a type used as a value, E4033 "
                           "at --check since 127.226/C024 (before that it checked clean "
                           "and failed the build with E9003/E9005)"),
                "repair": "@(K:V) / @(K:V)() in value position -> @($K:$V)",
                "prev_sha256": r["prev_source_sha256"], "output_check": "no_baseline",
                "output_after": r.get("stdout"), "min_bytes_before": mb_before,
                "min_bytes_after": m.get("min_bytes"),
                "archived": "audit/replaced/136/%s.tk" % r["task_id"],
            }
            with open(path, "w") as fh:
                json.dump(rec, fh)
            man.stamp(r["task_id"], path, save=False)
            led.append({"task_id": r["task_id"], "wave": WAVE, "story": STORY,
                        "status": "rewritten", "prev_sha256": prev_rec_sha,
                        "new_sha256": manifest_tool.sha256_file(path), "ts": now,
                        "tkc_bin_sha": pin.sha256, "output_check": "no_baseline"})
        man.save()
        with open(LEDGER, "a") as fh:
            for e in led:
                fh.write(json.dumps(e) + "\n")

    summary = {
        "story": STORY, "generated": now, "banked": bank,
        "tkc": {"sha256": pin.sha256, "version": pin.version, "toke_head": pin.toke_head,
                "toke_src_dirty": pin.toke_dirty},
        "scanned": scanned,
        "candidates": len(results),
        "by_status": {s: sum(1 for r in results if r["status"] == s)
                      for s in sorted({r["status"] for r in results})},
        "records": [{k: v for k, v in r.items() if k != "fixed"} for r in results],
    }
    with open(SUMMARY, "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps(summary["by_status"]), "scanned", scanned,
          "BANKED" if bank else "DRY RUN")
    pin.close()


if __name__ == "__main__":
    main(bank="--bank" in sys.argv)
