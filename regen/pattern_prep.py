#!/usr/bin/env python3
"""Epic 131.15 — prepare the agent pattern-rewrite wave (AGENT bucket).

Input: audit/pattern_sweep.jsonl (131.13) rows with bucket == AGENT. Output,
under work/pattern_131/ (the 129 repair-wave layout, so the workflow and the
check/bank scripts share one contract):
  specs/<tid>.json     spec (with a_tests-injected test cases)
  meta/<tid>.json      before-metrics, flagged rules, exemptions, attempts
  prompts/<tid>.txt    worker prompt (task, current source, ONLY the catalogue
                       entries for the flagged rules, expected output, card)
  batches/batch_NNN.json  {"batch": N, "task_ids": [...]}   (20 per batch)
  manifest.json        card_sha, catalogue_sha, tkc sha, counts, batch list
Idempotent: tasks already rewritten/unchanged in ledger/rewrite_131.jsonl
(wave agent) are skipped, as are tasks that exhausted their retry. Stale
higher-numbered batch files from a previous prep are removed (bank BEFORE
re-prepping — REBUILD_STATUS wave rules). At most --max-batches (100) batches
are written per prep; the remainder is deferred to the next wave.

"Current source" is the record on disk — post-131.14 when an auto-fix was
banked (banking replaces tk_source), else the frozen source. The flagged
rules are re-linted live at prep time (the sweep row may predate the
auto-fix); sweep-flagged rules tkc no longer reports are listed separately.
Nothing here writes to the corpus.
"""
import argparse, glob, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pattern_common as pc                       # noqa: E402
from audit import load_specs                      # noqa: E402

INSTRUCTION = ("REWRITE RULES: behaviour identical (same printed output for every "
               "input, same signature, same module shape); least tokens — every "
               "byte of the --min form counts; no pattern-lint violations "
               "(tkc --lint pattern rules must report 0 errors/warnings; fixing the "
               "hints too is expected unless the task mandate forbids it; add no new "
               "lint warnings of any kind); the --min byte count and proxy-token "
               "count must not grow. Self-check with check_pattern.py; at most 3 attempts.")


def build_prompt(spec, src, ctx):
    """Assemble one worker prompt. ctx keys: must, hints, sweep_only, exempt,
    exempt_hit, entries, before, attempts, prev_reason, card, rule_texts,
    row, toke_root."""
    ttype = spec.get("task_type", "full_program")
    row = ctx.get("row") or {}
    rule_texts = ctx.get("rule_texts") or {}
    lines = [f"PATTERN REWRITE TASK ({spec.get('category')}, {ttype}): the toke 2.8.0 "
             "program below is CORRECT (it passes its tests) but uses verbose / "
             "anti-pattern forms flagged by the Epic 131 pattern sweep. Rewrite it "
             "in the canonical forms.",
             "",
             f"Task: {spec.get('description_v03') or spec.get('description', '')}",
             ""]
    if ttype == "single_function":
        lines.append("Module shape: single_function — keep the `m=harness;` stub prefix "
                     "exactly as it is and do NOT add a main(); rewrite only the target "
                     "function (and any helpers you add).")
    elif ttype == "stdin_program":
        lines.append("Module shape: stdin_program — a complete program reading stdin; "
                     "keep main() and its build flags.")
    else:
        lines.append("Module shape: full_program — keep main() and its printed lines.")
    mandate = pc.style_mandate(spec)
    if mandate:
        lines += ["", f"NOTE the task description mandates: \"{mandate}\" — honour it. "
                      f"Rules exempted by that mandate: {', '.join(ctx['exempt']) or 'none'} "
                      "(a violation of an exempted rule does not fail the gate; still "
                      "prefer the canonical form where the mandate allows)."]
    if ctx.get("attempts", 1) > 1 and ctx.get("prev_reason"):
        lines += ["", f"RETRY (attempt {ctx['attempts']}): the previous candidate was "
                      f"rejected by the main-thread bank: {ctx['prev_reason']}"]
    lines += ["", "Flagged rules (tkc --lint, current source):"]
    if not ctx["must"] and not ctx["hints"]:
        lines.append("  (none reported by tkc right now)")
    for r in ctx["must"]:
        sev, txt = rule_texts.get(r, ("warning", ""))
        lines.append(f"  - {r} [{sev}, MUST fix]: {txt}")
    for r in ctx["hints"]:
        sev, txt = rule_texts.get(r, ("hint", ""))
        lines.append(f"  - {r} [{sev}, fix unless mandated]: {txt}")
    for r in ctx.get("exempt_hit") or []:
        lines.append(f"  - {r} [exempt by task mandate — not gated]")
    for r in ctx.get("sweep_only") or []:
        sev, txt = rule_texts.get(r, ("?", ""))
        lines.append(f"  - {r} [sweep-flagged; not reported by tkc on the current "
                     f"source — check by eye]: {txt}")
    if pc.bucket_est_saving(row) is not None or row.get("perf_sensitive"):
        lines.append(f"  sweep estimate: saving ~{pc.bucket_est_saving(row)} proxy tokens"
                     + ("; perf-sensitive pattern touched — a Tier-2 benchmark runs "
                        "after banking, keep the hot path the catalogue prescribes"
                        if row.get("perf_sensitive") else ""))
    b = ctx.get("before") or {}
    lines.append(f"  current size: --min {b.get('min_bytes')} bytes, proxy tokens "
                 f"{b.get('proxy_tokens')} — the rewrite must be <= both.")
    if ctx["entries"]:
        lines += ["", "Canonical forms (pattern catalogue, only the entries for the "
                      "flagged rules):", ""]
        for e in ctx["entries"]:
            lines += [pc.render_entry(e, ctx.get("toke_root", pc.TOKE_ROOT)), ""]
    lines += ["Expected output (test lock):"]
    lines += pc.expected_output_lines(spec)
    lines += ["", INSTRUCTION, "", "Current source:", src.rstrip("\n"), ""]
    if ctx.get("card"):
        lines += ["", "---", "SYNTAX CARD (the only authoritative toke 2.8.0 spec; "
                  f"sha {ctx.get('card_sha')}):", "", ctx["card"]]
    return "\n".join(lines) + "\n"


def read_retry_queue(path):
    """{task_id: last entry} of bank_pattern's retry_queue.jsonl ({} if absent)."""
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if line:
            try:
                e = json.loads(line)
                out[e["task_id"]] = e
            except (ValueError, KeyError):
                continue
    return out


def _clear_stale_batches(batch_dir, keep):
    removed = 0
    for p in glob.glob(os.path.join(batch_dir, "batch_*.json")):
        n = os.path.basename(p)[6:-5]
        if n.isdigit() and int(n) >= keep:
            os.unlink(p)
            removed += 1
    return removed


def prepare(paths, bucket_path, bucket_name="AGENT", max_batches=pc.MAX_BATCHES,
            limit=None, embed_card=True, specs=None, catalogue=None, rule_texts=None,
            only=None):
    """Prep the wave. Returns the manifest dict (also written to
    workdir/manifest.json)."""
    rows = pc.load_bucket(bucket_path, bucket_name)
    if only:
        rows = {t: r for t, r in rows.items() if t in set(only)}
    specs = specs if specs is not None else load_specs(paths.corpus)
    cat = catalogue or pc.load_catalogue(paths.catalogue)
    rule_texts = rule_texts if rule_texts is not None else pc.load_rule_texts()
    ledger = pc.read_ledger(paths.ledger)
    card = open(paths.card, encoding="utf-8").read()
    card_sha = pc.short(pc.sha256_text(card))
    for d in ("specs", "meta", "prompts", "batches", "gen"):
        os.makedirs(paths.sub(d), exist_ok=True)
    retry = read_retry_queue(paths.sub("retry_queue.jsonl"))
    skipped = {"done": 0, "exhausted": 0, "no_spec": 0, "no_record": 0}
    todo, unbanked = [], []
    with tempfile.TemporaryDirectory(prefix="pattern_prep_") as tmp:
        for tid in sorted(rows):
            row = rows[tid]
            st = ledger.get(tid)
            if st and st["done"]:
                skipped["done"] += 1
                continue
            if st and st["attempts"] >= pc.MAX_ATTEMPTS:
                skipped["exhausted"] += 1
                continue
            spec = specs.get(tid)
            if spec is None:
                skipped["no_spec"] += 1
                continue
            rec_path = paths.record(row.get("category") or spec.get("category"), tid)
            if not os.path.exists(rec_path):
                skipped["no_record"] += 1
                continue
            if limit is not None and len(todo) >= limit:
                break
            rec = json.load(open(rec_path))
            before = pc.before_metrics(rec, rec_path, tmp)
            if before.get("min_bytes") is None and row.get("min_bytes") is not None:
                before["min_bytes"] = row["min_bytes"]          # sweep value as last resort
            if before.get("proxy_tokens") is None and row.get("proxy_tokens") is not None:
                before["proxy_tokens"] = row["proxy_tokens"]
            exempt = pc.exempt_rules(spec, row)
            must, hints, exempt_hit = pc.live_rules(before.get("struct"), exempt)
            live = set(must) | set(hints) | set(exempt_hit)
            sweep_only = [r for r in pc.bucket_rules(row) if r not in live and r not in exempt]
            entries = pc.entries_for_row(cat, must + hints + sweep_only, row)
            attempts = (st["attempts"] if st else 0) + 1
            prev_reason = (st["last"] or {}).get("reason") if st else None
            if not prev_reason and tid in retry:
                prev_reason = retry[tid].get("reason")
            if os.path.exists(paths.sub("gen", pc.GEN_PREFIX + tid + ".tk")):
                unbanked.append(tid)                            # bank BEFORE re-prepping
            ctx = {"must": must, "hints": hints, "sweep_only": sweep_only,
                   "exempt": exempt, "exempt_hit": exempt_hit, "entries": entries,
                   "before": before, "attempts": attempts, "prev_reason": prev_reason,
                   "card": card if embed_card else None, "card_sha": card_sha,
                   "rule_texts": rule_texts, "row": row, "toke_root": paths.toke_root}
            with open(paths.sub("specs", tid + ".json"), "w") as f:
                json.dump(spec, f)
            struct = before.pop("struct", None)
            meta = {"task_id": tid, "category": spec.get("category"),
                    "task_type": spec.get("task_type"), "before": before,
                    "rules_must": must, "rules_hint": hints, "rules_sweep_only": sweep_only,
                    "exempt": exempt, "exempt_hit": exempt_hit,
                    "patterns": [e["id"] for e in entries],
                    "perf_sensitive": bool(row.get("perf_sensitive")),
                    "est_saving": pc.bucket_est_saving(row), "bucket_source": row.get("source"),
                    "violations": row.get("violations") or [], "attempts": attempts,
                    "prev_reason": prev_reason, "card_sha": card_sha,
                    "catalogue_sha": cat["sha"], "lint_before": struct.get("lint") if struct else None,
                    "prepared_at": pc.now_iso()}
            with open(paths.sub("meta", tid + ".json"), "w") as f:
                json.dump(meta, f)
            with open(paths.sub("prompts", tid + ".txt"), "w") as f:
                f.write(build_prompt(spec, rec["tk_source"], ctx))
            todo.append(tid)
    cap = max_batches * pc.BATCH_SIZE
    batched, deferred = todo[:cap], todo[cap:]
    batches = []
    for n, i in enumerate(range(0, len(batched), pc.BATCH_SIZE)):
        ids = batched[i:i + pc.BATCH_SIZE]
        with open(paths.sub("batches", f"batch_{n:03d}.json"), "w") as f:
            json.dump({"batch": n, "task_ids": ids}, f)
        batches.append({"batch": n, "n": len(ids)})
    stale = _clear_stale_batches(paths.sub("batches"), len(batches))
    manifest = {"story": pc.STORY, "wave": pc.WAVE, "ts": pc.now_iso(),
                "bucket": os.path.abspath(bucket_path), "bucket_name": bucket_name,
                "bucket_sha": pc.short(pc.sha256_file(bucket_path)),
                "bucket_rows": len(rows), "card": os.path.abspath(paths.card),
                "card_sha": card_sha, "card_embedded": bool(embed_card),
                "catalogue": os.path.abspath(paths.catalogue), "catalogue_sha": cat["sha"],
                **pc.tkc_stamp(paths.tkc, paths.toke_root),
                "prepared": len(batched), "deferred": len(deferred),
                "deferred_task_ids": deferred, "skipped": skipped,
                "batches": len(batches), "batch_size": pc.BATCH_SIZE,
                "max_batches": max_batches, "stale_batches_removed": stale,
                "unbanked_candidates": len(unbanked), "unbanked_task_ids": unbanked[:50],
                "workdir": os.path.abspath(paths.workdir)}
    with open(paths.sub("manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bucket", default=os.path.join(pc.CORPUS, "audit", "pattern_sweep.jsonl"))
    ap.add_argument("--bucket-name", default="AGENT")
    ap.add_argument("--corpus", default=pc.CORPUS)
    ap.add_argument("--workdir", default=None, help="default <corpus>/work/pattern_131")
    ap.add_argument("--ledger", default=None, help="default <corpus>/ledger/rewrite_131.jsonl")
    ap.add_argument("--catalogue", default=pc.CATALOGUE_PATH)
    ap.add_argument("--card", default=pc.CARD_PATH)
    ap.add_argument("--max-batches", type=int, default=pc.MAX_BATCHES)
    ap.add_argument("--limit", type=int, default=None, help="prep at most N tasks")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these task_ids")
    ap.add_argument("--no-embed-card", action="store_true",
                    help="omit the syntax card from prompts (workers read it once per batch)")
    args = ap.parse_args(argv)
    paths = pc.Paths(corpus=args.corpus, workdir=args.workdir, ledger=args.ledger,
                     catalogue=args.catalogue, card=args.card)
    m = prepare(paths, args.bucket, args.bucket_name, args.max_batches, args.limit,
                not args.no_embed_card, only=args.only)
    print(json.dumps({k: v for k, v in m.items() if k not in ("deferred_task_ids", "unbanked_task_ids")}))
    if m["unbanked_candidates"]:
        print(f"WARNING: {m['unbanked_candidates']} gen/pat_*.tk candidates were already on disk "
              "— run bank_pattern.py before launching (REBUILD_STATUS wave rule: bank before re-prep)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
