#!/usr/bin/env python3
"""131.99 — the return-type gate is an exact match: census, A/B proof,
allowlist justification, routing (owner register 2026-11-12 item 4.3 (a)).

`regen_validate.return_type_conforms` used to hard-fail only an error-union
spec declared without a union (131.42) and flag every other spec/declared
return difference as the soft `return_type_mismatch` (172 banked
single_function records, 131.43's finding). It now hard-fails every
difference except the RETURN_TYPE_ALLOWLIST widenings (i32->i64,
u32->u64/i64). This tool:

  census     runs the old gate (reproduced verbatim below as
             `legacy_return_type_conforms`) and the new one over every banked
             record with a spec, text only (no compile). Before = soft
             mismatches; after = hard rejects; every flip is listed.
  --gates    A/B of run_shard.validate_one_gates on every flipped record
             (needs tkc): the old gate accepts, the new one rejects with a
             `return_type:` reason, and no other gate changes.
  --justify  the allowlist's identical-stdout justification (needs tkc):
             for each entry, a function of the spec type vs the same function
             declared at the wider type (`<a as T`) through diff_check
             (spec cases at the spec type's bounds plus 20 generated inputs);
             a narrowing (i64 -> i32) is the negative control and must
             diverge. Banked records that use an allowlist entry are listed
             too (the gate accepts them).
  --route    every flipped record goes to AGENT through
             `audit/pattern_sweep.131.99.patch.jsonl` (route_patch.merge_row:
             the record's current merged row is kept, a `return-type-drift`
             violation is appended); REGEN stays REGEN for the REGEN class
             only (131.109). route_merge replays the patch after 131.127, and
             131.99 is in size_gate_waiver.CORRECTNESS_SET (register 4.3 (a)
             joins it to the 4.1 set). No record is repaired here.

Outputs
  <corpus>/audit/return_type_gate_131.99.jsonl          one row per flipped record
  regen/freeze/return_type_gate_131.99.json             tracked summary
  <corpus>/audit/pattern_sweep.131.99.patch.jsonl       (--route)
"""
import argparse
import collections
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit
import driver as drv
import idiom_judge
import regen_validate as rv
import route_patch

STORY = "131.99"
RULE = "return-type-drift"
CORPUS = os.environ.get("TOKE_CORPUS_DIR", os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04"))
OUT_SUMMARY = os.path.join(HERE, "freeze", f"return_type_gate_{STORY}.json")
TS = 1795392000                                   # 2026-11-23T00:00:00Z (reproducible patch)


def legacy_return_type_conforms(spec, src):
    """The pre-131.99 gate, verbatim (empty allowlist): hard only for the
    131.42 error-union cases, every other difference a soft mismatch."""
    name = rv.target_name(spec)
    if not name:
        return True, "no target signature in spec", None, None
    want = rv.spec_return_type(spec)
    if not want:
        return True, "no spec return type", None, None
    got = rv.declared_return_type(src, name)
    if got is None:
        return True, f"function {name} not found / return type unparsed", None, None
    if rv.is_error_union(want) and not rv.is_error_union(got):
        return False, f"{name} declared `{got}`, spec `{want}` requires an error union", None, got
    callee = drv.find_target(spec, src) if spec.get("task_type") == "single_function" else None
    if rv.is_error_union(want) and callee and callee != name:
        cgot = rv.declared_return_type(src, callee)
        if cgot is not None and not rv.is_error_union(cgot):
            return (False, (f"driver callee `{callee}` declared `{cgot}` (target {name} is `{got}`), "
                            f"spec `{want}` requires an error union"), None, got)
    nw, ng = rv.norm_return_type(want), rv.norm_return_type(got)
    if nw != ng:
        return True, "ok (soft mismatch)", f"{name} declared `{got}`, spec `{want}`", got
    return True, "ok", None, got


def load_manifest(corpus):
    with open(os.path.join(corpus, "MANIFEST.jsonl")) as f:
        return [json.loads(line) for line in f if line.strip()]


def _src(corpus, entry):
    with open(os.path.join(corpus, entry["path"])) as f:
        return json.load(f)["tk_source"]


def census(specs, man, corpus):
    """(flip rows, allowlisted rows, counts, pairs). Every non-stdin record
    with a spec is checked."""
    counts = collections.Counter()
    pairs = collections.Counter()
    rows, allowed = [], []
    for e in man:
        spec = specs.get(e["task_id"])
        if not spec or spec.get("task_type") == "stdin_program":
            counts["not_applicable"] += 1
            continue
        src = _src(corpus, e)
        o_ok, o_d, o_soft, _o_decl = legacy_return_type_conforms(spec, src)
        n_ok, n_d, _n_soft, n_decl = rv.return_type_conforms(spec, src)
        if n_decl is None:
            counts["gate_not_applicable"] += 1
            continue
        tt = spec.get("task_type")
        old = "reject" if not o_ok else ("soft" if o_soft else "pass")
        new = "pass" if n_ok else "reject"
        counts[f"before:{tt}:{old}"] += 1
        counts[f"after:{tt}:{new}"] += 1
        counts[f"ab:{old}->{new}"] += 1
        want = rv.spec_return_type(spec)
        nw, ng = rv.norm_return_type(want), rv.norm_return_type(n_decl)
        if n_ok and nw != ng:
            allowed.append({"task_id": e["task_id"], "spec": want, "declared": n_decl, "detail": n_d})
        if o_ok and not n_ok:
            pairs[f"{nw} -> {ng}"] += 1
            rows.append({"task_id": e["task_id"], "category": e["category"], "task_type": tt,
                         "spec": want, "declared": n_decl, "pair": f"{nw} -> {ng}",
                         "old_detail": o_d, "old_soft": o_soft, "new_detail": n_d})
    return rows, allowed, counts, pairs


# ----------------------------------------------------------- A/B gates ---
def gates_ab(rows, specs, man_by_id, corpus, workdir):
    """run_shard.validate_one_gates on each flipped record with the legacy
    return-type function and with the new one."""
    import run_shard
    out = {"records": 0, "return_type_gate_true_to_false": 0, "other_gates_identical": 0,
           "before_ok": 0, "after_ok": 0, "after_first_reason_return_type": 0,
           "rejected_before_by_an_earlier_gate": {}, "anomalies": []}
    earlier = collections.Counter()
    new_fn = run_shard.return_type_conforms
    for r in rows:
        tid = r["task_id"]
        spec = specs[tid]
        src = _src(corpus, man_by_id[tid])
        raw = src
        if spec.get("task_type") == "single_function":
            n = idiom_judge.stub_prefix_len(src)
            raw = src[n:] if n else src
        res = {}
        try:
            for label, fn in (("before", legacy_return_type_conforms), ("after", new_fn)):
                run_shard.return_type_conforms = fn
                _rec, ok, reason, g = run_shard.validate_one_gates(spec, raw, workdir)
                res[label] = (ok, reason, g)
        finally:
            run_shard.return_type_conforms = new_fn
        (bok, breason, bg), (aok, areason, ag) = res["before"], res["after"]
        out["records"] += 1
        out["before_ok"] += bool(bok)
        out["after_ok"] += bool(aok)
        flipped = bg.get("return_type") is True and ag.get("return_type") is False
        out["return_type_gate_true_to_false"] += flipped
        out["after_first_reason_return_type"] += (areason or "").startswith("return_type:")
        if not bok:
            earlier[(breason or "?").split(":")[0]] += 1
        same = {k: v for k, v in bg.items() if k != "return_type"} == \
               {k: v for k, v in ag.items() if k != "return_type"}
        out["other_gates_identical"] += same
        if not (flipped and same and not aok):
            out["anomalies"].append({"task_id": tid, "before": [bok, breason, bg.get("return_type")],
                                     "after": [aok, areason, ag.get("return_type")],
                                     "other_gates_identical": same})
    out["rejected_before_by_an_earlier_gate"] = dict(earlier)
    return out


# ------------------------------------------------- allowlist justification ---
BOUNDS = {"i32": [-2147483648, -1, 0, 1, 2147483647],
          "u32": [0, 1, 2147483647, 2147483648, 4294967295],
          "i64": [-9223372036854775808, -2147483649, 4294967296, 9223372036854775807]}
NEGATIVE_CONTROL = ("i64", "i32")


def probe(w, g, workdir=None):
    """diff_check: f=widen(a:w):w{<a} vs f=widen(a:w):g{<a as g}."""
    import diff_check
    spec = {"task_id": "A-MTH-9999v1", "category": "A-MTH", "task_type": "single_function",
            "difficulty": 1, "description": f"Write f=widen(a:{w}):{w} returning a.",
            "input_types": [w], "output_type": w,
            "test_cases": [{"inputs": [v], "expected": v} for v in BOUNDS[w]]}
    orig = f"m=harness;\ni=io:std.io;\nf=widen(a:{w}):{w}{{<a}};\n"
    cand = f"m=harness;\ni=io:std.io;\nf=widen(a:{w}):{g}{{<a as {g}}};\n"
    r = diff_check.differential(spec, orig, cand, workdir)
    ch = r.get("checks") or {}
    return {"spec": w, "declared": g, "verdict": r["verdict"],
            "spec_cases": (ch.get("spec_cases") or {}).get("identical"),
            "spec_case_inputs": BOUNDS[w],
            "generated": {k: (ch.get("generated") or {}).get(k) for k in ("n", "identical")},
            "reason": r.get("reason")}


def justify(workdir=None):
    out = {"entries": [], "negative_control": None}
    for w in sorted(rv.RETURN_TYPE_ALLOWLIST):
        for g in sorted(rv.RETURN_TYPE_ALLOWLIST[w]):
            out["entries"].append(probe(w, g, workdir))
    out["negative_control"] = probe(*NEGATIVE_CONTROL, workdir)
    out["all_identical"] = all(e["verdict"] == "identical" for e in out["entries"])
    out["negative_control_diverged"] = out["negative_control"]["verdict"] == "diverged"
    return out


# --------------------------------------------------------------- routing ---
def reason(r):
    return (f"return-type drift — the target is declared `{r['declared']}` but the spec says "
            f"`{r['spec']}`. Declare the spec's return type and return a value of it; this is the "
            "one signature change the rewrite must make (the bank's return_type gate is an exact "
            "match since 131.99, allowing only i32->i64 and u32->u64/i64), and every printed line "
            f"must stay the same (regen/freeze/return_type_gate_{STORY}.json)")


def route_rows(rows, cur, ts=TS):
    out, dest = [], collections.Counter()
    for r in sorted(rows, key=lambda x: x["task_id"]):
        tid = r["task_id"]
        base = cur.get(tid)
        keep = ("REGEN",) if base and route_patch.regen_class(base) else ()
        row = route_patch.merge_row(base, tid, STORY, RULE, "AGENT", reason(r),
                                    category=r["category"], task_type=r["task_type"], ts=ts,
                                    keep_buckets=keep)
        row.setdefault("source", "regen")
        out.append(row)
        dest[f"{(base or {}).get('bucket')}->{row['bucket']}"] += 1
    return out, dest


def route(rows, corpus):
    import route_merge
    upto = [p for p in route_merge.PATCHES if p[0] != STORY]
    _, cur, _, _ = route_merge.build(corpus, patches=tuple(upto))
    out, dest = route_rows(rows, cur)
    path = route_patch.write_patch(corpus, STORY, out)
    prior = {r["task_id"]: (cur.get(r["task_id"]) or {}).get("patches") or [] for r in rows}
    return {"patch": os.path.join("corpus", "regen_v04", "audit", os.path.basename(path)),
            "patch_rows": len(out), "transitions": dict(dest),
            "already_routed_by": dict(collections.Counter(",".join(p) or "none" for p in prior.values())),
            "not_routed_before": sorted(t for t, p in prior.items() if not p),
            "rebuild": "python3 regen/route_merge.py (replays 131.99 after 131.127)"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--gates", action="store_true", help="A/B validate_one_gates on every flip (tkc)")
    ap.add_argument("--justify", action="store_true", help="differential proof of the allowlist (tkc)")
    ap.add_argument("--route", action="store_true", help="write the AGENT routing patch")
    ap.add_argument("--summary", default=OUT_SUMMARY)
    a = ap.parse_args(argv)
    specs = audit.load_specs(a.corpus)
    man = load_manifest(a.corpus)
    rows, allowed, counts, pairs = census(specs, man, a.corpus)
    out_jsonl = os.path.join(a.corpus, "audit", f"return_type_gate_{STORY}.jsonl")
    with open(out_jsonl, "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    s = {
        "story": STORY, "register": "owner-decisions-2026-11-12 item 4.3 (a), recorded 2026-11-15",
        "date": time.strftime("%Y-%m-%d", time.gmtime(TS)),
        "gate": "regen_validate.return_type_conforms: 131.42 error-union cases, then an exact match "
                "(norm_return_type) modulo RETURN_TYPE_ALLOWLIST",
        "allowlist": {k: sorted(v) for k, v in rv.RETURN_TYPE_ALLOWLIST.items()},
        "records_checked": sum(v for k, v in counts.items() if k.startswith("ab:")),
        "counts": dict(sorted(counts.items())),
        "before_soft_mismatch": sum(v for k, v in counts.items() if k.startswith("before:") and k.endswith(":soft")),
        "after_newly_rejected": len(rows),
        "newly_rejected_by_task_type": dict(collections.Counter(r["task_type"] for r in rows)),
        "newly_rejected_by_category": dict(sorted(collections.Counter(r["category"] for r in rows).items())),
        "pairs": dict(pairs.most_common()),
        "banked_records_using_allowlist": allowed,
        "newly_rejected_task_ids": sorted(r["task_id"] for r in rows),
        "jsonl": os.path.join("corpus", "regen_v04", "audit", os.path.basename(out_jsonl)),
    }
    if a.gates or a.justify:
        with tempfile.TemporaryDirectory(prefix="rt131_99_") as wd:
            if a.gates:
                s["gates_ab"] = gates_ab(rows, specs, {e["task_id"]: e for e in man}, a.corpus, wd)
            if a.justify:
                s["allowlist_justification"] = justify(wd)
    if a.route:
        s["routing"] = route(rows, a.corpus)
    if a.summary:
        with open(a.summary, "w") as f:
            json.dump(s, f, indent=1)
            f.write("\n")
    brief = {k: s[k] for k in ("records_checked", "before_soft_mismatch", "after_newly_rejected",
                               "newly_rejected_by_task_type", "pairs")}
    brief["banked_records_using_allowlist"] = len(allowed)
    for k in ("gates_ab", "allowlist_justification", "routing"):
        if k in s:
            brief[k] = {x: y for x, y in s[k].items() if x not in ("entries", "not_routed_before")}
    print(json.dumps(brief, indent=1))
    return s


if __name__ == "__main__":
    main()
