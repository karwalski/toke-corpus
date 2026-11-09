#!/usr/bin/env python3
"""131.83 — banked records that fail inside an interpolation segment.

Until toke 127.24 (`cbb1f37`) an interpolated segment `"\\(x)"` was bytes in a
string token: name resolution never saw it, so `tkc --check` passed a record
whose interpolation names an identifier that does not exist, and the record
was banked with `compiler_exit_code: 0`, `judge.accepted: true`. The compiler
now parses each segment into the AST, so name resolution AND type checking see
it. 127.24 reported 11 such records with an undeclared name; re-measured on the
same 10,639 interpolation-bearing records that class is EMPTY (no E3011 at
all under the 3.1.0 pin, nor inside a segment of any of the 3,177 archived
originals under audit/replaced/), but the same root cause hides TYPE
errors: a call inside `\\(...)` was never type-checked either. This tool finds
every banked record whose diagnostic lies inside a segment, whatever the code,
so it can be repaired before the re-freeze, and routes it:

  1. `tkc --check` (pinned binary) on every banked record whose source has an
     interpolation (`\\(`) — the 10,639 127.24 measured;
  2. a record is IN SCOPE when it is banked as accepted (judge.accepted and
     compiler_exit_code 0) and now fails with a diagnostic whose offset lies
     inside an interpolation segment of a string literal;
  3. --route: AGENT (131.15) when the spec carries test cases — the rewrite
     fixes the segment and the test lock proves it. Without test cases:
     REGEN for the A-*/full_program class (131.16 regenerates it), AGENT for
     anything else, which 131.16 cannot regenerate (route_patch.route_bucket,
     131.109).

Every other compile failure is counted (by error code), not routed: it is not
this defect. Reads records; writes nothing under corpus/regen_v04/<CAT>/.

Outputs
  corpus/regen_v04/audit/interp_name_check_131.83.jsonl   one row per failing record
  regen/freeze/interp_name_check_131.83.json              tracked summary
  corpus/regen_v04/audit/pattern_sweep.131.83.patch.jsonl (--route)
"""
import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                    # noqa: E402
import route_patch                              # noqa: E402
import tkc_pin                                  # noqa: E402

STORY = "131.83"
RULE = "interp-segment-unchecked"
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
OUT_JSONL = os.path.join(CORPUS, "audit", f"interp_name_check_{STORY}.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", f"interp_name_check_{STORY}.json")


def interp_spans(src):
    """[(start, end)] byte offsets of every `\\(...)` segment inside a string
    literal (depth-aware over parentheses, nested strings skipped)."""
    spans, i, n = [], 0, len(src)
    while i < n:
        if src[i] == '"':
            i += 1
            while i < n and src[i] != '"':
                if src[i] == "\\" and i + 1 < n and src[i + 1] == "(":
                    start, j, depth = i, i + 2, 1
                    while j < n and depth:
                        c = src[j]
                        if c == '"':                    # a string inside the segment
                            j += 1
                            while j < n and src[j] != '"':
                                j += 2 if src[j] == "\\" else 1
                        elif c == "(":
                            depth += 1
                        elif c == ")":
                            depth -= 1
                        j += 1
                    spans.append((start, j))
                    i = j
                    continue
                i += 2 if src[i] == "\\" else 1
            i += 1
        elif src[i] == "#":                             # line comment
            while i < n and src[i] != "\n":
                i += 1
        else:
            i += 1
    return spans


def diagnostics(stdout, stderr):
    out = []
    for line in (stderr + "\n" + stdout).splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("error_code"):
                out.append(d)
    return out


def check_one(args):
    tid, path, tkc = args
    rec = json.load(open(path))
    src = rec.get("tk_source") or ""
    if "\\(" not in src:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".tk", delete=False) as f:
        f.write(src)
        tk = f.name
    try:
        p = subprocess.run([tkc, "--check", tk], capture_output=True, text=True, errors="replace", timeout=60)
    finally:
        os.unlink(tk)
    if p.returncode == 0:
        return {"task_id": tid, "rc": 0}
    diags = diagnostics(p.stdout, p.stderr)
    spans = interp_spans(src)
    hits = []
    for d in diags:
        off = d.get("span_start", (d.get("pos") or {}).get("offset"))
        if off is not None and any(a <= off < b for a, b in spans):
            seg = next(src[a:b] for a, b in spans if a <= off < b)
            hits.append({"error_code": d["error_code"], "message": d.get("message"), "offset": off,
                         "segment": seg, "line": (d.get("pos") or {}).get("line")})
    v = rec.get("validation") or {}
    j = rec.get("judge") or {}
    return {"task_id": tid, "rc": p.returncode,
            "error_codes": [d["error_code"] for d in diags],
            "messages": [d.get("message") for d in diags][:3],
            "in_interpolation": hits,
            "banked_accepted": v.get("compiler_exit_code") == 0 and j.get("accepted") is True,
            "task_type": (rec.get("regen") or {}).get("task_type")}


def route(rows, corpus, specs, ts=None):
    cur = route_patch.current_rows(corpus, extra=[s for s in route_patch.APPLY_ORDER if s < STORY])
    out, dest = [], {"AGENT": [], "REGEN": []}
    for r in rows:
        if not r.get("in_scope"):
            continue
        spec = specs.get(r["task_id"]) or {}
        tests = bool(spec.get("test_cases"))
        bucket = route_patch.route_bucket({"source": "regen", "category": spec.get("category"),
                                           "task_type": spec.get("task_type") or r.get("task_type")}, tests)
        names = ", ".join(sorted({h["segment"] for h in r["in_interpolation"]}))
        h = r["in_interpolation"][0]
        reason = (f"accepted record that no longer compiles: inside the interpolation {names} "
                  f"tkc reports {h['error_code']}: {h['message']}. It was banked only because tkc "
                  f"did not name-resolve or type-check interpolated segments before toke 127.24 — "
                  f"fix the expression in the segment (declare the binding, or pass arguments of "
                  f"the declared parameter types), so the program compiles and prints what its "
                  f"test lock expects"
                  + ("" if tests else "; " + route_patch.no_tests_note(bucket))
                  + f" (regen/freeze/interp_name_check_{STORY}.json)")
        out.append(route_patch.merge_row(cur.get(r["task_id"]), r["task_id"], STORY, RULE, bucket, reason,
                                         category=spec.get("category"), task_type=spec.get("task_type"),
                                         ts=ts))
        dest[bucket].append(r["task_id"])
    path = route_patch.write_patch(corpus, STORY, out)
    return {"patch": os.path.relpath(path, os.path.dirname(HERE)), "patch_rows": len(out),
            "agent_task_ids": sorted(dest["AGENT"]), "regen_task_ids": sorted(dest["REGEN"]),
            "bucket_before": {str(k): v for k, v in collections.Counter(
                (cur.get(x["task_id"]) or {}).get("bucket") for x in out).items()},
            "apply": route_patch.apply_command()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--route", action="store_true")
    ap.add_argument("--reuse", action="store_true",
                    help="route from the existing jsonl + summary instead of re-checking the corpus")
    a = ap.parse_args(argv)
    man = [json.loads(line) for line in open(os.path.join(a.corpus, "MANIFEST.jsonl")) if line.strip()]
    if a.reuse:
        with open(OUT_JSONL) as f:
            failing = [json.loads(x) for x in f if x.strip()]
        with open(OUT_SUMMARY) as f:
            prev = json.load(f)
        stamp, n_interp = prev["tkc"], prev["with_interpolation"]
    else:
        with tkc_pin.pin() as pinned:
            jobs = [(e["task_id"], os.path.join(a.corpus, e["path"]), pinned.argv0) for e in man]
            with ProcessPoolExecutor(a.jobs) as ex:
                results = [r for r in ex.map(check_one, jobs, chunksize=32) if r is not None]
            stamp = pinned.stamp()
        failing = [r for r in results if r["rc"] != 0]
        n_interp = len(results)
    for r in failing:
        r["in_scope"] = bool(r["in_interpolation"]) and r["banked_accepted"]
        r.setdefault("tkc_bin_sha", stamp["tkc_bin_sha"])
    with open(OUT_JSONL, "w") as f:
        for r in failing:
            f.write(json.dumps(r) + "\n")
    scope = sorted(r["task_id"] for r in failing if r["in_scope"])
    other = collections.Counter(c for r in failing if not r["in_scope"] for c in sorted(set(r["error_codes"])))
    summary = {
        "story": STORY, "date": time.strftime("%Y-%m-%d"), "tkc": stamp,
        "records": len(man), "with_interpolation": n_interp,
        "compile_fail": len(failing),
        "in_scope": len(scope), "in_scope_task_ids": scope,
        "in_scope_detail": [{"task_id": r["task_id"], "task_type": r["task_type"],
                             "segments": sorted({h["segment"] for h in r["in_interpolation"]}),
                             "error_codes": sorted({h["error_code"] for h in r["in_interpolation"]})}
                            for r in failing if r["in_scope"]],
        "interp_diag_not_banked_accepted": sorted(r["task_id"] for r in failing
                                                  if r["in_interpolation"] and not r["banked_accepted"]),
        "other_compile_fail": len(failing) - len(scope),
        "other_compile_fail_codes": dict(other.most_common()),
        "jsonl": os.path.relpath(OUT_JSONL, os.path.dirname(HERE)),
    }
    if a.route:
        specs = audit.load_specs(a.corpus)
        summary["routing"] = route(failing, a.corpus, specs)
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    brief = {k: summary[k] for k in ("records", "with_interpolation", "compile_fail", "in_scope",
                                     "in_scope_task_ids", "other_compile_fail", "other_compile_fail_codes")}
    if "routing" in summary:
        brief["routing"] = {k: summary["routing"][k] for k in ("patch_rows", "agent_task_ids", "regen_task_ids")}
    print(json.dumps(brief, indent=1))
    return summary


if __name__ == "__main__":
    main()
