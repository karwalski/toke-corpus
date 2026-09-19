#!/usr/bin/env python3
"""Epic 131.70 — prepare the 131.17 library pattern-rewrite wave.

Input set: the rows of audit/pattern_sweep.jsonl with bucket == AGENT AND
source == "library" (1,272 of the 2,469 AGENT rows; the other 1,197 are
`source: regen` and belong to the corpus wave 131.15 — filter on the field,
a plain bucket merge sweeps the two together). Specs come from
shards/library_131.jsonl; the program under rewrite is
    ~/tk/toke-test-programs/results/solutions/<cat>/<id>/solution.tk
and the worker writes its candidate next to it as solution.131.tk.

Output, under corpus/regen_v04/work/library_rewrite_131/:
  specs/<tid>.json        the manifest-derived spec (test_cases = the ORACLE)
  meta/<tid>.json         before-metrics, flagged rules, exemptions, paths,
                          attempts, card/catalogue/tkc shas, carve-out flags
  prompts/<tid>.txt       worker prompt (task, current source, ONLY the
                          catalogue entries for the flagged rules, the
                          manifest test lock, the syntax card)
  batches/batch_NNN.json  {"batch": N, "task_ids": [...]}   (20 per batch)
  manifest.json           shas, counts, batch list, carve-outs, exclusions

Excluded (127.64, a compiler regression handled separately): L-GAZ-117
(deterministic segfault) and L-DEV-123 (E4031) on the pinned tkc.

Carved out (131.32): the four raw-NUL programs keep their prompt but their
`before` size baseline is marked corrupt (`tkc --min` truncates at the NUL),
so bank_library's <=-before gates are n/a for them. Their prompt carries the
escape-the-NUL mandate.

THE MANIFEST IS THE ORACLE. Nothing here (or in bank_library.py) compares a
candidate against the original program's output — see library_common.py's
header for why that matters (131.69).

Idempotent: tasks already rewritten/unchanged in the wave ledger are skipped,
as are tasks that exhausted their retries. Stale higher-numbered batch files
from a previous prep are removed (bank BEFORE re-prepping). Nothing here
writes to the corpus or to toke-test-programs.
"""
import argparse, glob, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import library_common as lc                       # noqa: E402
import idiom_judge                                # noqa: E402

INSTRUCTION = (
    "REWRITE RULES: behaviour is locked by the MANIFEST, not by the current program — "
    "every manifest case must still print exactly its `expected_output` and exit 0 "
    "(if the current program is wrong about anything the manifest does not pin, the "
    "manifest wins); keep the module a complete stdin_program with f=main():i64 and "
    "the same build flags; least tokens — every byte of the --min form counts; no "
    "pattern-lint violations (tkc --lint must report 0 errors AND 0 warnings of any "
    "kind; fixing the hints too is expected unless the task mandate forbids it); the "
    "--min byte count and the proxy-token count must not grow; nesting depth <= 4 and "
    "no function over 600 bytes. Write the result to solution.131.tk beside the "
    "original — never edit solution.tk. At most 3 attempts.")

NUL_INSTRUCTION = (
    "RAW NUL BYTES (131.32) — read this before anything else: this program contains "
    "raw 0x00 bytes inside string literals. Replace every one with the `\\0` escape "
    "(or a sentinel the program already understands); the rewritten source must be "
    "printable ASCII + escapes. Because `tkc --min` terminates at the NUL, this "
    "program has NO usable size baseline: the --min byte and proxy-token "
    "not-worse-than-before gates do not apply to it — write the clearest correct "
    "form and keep it tight on its own merits.")


def build_prompt(spec, src, ctx):
    """One worker prompt. ctx keys: must, hints, sweep_only, exempt, exempt_hit,
    entries, before, attempts, prev_reason, card, card_sha, rule_texts, row,
    toke_root, nul_carve_out, candidate_path."""
    row = ctx.get("row") or {}
    rule_texts = ctx.get("rule_texts") or {}
    libd = spec.get("library") or {}
    lines = [f"LIBRARY PATTERN REWRITE ({spec.get('category')}, {libd.get('category')}/"
             f"{libd.get('id')}): the toke 2.8.0 program below passes its manifest "
             "tests but uses verbose / anti-pattern forms flagged by the Epic 131 "
             "pattern sweep. Rewrite it in the canonical forms.",
             "",
             f"Task: {spec.get('description', '')}",
             ""]
    if libd.get("title"):
        lines.append(f"Library title: {libd['title']}  (manifest {libd.get('manifest')})")
    if libd.get("input_format"):
        lines.append(f"Input format: {libd['input_format']}")
    if libd.get("output_format"):
        lines.append(f"Output format: {libd['output_format']}")
    lines += ["", "Module shape: stdin_program — a complete program reading stdin; keep "
                  f"main() and the build flags {json.dumps(spec.get('build_flags') or [])}.",
              "", "THE ORACLE IS THE MANIFEST: the gate runs your program once per manifest "
                  "case and compares the whole stdout to the case's expected_output (exit 0). "
                  "It never compares your output against the current program's output, so a "
                  "rewrite that happens to correct a latent defect is not penalised — but you "
                  "must not change anything the manifest pins."]
    if ctx.get("nul_carve_out"):
        lines += ["", NUL_INSTRUCTION]
    mandate = idiom_judge.style_mandate(spec)
    if mandate:
        lines += ["", f"NOTE the task description mandates: \"{mandate}\" — honour it. "
                      f"Rules exempted by that mandate: {', '.join(ctx['exempt']) or 'none'} "
                      "(a violation of an exempted rule does not fail the gate; still prefer "
                      "the canonical form where the mandate allows)."]
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
        lines.append(f"  - {r} [sweep-flagged; not reported by tkc on the current source "
                     f"— check by eye]: {txt}")
    b = ctx.get("before") or {}
    if b.get("lint_warnings"):
        lines.append(f"  non-pattern lint warnings on the current source: {b['lint_warnings']} "
                     "(total must be 0 after the rewrite)")
    if b.get("max_depth") is not None:
        lines.append(f"  current structure: depth {b['max_depth']} (limit 4), largest "
                     f"function {b.get('max_func_bytes')} bytes (limit 600), idiom "
                     f"{b.get('idiom')} (floor {idiom_judge.IDIOM_FLOOR})")
    if lc.bucket_est_saving(row) is not None:
        lines.append(f"  sweep estimate: saving ~{lc.bucket_est_saving(row)} proxy tokens")
    if ctx.get("nul_carve_out"):
        lines.append("  current size: NOT MEASURABLE (131.32 raw NUL truncates --min) — "
                     "the size gates are n/a for this program.")
    else:
        lines.append(f"  current size: --min {b.get('min_bytes')} bytes, proxy tokens "
                     f"{b.get('proxy_tokens')} — the rewrite must be <= both.")
    if ctx["entries"]:
        lines += ["", "Canonical forms (pattern catalogue, only the entries for the flagged "
                      "rules):", ""]
        for e in ctx["entries"]:
            lines += [lc.render_entry(e, ctx.get("toke_root", lc.TOKE_ROOT)), ""]
    lines += ["Expected output (manifest test lock — the oracle):"]
    lines += lc.expected_output_lines(spec)
    lines += ["", INSTRUCTION,
              "", f"Write your rewrite to: {ctx.get('candidate_path')}",
              "", "Current source:", src.rstrip("\n"), ""]
    if ctx.get("card"):
        lines += ["", "---", "SYNTAX CARD (the only authoritative toke 2.8.0 spec; "
                  f"sha {ctx.get('card_sha')}):", "", ctx["card"]]
    return "\n".join(lines) + "\n"


def read_retry_queue(path):
    """{task_id: last entry} of bank_library's retry_queue.jsonl ({} if absent)."""
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if not line:
            continue
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


def prepare(paths, max_batches=lc.MAX_BATCHES, limit=None, embed_card=True,
            only=None, category=None, specs=None, rows=None, catalogue=None,
            rule_texts=None):
    """Prep the wave. Returns the manifest dict (also written to
    workdir/manifest.json)."""
    if rows is None or specs is None:
        rows, specs, excluded = lc.wave_rows(paths.sweep, paths.shard)
    else:
        excluded = {t: lc.EXCLUDED[t] for t in sorted(rows) if t in lc.EXCLUDED}
        for t in excluded:
            rows.pop(t, None)
    if only:
        keep = set(only)
        rows = {t: r for t, r in rows.items() if t in keep}
    if category:
        rows = {t: r for t, r in rows.items()
                if (specs.get(t) or {}).get("category") == category
                or ((specs.get(t) or {}).get("library") or {}).get("category") == category}
    cat = catalogue or lc.load_catalogue(paths.catalogue)
    rule_texts = rule_texts if rule_texts is not None else lc.load_rule_texts()
    ledger = lc.read_ledger(paths.ledger)
    card = open(paths.card, encoding="utf-8").read()
    card_sha = lc.short(lc.sha256_text(card))
    tkc = lc.tkc_stamp(paths.tkc, paths.toke_root)
    for d in ("specs", "meta", "prompts", "batches"):
        os.makedirs(paths.sub(d), exist_ok=True)
    retry = read_retry_queue(paths.sub("retry_queue.jsonl"))
    skipped = {"done": 0, "exhausted": 0, "no_spec": 0, "no_solution": 0}
    todo, unbanked, carved = [], [], []
    with tempfile.TemporaryDirectory(prefix="library_prep_") as tmp:
        for tid in sorted(rows):
            row = rows[tid]
            st = ledger.get(tid)
            if st and st["done"]:
                skipped["done"] += 1
                continue
            if st and st["attempts"] >= lc.MAX_ATTEMPTS:
                skipped["exhausted"] += 1
                continue
            spec = specs.get(tid)
            if spec is None:
                skipped["no_spec"] += 1
                continue
            libd = spec.get("library") or {}
            # paths.original() first so --lib-root really redirects the tree
            # (the spec's `source_program` is an absolute path into the real one)
            sol = paths.original(libd.get("category"), libd.get("id"))
            if not os.path.exists(sol):
                sol = spec.get("source_program") or sol
            if not os.path.exists(sol):
                skipped["no_solution"] += 1
                continue
            if limit is not None and len(todo) >= limit:
                break
            src = open(sol, encoding="utf-8", errors="replace").read()
            nul = lc.is_nul_carve_out(tid, row)
            if nul:
                carved.append(tid)
            before = lc.before_metrics(src, sol, tmp, tid, nul_carve_out=nul)
            exempt = lc.exempt_rules(spec, row)
            must, hints, exempt_hit = lc.live_rules(before.get("struct"), exempt)
            live = set(must) | set(hints) | set(exempt_hit)
            sweep_only = [r for r in lc.bucket_rules(row)
                          if r not in live and r not in exempt]
            entries = lc.entries_for_row(cat, must + hints + sweep_only, row)
            attempts = (st["attempts"] if st else 0) + 1
            prev_reason = (st["last"] or {}).get("reason") if st else None
            if not prev_reason and tid in retry:
                prev_reason = retry[tid].get("reason")
            cand_path = paths.candidate(libd.get("category"), libd.get("id"))
            if os.path.exists(cand_path):
                unbanked.append(tid)                  # bank BEFORE re-prepping
            ctx = {"must": must, "hints": hints, "sweep_only": sweep_only,
                   "exempt": exempt, "exempt_hit": exempt_hit, "entries": entries,
                   "before": before, "attempts": attempts, "prev_reason": prev_reason,
                   "card": card if embed_card else None, "card_sha": card_sha,
                   "rule_texts": rule_texts, "row": row, "toke_root": paths.toke_root,
                   "nul_carve_out": nul, "candidate_path": cand_path}
            with open(paths.sub("specs", tid + ".json"), "w") as f:
                json.dump(spec, f)
            struct = before.pop("struct", None)
            meta = {"task_id": tid, "category": spec.get("category"),
                    "task_type": spec.get("task_type"), "story": lc.STORY,
                    "wave": lc.WAVE, "library": libd,
                    "solution_path": sol, "candidate_path": cand_path,
                    "archive_path": paths.archive(libd.get("category"), libd.get("id")),
                    "before": before, "rules_must": must, "rules_hint": hints,
                    "rules_sweep_only": sweep_only, "exempt": exempt,
                    "exempt_hit": exempt_hit, "patterns": [e["id"] for e in entries],
                    "est_saving": lc.bucket_est_saving(row),
                    "violations": row.get("violations") or [],
                    "nul_carve_out": nul, "attempts": attempts,
                    "prev_reason": prev_reason, "card_sha": card_sha,
                    "catalogue_sha": cat["sha"],
                    "lint_before": struct.get("lint") if struct else None,
                    "tkc_sha": tkc["tkc_sha"], "tkc_bin_sha": tkc["tkc_bin_sha"],
                    "prepared_at": lc.now_iso()}
            with open(paths.sub("meta", tid + ".json"), "w") as f:
                json.dump(meta, f)
            with open(paths.sub("prompts", tid + ".txt"), "w") as f:
                f.write(build_prompt(spec, src, ctx))
            todo.append(tid)
    cap = max_batches * lc.BATCH_SIZE
    batched, deferred = todo[:cap], todo[cap:]
    batches = []
    for n, i in enumerate(range(0, len(batched), lc.BATCH_SIZE)):
        ids = batched[i:i + lc.BATCH_SIZE]
        with open(paths.sub("batches", f"batch_{n:03d}.json"), "w") as f:
            json.dump({"batch": n, "task_ids": ids}, f)
        batches.append({"batch": n, "n": len(ids)})
    stale = _clear_stale_batches(paths.sub("batches"), len(batches))
    manifest = {"story": lc.STORY, "tooling_story": lc.TOOLING_STORY, "wave": lc.WAVE,
                "ts": lc.now_iso(), "sweep": os.path.abspath(paths.sweep),
                "sweep_filter": {"bucket": "AGENT", "source": "library"},
                "sweep_rows": len(rows) + len(excluded),
                "shard": os.path.abspath(paths.shard),
                "card": os.path.abspath(paths.card), "card_sha": card_sha,
                "card_embedded": bool(embed_card),
                "catalogue": os.path.abspath(paths.catalogue),
                "catalogue_sha": cat["sha"], **tkc,
                "prepared": len(batched), "deferred": len(deferred),
                "deferred_task_ids": deferred, "skipped": skipped,
                "excluded_127_64": excluded, "nul_carve_out": carved,
                "batches": len(batches), "batch_size": lc.BATCH_SIZE,
                "max_batches": max_batches, "stale_batches_removed": stale,
                "unbanked_candidates": len(unbanked), "unbanked_task_ids": unbanked[:50],
                "lib_root": os.path.abspath(paths.lib_root),
                "workdir": os.path.abspath(paths.workdir)}
    with open(paths.sub("manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=lc.CORPUS)
    ap.add_argument("--lib-root", default=lc.LIB_ROOT)
    ap.add_argument("--sweep", default=None, help="default <corpus>/audit/pattern_sweep.jsonl")
    ap.add_argument("--shard", default=None, help="default <corpus>/shards/library_131.jsonl")
    ap.add_argument("--workdir", default=None, help="default <corpus>/work/library_rewrite_131")
    ap.add_argument("--ledger", default=None,
                    help="default <corpus>/ledger/rewrite_131_library.jsonl")
    ap.add_argument("--catalogue", default=lc.CATALOGUE_PATH)
    ap.add_argument("--card", default=lc.CARD_PATH)
    ap.add_argument("--max-batches", type=int, default=lc.MAX_BATCHES)
    ap.add_argument("--limit", type=int, default=None, help="prep at most N tasks")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these task_ids")
    ap.add_argument("--category", default=None, help="restrict to one category")
    ap.add_argument("--no-embed-card", action="store_true",
                    help="omit the syntax card from prompts")
    args = ap.parse_args(argv)
    with lc.pin_tkc(sys.modules[__name__]) as pinned:
        paths = lc.LibraryPaths(corpus=args.corpus, lib_root=args.lib_root,
                                workdir=args.workdir, ledger=args.ledger,
                                shard=args.shard, sweep=args.sweep,
                                catalogue=args.catalogue, card=args.card)
        print(f"library_prep: tkc {pinned.version} sha256 {pinned.sha256[:12]} "
              f"(pinned copy {pinned.path})", file=sys.stderr)
        m = prepare(paths, args.max_batches, args.limit, not args.no_embed_card,
                    only=args.only, category=args.category)
    print(json.dumps({k: v for k, v in m.items()
                      if k not in ("deferred_task_ids", "unbanked_task_ids")}))
    if m["unbanked_candidates"]:
        print(f"WARNING: {m['unbanked_candidates']} solution.131.tk candidates were already "
              "on disk — run bank_library.py before launching (bank before re-prep)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
