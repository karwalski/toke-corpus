#!/usr/bin/env python3
"""Shard driver for the v0.4 corpus regeneration run.

Subcommands:
  prepare   - build per-task prompt files + batch manifests for a shard,
              skipping task_ids already accepted in the ledger
  validate  - assemble + independently re-check worker outputs, run test
              cases where the spec has them, emit corpus records, append
              MANIFEST + ledger, queue rejects for one retry
              task_type full_program: binary run once, one line per case;
              task_type stdin_program (131.18): one run per case, input on
              stdin, whole stdout vs expected_output (validate.run_stdin_cases)
  stats     - pass-rate summary per category / task_type for a shard

Layout (all under --workdir, one workdir per shard):
  prompts/<task_id>.txt      worker prompts
  batches/batch_NNN.json     {"batch": N, "task_ids": [...]}
  gen/out_<task_id>.tk       worker outputs (written by agents)
  retry_queue.jsonl          rejected tasks awaiting one re-issue

Corpus output (--outdir, shared across shards):
  <CATEGORY>/<task_id>.json  accepted records (corpus schema v2 + regen ext)
  MANIFEST.jsonl
  ledger/<shard_name>.jsonl  {"task_id", "status", "reason", "attempts"}
"""
import argparse, hashlib, json, os, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from assemble import assemble, sanitize          # noqa: E402
from build_prompt import build                   # noqa: E402
from validate import tkc_check, signature_conforms  # noqa: E402
from validate import run_stdin_cases, STDIN_CASE_TIMEOUT  # noqa: E402  (131.18)
import idiom_judge                                   # noqa: E402
import metrics                                       # noqa: E402
import manifest_tool                                 # noqa: E402  (131.35)

TKC = "/Users/matthew.watt/tk/toke/tkc"
BATCH_SIZE = 20
CARD = open(os.path.join(HERE, "syntax_card.md")).read()
CARD_SHA = hashlib.sha256(CARD.encode()).hexdigest()[:12]


def load_ledger(outdir, shard_name):
    path = os.path.join(outdir, "ledger", shard_name + ".jsonl")
    done = {}
    if os.path.exists(path):
        for line in open(path):
            e = json.loads(line)
            done[e["task_id"]] = e
    return done


def append_ledger(outdir, shard_name, entry):
    os.makedirs(os.path.join(outdir, "ledger"), exist_ok=True)
    path = os.path.join(outdir, "ledger", shard_name + ".jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def cmd_prepare(args):
    shard_name = os.path.splitext(os.path.basename(args.shard))[0]
    done = load_ledger(args.outdir, shard_name)
    specs = [json.loads(l) for l in open(args.shard)]
    todo = [s for s in specs if done.get(s["task_id"], {}).get("status") != "accepted"]
    os.makedirs(os.path.join(args.workdir, "prompts"), exist_ok=True)
    os.makedirs(os.path.join(args.workdir, "batches"), exist_ok=True)
    os.makedirs(os.path.join(args.workdir, "gen"), exist_ok=True)
    os.makedirs(os.path.join(args.workdir, "specs"), exist_ok=True)
    card = CARD if args.embed_card else ""
    for s in todo:
        with open(os.path.join(args.workdir, "specs", s["task_id"] + ".json"), "w") as f:
            json.dump(s, f)
        with open(os.path.join(args.workdir, "prompts", s["task_id"] + ".txt"), "w") as f:
            f.write(build(s, card))
    for n, i in enumerate(range(0, len(todo), BATCH_SIZE)):
        batch = todo[i:i + BATCH_SIZE]
        with open(os.path.join(args.workdir, "batches", f"batch_{n:03d}.json"), "w") as f:
            json.dump({"batch": n, "task_ids": [s["task_id"] for s in batch]}, f)
    print(json.dumps({"shard": shard_name, "total": len(specs), "already_done": len(specs) - len(todo),
                      "prepared": len(todo), "batches": (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE,
                      "card_sha": CARD_SHA}))


def render_expected(val):
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val)


def run_test_cases(binpath, spec):
    """Run the binary once; expect one printed line per test case, in order."""
    tcs = spec.get("test_cases") or []
    if not tcs:
        return None
    try:
        # errors="replace": a generated program may emit non-UTF-8 bytes on stdout;
        # decode defensively so one bad program fails its output-match (reject) instead
        # of aborting the whole validate pass with UnicodeDecodeError.
        r = subprocess.run([binpath], capture_output=True, text=True,
                           errors="replace", timeout=15)
    except subprocess.TimeoutExpired:
        return {"ran": True, "exit": None, "match": False, "reason": "timeout"}
    got = [l for l in r.stdout.splitlines()]
    # 129.6: a str expected containing embedded newlines matches one stdout
    # line per embedded line
    want = []
    for tc in tcs:
        w = render_expected(tc.get("expected"))
        want.extend(w.split("\n"))
    if len(got) < len(want):
        return {"ran": True, "exit": r.returncode, "match": False,
                "reason": f"expected {len(want)} lines, got {len(got)}", "stdout": r.stdout[:500]}
    match = True
    for g, w in zip(got, want):
        if g.strip() == w.strip():
            continue
        try:  # float tolerance
            if abs(float(g) - float(w)) < 1e-6:
                continue
        except ValueError:
            pass
        match = False
        break
    # 129.6 tightened gates: non-zero exit and extra stdout lines now reject
    if match and r.returncode != 0:
        match = False
        reason = f"exit code {r.returncode}"
    elif match and len(got) > len(want):
        match = False
        reason = f"{len(got) - len(want)} extra stdout lines"
    else:
        reason = None if match else "output mismatch"
    return {"ran": True, "exit": r.returncode, "match": match,
            "reason": reason,
            "stdout": r.stdout[:500] if not match else None}


def validate_one(spec, raw_src, workdir):
    """Full acceptance pipeline for one worker output. Returns (record, ok, reason)."""
    record, ok, reason, _gates = validate_one_gates(spec, raw_src, workdir)
    return record, ok, reason


def validate_one_gates(spec, raw_src, workdir, lint_gate=False, case_timeout=None):
    """validate_one plus a per-gate verdict dict (131.18). Gate keys:
    compile, signature, build, tests, idiom, structure, pattern, lint — each
    True/False/None (None = not applicable). `pattern` (131.10) is a hard gate
    on every path: any 131.9 pattern-rule error/warning rejects (hints pass),
    net of the spec's style mandate. `lint_gate=True` additionally rejects on
    any other lint warning > 0 (library ingest; NOT applied by validate_one). task_type stdin_program: source is
    taken verbatim (no assemble/sanitize), no target-signature check, one
    execution per test case with stdin fed (validate.run_stdin_cases)."""
    ttype = spec.get("task_type", "full_program")
    is_stdin = ttype == "stdin_program"
    src = raw_src if is_stdin else assemble(spec, raw_src)
    gates = {"compile": None, "signature": None, "build": None, "tests": None,
             "idiom": None, "structure": None, "pattern": None, "lint": None}
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f]", src.replace("\x1b", "")) or "\x00" in src:
        gates["compile"] = False
        return None, False, "control bytes in source", gates
    with tempfile.NamedTemporaryFile("w", suffix=".tk", dir=workdir, delete=False) as f:
        f.write(src)
        tkpath = f.name
    try:
        rc, codes, diag = tkc_check(tkpath)
        gates["compile"] = rc == 0
        if is_stdin:
            sig_ok, sig_detail = True, "stdin_program: no target signature"
        else:
            sig_ok, sig_detail = signature_conforms(spec, src)
            gates["signature"] = sig_ok
        runtime = None
        if rc == 0 and ttype in ("full_program", "stdin_program") and spec.get("test_cases"):
            binpath = tkpath + ".bin"
            # stdin_program specs may carry build_flags (library: --allow-all for
            # fs/env/net capabilities); the full_program build line is unchanged
            flags = list(spec.get("build_flags") or []) if is_stdin else []
            b = subprocess.run([TKC, tkpath, "-o", binpath] + flags, capture_output=True, text=True, timeout=90)
            gates["build"] = b.returncode == 0
            if b.returncode == 0:
                if is_stdin:
                    runtime = run_stdin_cases(binpath, spec, workdir,
                                              case_timeout or STDIN_CASE_TIMEOUT)
                else:
                    runtime = run_test_cases(binpath, spec)
                gates["tests"] = bool(runtime.get("match", True))
                if os.path.exists(binpath):
                    os.unlink(binpath)
            else:
                runtime = {"ran": False, "reason": "build failed"}
                gates["tests"] = False
        # 129.6 rubric gates (quality_rubric.md): idiom floor + structural hard limits
        struct = metrics.analyse(tkpath, src) if rc == 0 else None
        # 131.10: the idiom judge scores from the --lint --diag-json run
        # metrics.analyse already made (no second tkc call); the regex is kept
        # only for hand-rolled-parser. Stub-prefix diagnostics are dropped in
        # metrics.lint (the m=harness;i=io:std.io; stubs import unconditionally).
        idiom_score, idiom_notes = idiom_judge.score(src, struct["lint"] if struct else None)
        gates["idiom"] = idiom_score >= idiom_judge.IDIOM_FLOOR
        struct_fail = None
        pattern_fail = []
        exempt = idiom_judge.mandate_exempt_rules(idiom_judge.style_mandate(spec))
        lint_exempt = []
        if struct:
            if struct["max_depth"] > 4:
                struct_fail = f"nesting depth {struct['max_depth']} > 4"
            elif struct["max_func_bytes"] > 600:
                struct_fail = f"function {struct['max_func_bytes']} bytes > 600"
            gates["structure"] = not struct_fail
            gates["lint"] = struct.get("lint_warnings", 0) == 0
            # 131.10 hard gate: any pattern-rule error/warning fails (hints
            # pass), net of the spec's style mandate (quality_rubric.md
            # "Exemptions"); discarded-value-result is never exempt.
            pattern_fail = idiom_judge.hard_gate(struct["lint"], exempt)
            gates["pattern"] = not pattern_fail
            hit_rules = {d["rule"] for d in idiom_judge.pattern_hits(struct["lint"])}
            lint_exempt = [r for r in exempt if r in hit_rules]
        lint_fail = lint_gate and struct is not None and struct.get("lint_warnings", 0) > 0
        ok = (rc == 0 and sig_ok and (runtime is None or runtime.get("match", True))
              and idiom_score >= idiom_judge.IDIOM_FLOOR and not struct_fail
              and not pattern_fail and not lint_fail)
        reason = None
        if rc != 0:
            reason = "compile: " + ",".join(codes[:3])
        elif not sig_ok:
            reason = "signature: " + sig_detail
        elif runtime and not runtime.get("match", True):
            reason = "runtime: " + str(runtime.get("reason"))
        elif idiom_score < idiom_judge.IDIOM_FLOOR:
            reason = f"idiom: {idiom_score:.2f} < {idiom_judge.IDIOM_FLOOR} ({'; '.join(idiom_notes)})"
        elif struct_fail:
            reason = "structure: " + struct_fail
        elif pattern_fail:
            reason = "pattern: " + idiom_judge.violation_summary(pattern_fail)
        elif lint_fail:
            rules = sorted({d.get("rule") for d in struct.get("lint", []) if d.get("severity") == "warning"})
            reason = f"lint: {struct['lint_warnings']} warnings ({','.join(r for r in rules if r)})"
        record = {
            "id": "P3-" + spec["task_id"],
            "version": 2,
            "phase": "C",
            "task_id": spec["task_id"],
            "tk_source": src,
            # 131.10: proxy8k count of the masked --min source (was always None)
            "tk_tokens": struct.get("proxy_tokens") if struct else None,
            "attempts": spec.get("_attempts", 1),
            "model": "claude-fable-5",
            "validation": {"compiler_exit_code": rc, "error_codes": codes},
            "differential": {"languages_agreed": [], "majority_output": ""},
            "judge": {"accepted": ok, "score": round(idiom_score, 2)},
            "regen": {
                "syntax_version": "v0.4-2.8.0",
                "card_sha": CARD_SHA,
                "task_type": spec.get("task_type", "full_program"),
                "category": spec.get("category"),
                "difficulty": spec.get("difficulty"),
                "signature_ok": sig_ok,
                "runtime": runtime,
                "source_sha256": hashlib.sha256(src.encode()).hexdigest(),
                "min_bytes": struct.get("min_bytes") if struct else None,
                "max_depth": struct.get("max_depth") if struct else None,
                # 131.10 pattern gate + proxy budget (soft flag; 131.13 decides
                # whether over_budget becomes hard). Budget: regen/freeze/proxy_budget_v04.json
                "proxy_tokens": struct.get("proxy_tokens") if struct else None,
                "lint_pattern_violations": struct.get("lint_pattern_violations") if struct else None,
                "lint_exempt": lint_exempt,
                "over_budget": metrics.over_budget(spec.get("category"), ttype,
                                                   struct.get("proxy_tokens") if struct else None),
            },
        }
        if lint_gate:
            record["regen"]["lint_warnings"] = struct.get("lint_warnings") if struct else None
        return record, ok, reason, gates
    finally:
        if os.path.exists(tkpath):
            os.unlink(tkpath)


def cmd_validate(args):
    shard_name = os.path.splitext(os.path.basename(args.shard))[0]
    done = load_ledger(args.outdir, shard_name)
    gen_dir = os.path.join(args.workdir, "gen")
    spec_dir = os.path.join(args.workdir, "specs")
    accepted = rejected = skipped = 0
    retry_path = os.path.join(args.workdir, "retry_queue.jsonl")
    manifest = manifest_tool.Manifest(os.path.join(args.outdir, "MANIFEST.jsonl"))  # 131.35
    for fn in sorted(os.listdir(gen_dir)):
        m = re.match(r"out_(.+)\.tk$", fn)
        if not m:
            continue
        task_id = m.group(1)
        if done.get(task_id, {}).get("status") == "accepted":
            skipped += 1
            continue
        spec_path = os.path.join(spec_dir, task_id + ".json")
        if not os.path.exists(spec_path):
            continue
        spec = json.load(open(spec_path))
        raw = open(os.path.join(gen_dir, fn)).read()
        record, ok, reason = validate_one(spec, raw, args.workdir)
        if ok:
            cat_dir = os.path.join(args.outdir, spec.get("category", "MISC"))
            os.makedirs(cat_dir, exist_ok=True)
            rec_path = os.path.join(cat_dir, task_id + ".json")
            with open(rec_path, "w") as f:
                json.dump(record, f)
            # 131.35: MANIFEST line is stamped from the file just written
            # (sha256 = file bytes, source_sha256 = tk_source). stamp() replaces
            # any existing line for task_id, so the 131.16 `validate --replace`
            # mode needs no extra manifest work — keep the record write and
            # this stamp adjacent when adding it.
            manifest.stamp(task_id, rec_path,
                           extra={"id": record["id"], "category": spec.get("category"),
                                  "task_type": spec.get("task_type"),
                                  "difficulty": spec.get("difficulty"), "shard": shard_name})
            append_ledger(args.outdir, shard_name,
                          {"task_id": task_id, "status": "accepted", "reason": None,
                           "attempts": spec.get("_attempts", 1), "ts": int(time.time())})
            accepted += 1
        else:
            prior = done.get(task_id, {})
            if prior.get("status") == "rejected":
                append_ledger(args.outdir, shard_name,
                              {"task_id": task_id, "status": "failed_final", "reason": reason,
                               "attempts": spec.get("_attempts", 1) + 1, "ts": int(time.time())})
            else:
                append_ledger(args.outdir, shard_name,
                              {"task_id": task_id, "status": "rejected", "reason": reason,
                               "attempts": spec.get("_attempts", 1), "ts": int(time.time())})
                spec["_attempts"] = spec.get("_attempts", 1) + 1
                spec["_retry_reason"] = reason
                with open(retry_path, "a") as f:
                    f.write(json.dumps(spec) + "\n")
            rejected += 1
        # processed outputs are archived so a re-run doesn't double-count
        os.rename(os.path.join(gen_dir, fn), os.path.join(gen_dir, fn + ".done"))
    print(json.dumps({"shard": shard_name, "accepted": accepted, "rejected": rejected,
                      "skipped_already_accepted": skipped}))


def cmd_stats(args):
    shard_name = os.path.splitext(os.path.basename(args.shard))[0]
    from collections import Counter, defaultdict
    ledger = load_ledger(args.outdir, shard_name)
    by_status = Counter(e["status"] for e in ledger.values())
    by_reason = Counter((e.get("reason") or "").split(":")[0] for e in ledger.values()
                        if e["status"] in ("rejected", "failed_final"))
    cat_pass = defaultdict(lambda: [0, 0])
    man_path = os.path.join(args.outdir, "MANIFEST.jsonl")
    if os.path.exists(man_path):
        for line in open(man_path):
            e = json.loads(line)
            if e.get("shard") == shard_name:
                cat_pass[e["category"]][0] += 1
    for e in ledger.values():
        if e["status"] in ("rejected", "failed_final"):
            pass  # category not in ledger; reason buckets suffice
    total = len(ledger)
    acc = by_status.get("accepted", 0)
    print(json.dumps({"shard": shard_name, "ledger_entries": total,
                      "status": dict(by_status),
                      "accept_rate": round(acc / total, 4) if total else None,
                      "reject_reasons": dict(by_reason),
                      "accepted_by_category": {k: v[0] for k, v in sorted(cat_pass.items())}},
                     indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "validate", "stats"])
    ap.add_argument("--shard", required=True, help="shard jsonl of task specs")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--embed-card", action="store_true",
                    help="embed the syntax card in every prompt (default: task-only; workers read the card once per batch)")
    args = ap.parse_args()
    {"prepare": cmd_prepare, "validate": cmd_validate, "stats": cmd_stats}[args.cmd](args)


if __name__ == "__main__":
    main()
