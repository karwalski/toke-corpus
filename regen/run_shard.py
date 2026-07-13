#!/usr/bin/env python3
"""Shard driver for the v0.4 corpus regeneration run.

Subcommands:
  prepare   - build per-task prompt files + batch manifests for a shard,
              skipping task_ids already accepted in the ledger
  validate  - assemble + independently re-check worker outputs, run test
              cases where the spec has them, emit corpus records, append
              MANIFEST + ledger, queue rejects for one retry
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
    for s in todo:
        with open(os.path.join(args.workdir, "specs", s["task_id"] + ".json"), "w") as f:
            json.dump(s, f)
        with open(os.path.join(args.workdir, "prompts", s["task_id"] + ".txt"), "w") as f:
            f.write(build(s, CARD))
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
        r = subprocess.run([binpath], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return {"ran": True, "exit": None, "match": False, "reason": "timeout"}
    got = [l for l in r.stdout.splitlines()]
    want = [render_expected(tc.get("expected")) for tc in tcs]
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
    return {"ran": True, "exit": r.returncode, "match": match,
            "reason": None if match else "output mismatch",
            "stdout": r.stdout[:500] if not match else None}


def validate_one(spec, raw_src, workdir):
    """Full acceptance pipeline for one worker output. Returns (record, ok, reason)."""
    src = assemble(spec, raw_src)
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f]", src.replace("\x1b", "")) or "\x00" in src:
        return None, False, "control bytes in source"
    with tempfile.NamedTemporaryFile("w", suffix=".tk", dir=workdir, delete=False) as f:
        f.write(src)
        tkpath = f.name
    try:
        rc, codes, diag = tkc_check(tkpath)
        sig_ok, sig_detail = signature_conforms(spec, src)
        runtime = None
        if rc == 0 and spec.get("task_type") == "full_program" and spec.get("test_cases"):
            binpath = tkpath + ".bin"
            b = subprocess.run([TKC, tkpath, "-o", binpath], capture_output=True, text=True, timeout=90)
            if b.returncode == 0:
                runtime = run_test_cases(binpath, spec)
                if os.path.exists(binpath):
                    os.unlink(binpath)
            else:
                runtime = {"ran": False, "reason": "build failed"}
        ok = rc == 0 and sig_ok and (runtime is None or runtime.get("match", True))
        reason = None
        if rc != 0:
            reason = "compile: " + ",".join(codes[:3])
        elif not sig_ok:
            reason = "signature: " + sig_detail
        elif runtime and not runtime.get("match", True):
            reason = "runtime: " + str(runtime.get("reason"))
        record = {
            "id": "P3-" + spec["task_id"],
            "version": 2,
            "phase": "C",
            "task_id": spec["task_id"],
            "tk_source": src,
            "tk_tokens": None,
            "attempts": spec.get("_attempts", 1),
            "model": "claude-fable-5",
            "validation": {"compiler_exit_code": rc, "error_codes": codes},
            "differential": {"languages_agreed": [], "majority_output": ""},
            "judge": {"accepted": ok, "score": 1.0 if ok else 0.0},
            "regen": {
                "syntax_version": "v0.4-2.8.0",
                "card_sha": CARD_SHA,
                "task_type": spec.get("task_type", "full_program"),
                "category": spec.get("category"),
                "difficulty": spec.get("difficulty"),
                "signature_ok": sig_ok,
                "runtime": runtime,
                "source_sha256": hashlib.sha256(src.encode()).hexdigest(),
            },
        }
        return record, ok, reason
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
            with open(os.path.join(cat_dir, task_id + ".json"), "w") as f:
                json.dump(record, f)
            with open(os.path.join(args.outdir, "MANIFEST.jsonl"), "a") as f:
                f.write(json.dumps({"id": record["id"], "task_id": task_id,
                                    "category": spec.get("category"),
                                    "task_type": spec.get("task_type"),
                                    "difficulty": spec.get("difficulty"),
                                    "sha256": record["regen"]["source_sha256"],
                                    "shard": shard_name}) + "\n")
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
    args = ap.parse_args()
    {"prepare": cmd_prepare, "validate": cmd_validate, "stats": cmd_stats}[args.cmd](args)


if __name__ == "__main__":
    main()
