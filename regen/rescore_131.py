#!/usr/bin/env python3
"""Epic 131.10 — re-score every banked record against the tkc pattern rules.

First honest look at the pattern-rewrite backlog (before 131.13): the 129-era
"lint 0 warnings" gate was vacuous (`--lint --diag-json` emitted nothing until
131.9) and the idiom score was a regex approximation. This sweep records, per
record, both judges plus the new hard-gate verdict and the proxy8k token count.

Subcommands:
  run     - parallel, resumable (append-only ledger audit/rescore_131.ledger.jsonl,
            same idempotency pattern as audit.py). Per record: raw pattern
            diagnostics (stub-prefix diags dropped), idiom_score_old (regex
            judge, byte-identical to the 129 gate), idiom_score_new (pattern
            rules), hard-gate rules gross/net of the style mandate, proxy_tokens.
  report  - dedupe the ledger -> audit/rescore_131.jsonl (with over_budget),
            audit/rescore_131_summary.json, regen/RESCORE_131.md; writes
            regen/freeze/proxy_budget_v04.json (category/task_type medians ×
            FACTOR) unless it exists (--rebudget overwrites).

Records are NOT modified; nothing here touches MANIFEST.jsonl.
"""
import argparse, glob, hashlib, json, multiprocessing, os, statistics, sys, time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import idiom_judge                                # noqa: E402
import metrics                                    # noqa: E402
import tkc_pin                                    # noqa: E402  (131.39)

CORPUS = os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04")
BUDGET_PATH = os.path.join(HERE, "freeze", "proxy_budget_v04.json")
MD_PATH = os.path.join(HERE, "RESCORE_131.md")
FACTOR = 1.5
RULES = list(idiom_judge.PATTERN_RULES)


def _sha_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def load_specs(corpus):
    specs = {}
    for sh in sorted(glob.glob(os.path.join(corpus, "shards", "*.jsonl"))):
        for line in open(sh):
            s = json.loads(line)
            specs[s["task_id"]] = s
    return specs


def _init_worker():
    metrics.proxy_counter()        # load the tokenizer once per process


def rescore_one(job):
    task_id, rec_path, meta, tmpdir = job
    row = {"task_id": task_id, "ts": int(time.time())}
    row.update(meta)
    try:
        rec = json.load(open(rec_path))
        src = rec["tk_source"]
        row["source_sha256"] = rec.get("regen", {}).get("source_sha256")
        row["idiom_score_129"] = rec.get("judge", {}).get("score")
        tkpath = os.path.join(tmpdir, task_id + ".tk")
        with open(tkpath, "w", encoding="utf-8") as f:
            f.write(src)
        try:
            raw = idiom_judge.lint_diags(path=tkpath)
            mn = metrics.min_form(tkpath)
        finally:
            if os.path.exists(tkpath):
                os.unlink(tkpath)
        stub_n = idiom_judge.stub_prefix_len(src)
        diags = idiom_judge.strip_stub_diags(raw, src)
        row["stub_diags_dropped"] = len(raw) - len(diags)
        row["stub_unused_import"] = sum(1 for d in raw if d.get("rule") == "unused-import"
                                        and idiom_judge.is_stub_import(d, src, stub_n))
        exempt = idiom_judge.mandate_exempt_rules(meta.get("style_mandate"))
        hits = idiom_judge.pattern_hits(diags)
        row["violations"] = [{"rule": d["rule"], "severity": d.get("severity"),
                              "line": (d.get("pos") or {}).get("line"),
                              "fixable": "fix" in d} for d in hits]
        row["suppressed"] = [{"rule": d["rule"], "line": (d.get("pos") or {}).get("line"),
                              "why": d["suppressed"]} for d in diags if d.get("suppressed")]
        row["other_lint"] = sorted(Counter(d["rule"] for d in diags
                                           if d["rule"] not in idiom_judge.PATTERN_RULES).items())
        gross = idiom_judge.hard_gate(diags)
        net = idiom_judge.hard_gate(diags, exempt)
        row["hard_fail_gross"] = sorted({d["rule"] for d in gross})
        row["hard_fail_net"] = sorted({d["rule"] for d in net})
        row["lint_exempt"] = [r for r in exempt if any(d["rule"] == r for d in hits)]
        old, old_notes = idiom_judge.legacy_score(src)
        new, new_notes = idiom_judge.score(src, diags)
        row.update({"idiom_score_old": round(old, 4), "idiom_notes_old": old_notes,
                    "idiom_score_new": round(new, 4), "idiom_notes_new": new_notes})
        row["min_bytes"] = len(mn.encode()) if mn is not None else None
        row["proxy_tokens"] = metrics.proxy_tokens(mn)
        if mn is None:
            row["error"] = "tkc --min failed"
    except Exception as e:  # defensive: one bad record must not kill the sweep
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def cmd_run(args):
    corpus = args.corpus_dir
    audit_dir = os.path.join(corpus, "audit")
    tmpdir = os.path.join(audit_dir, "tmp_rescore131")
    os.makedirs(tmpdir, exist_ok=True)
    ledger_path = os.path.join(audit_dir, "rescore_131.ledger.jsonl")
    done = set()
    if os.path.exists(ledger_path):
        for line in open(ledger_path):
            done.add(json.loads(line)["task_id"])
    specs = load_specs(corpus)
    man = {}
    for line in open(os.path.join(corpus, "MANIFEST.jsonl")):
        e = json.loads(line)
        man[e["task_id"]] = e               # last line wins (retried tasks)
    jobs = []
    for tid, e in man.items():
        if tid in done or (args.category and e.get("category") != args.category):
            continue
        rec_path = os.path.join(corpus, e.get("path") or f"{e['category']}/{tid}.json")
        if not os.path.exists(rec_path):
            continue
        spec = specs.get(tid, {})
        meta = {"category": e.get("category") or spec.get("category"),
                "task_type": e.get("task_type") or spec.get("task_type"),
                "difficulty": e.get("difficulty", spec.get("difficulty")),
                "style_mandate": idiom_judge.style_mandate(spec) if spec else None}
        jobs.append((tid, rec_path, meta, tmpdir))
    if args.limit:
        jobs = jobs[:args.limit]
    t0 = time.time()
    # 131.39: every worker execs one private copy of tkc (was: the symlink, which
    # a concurrent `make` swapped under the 131.10 sweep -> three build shas)
    pinned = tkc_pin.pin().install(sys.modules[__name__], idiom_judge, metrics)
    tkc_sha = pinned.sha256
    print(f"rescoring {len(jobs)} records ({len(done)} already done), {args.workers} workers, "
          f"tkc {pinned.version} sha256 {tkc_sha[:12]} (pinned copy {pinned.path})", file=sys.stderr)
    n = fail = 0
    with pinned, multiprocessing.Pool(args.workers, initializer=_init_worker) as pool, \
            open(ledger_path, "a") as led:
        for row in pool.imap_unordered(rescore_one, jobs, chunksize=16):
            row["tkc_sha256"] = tkc_sha       # kept: pre-131.39 column name
            row["tkc_bin_sha"] = tkc_sha      # 131.39: sha256 of the pinned binary
            led.write(json.dumps(row) + "\n")
            led.flush()
            n += 1
            fail += bool(row.get("hard_fail_net"))
            if n % 1000 == 0:
                print(f"  {n}/{len(jobs)} ({fail} hard-fail) {time.time() - t0:.0f}s", file=sys.stderr)
    # the SOURCE binary may have been rebuilt meanwhile; every row was scored on the pinned copy
    tkc_sha_end = tkc_pin.bin_sha(pinned.source)
    if tkc_sha_end != tkc_sha:
        print(f"note: {pinned.source} changed during the sweep ({tkc_sha[:12]} -> {(tkc_sha_end or 'missing')[:12]}); "
              f"all rows were scored on the pinned copy {tkc_sha[:12]}", file=sys.stderr)
    print(json.dumps({"rescored": n, "hard_fail_net": fail, "elapsed_s": round(time.time() - t0, 1),
                      "ledger": ledger_path, "tkc_sha256": tkc_sha, "tkc_sha256_end": tkc_sha_end,
                      "tkc_bin_sha": tkc_sha, "tkc_version": pinned.version}))


# ---------------------------------------------------------------- report ---
def _pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(p / 100 * len(vals)))]


def _dist(vals):
    vals = [v for v in vals if v is not None]
    return {"n": len(vals), "p50": _pct(vals, 50), "p90": _pct(vals, 90), "p99": _pct(vals, 99),
            "max": max(vals) if vals else None}


def build_budget(rows, factor, proxy_meta):
    by_key = defaultdict(list)
    by_cat = defaultdict(list)
    for r in rows:
        if r.get("proxy_tokens") is None:
            continue
        by_key[f"{r['category']}/{r['task_type']}"].append(r["proxy_tokens"])
        by_cat[r["category"]].append(r["proxy_tokens"])
    med = {k: statistics.median(v) for k, v in by_key.items()}
    cmed = {k: statistics.median(v) for k, v in by_cat.items()}
    budget = {k: round(factor * m) for k, m in {**cmed, **med}.items()}
    return {
        "schema": "proxy_budget_v04",
        "story": "131.10",
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "corpus/regen_v04 as frozen by freeze-129-20260819 (regen/freeze/freeze_129_manifest.jsonl), "
                  "rescore_131.py run",
        "records": sum(len(v) for v in by_key.values()),
        "metric": "proxy8k tokens of the string-masked `tkc --min` whole program (131.4 decision metric)",
        "factor": factor,
        "key": "category/task_type (single_function records carry the harness stub; category-only "
               "medians are kept for reference and as the fallback key)",
        "proxy": proxy_meta,
        "median": {k: med[k] for k in sorted(med)},
        "category_median": {k: cmed[k] for k in sorted(cmed)},
        "n": {k: len(by_key[k]) for k in sorted(by_key)},
        "budget": {k: budget[k] for k in sorted(budget)},
    }


def cmd_report(args):
    corpus = args.corpus_dir
    audit_dir = os.path.join(corpus, "audit")
    ledger_path = os.path.join(audit_dir, "rescore_131.ledger.jsonl")
    rows = {}
    for line in open(ledger_path):
        r = json.loads(line)
        rows[r["task_id"]] = r          # last write wins
    rows = list(rows.values())
    rows.sort(key=lambda r: r["task_id"])

    ctr = metrics.proxy_counter()
    proxy_meta = ({"file": ctr.proxy_path.name, "sha256": ctr.proxy_sha} if ctr
                  else {"error": metrics._counter_err})
    if os.path.exists(BUDGET_PATH) and not args.rebudget:
        budget = json.load(open(BUDGET_PATH))
    else:
        budget = build_budget(rows, args.factor, proxy_meta)
        os.makedirs(os.path.dirname(BUDGET_PATH), exist_ok=True)
        with open(BUDGET_PATH, "w") as f:
            json.dump(budget, f, indent=1)
            f.write("\n")
    for r in rows:
        r["over_budget"] = metrics.over_budget(r["category"], r["task_type"], r.get("proxy_tokens"), budget)
    final_path = os.path.join(audit_dir, "rescore_131.jsonl")
    with open(final_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    floor = idiom_judge.IDIOM_FLOOR
    cats = sorted({r["category"] for r in rows})

    def agg(sub):
        hits_rec = Counter()      # records with >=1 hit per rule
        hits_tot = Counter()      # total hits per rule
        sev = Counter()
        fixable = Counter()
        for r in sub:
            seen = set()
            for v in r.get("violations") or []:
                hits_tot[v["rule"]] += 1
                sev[(v["rule"], v["severity"])] += 1
                if v.get("fixable"):
                    fixable[v["rule"]] += 1
                seen.add(v["rule"])
            for ru in seen:
                hits_rec[ru] += 1
        gross = Counter(ru for r in sub for ru in r.get("hard_fail_gross") or [])
        net = Counter(ru for r in sub for ru in r.get("hard_fail_net") or [])
        exempt = Counter(ru for r in sub for ru in r.get("lint_exempt") or [])
        other = Counter()
        for r in sub:
            for ru, n in r.get("other_lint") or []:
                other[ru] += n
        old_below = sum(1 for r in sub if r.get("idiom_score_old") is not None and r["idiom_score_old"] < floor)
        new_below = sum(1 for r in sub if r.get("idiom_score_new") is not None and r["idiom_score_new"] < floor)
        return {
            "records": len(sub),
            "errors": sum(1 for r in sub if r.get("error")),
            "records_with_any_hit": sum(1 for r in sub if r.get("violations")),
            "hits_records_by_rule": {ru: hits_rec[ru] for ru in RULES if hits_rec[ru]},
            "hits_total_by_rule": {ru: hits_tot[ru] for ru in RULES if hits_tot[ru]},
            "hits_fixable_by_rule": {ru: fixable[ru] for ru in RULES if fixable[ru]},
            "hard_fail_records_gross": sum(1 for r in sub if r.get("hard_fail_gross")),
            "hard_fail_records_net": sum(1 for r in sub if r.get("hard_fail_net")),
            "hard_fail_by_rule_gross": {ru: gross[ru] for ru in RULES if gross[ru]},
            "hard_fail_by_rule_net": {ru: net[ru] for ru in RULES if net[ru]},
            "mandate_exempt_by_rule": {ru: exempt[ru] for ru in RULES if exempt[ru]},
            "style_mandated": sum(1 for r in sub if r.get("style_mandate")),
            "below_floor_old": old_below,
            "below_floor_new": new_below,
            "below_floor_new_not_old": sum(1 for r in sub if r.get("idiom_score_new") is not None
                                           and r["idiom_score_new"] < floor <= (r.get("idiom_score_old") or 1)),
            "below_floor_old_not_new": sum(1 for r in sub if r.get("idiom_score_old") is not None
                                           and r["idiom_score_old"] < floor <= (r.get("idiom_score_new") or 1)),
            "score_up": sum(1 for r in sub if (r.get("idiom_score_new") or 0) > (r.get("idiom_score_old") or 0)),
            "score_down": sum(1 for r in sub if (r.get("idiom_score_new") or 0) < (r.get("idiom_score_old") or 0)),
            "idiom_old_mean": round(statistics.fmean(r["idiom_score_old"] for r in sub
                                                     if r.get("idiom_score_old") is not None), 4) if sub else None,
            "idiom_new_mean": round(statistics.fmean(r["idiom_score_new"] for r in sub
                                                     if r.get("idiom_score_new") is not None), 4) if sub else None,
            "score_changed": sum(1 for r in sub if r.get("idiom_score_old") != r.get("idiom_score_new")),
            "other_lint_by_rule": dict(sorted(other.items(), key=lambda kv: -kv[1])),
            "stub_unused_import_records": sum(1 for r in sub if r.get("stub_unused_import")),
            "suppressed_linter_fp_records": sum(1 for r in sub if r.get("suppressed")),
            "suppressed_linter_fp_hits": sum(len(r.get("suppressed") or []) for r in sub),
            "proxy_tokens": _dist([r.get("proxy_tokens") for r in sub]),
            "min_bytes": _dist([r.get("min_bytes") for r in sub]),
            "over_budget": sum(1 for r in sub if r.get("over_budget")),
            "proxy_by_task_type": {tt: _dist([r.get("proxy_tokens") for r in sub if r["task_type"] == tt])
                                   for tt in sorted({r["task_type"] for r in sub})},
        }

    summary = {
        "story": "131.10",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ledger": ledger_path,
        "tkc_sha256": sorted({r.get("tkc_sha256") for r in rows if r.get("tkc_sha256")}),
        # 131.39: pinned-binary shas the rows carry (pre-131.39 rows have none)
        "tkc_bin_sha": sorted({r.get("tkc_bin_sha") for r in rows if r.get("tkc_bin_sha")}),
        "idiom_floor": floor,
        "factor": budget.get("factor"),
        "budget_file": BUDGET_PATH,
        "penalties": idiom_judge.PATTERN_RULES,
        "mandate_exemptions": {k: list(v) for k, v in idiom_judge.MANDATE_EXEMPTIONS.items()},
        "total": agg(rows),
        "by_category": {c: agg([r for r in rows if r["category"] == c]) for c in cats},
        "by_task_type": {t: agg([r for r in rows if r["task_type"] == t])
                         for t in sorted({r["task_type"] for r in rows})},
    }
    # legacy-vs-new disagreement samples per rule (false-positive triage aid)
    samples = defaultdict(list)
    for r in rows:
        for ru in r.get("hard_fail_gross") or []:
            if len(samples[ru]) < 8:
                samples[ru].append(r["task_id"])
    summary["samples_by_rule"] = dict(samples)
    with open(os.path.join(audit_dir, "rescore_131_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    write_md(summary, budget, rows)
    print(json.dumps({"records": len(rows), "final": final_path,
                      "hard_fail_net": summary["total"]["hard_fail_records_net"],
                      "hard_fail_gross": summary["total"]["hard_fail_records_gross"],
                      "below_floor_old": summary["total"]["below_floor_old"],
                      "below_floor_new": summary["total"]["below_floor_new"],
                      "over_budget": summary["total"]["over_budget"], "md": MD_PATH}))


def write_md(summary, budget, rows):
    T = summary["total"]
    cats = summary["by_category"]
    L = []
    L.append("# RESCORE_131 — corpus re-score on the tkc pattern rules (story 131.10)\n")
    L.append(f"Generated {summary['generated']} · {T['records']:,} records · tkc sha256 "
             f"{', '.join(s[:12] for s in summary['tkc_sha256'])} · proxy "
             f"{budget.get('proxy', {}).get('file')} · idiom floor {summary['idiom_floor']}\n")
    L.append("**Why this exists.** Before 131.9 `tkc --lint --diag-json` emitted no JSON, so "
             "`metrics.lint()` returned `[]` on every record and the 129.6 \"lint 0 warnings\" gate was "
             "vacuous; the only idiom signal on the frozen corpus was a regex approximation. This sweep "
             "scores every frozen record with the real pattern rules (`docs/lint-rules-v1.md`, Pattern "
             "rules) and reports what the new hard gate (`run_shard.py validate`: any pattern-rule "
             "error/warning fails, hints pass, spec style mandates exempt) would reject. It is the "
             "input to 131.13's bucketing; no record was modified.\n")
    L.append("## Headline\n")
    L.append("| metric | value |\n|---|---|")
    L.append(f"| records failing the new hard gate (net of style mandates) | **{T['hard_fail_records_net']:,}** "
             f"({100 * T['hard_fail_records_net'] / max(1, T['records']):.1f}%) |")
    L.append(f"| records failing gross (before mandate exemption) | {T['hard_fail_records_gross']:,} |")
    L.append(f"| records with any pattern hit (incl. hints) | {T['records_with_any_hit']:,} |")
    L.append(f"| below idiom floor — old regex judge | {T['below_floor_old']:,} |")
    L.append(f"| below idiom floor — new pattern judge | {T['below_floor_new']:,} "
             f"(newly below: {T['below_floor_new_not_old']:,}) |")
    L.append(f"| mean idiom old → new | {T['idiom_old_mean']} → {T['idiom_new_mean']} "
             f"({T['score_changed']:,} records changed score: {T['score_down']:,} down, {T['score_up']:,} up; "
             f"{T['below_floor_old_not_new']:,} were below floor only under the regex) |")
    L.append(f"| over proxy budget (> {summary['factor']}× category/task_type median) | {T['over_budget']:,} |")
    L.append(f"| proxy tokens p50 / p90 / p99 | {T['proxy_tokens']['p50']} / {T['proxy_tokens']['p90']} / "
             f"{T['proxy_tokens']['p99']} |")
    L.append(f"| records with a stub `unused-import` dropped (harness exemption) | "
             f"{T['stub_unused_import_records']:,} |")
    L.append(f"| `discarded-value-result` hits suppressed as a linter false positive "
             f"(expression-if branch value; see below) | {T['suppressed_linter_fp_hits']:,} hits on "
             f"{T['suppressed_linter_fp_records']:,} records |")
    L.append(f"| sweep errors | {T['errors']} |\n")
    L.append("**Linter false positive found by this sweep.** `discarded-value-result` (severity error) "
             "fires on a value-returning call that is the last expression (the value) of an expression-`if` branch — "
             "`out=if(c){out.append(v)}el{out}`, `let y=if(c){x.push(9)}el{x}`, `<if(c){r.append(v)}el{r}`, "
             "`m=if(t){let v=a.get(k);k=k+1;m.append(v)}el{m}` — "
             "where the value is the branch's value, not discarded (the programs run correctly). "
             "fixed in tkc by 127.33 (`src/lint.c`: a tail call of an expression-if/mt branch is the branch value); "
             "the counts above are the hits the pre-fix judge suppressed (0 on a post-fix re-run).\n")

    L.append("## Hard-gate failures by rule (records)\n")
    L.append("| rule | severity | penalty | records hit | total hits | fixable hits | hard-fail gross | "
             "mandate-exempt | hard-fail net |\n|---|---|---|---|---|---|---|---|---|")
    sev = {"mut-flag-if": "warning", "flag-soup": "warning", "string-concat-chain": "warning",
           "single-use-let": "hint", "discarded-value-result": "error", "loop-rebuilds-array": "hint"}
    for ru in RULES:
        L.append(f"| `{ru}` | {sev[ru]} | −{summary['penalties'][ru]:.2f} | "
                 f"{T['hits_records_by_rule'].get(ru, 0):,} | {T['hits_total_by_rule'].get(ru, 0):,} | "
                 f"{T['hits_fixable_by_rule'].get(ru, 0):,} | {T['hard_fail_by_rule_gross'].get(ru, 0):,} | "
                 f"{T['mandate_exempt_by_rule'].get(ru, 0):,} | {T['hard_fail_by_rule_net'].get(ru, 0):,} |")
    L.append("\nMandate exemptions (`quality_rubric.md` Exemptions, `idiom_judge.MANDATE_EXEMPTIONS`): "
             + "; ".join(f"\"…{k}…\" → {', '.join(v)}" for k, v in summary["mandate_exemptions"].items())
             + ". `discarded-value-result` is never exempt.\n")

    L.append("## Per-category violations by rule (records with ≥ 1 hit)\n")
    hdr = "| category | records | " + " | ".join(f"`{r}`" for r in RULES) + " | hard-fail net | below floor old → new |"
    L.append(hdr)
    L.append("|---|---|" + "---|" * len(RULES) + "---|---|")
    for c, a in cats.items():
        L.append(f"| {c} | {a['records']:,} | " + " | ".join(f"{a['hits_records_by_rule'].get(r, 0):,}" for r in RULES)
                 + f" | {a['hard_fail_records_net']:,} | {a['below_floor_old']:,} → {a['below_floor_new']:,} |")
    L.append(f"| **all** | {T['records']:,} | " + " | ".join(f"{T['hits_records_by_rule'].get(r, 0):,}" for r in RULES)
             + f" | {T['hard_fail_records_net']:,} | {T['below_floor_old']:,} → {T['below_floor_new']:,} |\n")

    L.append("## Proxy-token distribution and budget\n")
    L.append(f"Budget file: `regen/freeze/proxy_budget_v04.json` — median per `category/task_type` over the "
             f"frozen corpus, budget = {budget.get('factor')} × median; `over_budget` is a **soft** flag "
             "(131.13 decides whether it becomes hard). Task-type keying: a single_function record is the "
             "function plus the harness stub, a full_program carries `main` and its prints — one median per "
             "category would flag nearly every full_program in the A-categories.\n")
    L.append("| category / task_type | n | median | budget | p50 | p90 | p99 | max | over budget |\n|---|---|---|---|---|---|---|---|---|")
    for c, a in cats.items():
        for tt, d in a["proxy_by_task_type"].items():
            k = f"{c}/{tt}"
            ob = sum(1 for r in rows if r["category"] == c and r["task_type"] == tt and r.get("over_budget"))
            L.append(f"| {k} | {d['n']:,} | {budget['median'].get(k)} | {budget['budget'].get(k)} | "
                     f"{d['p50']} | {d['p90']} | {d['p99']} | {d['max']} | {ob:,} |")
    L.append(f"| **all** | {T['proxy_tokens']['n']:,} | — | — | {T['proxy_tokens']['p50']} | "
             f"{T['proxy_tokens']['p90']} | {T['proxy_tokens']['p99']} | {T['proxy_tokens']['max']} | "
             f"{T['over_budget']:,} |\n")

    L.append("## Other lint rules seen (not gated on the shard path; gated for library ingest)\n")
    L.append("| rule | total hits |\n|---|---|")
    for ru, n in T["other_lint_by_rule"].items():
        L.append(f"| `{ru}` | {n:,} |")
    L.append("")
    L.append("## Samples per hard-gate rule (for false-positive triage)\n")
    for ru, ids in summary["samples_by_rule"].items():
        L.append(f"- `{ru}`: " + ", ".join(f"`{i}`" for i in ids))
    L.append("")
    L.append("Per-record rows: `corpus/regen_v04/audit/rescore_131.jsonl` (`idiom_score_old`, "
             "`idiom_score_new`, `violations`, `hard_fail_gross`, `hard_fail_net`, `lint_exempt`, "
             "`proxy_tokens`, `over_budget`); aggregates: `audit/rescore_131_summary.json`.\n")
    with open(MD_PATH, "w") as f:
        f.write("\n".join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--corpus-dir", default=CORPUS)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--category")
    ap.add_argument("--factor", type=float, default=FACTOR, help="budget = factor × median")
    ap.add_argument("--rebudget", action="store_true", help="recompute proxy_budget_v04.json even if present")
    args = ap.parse_args()
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
