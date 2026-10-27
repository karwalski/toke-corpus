#!/usr/bin/env python3
"""131.43 — the signature gate is type-aware: census, A/B proof, routing.

`regen_validate.signature_conforms` compared parameter COUNTS only, so
single_function records were banked declaring `i64` where the spec says
`u64`. It now compares every parameter type (norm_type, plus the explicit
lossless SIGNATURE_WIDENING allowlist). This tool runs the old (arity-only,
`[^)]*` regex — reproduced verbatim below) and the new gate over every banked
record with a spec signature, so the blast radius is the whole corpus, not a
sample: the gate is pure text, no compile.

Routing (--route): every record the new gate rejects goes to the 131.15 AGENT
bucket through `audit/pattern_sweep.131.43.patch.jsonl` (route_patch.merge_row:
the record's current row is kept, a `signature-drift` violation is appended,
bucket_reason says which parameter to re-type). The bank's worker gates run
the same signature_conforms, so a candidate that keeps the drift cannot bank.
Records already bucketed REGEN stay REGEN (a regenerated record meets the gate
or is not banked). Nothing under corpus/regen_v04/<CAT>/ is written.

Outputs
  corpus/regen_v04/audit/signature_gate_131.43.jsonl   one row per rejected record
  regen/freeze/signature_gate_131.43.json              tracked summary
  corpus/regen_v04/audit/pattern_sweep.131.43.patch.jsonl  (--route)
"""
import argparse
import collections
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                    # noqa: E402
import regen_validate as rv                     # noqa: E402
import route_patch                              # noqa: E402

STORY = "131.43"
RULE = "signature-drift"
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
OUT_JSONL = os.path.join(CORPUS, "audit", f"signature_gate_{STORY}.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", f"signature_gate_{STORY}.json")
PREV_FINDING = os.path.join(HERE, "freeze", "u64_recheck_131.40.json")
_DETAIL = re.compile(r"^(?P<fn>[a-z0-9]+): param (?P<i>\d+) `(?P<p>[^`]*)` declared `(?P<got>[^`]*)`, spec `(?P<want>[^`]*)`$")


def legacy_signature_conforms(spec, src):
    """The pre-131.43 gate, verbatim: arity only, `[^)]*` parameter lists."""
    m = re.search(r"f=([a-z][a-z0-9]*)\(([^)]*)\)", spec.get("description_v03", "") or spec.get("description", ""))
    if not m:
        return True, "no target signature in spec"
    name, params = m.group(1), m.group(2)
    want_arity = len([p for p in params.split(";") if p.strip()])
    g = re.search(rf"f={name}\(([^)]*)\)", src)
    if not g:
        return False, f"function {name} not found in output"
    got_arity = len([p for p in g.group(1).split(";") if p.strip()])
    if got_arity != want_arity:
        return False, f"{name}: expected {want_arity} params, got {got_arity}"
    return True, "ok"


def load_manifest(corpus):
    with open(os.path.join(corpus, "MANIFEST.jsonl")) as f:
        return [json.loads(line) for line in f if line.strip()]


def census(specs, man, corpus):
    """(rows for every record whose verdict changed or the new gate rejects,
    counts). Every non-stdin record with a spec is checked."""
    counts = collections.Counter()
    pairs = collections.Counter()
    rows = []
    for e in man:
        spec = specs.get(e["task_id"])
        if not spec or spec.get("task_type") == "stdin_program":
            counts["not_applicable"] += 1
            continue
        src = json.load(open(os.path.join(corpus, e["path"])))["tk_source"]
        old_ok, old_d = legacy_signature_conforms(spec, src)
        new_ok, new_d = rv.signature_conforms(spec, src)
        counts[f"{spec.get('task_type')}:{'pass' if new_ok else 'reject'}"] += 1
        counts[f"ab:{'pass' if old_ok else 'reject'}->{'pass' if new_ok else 'reject'}"] += 1
        if old_ok and new_ok:
            continue
        m = _DETAIL.match(new_d or "")
        if m:
            pairs[f"{rv.norm_type(m['want'])} -> {rv.norm_type(m['got'])}"] += 1
        rows.append({"task_id": e["task_id"], "category": e["category"], "task_type": spec.get("task_type"),
                     "old_ok": old_ok, "old_detail": old_d, "new_ok": new_ok, "new_detail": new_d,
                     "function": m["fn"] if m else None, "param": int(m["i"]) if m else None,
                     "declared": m["got"] if m else None, "spec": m["want"] if m else None})
    return rows, counts, pairs


def drift_slots(spec, src):
    """Every (index, spec type, declared type) the new gate would reject —
    signature_conforms stops at the first, the census counts all."""
    desc = spec.get("description_v03", "") or spec.get("description", "")
    name, want = rv._sig_params(desc)
    _n, got = rv._sig_params(src, name, require_ret=True)
    out = []
    for i, (w, g) in enumerate(zip(want or [], got or [])):
        if ":" in w and ":" in g:
            wt, gt = w.split(":", 1)[1].strip(), g.split(":", 1)[1].strip()
            if not rv.param_type_conforms(wt, gt):
                out.append((i + 1, wt, gt))
    return out


def route(rows, corpus, specs, ts=None):
    cur = route_patch.current_rows(corpus)
    out, kept_regen = [], []
    for r in rows:
        if r["new_ok"]:
            continue
        tid = r["task_id"]
        base = cur.get(tid)
        if base and base.get("bucket") == "REGEN":
            kept_regen.append(tid)
        slots = r.get("slots") or []
        what = "; ".join(f"param {i} is declared `{g}` but the spec says `{w}`" for i, w, g in slots) \
            or r["new_detail"]
        reason = (f"signature drift — {what}. Re-type the parameter(s) to the spec's type; this is "
                  f"the one signature change the rewrite must make (the bank's signature gate now "
                  f"rejects the drifted declaration), and every printed line must stay the same "
                  f"(regen/freeze/signature_gate_{STORY}.json)")
        out.append(route_patch.merge_row(base, tid, STORY, RULE, "AGENT", reason,
                                         category=r["category"], task_type=r["task_type"], ts=ts,
                                         keep_buckets=("REGEN",)))
    path = route_patch.write_patch(corpus, STORY, out)
    buckets = collections.Counter(x["bucket"] for x in out)
    prior = collections.Counter((cur.get(x["task_id"]) or {}).get("bucket") for x in out)
    return {"patch": os.path.relpath(path, os.path.dirname(HERE)), "patch_rows": len(out),
            "bucket_after": dict(buckets), "bucket_before": {str(k): v for k, v in prior.items()},
            "kept_regen": kept_regen, "apply": route_patch.apply_command()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--route", action="store_true", help="write the AGENT routing patch")
    a = ap.parse_args(argv)
    specs = audit.load_specs(a.corpus)
    man = load_manifest(a.corpus)
    rows, counts, pairs = census(specs, man, a.corpus)
    slot_pairs = collections.Counter()
    for r in rows:
        if not r["new_ok"]:
            spec = specs[r["task_id"]]
            src = json.load(open(os.path.join(a.corpus, next(e["path"] for e in man if e["task_id"] == r["task_id"]))))["tk_source"]
            r["slots"] = drift_slots(spec, src)
            for _i, w, g in r["slots"]:
                slot_pairs[f"{rv.norm_type(w)} -> {rv.norm_type(g)}"] += 1
    with open(OUT_JSONL, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    rejected = sorted(r["task_id"] for r in rows if not r["new_ok"])
    prev = set()
    if os.path.exists(PREV_FINDING):
        rows40 = os.path.join(a.corpus, "audit", "u64_recheck_131.40.jsonl")
        if os.path.exists(rows40):
            for line in open(rows40):
                x = json.loads(line)
                if x.get("input_affected") and not x.get("record_sig_unsigned"):
                    prev.add(x["task_id"])
    summary = {
        "story": STORY, "date": time.strftime("%Y-%m-%d"),
        "gate": "regen_validate.signature_conforms: arity + every parameter type (norm_type), "
                "lossless widening allowlist SIGNATURE_WIDENING",
        "widening_allowlist": {k: sorted(v) for k, v in rv.SIGNATURE_WIDENING.items()},
        "records_checked": sum(v for k, v in counts.items() if k.startswith("ab:")),
        "counts": dict(sorted(counts.items())),
        "rejected": len(rejected),
        "rejected_by_task_type": dict(collections.Counter(r["task_type"] for r in rows if not r["new_ok"])),
        "first_mismatch_pairs": dict(pairs.most_common()),
        "drift_slots": dict(slot_pairs.most_common()),
        "drift_slots_total": sum(slot_pairs.values()),
        "rejected_task_ids": rejected,
        "vs_131_40": {"finding_131_40": len(prev),
                      "confirmed": len(prev & set(rejected)),
                      "only_in_131_40": sorted(prev - set(rejected)),
                      "only_in_131_43": sorted(set(rejected) - prev),
                      "note": "131.40 counted a record as drifted when its `[^)]*`-regex scan of the "
                              "source found no unsigned parameter; that regex cannot read `@(@u64)` "
                              "and also saw same-arity `…raw` helpers, so its 252 includes the "
                              "only_in_131_40 ids, which declare the spec's types."},
        "jsonl": os.path.relpath(OUT_JSONL, os.path.dirname(HERE)),
    }
    if a.route:
        summary["routing"] = route(rows, a.corpus, specs)
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    brief = {k: summary[k] for k in ("records_checked", "counts", "rejected", "drift_slots", "drift_slots_total")}
    brief["vs_131_40"] = {k: (v if isinstance(v, (int, str)) else len(v)) for k, v in summary["vs_131_40"].items()
                          if k != "note"}
    if "routing" in summary:
        brief["routing"] = {k: summary["routing"][k] for k in ("patch_rows", "bucket_before", "bucket_after")}
    print(json.dumps(brief, indent=1))
    return summary


if __name__ == "__main__":
    main()
