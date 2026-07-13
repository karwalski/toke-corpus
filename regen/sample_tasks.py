#!/usr/bin/env python3
"""Stratified sampler over task_specs_v2.jsonl for corpus regeneration.

Samples evenly across the 14 categories and 3 difficulty tiers, assigns a
task_type to each sampled spec, and splits the result into N worker shards.

Task types (weighted):
  full_program    - write a complete toke program for the task
  single_function - write one function given helper signatures (domain_context;
                    A-* categories have none and run contextless)
  migrate_fix     - a legacy phase2 record that fails tkc 2.8.0, presented with
                    its current diagnostics; the worker migrates it to v0.4
"""
import argparse, json, os, random, re, subprocess, sys
from collections import defaultdict

SPECS = "/Users/matthew.watt/tk/toke-corpus/data/task_specs_v2.jsonl"
HOLDOUT = "/Users/matthew.watt/tk/toke-corpus/data/holdout_task_ids.txt"
PHASE2 = "/Users/matthew.watt/tk/toke-corpus/corpus/phase2_deduplicated"
TKC = "/Users/matthew.watt/tk/toke/tkc"

TASK_TYPE_WEIGHTS = [("full_program", 0.55), ("single_function", 0.35), ("migrate_fix", 0.10)]

# spec category -> phase2 directory to mine migrate_fix sources from
MIGRATE_DIRS = {
    "A-ARR": "A-ARR", "A-CND": "A-CND", "A-ERR": "A-ERR", "A-MTH": "A-MTH",
    "A-SRT": "A-SRT", "A-STR": "A-STR",
    "D-CLI": "EC2-D-CLI", "D-NET": "EC2-D-NET", "D-TST": "EC2-D-TST",
    "D-CFG": "EC2-D-CFG", "D-FIO": "EC2-D-FIO", "D-CRY": "EC2-D-CRY",
    "D-DAT": "EC2-D-DAT", "D-WEB": "EC2-D-WEB",
}


def modernize_type(t: str) -> str:
    """Convert old spec type notation to current syntax."""
    t = t.strip()
    t = t.replace("$str", "str").replace("$void", "void")
    # @(T) single-type array -> @T ; @(K:V) map stays
    m = re.fullmatch(r"@\(([a-z0-9]+)\)", t)
    if m:
        t = "@" + m.group(1)
    return t


def modernize_signature_hint(ctx: str) -> str:
    """domain_context holds helper signatures in old notation; modernize them."""
    if not ctx:
        return ""
    out = ctx.replace("$str", "str")
    out = re.sub(r"@\(([a-z0-9]+)\)", r"@\1", out)
    return out


def modernize_description(desc: str) -> str:
    """Task descriptions embed legacy signatures ($str, @(u64), MixedCase error
    type names). Modernize the mechanical parts; the syntax card instructs the
    worker to normalize anything that slips through (e.g. uppercase names)."""
    out = desc.replace("$str", "str")
    out = re.sub(r"@\(([a-z0-9]+)\)", r"@\1", out)
    # legacy MixedCase error-union names like !MathErr -> !$matherr
    out = re.sub(r"!([A-Z][A-Za-z0-9]*)", lambda m: "!$" + m.group(1).lower(), out)
    return out


def pick_task_type(rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for name, w in TASK_TYPE_WEIGHTS:
        acc += w
        if r < acc:
            return name
    return "full_program"


def load_specs():
    with open(SPECS) as f:
        for line in f:
            yield json.loads(line)


def load_holdout():
    ids = set()
    if os.path.exists(HOLDOUT):
        for line in open(HOLDOUT):
            line = line.strip()
            if line and not line.startswith("#"):
                ids.add(line)
    return ids


def mine_migrate_tasks(category, count, rng, used_ids):
    """Find phase2 records in this category that fail tkc 2.8.0 — they become
    migrate_fix tasks (legacy source + current diagnostics)."""
    d = os.path.join(PHASE2, MIGRATE_DIRS.get(category, category))
    if not os.path.isdir(d):
        return []
    files = os.listdir(d)
    rng.shuffle(files)
    out = []
    probe = f"/tmp/_mig_probe_{os.getpid()}.tk"
    for fn in files:
        if len(out) >= count:
            break
        try:
            rec = json.load(open(os.path.join(d, fn)))
        except Exception:
            continue
        src = rec.get("tk_source", "")
        rid = rec.get("id", fn)
        if not src or len(src) > 800 or rid in used_ids:
            continue
        with open(probe, "w") as f:
            f.write(src)
        try:
            p = subprocess.run([TKC, "--check", probe], capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            continue
        if p.returncode == 0:
            continue  # still compiles; not a migration candidate
        diags = "\n".join((p.stderr or p.stdout).strip().splitlines()[:3])
        used_ids.add(rid)
        out.append({
            "task_id": "MIG-" + rid,
            "category": category,
            "difficulty": 2,
            "task_type": "migrate_fix",
            "legacy_source": src,
            "legacy_diagnostics": diags,
            "description": "migrate " + rid,
        })
    if os.path.exists(probe):
        os.unlink(probe)
    return out


def stratified_sample(n_total: int, seed: int, migrate: bool = True):
    rng = random.Random(seed)
    holdout = load_holdout()
    by_cat = defaultdict(list)
    for spec in load_specs():
        if spec["task_id"] in holdout:
            continue
        by_cat[spec["category"]].append(spec)
    cats = sorted(by_cat)
    migrate_share = TASK_TYPE_WEIGHTS[2][1] if migrate else 0.0
    n_specs = round(n_total * (1 - migrate_share))
    per_cat = n_specs // len(cats)
    extra = n_specs - per_cat * len(cats)
    picked = []
    for i, cat in enumerate(cats):
        want = per_cat + (1 if i < extra else 0)
        # balance difficulty within category: 1/4 easy, 1/2 medium, 1/4 hard
        by_diff = defaultdict(list)
        for s in by_cat[cat]:
            by_diff[s.get("difficulty", 2)].append(s)
        quota = {1: round(want * 0.25), 3: round(want * 0.25)}
        quota[2] = want - quota[1] - quota[3]
        for d in (1, 2, 3):
            pool = by_diff.get(d, [])
            take = min(quota[d], len(pool))
            picked.extend(rng.sample(pool, take))
        # top up from full category pool if a tier ran short
        short = want - sum(min(quota[d], len(by_diff.get(d, []))) for d in (1, 2, 3))
        if short > 0:
            remaining = [s for s in by_cat[cat] if s not in picked]
            picked.extend(rng.sample(remaining, min(short, len(remaining))))
    for spec in picked:
        # only full_program / single_function here; migrate_fix is mined below
        spec["task_type"] = "full_program" if rng.random() < 0.61 else "single_function"
        spec["description_v03"] = modernize_description(spec.get("description", ""))
        spec["input_types_v03"] = [modernize_type(t) for t in spec.get("input_types", [])]
        spec["output_type_v03"] = modernize_type(spec.get("output_type", ""))
        spec["domain_context_v03"] = modernize_signature_hint(spec.get("domain_context", ""))
    if migrate:
        n_mig = n_total - len(picked)
        per_cat_mig = max(1, n_mig // len(cats))
        used = set()
        mined = []
        for cat in cats:
            if len(mined) >= n_mig:
                break
            mined.extend(mine_migrate_tasks(cat, min(per_cat_mig, n_mig - len(mined)), rng, used))
        picked.extend(mined)
        sys.stderr.write(f"mined {len(mined)} migrate_fix tasks\n")
    rng.shuffle(picked)
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, required=True)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--no-migrate", action="store_true")
    args = ap.parse_args()

    picked = stratified_sample(args.total, args.seed, migrate=not args.no_migrate)
    os.makedirs(args.outdir, exist_ok=True)
    for i in range(args.shards):
        shard = picked[i::args.shards]
        path = f"{args.outdir}/shard_{i:02d}.jsonl"
        with open(path, "w") as f:
            for spec in shard:
                f.write(json.dumps(spec) + "\n")
        print(f"{path}: {len(shard)} tasks")


if __name__ == "__main__":
    main()
