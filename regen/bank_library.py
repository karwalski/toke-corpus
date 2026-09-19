#!/usr/bin/env python3
"""Epic 131.70 — main-thread acceptance for the 131.17 library rewrite wave
(workers are never trusted).

For each prepped task with a candidate at
    ~/tk/toke-test-programs/results/solutions/<cat>/<id>/solution.131.tk

  validate gates   run_shard.validate_one_gates(spec, cand, tmp, lint_gate=True)
                   shape -> compile -> build (--allow-all etc. from the
                   manifest build_flags) -> tests -> idiom -> structure ->
                   pattern -> lint
  wave gates       lint net zero (total lint warnings after == 0 and not worse
                   than the original) -> min_bytes <= before -> proxy_tokens
                   <= before -> Tier-1 perf (median of 3, fail iff ratio > 1.5
                   AND delta > 5 ms / 1 MB)
  stale guard      solution.tk on disk must still be the file the prompt was
                   prepped from (meta.before.file_sha256)

THE ORACLE IS THE MANIFEST. The `tests` gate is validate.run_stdin_cases:
each case's stdout is compared to the manifest's independently authored
`expected_output` (validate.py:300). This file does NOT import
bank_pattern.diff_check — nor pattern_common, nor pattern_autofix — and makes
no comparison of any kind against the original program's output. That gate is
what forced the corpus wave's withdrawal (131.69): where the original is the
defect, byte-identity against it makes the correct fix unbankable. The only
readings taken from the original here are a compile-time size/lint/idiom
baseline and a wall/RSS timing baseline; both are deltas for reporting, never
oracles. Every banked provenance block records `original_output_compared:
false` so an auditor can check the claim from the data.

Carve-out (131.32): L-DAT-050, L-DEV-115, L-SEC-122, L-SYS-125 carry raw NUL
bytes. `tkc --min` is NUL-terminated, so their `before` min_bytes /
proxy_tokens are truncation artefacts (DEV-115: 138 bytes for a 2,597-byte
program), not baselines. Both <=-before gates are explicitly n/a for them and
the result/provenance carries `nul_carve_out: true` — they are neither
silently passed nor failed on a meaningless comparison.

Excluded (127.64): L-GAZ-117 and L-DEV-123 fail on the pinned tkc as a
compiler regression handled separately; the bank skips them by name.

Verdicts: rewritten (banked) | unchanged (candidate == the current program and
it already passes every gate) | failed (rejected -> retry_queue.jsonl; one
retry via re-prep, MAX_ATTEMPTS then exhausted) | skipped (no candidate /
stale / excluded).

Modes
  --dry-run (DEFAULT)  evaluate every candidate, write dryrun/<tid>.json + a
                       summary report; nothing on disk changes.
  --baseline           dry-run the gate stack over the ORIGINAL programs as if
                       each were its own candidate: the pre-wave would-bank
                       rate over the 1,272, i.e. how many library programs
                       already clear every gate. Supports --workers.
  --bank               archive results/solutions/<cat>/<id>/solution.tk to
                       results/solutions-pre131/<cat>/<id>/solution.tk (first
                       touch only), promote the candidate over solution.tk,
                       rename the candidate to .done, write the rewrite131
                       provenance block to work/.../provenance/<tid>.json and
                       append ledger/rewrite_131_library.jsonl.
"""
import argparse, json, multiprocessing, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import library_common as lc                       # noqa: E402

BANK_GATES = lc.BANK_GATES


def _load_meta(paths, tid):
    p = paths.sub("meta", tid + ".json")
    return json.load(open(p)) if os.path.exists(p) else None


def _load_spec(paths, tid, specs=None):
    p = paths.sub("specs", tid + ".json")
    if os.path.exists(p):
        return json.load(open(p))
    return (specs or {}).get(tid)


def _first_failure(gates):
    return [k for k in BANK_GATES if gates.get(k) is False]


def evaluate(paths, tid, tmp, meta=None, spec=None, baseline=False, run_perf=True):
    """Every gate for one candidate. Returns the result dict. Never compares
    the candidate's output against the original's — the manifest is the
    oracle."""
    out = {"task_id": tid, "verdict": None, "reason": None, "gates": {},
           "baseline": bool(baseline)}
    if tid in lc.EXCLUDED:
        out.update({"verdict": "skipped", "reason": lc.EXCLUDED[tid], "excluded": True})
        return out
    meta = meta if meta is not None else _load_meta(paths, tid)
    if meta is None:
        out.update({"verdict": "skipped", "reason": "no meta — re-prep"})
        return out
    spec = spec if spec is not None else _load_spec(paths, tid)
    if spec is None:
        out.update({"verdict": "skipped", "reason": "no spec — re-prep"})
        return out
    libd = meta.get("library") or spec.get("library") or {}
    sol = meta.get("solution_path") or paths.original(libd.get("category"), libd.get("id"))
    cand_path = meta.get("candidate_path") or paths.candidate(libd.get("category"),
                                                              libd.get("id"))
    nul = bool(meta.get("nul_carve_out")) or lc.is_nul_carve_out(tid)
    before = meta.get("before") or {}
    out.update({"category": spec.get("category"), "library": libd,
                "task_type": spec.get("task_type"), "solution_path": sol,
                "candidate_path": cand_path, "archive_path": meta.get("archive_path"),
                "before": {k: v for k, v in before.items() if k != "struct"},
                "attempts": meta.get("attempts", 1), "nul_carve_out": nul})
    if not os.path.exists(sol):
        out.update({"verdict": "skipped", "reason": f"no original at {sol}"})
        return out
    orig_src = open(sol, encoding="utf-8", errors="replace").read()
    out["prev_sha256"] = lc.sha256_file(sol)
    if not baseline and before.get("file_sha256") and before["file_sha256"] != out["prev_sha256"]:
        out.update({"verdict": "skipped",
                    "reason": "original changed since prep (stale file_sha256) — re-prep"})
        return out
    if baseline:
        cand_text = orig_src
    else:
        if not os.path.exists(cand_path):
            out.update({"verdict": "skipped", "reason": "no candidate on disk"})
            return out
        cand_text = open(cand_path, encoding="utf-8", errors="replace").read()

    res = lc.static_and_test_gates(spec, cand_text, tmp)
    lc.apply_exemptions(res, meta.get("exempt") or [])
    lc.lint_net_gate(res, before)
    lc.size_gates(res, before, nul_carve_out=nul)
    out["gates"] = res["gates"]
    out["reason"] = res.get("reason")
    out["after"] = {"min_bytes": res.get("min_bytes"), "proxy_tokens": res.get("proxy_tokens"),
                    "idiom": res.get("idiom"), "max_depth": res.get("max_depth"),
                    "lint_warnings": res.get("lint_warnings")}
    out["lint"] = {"before": res.get("lint_before"), "after": res.get("lint_after"),
                   "delta": res.get("lint_delta")}
    out["lint_exempt"] = res.get("lint_exempt") or []
    if res.get("size_gate_note"):
        out["size_gate_note"] = res["size_gate_note"]
    out["pattern_hits"] = [{k: d.get(k) for k in ("rule", "severity", "line")}
                           for d in res.get("pattern_hits") or []]
    out["hints"] = sorted({d.get("rule") for d in res.get("hints") or []})
    rt = res.get("runtime") or {}
    out["tests"] = {"cases_total": rt.get("cases_total"), "cases_passed": rt.get("cases_passed"),
                    "oracle": "manifest:expected_output"}
    fails = _first_failure(out["gates"])
    if fails:
        out["verdict"] = "failed"
        out["reason"] = out["reason"] or ", ".join(fails)
        out["failed_gates"] = fails
        return out
    cand_src = res["tk_source"]
    out["candidate"] = cand_src
    out["record"] = res["record"]
    # A SOURCE delta, reported not gated: whether the worker changed the file
    # at all. The candidate's OUTPUT is never compared against the original's.
    out["source_changed"] = cand_src != lc.normalise_source(orig_src)
    # compare like with like: both sides through the same wrapper-strip
    if not out["source_changed"]:
        out["verdict"] = "unchanged"
        out["reason"] = ("baseline: already passes every gate" if baseline else
                         "candidate identical to the current program; already passes every gate")
        out["gates"]["perf"] = None
        return out
    if run_perf:
        perf = lc.tier1_perf(spec, orig_src, cand_src, tmp)
        lc.perf_gate(res, perf)
        out["perf"] = perf
        out["gates"] = res["gates"]
        out["reason"] = res.get("reason")
        if out["gates"].get("perf") is False:
            out["verdict"] = "failed"
            out["failed_gates"] = ["perf"]
            return out
    else:
        out["gates"]["perf"] = None
    out["verdict"] = "rewritten"
    fixed = sorted({r for r in (meta.get("rules_must") or []) + (meta.get("rules_hint") or [])
                    + (meta.get("rules_sweep_only") or [])})
    still = {d["rule"] for d in out["pattern_hits"]} | set(out["hints"])
    out["rules_fixed"] = [r for r in fixed if r not in still]
    out["patterns_fixed"] = meta.get("patterns") or []
    out["reason"] = ("rewritten: " + ",".join(out["rules_fixed"])) if out["rules_fixed"] else "rewritten"
    return out


# ------------------------------------------------------------ provenance ---
def provenance(ev, meta, shas, now):
    """The `rewrite131` block, in the shape the corpus wave banks (131.18 joins
    it onto the corpus record it creates from the rewritten solution.tk).

    `oracle` / `original_output_compared` are the library path's own fields:
    they state, in the data, that nothing here was gated on the original
    program's output (131.69/131.70)."""
    perf = ev.get("perf") or {"tier": "1", "ratio": None, "verdict": "n/a"}
    before, after = ev.get("before") or {}, ev.get("after") or {}
    libd = ev.get("library") or {}
    return {
        "wave": lc.WAVE, "story": lc.STORY, "tooling_story": lc.TOOLING_STORY,
        "ts": now, "prev_sha256": ev["prev_sha256"],
        "card_sha": meta.get("card_sha") or shas.get("card_sha"),
        "catalogue_sha": meta.get("catalogue_sha") or shas.get("catalogue_sha"),
        "tkc_sha": shas.get("tkc_sha"),
        "tkc_bin_sha": shas.get("tkc_bin_sha") or ev.get("tkc_bin_sha"),
        "patterns_fixed": ev.get("patterns_fixed") or [],
        "rules_fixed": ev.get("rules_fixed") or [],
        "lint_violations": 0,
        "lint_exempt": ev.get("lint_exempt") or [],
        "lint_warnings_before": (ev.get("lint") or {}).get("before"),
        "lint_warnings_after": (ev.get("lint") or {}).get("after"),
        "proxy_tokens_before": before.get("proxy_tokens"),
        "proxy_tokens_after": after.get("proxy_tokens"),
        "min_bytes_before": before.get("min_bytes"),
        "min_bytes_after": after.get("min_bytes"),
        "idiom_before": before.get("idiom"), "idiom_after": after.get("idiom"),
        "max_depth_before": before.get("max_depth"), "max_depth_after": after.get("max_depth"),
        "perf": {"tier": str(perf.get("tier", "1")), "ratio": perf.get("ratio"),
                 "verdict": perf.get("verdict", "n/a")},
        "tier2_pending": False,
        # library path: the manifest is the oracle, never the original program
        "oracle": "manifest:expected_output",
        "original_output_compared": False,
        "behaviour": "manifest-locked",
        "tests": ev.get("tests"),
        "nul_carve_out": bool(ev.get("nul_carve_out")),
        "size_gates": {"min_bytes": ev["gates"].get("min_bytes"),
                       "proxy_tokens": ev["gates"].get("proxy_tokens"),
                       "note": lc.NUL_CARVE_REASON if ev.get("nul_carve_out") else None},
        "library": {"id": libd.get("id"), "category": libd.get("category"),
                    "manifest": libd.get("manifest"), "title": libd.get("title"),
                    "source_program": ev.get("solution_path")},
        "archived_to": ev.get("archive_path"),
        "attempts": ev.get("attempts", 1),
    }


def bank_one(paths, ev, meta, shas, now):
    """Apply a `rewritten` evaluation: archive the original (first touch only),
    promote the candidate, write provenance, append the ledger."""
    tid = ev["task_id"]
    sol, cand_path = ev["solution_path"], ev["candidate_path"]
    archive = ev.get("archive_path") or paths.archive((ev.get("library") or {}).get("category"),
                                                      (ev.get("library") or {}).get("id"))
    os.makedirs(os.path.dirname(archive), exist_ok=True)
    if not os.path.exists(archive):               # archive-not-delete, first touch wins
        shutil.copy2(sol, archive)
    with open(sol, "w", encoding="utf-8") as f:
        f.write(ev["candidate"])
    if os.path.exists(cand_path):
        os.replace(cand_path, cand_path + ".done")
    prov = provenance(ev, meta, shas, now)
    prov_dir = paths.sub("provenance")
    os.makedirs(prov_dir, exist_ok=True)
    with open(os.path.join(prov_dir, tid + ".json"), "w") as f:
        json.dump({"task_id": tid, "category": ev.get("category"),
                   "task_type": ev.get("task_type"), "rewrite131": prov,
                   "new_sha256": lc.sha256_file(sol)}, f, indent=1)
    entry = {"task_id": tid, "wave": lc.WAVE, "status": "rewritten", "reason": ev["reason"],
             "attempts": ev.get("attempts", 1), "prev_sha256": ev["prev_sha256"],
             "new_sha256": lc.sha256_file(sol), "ts": now,
             "tkc_bin_sha": ev.get("tkc_bin_sha"), "rewrite131": prov}
    lc.append_ledger(paths.ledger, entry)
    return entry


def _ledger_entry(ev, now, status=None):
    return {"task_id": ev["task_id"], "wave": lc.WAVE, "status": status or ev["verdict"],
            "reason": ev.get("reason"), "attempts": ev.get("attempts", 1),
            "prev_sha256": ev.get("prev_sha256"), "new_sha256": None, "ts": now,
            "tkc_bin_sha": ev.get("tkc_bin_sha")}


def _public(ev):
    return {k: v for k, v in ev.items() if k not in ("record", "candidate")}


# --------------------------------------------------------------- driving ---
_JOB = {}


def _init_job(paths, baseline, run_perf, specs):
    _JOB.update({"paths": paths, "baseline": baseline, "run_perf": run_perf, "specs": specs})


def _eval_job(tid):
    paths = _JOB["paths"]
    with tempfile.TemporaryDirectory(prefix="bank_library_") as tmp:
        try:
            ev = evaluate(paths, tid, tmp, baseline=_JOB["baseline"],
                          run_perf=_JOB["run_perf"],
                          spec=_load_spec(paths, tid, _JOB.get("specs")))
        except Exception as e:                    # noqa: BLE001 — one bad program never kills the run
            ev = {"task_id": tid, "verdict": "skipped", "gates": {},
                  "reason": f"bank_error: {type(e).__name__}: {e}"}
    return _public(ev)


def candidate_ids(paths, specs=None):
    """Task ids with a candidate on disk (prepped meta + solution.131.tk)."""
    out = []
    meta_dir = paths.sub("meta")
    if not os.path.isdir(meta_dir):
        return out
    for fn in sorted(os.listdir(meta_dir)):
        if not fn.endswith(".json"):
            continue
        tid = fn[:-5]
        meta = _load_meta(paths, tid)
        cand = (meta or {}).get("candidate_path")
        if cand and os.path.exists(cand):
            out.append(tid)
    return out


def prepped_ids(paths):
    meta_dir = paths.sub("meta")
    if not os.path.isdir(meta_dir):
        return []
    return sorted(fn[:-5] for fn in os.listdir(meta_dir) if fn.endswith(".json"))


def run(paths, bank=False, baseline=False, only=None, limit=None, shas=None,
        workers=1, run_perf=True, specs=None):
    """Evaluate (and optionally bank) every candidate. Returns the summary."""
    tids = list(only) if only else (prepped_ids(paths) if baseline else candidate_ids(paths))
    tids = [t for t in tids if t not in lc.EXCLUDED]
    if limit is not None:
        tids = tids[:limit]
    shas = dict(shas or {})
    shas.setdefault("tkc_bin_sha", lc.tkc_bin_sha(paths.tkc))
    now = lc.now_iso()
    counts = {"rewritten": 0, "unchanged": 0, "failed": 0, "skipped": 0}
    gate_fails = {}
    exhausted = 0
    tokens_before = tokens_after = bytes_before = bytes_after = 0
    carved = []
    results = []
    dry_dir = paths.sub("dryrun" if not baseline else "baseline")
    if not bank:
        os.makedirs(dry_dir, exist_ok=True)

    if bank:
        evs = []
        with tempfile.TemporaryDirectory(prefix="bank_library_") as tmp:
            for tid in tids:
                meta = _load_meta(paths, tid)
                ev = evaluate(paths, tid, tmp, meta, _load_spec(paths, tid, specs),
                              baseline=False, run_perf=run_perf)
                ev["tkc_bin_sha"] = shas["tkc_bin_sha"]
                if ev["verdict"] == "rewritten":
                    bank_one(paths, ev, meta or {}, shas, now)
                elif ev["verdict"] == "unchanged":
                    lc.append_ledger(paths.ledger, _ledger_entry(ev, now))
                    if os.path.exists(ev.get("candidate_path") or ""):
                        os.replace(ev["candidate_path"], ev["candidate_path"] + ".done")
                elif ev["verdict"] == "failed":
                    lc.append_ledger(paths.ledger, _ledger_entry(ev, now))
                    if ev.get("attempts", 1) >= lc.MAX_ATTEMPTS:
                        exhausted += 1
                    with open(paths.sub("retry_queue.jsonl"), "a") as f:
                        f.write(json.dumps({"task_id": tid, "reason": ev.get("reason"),
                                            "attempts": ev.get("attempts", 1),
                                            "exhausted": ev.get("attempts", 1) >= lc.MAX_ATTEMPTS,
                                            "gates": ev.get("gates"), "ts": now}) + "\n")
                    if os.path.exists(ev.get("candidate_path") or ""):
                        os.replace(ev["candidate_path"], ev["candidate_path"] + ".done")
                evs.append(_public(ev))
        results = evs
    elif workers > 1 and len(tids) > 1:
        with multiprocessing.Pool(workers, initializer=_init_job,
                                  initargs=(paths, baseline, run_perf, specs)) as pool:
            for i, ev in enumerate(pool.imap_unordered(_eval_job, tids, chunksize=4), 1):
                ev["tkc_bin_sha"] = shas["tkc_bin_sha"]
                results.append(ev)
                with open(os.path.join(dry_dir, ev["task_id"] + ".json"), "w") as f:
                    json.dump(ev, f, indent=1)
                if i % 100 == 0:
                    print(f"  {i}/{len(tids)}", file=sys.stderr)
    else:
        with tempfile.TemporaryDirectory(prefix="bank_library_") as tmp:
            for tid in tids:
                ev = evaluate(paths, tid, tmp, baseline=baseline, run_perf=run_perf,
                              spec=_load_spec(paths, tid, specs))
                ev["tkc_bin_sha"] = shas["tkc_bin_sha"]
                ev = _public(ev)
                results.append(ev)
                with open(os.path.join(dry_dir, tid + ".json"), "w") as f:
                    json.dump(ev, f, indent=1)

    for ev in results:
        counts[ev["verdict"]] = counts.get(ev["verdict"], 0) + 1
        if ev.get("nul_carve_out"):
            carved.append(ev["task_id"])
        for g in ev.get("failed_gates") or []:
            gate_fails[g] = gate_fails.get(g, 0) + 1
        if ev["verdict"] == "rewritten":
            tokens_before += (ev.get("before") or {}).get("proxy_tokens") or 0
            tokens_after += (ev.get("after") or {}).get("proxy_tokens") or 0
            bytes_before += (ev.get("before") or {}).get("min_bytes") or 0
            bytes_after += (ev.get("after") or {}).get("min_bytes") or 0

    decided = counts["rewritten"] + counts["unchanged"] + counts["failed"]
    banked = counts["rewritten"] + counts["unchanged"]
    summary = {"story": lc.STORY, "tooling_story": lc.TOOLING_STORY, "wave": lc.WAVE,
               "mode": "bank" if bank else ("baseline" if baseline else "dry-run"),
               "ts": now, "candidates": len(tids), **counts,
               "would_bank_rate" if not bank else "banked_rate":
                   round(banked / decided, 4) if decided else None,
               "wave_gate_95pct": (banked / decided >= 0.95) if decided else None,
               "gate_failures": dict(sorted(gate_fails.items(), key=lambda kv: -kv[1])),
               "reasons": _reason_counts(results),
               "nul_carve_out": sorted(carved), "excluded_127_64": sorted(lc.EXCLUDED),
               "oracle": "manifest:expected_output", "original_output_compared": False,
               "exhausted": exhausted,
               "proxy_tokens_before": tokens_before, "proxy_tokens_after": tokens_after,
               "min_bytes_before": bytes_before, "min_bytes_after": bytes_after,
               **shas, "workdir": os.path.abspath(paths.workdir)}
    tag = "bank_report_" if bank else ("baseline_report_" if baseline else "dryrun_report_")
    rep_path = paths.sub(tag + now.replace(":", "") + ".json")
    os.makedirs(paths.workdir, exist_ok=True)
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
    ap.add_argument("--corpus", default=lc.CORPUS)
    ap.add_argument("--lib-root", default=lc.LIB_ROOT)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--shard", default=None)
    ap.add_argument("--sweep", default=None)
    ap.add_argument("--catalogue", default=lc.CATALOGUE_PATH)
    ap.add_argument("--card", default=lc.CARD_PATH)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="(default) evaluate candidates only; write dryrun/<tid>.json")
    g.add_argument("--bank", action="store_true",
                   help="really bank: archive, promote, provenance, ledger")
    ap.add_argument("--baseline", action="store_true",
                    help="dry-run the gate stack over the ORIGINAL programs (pre-wave "
                         "would-bank rate); implies --dry-run")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=1, help="dry-run/baseline only")
    ap.add_argument("--no-perf", action="store_true", help="skip the Tier-1 perf gate")
    args = ap.parse_args(argv)
    if args.bank and args.baseline:
        ap.error("--baseline is a dry run; it cannot bank")
    with lc.pin_tkc(sys.modules[__name__]) as pinned:
        paths = lc.LibraryPaths(corpus=args.corpus, lib_root=args.lib_root,
                                workdir=args.workdir, ledger=args.ledger,
                                shard=args.shard, sweep=args.sweep,
                                catalogue=args.catalogue, card=args.card)
        shas = {"card_sha": lc.short(lc.sha256_file(paths.card)),
                "catalogue_sha": lc.short(lc.sha256_file(paths.catalogue)),
                **{k: v for k, v in lc.tkc_stamp(paths.tkc, paths.toke_root).items()
                   if k in ("tkc_sha", "tkc_version", "tkc_bin_sha")}}
        assert shas["tkc_bin_sha"] == pinned.sha256
        print(f"bank_library: tkc {pinned.version} sha256 {pinned.sha256[:12]} "
              f"(pinned copy {pinned.path})", file=sys.stderr)
        s = run(paths, bank=args.bank, baseline=args.baseline, only=args.only,
                limit=args.limit, shas=shas, workers=args.workers,
                run_perf=not args.no_perf)
    print(json.dumps(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
