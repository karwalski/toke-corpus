#!/usr/bin/env python3
"""Story 131.64 remainder — re-run of the 131.13 pattern sweep after the
`nested-if-ladder` lint rule shipped (toke 5800ac34, Z74), compared with the
committed 131.13 sweep output.

The sweep itself is `pattern_sweep.py run` / `report`, unchanged, pointed at a
read-only mirror of the corpus (`--corpus-dir` = a directory of symlinks to
corpus/regen_v04 with private `audit/` + `ledger/` dirs, so nothing under the
corpus is written) and with `$TKC` set to the compiler under test. This script
only compares the resulting rows with

  * `audit/pattern_sweep.jsonl`         the 131.13 output (2026-09-18), and
  * `audit/pattern_sweep.merged.jsonl`  the current routing (131.13 + patches),

and writes regen/freeze/pattern_sweep_131.64.json.

nested-if-ladder accounting. pattern_sweep only turns a diagnostic into a
violation when its rule is in idiom_judge.PATTERN_RULES (a fixed dict, not
read from lint-rules-v1.md); nested-if-ladder is not in it, so the re-run puts
every ladder hit in `other_lint`, where it cannot move a regen row's bucket.
The report therefore counts the ladder hits from `other_lint` and ALSO gives a
counterfactual bucket per row with each ladder hit treated as a violation:
pattern `cond-elif-chain`, flagged form `b` (nested if/el), deterministic when
tkc attaches its `fix` (lint-rules-v1.md: the rule's fix is deterministic),
re-classified with pattern_sweep.classify. The counterfactual is a measurement,
not a routing: it changes no bucket file.

Usage:
  pattern_sweep_131_64.py --new <re-run pattern_sweep.jsonl> --old <pattern_sweep.jsonl>
      --merged <pattern_sweep.merged.jsonl> [--patch <patch.jsonl> ...] --tkc <path> --toke-repo <dir>
      [--out regen/freeze/pattern_sweep_131.64.json] [--rows <jsonl>]
"""
import argparse, collections, copy, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pattern_sweep as ps                       # noqa: E402
import tkc_pin                                   # noqa: E402

RULE = "nested-if-ladder"
PATTERN, FORM = "cond-elif-chain", "b"
SCOPES = ("regen", "library")


def load(path):
    rows = {}
    for line in open(path):
        r = json.loads(line)
        rows[r["task_id"]] = r                   # last line wins (ledger / patch semantics)
    return rows


def counts(rows, key="bucket"):
    out = {}
    for s in SCOPES + ("all",):
        c = collections.Counter(r.get(key) for r in rows.values() if s == "all" or r.get("source") == s)
        out[s] = {b: c.get(b, 0) for b in ps.BUCKETS}
        out[s]["records"] = sum(c.values())
    return out


def delta(a, b):
    return {s: {k: b[s][k] - a[s][k] for k in a[s]} for s in a}


def transitions(old, new, ids):
    t = collections.Counter()
    for tid in ids:
        if tid in old and tid in new and old[tid]["bucket"] != new[tid]["bucket"]:
            t[f"{old[tid]['bucket']}->{new[tid]['bucket']}"] += 1
    return dict(sorted(t.items(), key=lambda kv: -kv[1]))


def counterfactual(row, cat, thresholds):
    """Bucket with each nested-if-ladder hit counted as a cond-elif-chain violation."""
    ladders = [o for o in row.get("other_lint") or [] if o.get("rule") == RULE]
    if not ladders or row.get("error"):
        return row["bucket"], 0
    viol = copy.deepcopy(row.get("violations") or [])
    saving = ps.pattern_saving(cat, PATTERN, FORM) or 0
    for o in ladders:
        viol.append({"rule": RULE, "severity": o.get("severity"), "pattern_id": PATTERN,
                     "flagged_form": FORM, "fix": o.get("fix"), "deterministic": bool(o.get("fix")),
                     "saving": saving})
    est = sum(v.get("saving") or 0 for v in viol)
    regen_class = (row.get("source") == "regen" and (row.get("category") or "").startswith("A-")
                   and row.get("task_type") == "full_program")
    b, _, _ = ps.classify(viol, row.get("exemptions") or [], est, row.get("perf_sensitive"),
                          thresholds, regen_class, row.get("library"))
    return b, len(ladders)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--merged", required=True)
    ap.add_argument("--tkc", required=True)
    ap.add_argument("--toke-repo", required=True)
    ap.add_argument("--catalogue")
    ap.add_argument("--date", default="2026-11-09")
    ap.add_argument("--out", default=os.path.join(HERE, "freeze", "pattern_sweep_131.64.json"))
    ap.add_argument("--merged-note", default="", help="what the on-disk merged file was built from")
    ap.add_argument("--patch", action="append", default=[],
                    help="routing patch applied over the re-run, in order (the merged-routing recipe)")
    ap.add_argument("--rows", help="optional per-row diff jsonl (task_id, old, merged, new, counterfactual)")
    a = ap.parse_args()

    new, old, merged = load(a.new), load(a.old), load(a.merged)
    cat = ps.load_catalogue(a.catalogue or os.path.join(a.toke_repo, "patterns", "catalogue.json"))
    thresholds = {"agent_saving": 3, "concat_fix_armed": False, "preform_exempt": True,
                  "regen_class": "A-*/full_program", "budget_max_over": 0.05, "factor_step": 0.05}
    with tkc_pin.pin(toke_repo=a.toke_repo, tkc=a.tkc) as p:
        stamp = p.stamp()
    stamp.pop("tkc_pinned_copy", None)

    ids = sorted(new)
    errors = sorted(t for t in ids if new[t].get("error"))
    cf_rows = {}
    ladder = {s: collections.Counter() for s in SCOPES}
    ladder_bucket = collections.Counter()
    for tid in ids:
        r = new[tid]
        b, n = counterfactual(r, cat, thresholds)
        cf_rows[tid] = dict(r, bucket=b)
        if n:
            s = r.get("source")
            ladder[s]["records"] += 1
            ladder[s]["hits"] += n
            fixed = sum(1 for o in r["other_lint"] if o.get("rule") == RULE and o.get("fix"))
            ladder[s]["hits_with_fix"] += fixed
            ladder[s]["records_all_fixed"] += int(fixed == n)
            ladder_bucket[r["bucket"]] += 1

    rewritten = sorted(t for t in ids if t in old and old[t].get("source_sha256") != new[t].get("source_sha256"))
    rw = set(rewritten)
    same_src = [t for t in ids if t in old and t not in rw]
    c_old, c_merged, c_new, c_cf = counts(old), counts(merged), counts(new), counts(cf_rows)
    patched = dict(new)
    patched_ids = set()
    for pp in a.patch:
        for tid, r in load(pp).items():
            if tid in patched:
                patched[tid] = dict(patched[tid], bucket=r["bucket"])
                patched_ids.add(tid)
    c_patched = counts(patched)
    agent_reason = collections.Counter()
    for tid in ids:
        if new[tid]["bucket"] == "AGENT" and merged.get(tid, {}).get("bucket") != "AGENT":
            agent_reason[(new[tid].get("bucket_reason") or "").split(":")[0][:60]] += 1

    old_auto_changed = sum(1 for t in rewritten if old[t]["bucket"] == "AUTO")
    unused_let = sorted(t for t in ids if old.get(t, {}).get("bucket") == "AUTO" and new[t]["bucket"] == "AGENT"
                        and "unused-let" in (new[t].get("bucket_reason") or ""))
    other_auto_agent = sorted(t for t in ids if old.get(t, {}).get("bucket") == "AUTO"
                              and new[t]["bucket"] == "AGENT" and t not in set(unused_let))
    rule_new = collections.Counter()
    rule_old = collections.Counter()
    for tid in ids:
        for v in new[tid].get("violations") or []:
            rule_new[v["rule"]] += 1
        for v in old.get(tid, {}).get("violations") or []:
            rule_old[v["rule"]] += 1

    out = {
        "story": "131.64",
        "date": a.date,
        "what": "131.13 pattern sweep re-run (pattern_sweep.py run+report, unchanged thresholds) with the integrate "
                "compiler, after the nested-if-ladder lint rule shipped; compared with the committed 131.13 output",
        "tkc": stamp,
        "catalogue_sha": cat["sha"],
        "catalogue_entries": cat["n"],
        "thresholds": thresholds,
        "rows": {"new": len(new), "old_131_13": len(old), "merged_routing": len(merged),
                 "merged_routing_note": a.merged_note,
                 "sweep_errors": len(errors), "sweep_error_task_ids": errors[:50],
                 "source_changed_since_131_13": len(rewritten),
                 "source_unchanged_since_131_13": len(same_src)},
        "buckets": {"old_131_13": c_old, "merged_routing": c_merged, "rerun": c_new,
                    "rerun_minus_old": delta(c_old, c_new), "rerun_minus_merged": delta(c_merged, c_new),
                    "rerun_plus_patches": c_patched,
                    "rerun_plus_patches_minus_merged": delta(c_merged, c_patched),
                    "patches": [os.path.basename(pp) for pp in a.patch], "patched_rows": len(patched_ids)},
        "transitions": {
            "old_to_rerun_all": transitions(old, new, ids),
            "old_to_rerun_source_unchanged": transitions(old, new, same_src),
            "old_to_rerun_source_changed": transitions(old, new, rewritten),
            "merged_to_rerun_all": transitions(merged, new, ids),
            "merged_to_rerun_plus_patches": transitions(merged, patched, ids),
        },
        "explained": {
            "old_auto_rows_whose_source_changed": old_auto_changed,
            "old_auto_rows_whose_source_changed_note": "the 131.14 auto wave banked these rewrites, so their "
                                                       "deterministic hits are gone (AUTO->LEAVE)",
            "library_auto_to_agent_unused_let": len(unused_let),
            "library_auto_to_agent_unused_let_note": "tkc no longer attaches a fix to these unused-let warnings "
                                                     "(127.81/127.82: a lint fix must not change behaviour), so "
                                                     "the 131.17 'all lint warnings fixable' AUTO route no longer applies",
            "library_auto_to_agent_unused_let_ids": unused_let,
            "other_auto_to_agent_ids": other_auto_agent,
        },
        "new_agent_vs_merged_by_reason": dict(agent_reason.most_common()),
        "violation_hits_by_rule": {"old_131_13": dict(rule_old), "rerun": dict(rule_new)},
        "nested_if_ladder": {
            "counted_by_sweep": False,
            "why_not_counted": "pattern_sweep.py keeps a diagnostic as a violation only when its rule is in "
                               "idiom_judge.PATTERN_RULES, a fixed dict that does not list nested-if-ladder; the "
                               "hits land in other_lint, which never moves a regen row's bucket",
            "hits": {s: dict(ladder[s]) for s in SCOPES},
            "records_with_hits_by_rerun_bucket": dict(ladder_bucket),
            "counterfactual": {
                "rule_as": f"pattern {PATTERN} form {FORM}; deterministic when tkc attaches a fix; saving "
                           f"{ps.pattern_saving(cat, PATTERN, FORM)} proxy8k per hit (catalogue)",
                "buckets": c_cf,
                "minus_rerun": delta(c_new, c_cf),
                "transitions": transitions(new, cf_rows, ids),
            },
        },
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    if a.rows:
        with open(a.rows, "w") as f:
            for tid in ids:
                f.write(json.dumps({"task_id": tid, "source": new[tid].get("source"),
                                    "old": old.get(tid, {}).get("bucket"),
                                    "merged": merged.get(tid, {}).get("bucket"),
                                    "new": new[tid]["bucket"], "counterfactual": cf_rows[tid]["bucket"],
                                    "source_changed": tid in rw}) + "\n")
    print(json.dumps({"out": a.out, "buckets_rerun": c_new["all"], "counterfactual": c_cf["all"]}))


if __name__ == "__main__":
    main()
