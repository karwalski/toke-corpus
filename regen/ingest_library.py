#!/usr/bin/env python3
"""131.18 — library ingest: run the full run_shard.validate gate set over the
back-generated library specs (shards/library_131.jsonl) and either report
(dry-run, default) or bank corpus records (--bank, used by 131.18-run only
AFTER the 131.17 library rewrite).

Gates (run_shard.validate_one_gates with lint_gate=True): compile (tkc --check),
build (tkc -o), tests (one execution per case, input on stdin, whole stdout ==
expected_output after trailing-ws norm, exit 0, fixtures materialised),
idiom >= 0.6, depth <= 4, fn <= 600 B, no pattern-rule error/warning (131.10),
lint warnings == 0.

Dry-run  : ledger/library_131_dryrun.jsonl — one row per program with the
           per-gate verdicts + metrics; nothing banked. Truncated on each run
           unless --resume.
--bank   : corpus/regen_v04/L-<CODE>/<task_id>.json (corpus schema v2 +
           regen.source="library" + regen.library + regen.rewrite131 provenance
           block per regen/freeze/README.md), MANIFEST.jsonl stamp
           (manifest_tool, 131.35: sha256 = record file bytes),
           ledger/library_131.jsonl (accepted/rejected, resumable, idempotent)
           and ledger/rewrite_131.jsonl (wave=library). Records that fail any
           gate are NOT banked (ledger row only).

Provenance choices (bank mode):
  rewrite131.prev_sha256  sha256 of the solution.tk bytes ingested (there is no
                          prior corpus record for a library program)
  rewrite131.tkc_sha      `git -C ~/tk/toke rev-parse --short HEAD` at run time
  rewrite131.catalogue_sha sha256(~/tk/toke/patterns/catalogue.json)[:12]
  rewrite131.card_sha     run_shard.CARD_SHA
  proxy_tokens_*          null until the 131.4 proxy tokenizer is wired
  perf                    {"tier": null, "ratio": null, "verdict": "n/a"}
  rewrite_131 ledger      status "rewritten" (README vocabulary; reason says
                          "131.18 library ingest") — parked for owner review
"""
import argparse, hashlib, json, multiprocessing, os, subprocess, sys, time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_shard                                  # noqa: E402
import manifest_tool                              # noqa: E402  (131.35)

CORPUS = os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04")
TOKE_REPO = os.path.expanduser("~/tk/toke")
CATALOGUE = os.path.join(TOKE_REPO, "patterns", "catalogue.json")
GATE_ORDER = ["compile", "build", "tests", "idiom", "structure", "pattern", "lint"]  # pattern: 131.10


def _sha_file(path, n=None):
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return h[:n] if n else h


def tool_shas():
    try:
        tkc_sha = subprocess.run(["git", "-C", TOKE_REPO, "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        tkc_sha = None
    cat_sha = _sha_file(CATALOGUE, 12) if os.path.exists(CATALOGUE) else None
    return tkc_sha or "unknown", cat_sha or "none"


def ingest_one(job):
    """One library program through the gates. Never raises."""
    spec, workdir, timeout = job
    t0 = time.time()
    row = {"task_id": spec["task_id"], "category": spec["category"],
           "library_id": spec["library"]["id"], "difficulty": spec.get("difficulty"),
           "ts": int(t0)}
    try:
        src_bytes = open(spec["source_program"], "rb").read()
        row["source_drift"] = hashlib.sha256(src_bytes).hexdigest() != spec.get("source_sha256")
        src = src_bytes.decode("utf-8", errors="replace")
        record, ok, reason, gates = run_shard.validate_one_gates(
            spec, src, workdir, lint_gate=True, case_timeout=timeout)
        row.update({"ok": ok, "reason": reason, "gates": gates})
        if record:
            rg = record["regen"]
            rt = rg.get("runtime") or {}
            row.update({"idiom": record["judge"]["score"], "min_bytes": rg.get("min_bytes"),
                        "max_depth": rg.get("max_depth"), "lint_warnings": rg.get("lint_warnings"),
                        "proxy_tokens": rg.get("proxy_tokens"), "over_budget": rg.get("over_budget"),
                        "pattern_violations": rg.get("lint_pattern_violations"),
                        "lint_exempt": rg.get("lint_exempt"),
                        "cases_total": rt.get("cases_total"), "cases_passed": rt.get("cases_passed"),
                        "error_codes": record["validation"]["error_codes"]})
        row["_record"] = record if ok else None
        row["_src_sha256"] = hashlib.sha256(src_bytes).hexdigest()
    except Exception as e:  # defensive: one bad program must not kill the sweep
        row.update({"ok": False, "reason": f"ingest_error: {type(e).__name__}: {e}",
                    "gates": {}})
    row["elapsed_s"] = round(time.time() - t0, 2)
    return row


def bank(row, spec, outdir, shas, shard_name, manifest=None):
    """Write the accepted record + MANIFEST/ledger rows. Returns record sha.
    `manifest`: a manifest_tool.Manifest held open by the caller (loads once);
    None falls back to the one-shot manifest_tool.stamp()."""
    record = row["_record"]
    tkc_sha, cat_sha = shas
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record["model"] = "library-worker (toke-test-programs results/solutions; see meta.json)"
    rg = record["regen"]
    rg["source"] = "library"
    rg["library"] = {"id": spec["library"]["id"], "category": spec["library"]["category"],
                     "manifest": spec["library"]["manifest"], "title": spec["library"]["title"],
                     "source_program": spec["source_program"]}
    rg["rewrite131"] = {
        "wave": "library", "story": "131.18", "ts": now,
        "prev_sha256": row["_src_sha256"],
        "card_sha": run_shard.CARD_SHA, "catalogue_sha": cat_sha, "tkc_sha": tkc_sha,
        "patterns_fixed": [], "lint_violations": rg.get("lint_warnings") or 0,
        "lint_exempt": list(rg.get("lint_exempt") or []),
        "proxy_tokens_before": None, "proxy_tokens_after": rg.get("proxy_tokens"),
        "perf": {"tier": None, "ratio": None, "verdict": "n/a"},
    }
    cat_dir = os.path.join(outdir, spec["category"])
    os.makedirs(cat_dir, exist_ok=True)
    data = json.dumps(record)
    rec_sha = hashlib.sha256(data.encode()).hexdigest()
    rec_path = os.path.join(cat_dir, spec["task_id"] + ".json")
    with open(rec_path, "w") as f:
        f.write(data)
    extra = {"id": record["id"], "category": spec["category"], "task_type": "stdin_program",
             "difficulty": spec.get("difficulty"), "shard": shard_name}
    if manifest is not None:                    # 131.35: stamp, never blind-append
        manifest.stamp(spec["task_id"], rec_path, extra=extra)
    else:
        manifest_tool.stamp(os.path.join(outdir, "MANIFEST.jsonl"), spec["task_id"], rec_path, extra)
    run_shard.append_ledger(outdir, "rewrite_131",
                            {"task_id": spec["task_id"], "wave": "library", "status": "rewritten",
                             "reason": "131.18 library ingest (new record from "
                                       f"{spec['library']['category']}/{spec['library']['id']})",
                             "attempts": 1, "prev_sha256": row["_src_sha256"],
                             "new_sha256": rec_sha, "ts": now})
    return rec_sha


def cmd_run(args):
    shard_name = os.path.splitext(os.path.basename(args.shard))[0]
    specs = [json.loads(l) for l in open(args.shard)]
    if args.category:
        specs = [s for s in specs if s["category"] == args.category]
    if args.limit:
        specs = specs[:args.limit]
    ledger_name = shard_name if args.bank else shard_name + "_dryrun"
    ledger_path = os.path.join(args.outdir, "ledger", ledger_name + ".jsonl")
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    done = {}
    if args.bank or args.resume:
        done = run_shard.load_ledger(args.outdir, ledger_name)
        skip = {t for t, e in done.items() if e.get("status") == "accepted" or
                (not args.bank and args.resume)}
        specs = [s for s in specs if s["task_id"] not in skip]
    elif os.path.exists(ledger_path):
        os.unlink(ledger_path)  # dry-run is a report: fresh each time
    workdir = os.path.join(args.outdir, "work", "library_131")
    os.makedirs(workdir, exist_ok=True)
    shas = tool_shas()
    mode = "BANK" if args.bank else "dry-run"
    print(f"{mode}: {len(specs)} programs, {args.workers} workers, ledger {ledger_path}, "
          f"tkc_sha={shas[0]} catalogue_sha={shas[1]}", file=sys.stderr)
    t0 = time.time()
    n = ok = 0
    jobs = [(s, workdir, args.timeout) for s in specs]
    by_id = {s["task_id"]: s for s in specs}
    manifest = manifest_tool.Manifest(os.path.join(args.outdir, "MANIFEST.jsonl")) if args.bank else None
    with multiprocessing.Pool(args.workers) as pool, open(ledger_path, "a") as led:
        for row in pool.imap_unordered(ingest_one, jobs, chunksize=4):
            n += 1
            ok += bool(row["ok"])
            spec = by_id[row["task_id"]]
            if args.bank:
                if row["ok"]:
                    rec_sha = bank(row, spec, args.outdir, shas, shard_name, manifest)
                    entry = {"task_id": row["task_id"], "status": "accepted", "reason": None,
                             "attempts": 1, "record_sha256": rec_sha, "ts": int(time.time())}
                else:
                    entry = {"task_id": row["task_id"], "status": "rejected",
                             "reason": row["reason"], "gates": row.get("gates"),
                             "attempts": 1, "ts": int(time.time())}
                led.write(json.dumps(entry) + "\n")
            else:
                row.pop("_record", None)
                row.pop("_src_sha256", None)
                led.write(json.dumps(row) + "\n")
            led.flush()
            if n % 200 == 0:
                print(f"  {n}/{len(specs)} ({ok} pass) {time.time() - t0:.0f}s", file=sys.stderr)
    print(json.dumps({"mode": mode, "processed": n, "pass": ok, "fail": n - ok,
                      "runtime_s": round(time.time() - t0, 1), "ledger": ledger_path}))


def cmd_report(args):
    shard_name = os.path.splitext(os.path.basename(args.shard))[0]
    path = os.path.join(args.outdir, "ledger", shard_name + "_dryrun.jsonl")
    rows = {}
    for line in open(path):
        r = json.loads(line)
        rows[r["task_id"]] = r
    rows = list(rows.values())
    gate_fail = Counter()
    gate_na = Counter()
    by_cat = defaultdict(lambda: Counter())
    reasons = Counter()
    first_gate = Counter()
    for r in rows:
        cat = r["category"]
        by_cat[cat]["total"] += 1
        by_cat[cat]["pass"] += bool(r["ok"])
        g = r.get("gates") or {}
        for k in GATE_ORDER:
            if g.get(k) is False:
                gate_fail[k] += 1
                by_cat[cat]["fail_" + k] += 1
            elif g.get(k) is None:
                gate_na[k] += 1
        if not r["ok"]:
            reason = r.get("reason") or "?"
            first_gate[reason.split(":")[0]] += 1
            # bucket by gate + short detail (drop numbers so buckets aggregate)
            key = reason
            if key.startswith("idiom:"):
                notes = key.split("(", 1)[1].rstrip(")").split(";") if "(" in key else []
                key = "idiom: " + ";".join(sorted({n.split("(")[0].split("\u00d7")[0].strip()
                                                    for n in notes}))
            elif key.startswith("structure: nesting"):
                key = "structure: nesting depth > 4"
            elif key.startswith("structure: function"):
                key = "structure: function > 600 bytes"
            elif key.startswith("lint:"):
                key = "lint: " + key.split("(", 1)[1].rstrip(")") if "(" in key else key
            elif key.startswith("runtime:"):
                key = "runtime: " + key.split(":", 1)[1].split("(case")[0].strip()
            reasons[key] += 1
    total = len(rows)
    print(f"dry-run report: {total} programs, pass {sum(r['ok'] for r in rows)}, "
          f"fail {sum(not r['ok'] for r in rows)}, "
          f"source_drift {sum(1 for r in rows if r.get('source_drift'))}")
    print("\nper gate (independent verdicts; a program can fail several):")
    print(f"  {'gate':10s} {'fail':>5s} {'n/a':>5s}")
    for k in GATE_ORDER:
        print(f"  {k:10s} {gate_fail[k]:5d} {gate_na[k]:5d}")
    print("\nfirst failing gate (validate reason prefix):", dict(first_gate))
    print("\nper category: total pass | fail per gate")
    hdr = " ".join(f"{k[:5]:>5s}" for k in GATE_ORDER)
    print(f"  {'cat':7s} {'total':>5s} {'pass':>5s} | {hdr}")
    for cat in sorted(by_cat):
        c = by_cat[cat]
        cells = " ".join(f"{c['fail_' + k]:5d}" for k in GATE_ORDER)
        print(f"  {cat:7s} {c['total']:5d} {c['pass']:5d} | {cells}")
    print("\ntop failure reasons (first failing gate, bucketed):")
    for k, v in reasons.most_common(args.top):
        print(f"  {v:5d}  {k}")
    for k in ("idiom", "max_depth", "min_bytes", "lint_warnings", "proxy_tokens"):
        vals = sorted(r[k] for r in rows if r.get(k) is not None)
        if vals:
            pct = {p: vals[min(len(vals) - 1, int(p / 100 * len(vals)))] for p in (50, 90, 99)}
            print(f"{k}: p50/p90/p99 = {pct[50]}/{pct[90]}/{pct[99]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--shard", default=os.path.join(CORPUS, "shards", "library_131.jsonl"))
    ap.add_argument("--outdir", default=CORPUS)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--category", help="e.g. L-SYS")
    ap.add_argument("--timeout", type=float, default=10.0, help="per test case seconds")
    ap.add_argument("--resume", action="store_true", help="dry-run: keep existing ledger rows")
    ap.add_argument("--bank", action="store_true",
                    help="write records/MANIFEST/ledgers (131.18-run only, after 131.17)")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
