#!/usr/bin/env python3
"""Main-thread acceptance for the 131.15 agent pattern-rewrite wave (workers
are never trusted). For each work/pattern_131/gen/pat_<task_id>.tk:

  worker gates re-run  shape -> compile -> signature -> build -> tests ->
                       idiom >= floor -> structure -> pattern lint net 0
                       (exemptions honoured) -> non-pattern lint <= before ->
                       --min bytes <= before -> proxy tokens <= before
  main-thread gates    differential (131.14 diff_check; n/a for not-executable
                       records) -> Tier-1 perf (median of 3, flag iff ratio >
                       1.5 AND delta > 5 ms / 1 MB) -> Tier-2 flag recorded
                       when the row is perf_sensitive (131.5 runs Tier-2
                       serially after the wave; not here)
  differential mode    131.69 — the rule is chosen from the BASELINE, not the
                       candidate. Original PASSES its own test cases ->
                       `identity`: byte-identical stdout, exactly as before.
                       Original FAILS them and some case expects an err marker
                       -> `repair`: the candidate must PASS its test cases and
                       every NON-error stdout line must still equal the
                       original's; only the err line may move. That is the
                       429 gamed A-ERR records, whose error line IS the defect
                       — byte-identity against a known-wrong baseline threw
                       away precisely the correct rewrite. A failing baseline
                       with no err expectation stays in `identity` mode.
                       Banked as regen.rewrite131.{diff_mode,behaviour,
                       baseline_tests,error_lines_changed} so the freeze can
                       tell "behaviour preserved" from "behaviour repaired".
  stale guard          the record on disk must still be the one the prompt
                       was prepped from (meta.before.file_sha256)

Verdicts: rewritten (banked) | unchanged (candidate == current source, and it
already passes every gate) | failed (rejected -> retry_queue.jsonl; one
retry via re-prep, MAX_ATTEMPTS then exhausted) | skipped (stale / no spec).

--dry-run (DEFAULT) evaluates everything and writes only
work/pattern_131/dryrun/<task_id>.json + a summary; --bank performs the bank:
  archive original -> audit/replaced/131/<tid>.tk (first touch only)
  record: tk_source, regen.{source_sha256,min_bytes,proxy_tokens,max_depth,
          lint_pattern_violations=[],lint_exempt,over_budget,rewrite131{...,
          diff_mode,behaviour,baseline_tests,error_lines_changed}}
  ledger/rewrite_131.jsonl  {task_id, wave:"agent", status, reason,
          attempts, prev_sha256, new_sha256, ts, tkc_bin_sha}   (freeze/README
          schema + the 131.39 pinned-binary sha)
  manifest_tool.Manifest.stamp(task_id, rec_path)  (131.35)
  gen/pat_<tid>.tk -> .done
Per-wave acceptance printed: banked rate (>= 95%), test regressions (0 by
construction), lint net (0), Tier-1 regressions (0), Tier-2 flags.
"""
import argparse, glob, hashlib, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pattern_common as pc                       # noqa: E402
import manifest_tool                              # noqa: E402  (131.35)
from check_pattern import run_worker_gates, WORKER_GATES  # noqa: E402

BANK_GATES = WORKER_GATES + ("diff", "perf")


def _sha_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def evaluate(paths, tid, tmp, meta=None):
    """All gates for one candidate. Returns the result dict:
    {task_id, verdict: rewritten|unchanged|failed|skipped, reason, gates,
    before/after metrics, diff, perf, tier2_flag, candidate (normalised),
    prev_sha256, record_path, ...}."""
    out = {"task_id": tid, "verdict": None, "reason": None, "gates": {}}
    res, before, spec, err = run_worker_gates(paths, tid, tmp)
    if res is None:
        out.update({"verdict": "skipped", "reason": err})
        return out
    meta = meta if meta is not None else (
        json.load(open(paths.sub("meta", tid + ".json")))
        if os.path.exists(paths.sub("meta", tid + ".json")) else {})
    category = meta.get("category") or spec.get("category")
    rec_path = paths.record(category, tid)
    out.update({"category": category, "task_type": spec.get("task_type"),
                "record_path": rec_path, "before": before, "attempts": meta.get("attempts", 1),
                "perf_sensitive": bool(meta.get("perf_sensitive"))})
    if not os.path.exists(rec_path):
        out.update({"verdict": "skipped", "reason": f"no record at {rec_path}", "gates": res["gates"]})
        return out
    cur_sha = pc.sha256_file(rec_path)
    out["prev_sha256"] = cur_sha
    if before and before.get("file_sha256") and before["file_sha256"] != cur_sha:
        out.update({"verdict": "skipped", "gates": res["gates"],
                    "reason": "record changed since prep (stale file_sha256) — re-prep"})
        return out
    rec = json.load(open(rec_path))
    orig_src = rec["tk_source"]
    out["gates"] = res["gates"]
    out["reason"] = res.get("reason")
    out["after"] = {"min_bytes": res.get("min_bytes"), "proxy_tokens": res.get("proxy_tokens"),
                    "idiom": res.get("idiom"), "max_depth": res.get("max_depth"),
                    "lint_other_warnings": res.get("lint_other_warnings")}
    out["lint_exempt"] = res.get("lint_exempt") or []
    out["pattern_hits"] = res.get("pattern_hits") or []
    out["hints"] = [d.get("rule") for d in res.get("hints") or []]
    worker_fail = [k for k in WORKER_GATES if res["gates"].get(k) is False]
    if worker_fail:
        out["verdict"] = "failed"
        out["reason"] = out["reason"] or ", ".join(worker_fail)
        return out
    cand_src = res["record"]["tk_source"]             # normalised (assembled) candidate
    out["candidate"] = cand_src
    out["record"] = res["record"]
    if cand_src == orig_src:
        out["verdict"] = "unchanged"
        out["reason"] = "candidate identical to the current source; already passes every gate"
        out["gates"]["diff"] = None
        out["gates"]["perf"] = None
        return out
    dp = pc.diff_and_perf(spec, orig_src, cand_src, tmp, task_id=tid,
                          a_test=pc.a_test_for(tid, paths.corpus))
    pc.diff_gate_apply(res, dp["diff"])
    pc.perf_gate(res, dp["perf"], out["perf_sensitive"])
    out["diff"] = dp["diff"]
    out["perf"] = dp["perf"]
    # 131.69: the gate's mode. "identity" = byte-identity was required and held
    # (behaviour preserved); "repair" = the ORIGINAL failed its own test cases,
    # so the candidate had to PASS them with every non-error line unchanged
    # (behaviour repaired). Two different claims — AUDIT_131 must see which.
    out["diff_mode"] = dp["diff"].get("mode") or "identity"
    out["baseline_tests"] = ((dp["diff"].get("baseline") or {}).get("verdict"))
    out["tier2_flag"] = res.get("tier2_flag", False)
    out["gates"] = res["gates"]
    out["reason"] = res.get("reason")
    if out["gates"].get("diff") is False or out["gates"].get("perf") is False:
        out["verdict"] = "failed"
        return out
    out["verdict"] = "rewritten"
    fixed = sorted({r for r in (meta.get("rules_must") or []) + (meta.get("rules_hint") or [])
                    + (meta.get("rules_sweep_only") or [])})
    out["rules_fixed"] = [r for r in fixed if r not in {d.get("rule") for d in out["pattern_hits"]}
                          and r not in out["hints"]]
    out["patterns_fixed"] = meta.get("patterns") or []
    out["reason"] = ("rewritten: " + ",".join(out["rules_fixed"])) if out["rules_fixed"] else "rewritten"
    return out


def _error_lines_changed(ev):
    """131.69: the error lines a `repair`-mode rewrite was allowed to change
    (empty in `identity` mode). Banked as provenance so AUDIT_131 can show
    exactly which output line moved and to what."""
    spec_cases = ((ev.get("diff") or {}).get("checks") or {}).get("spec_cases") or {}
    return [{k: c.get(k) for k in ("run", "line", "orig", "cand")}
            for c in (spec_cases.get("error_lines_changed") or [])]


def bank_one(paths, ev, meta, manifest, shas, now):
    """Apply a `rewritten` evaluation: archive, rewrite the record, stamp,
    ledger. Returns the ledger entry."""
    tid = ev["task_id"]
    rec_path = ev["record_path"]
    rec = json.load(open(rec_path))
    os.makedirs(paths.replaced, exist_ok=True)
    archive = os.path.join(paths.replaced, tid + ".tk")
    if not os.path.exists(archive):                   # first touch keeps the freeze-129 original
        with open(archive, "w") as f:
            f.write(rec["tk_source"])
    new_rec = ev["record"]                            # validate's fresh record for the candidate
    cand = ev["candidate"]
    rec["tk_source"] = cand
    rec["tk_tokens"] = new_rec.get("tk_tokens", rec.get("tk_tokens"))
    rg = rec.setdefault("regen", {})
    nrg = new_rec.get("regen") or {}
    rg["source_sha256"] = _sha_text(cand)
    for k in ("min_bytes", "proxy_tokens", "max_depth", "over_budget", "runtime"):
        if nrg.get(k) is not None:
            rg[k] = nrg[k]
    rg["lint_pattern_violations"] = []
    rg["lint_exempt"] = ev.get("lint_exempt") or []
    if (ev.get("after") or {}).get("idiom") is not None:
        rec.setdefault("judge", {})["score"] = ev["after"]["idiom"]
    perf = ev.get("perf") or {}
    dmode = ev.get("diff_mode") or "identity"                     # 131.69
    err_changed = _error_lines_changed(ev)
    rg["rewrite131"] = {
        "wave": pc.WAVE, "story": pc.STORY, "ts": now, "prev_sha256": ev["prev_sha256"],
        "card_sha": meta.get("card_sha") or shas.get("card_sha"),
        "catalogue_sha": meta.get("catalogue_sha") or shas.get("catalogue_sha"),
        "tkc_sha": shas.get("tkc_sha"),
        "tkc_bin_sha": shas.get("tkc_bin_sha") or ev.get("tkc_bin_sha"),   # 131.39: the binary that judged it
        "patterns_fixed": ev.get("patterns_fixed") or [],
        "rules_fixed": ev.get("rules_fixed") or [],
        "lint_violations": 0, "lint_exempt": ev.get("lint_exempt") or [],
        "proxy_tokens_before": (ev.get("before") or {}).get("proxy_tokens"),
        "proxy_tokens_after": (ev.get("after") or {}).get("proxy_tokens"),
        "min_bytes_before": (ev.get("before") or {}).get("min_bytes"),
        "min_bytes_after": (ev.get("after") or {}).get("min_bytes"),
        "perf": {"tier": str(perf.get("tier", "1")), "ratio": perf.get("ratio"),
                 "verdict": perf.get("verdict", "n/a")},
        "tier2_pending": bool(ev.get("tier2_flag")),
        "diff_check": (ev.get("diff") or {}).get("verdict"),
        # 131.69: which differential rule judged this rewrite, and what the
        # baseline did — so the freeze can separate "behaviour preserved" from
        # "behaviour repaired" instead of blurring them into one `diff_check`.
        "diff_mode": dmode,
        "behaviour": "repaired" if dmode == "repair" else "preserved",
        "baseline_tests": ev.get("baseline_tests"),
        "error_lines_changed": err_changed,
        "attempts": ev.get("attempts", 1),
    }
    with open(rec_path, "w") as f:
        json.dump(rec, f)
    manifest.stamp(tid, rec_path)                     # 131.35: never replace without re-stamping
    entry = {"task_id": tid, "wave": pc.WAVE, "status": "rewritten", "reason": ev["reason"],
             "attempts": ev.get("attempts", 1), "prev_sha256": ev["prev_sha256"],
             "new_sha256": pc.sha256_file(rec_path), "ts": now, "tkc_bin_sha": ev.get("tkc_bin_sha")}
    pc.append_ledger(paths.ledger, entry)
    return entry


def _ledger_entry(ev, now, status=None):
    return {"task_id": ev["task_id"], "wave": pc.WAVE, "status": status or ev["verdict"],
            "reason": ev.get("reason"), "attempts": ev.get("attempts", 1),
            "prev_sha256": ev.get("prev_sha256"), "new_sha256": None, "ts": now,
            "tkc_bin_sha": ev.get("tkc_bin_sha")}


def _public(ev):
    """Evaluation minus the bulky fields (for dryrun/ and the report)."""
    return {k: v for k, v in ev.items() if k not in ("record", "candidate")}


def run(paths, bank=False, only=None, limit=None, shas=None):
    gen = paths.sub("gen")
    files = sorted(glob.glob(os.path.join(gen, pc.GEN_PREFIX + "*.tk")))
    tids = [os.path.basename(p)[len(pc.GEN_PREFIX):-3] for p in files]
    if only:
        keep = set(only)
        tids = [t for t in tids if t in keep]
    if limit is not None:
        tids = tids[:limit]
    shas = dict(shas or {})
    # 131.39: every evaluation, ledger line, record stamp and the summary carry
    # the sha256 of the binary the gates exec'd (the pinned copy when main()
    # pinned; else whatever pc.TKC is right now)
    shas.setdefault("tkc_bin_sha", pc.tkc_bin_sha(paths.tkc))
    manifest = manifest_tool.Manifest(paths.manifest) if bank else None
    now = pc.now_iso()
    counts = {"rewritten": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    modes = {"identity": 0, "repair": 0}              # 131.69: rewritten, by diff mode
    tier1_regressions = tier2_flags = exhausted = 0
    lint_net_after = 0
    tokens_before = tokens_after = 0
    results = []
    dry_dir = paths.sub("dryrun")
    if not bank:
        os.makedirs(dry_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bank_pattern_") as tmp:
        for tid in tids:
            meta_path = paths.sub("meta", tid + ".json")
            meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
            ev = evaluate(paths, tid, tmp, meta)
            ev["tkc_bin_sha"] = shas["tkc_bin_sha"]
            v = ev["verdict"]
            counts[v] += 1
            if (ev.get("perf") or {}).get("verdict") == "fail":
                tier1_regressions += 1
            if ev.get("tier2_flag") and v == "rewritten":
                tier2_flags += 1
            if v == "rewritten":
                modes[ev.get("diff_mode") or "identity"] = \
                    modes.get(ev.get("diff_mode") or "identity", 0) + 1     # 131.69
                lint_net_after += len(ev.get("pattern_hits") or [])
                tokens_before += (ev.get("before") or {}).get("proxy_tokens") or 0
                tokens_after += (ev.get("after") or {}).get("proxy_tokens") or 0
            if bank:
                path = os.path.join(gen, pc.GEN_PREFIX + tid + ".tk")
                if v == "rewritten":
                    bank_one(paths, ev, meta, manifest, shas, now)
                elif v == "unchanged":
                    pc.append_ledger(paths.ledger, _ledger_entry(ev, now))
                elif v == "failed":
                    pc.append_ledger(paths.ledger, _ledger_entry(ev, now))
                    if ev.get("attempts", 1) >= pc.MAX_ATTEMPTS:
                        exhausted += 1
                    with open(paths.sub("retry_queue.jsonl"), "a") as f:
                        f.write(json.dumps({"task_id": tid, "reason": ev.get("reason"),
                                            "attempts": ev.get("attempts", 1),
                                            "exhausted": ev.get("attempts", 1) >= pc.MAX_ATTEMPTS,
                                            "gates": ev.get("gates"), "ts": now}) + "\n")
                else:                                 # skipped: stale/no spec — ledger + keep for re-prep
                    if ev.get("prev_sha256"):         # a skip is not an attempt: keep the prior count
                        e = _ledger_entry(ev, now)
                        e["attempts"] = max(0, ev.get("attempts", 1) - 1)
                        pc.append_ledger(paths.ledger, e)
                if v != "skipped" or ev.get("prev_sha256"):
                    os.rename(path, path + ".done")
                if ev.get("tier2_flag") and v == "rewritten":
                    with open(paths.sub("tier2_queue.jsonl"), "a") as f:
                        f.write(json.dumps({"task_id": tid, "category": ev.get("category"),
                                            "patterns": ev.get("patterns_fixed"), "ts": now}) + "\n")
            else:
                with open(os.path.join(dry_dir, tid + ".json"), "w") as f:
                    json.dump(_public(ev), f, indent=1)
            results.append(_public(ev))
    n = len(tids)
    decided = counts["rewritten"] + counts["unchanged"] + counts["failed"]
    banked = counts["rewritten"] + counts["unchanged"]
    summary = {"story": pc.STORY, "wave": pc.WAVE, "mode": "bank" if bank else "dry-run",
               "ts": now, "candidates": n, **counts,
               "banked_rate": round(banked / decided, 4) if decided else None,
               "wave_gate_95pct": (banked / decided >= 0.95) if decided else None,
               "test_regressions": 0, "lint_net": lint_net_after,
               # 131.69: how many banked rewrites preserved behaviour vs repaired it
               "behaviour_preserved": modes.get("identity", 0),
               "behaviour_repaired": modes.get("repair", 0),
               "tier1_regressions": tier1_regressions, "tier2_flagged": tier2_flags,
               "exhausted": exhausted,
               "proxy_tokens_before": tokens_before, "proxy_tokens_after": tokens_after,
               "reasons": _reason_counts(results), **shas, "workdir": os.path.abspath(paths.workdir)}
    rep_path = paths.sub(("bank_report_" if bank else "dryrun_report_") + now.replace(":", "") + ".json")
    with open(rep_path, "w") as f:
        json.dump({"summary": summary, "results": results}, f, indent=1)
    summary["report"] = rep_path
    return summary


def _reason_counts(results):
    from collections import Counter
    c = Counter()
    for r in results:
        if r["verdict"] in ("failed", "skipped"):
            c[(r.get("reason") or "?").split(":")[0]] += 1
    return dict(c)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=pc.CORPUS)
    ap.add_argument("--workdir", default=None, help="default <corpus>/work/pattern_131")
    ap.add_argument("--ledger", default=None, help="default <corpus>/ledger/rewrite_131.jsonl")
    ap.add_argument("--catalogue", default=pc.CATALOGUE_PATH)
    ap.add_argument("--card", default=pc.CARD_PATH)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="(default) evaluate only; write dryrun/<tid>.json")
    g.add_argument("--bank", action="store_true", help="really bank: records, ledger, manifest, .done")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    # 131.39: pin the compiler before Paths (paths.tkc) and before the loop;
    # a `make` relinking ~/tk/toke/tkc mid-bank cannot change the judge
    with pc.pin_tkc(sys.modules[__name__]) as pinned:
        paths = pc.Paths(corpus=args.corpus, workdir=args.workdir, ledger=args.ledger,
                         catalogue=args.catalogue, card=args.card)
        shas = {"card_sha": pc.short(pc.sha256_file(paths.card)),
                "catalogue_sha": pc.short(pc.sha256_file(paths.catalogue)),
                **{k: v for k, v in pc.tkc_stamp(paths.tkc, paths.toke_root).items()
                   if k in ("tkc_sha", "tkc_version", "tkc_bin_sha")}}
        assert shas["tkc_bin_sha"] == pinned.sha256
        print(f"bank_pattern: tkc {pinned.version} sha256 {pinned.sha256[:12]} (pinned copy {pinned.path})",
              file=sys.stderr)
        s = run(paths, bank=args.bank, only=args.only, limit=args.limit, shas=shas)
    print(json.dumps(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
