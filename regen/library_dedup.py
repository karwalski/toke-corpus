#!/usr/bin/env python3
"""131.18 — exact-duplicate check: library programs vs banked regen records.

Key = sha256 of the canonical minified form (`tkc --min`) of each source, so
whitespace/formatting differences do not hide a duplicate. Compared:
  library  every spec in shards/library_131.jsonl (source_program file)
  regen    every record in MANIFEST.jsonl (tk_source), min-sha cached in
           audit/min_sha_regen.jsonl (task_id, sha256_of_record_source ->
           min_sha) so re-runs only re-minify changed records.
Reports library<->regen exact pairs (the story's dedup gate), plus
library<->library and regen<->regen duplicate groups (informational).
Output: audit/library_dedup_131.json + summary on stdout.
Prior audit (129.3): 0 dups vs the 54 doc programs.
"""
import argparse, hashlib, json, multiprocessing, os, subprocess, sys, tempfile, time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tkc_pin  # noqa: E402  (131.39)

TKC = tkc_pin.default_tkc()   # 131.39: pinned copy in workers via $TOKE_TKC_PIN
CORPUS = os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04")


def min_sha_of_source(src):
    """sha256 of `tkc --min` output, or None when tkc cannot minify."""
    with tempfile.NamedTemporaryFile("w", suffix=".tk", delete=False) as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run([TKC, path, "--min"], capture_output=True, text=True,
                           errors="replace", timeout=30)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return hashlib.sha256(r.stdout.strip().encode()).hexdigest()
    except subprocess.TimeoutExpired:
        return None
    finally:
        os.unlink(path)


def _job(item):
    key, src = item
    try:
        return key, hashlib.sha256(src.encode()).hexdigest(), min_sha_of_source(src)
    except Exception:
        return key, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-dir", default=CORPUS)
    ap.add_argument("--shard", default=os.path.join(CORPUS, "shards", "library_131.jsonl"))
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    t0 = time.time()
    audit_dir = os.path.join(args.corpus_dir, "audit")
    os.makedirs(audit_dir, exist_ok=True)
    cache_path = os.path.join(audit_dir, "min_sha_regen.jsonl")
    cache = {}
    if os.path.exists(cache_path):
        for line in open(cache_path):
            e = json.loads(line)
            cache[e["task_id"]] = e

    # regen records (dedupe MANIFEST rows: last write wins per task_id)
    regen_items, regen_meta = [], {}
    seen = set()
    for line in open(os.path.join(args.corpus_dir, "MANIFEST.jsonl")):
        e = json.loads(line)
        tid = e["task_id"]
        if tid in seen:
            continue
        seen.add(tid)
        p = os.path.join(args.corpus_dir, e["category"], tid + ".json")
        if not os.path.exists(p):
            continue
        rec = json.load(open(p))
        src = rec["tk_source"]
        src_sha = hashlib.sha256(src.encode()).hexdigest()
        regen_meta[tid] = {"category": e["category"], "src_sha": src_sha}
        c = cache.get(tid)
        if c and c.get("src_sha") == src_sha:
            regen_meta[tid]["min_sha"] = c.get("min_sha")
        else:
            regen_items.append((tid, src))
    lib_items, lib_meta = [], {}
    for line in open(args.shard):
        s = json.loads(line)
        src = open(s["source_program"], errors="replace").read()
        lib_meta[s["task_id"]] = {"category": s["category"], "source_program": s["source_program"]}
        lib_items.append((s["task_id"], src))
    print(f"minifying regen={len(regen_items)} (cached {len(regen_meta) - len(regen_items)}) "
          f"library={len(lib_items)}", file=sys.stderr)
    unminified = []
    pinned = tkc_pin.pin().install(sys.modules[__name__])   # 131.39: before the pool forks/spawns
    with pinned, multiprocessing.Pool(args.workers) as pool, open(cache_path, "a") as cf:
        for tid, src_sha, msha in pool.imap_unordered(_job, regen_items, chunksize=16):
            regen_meta[tid]["min_sha"] = msha
            cf.write(json.dumps({"task_id": tid, "src_sha": src_sha, "min_sha": msha,
                                 "tkc_bin_sha": pinned.sha256}) + "\n")
            if msha is None:
                unminified.append(tid)
        for tid, _, msha in pool.imap_unordered(_job, lib_items, chunksize=16):
            lib_meta[tid]["min_sha"] = msha
            if msha is None:
                unminified.append(tid)

    by_min = defaultdict(lambda: {"library": [], "regen": []})
    for tid, m in regen_meta.items():
        if m.get("min_sha"):
            by_min[m["min_sha"]]["regen"].append(tid)
    for tid, m in lib_meta.items():
        if m.get("min_sha"):
            by_min[m["min_sha"]]["library"].append(tid)
    cross = [(sorted(g["library"]), sorted(g["regen"])) for g in by_min.values()
             if g["library"] and g["regen"]]
    lib_dups = [sorted(g["library"]) for g in by_min.values() if len(g["library"]) > 1]
    regen_dups = [sorted(g["regen"]) for g in by_min.values() if len(g["regen"]) > 1]
    out = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "library_programs": len(lib_meta), "regen_records": len(regen_meta),
        "unminified": unminified,
        "library_vs_regen_pairs": [{"library": l, "regen": r} for l, r in cross],
        "library_internal_dup_groups": lib_dups,
        "regen_internal_dup_groups": regen_dups,
        "regen_internal_dup_records": sum(len(g) - 1 for g in regen_dups),
        "runtime_s": round(time.time() - t0, 1),
        "tkc_bin_sha": pinned.sha256, "tkc_version": pinned.version,   # 131.39
    }
    path = os.path.join(audit_dir, "library_dedup_131.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in out.items()}))
    for l, r in cross[:20]:
        print("  DUP", l, "==", r)
    print("written", path)


if __name__ == "__main__":
    main()
