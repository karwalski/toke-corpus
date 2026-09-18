#!/usr/bin/env python3
"""131.18 — back-generate one `stdin_program` task spec per verified library
program from the toke-test-programs manifests.

Input : ~/tk/toke-test-programs/results/library/<category>.json (16 manifests,
        `programs[{id, title, description, difficulty, stdlib_modules,
        input_format, output_format, source, test_cases[{input,
        expected_output, fixtures?}], ...}]`)
Source: results/solutions/<category>/<id>/solution.tk — the migrated, verified
        program (the manifest's inline `source` is stale for ~1,100 programs:
        pre-`==` migration; solution.tk is what 129.3 audited 1,583/1,583).
Output: corpus/regen_v04/shards/library_131.jsonl (gitignored), one spec per
        program, consumed by ingest_library.py / run_shard.py / audit.py.

Spec shape (superset of the shard spec used by run_shard.validate):
  task_id          L-<CODE>-<NNN>   (CODE = manifest id prefix, see CATEGORY_CODES)
  category         L-<CODE>         (record directory, like D-CLI)
  task_type        stdin_program
  build_flags      ["--allow-all"] — library programs use fs/env/net capabilities
                   (tkc CAP001 denies them at run time otherwise; matches the
                   verify-one.py / audit_library.py build); honoured by
                   run_shard.validate_one_gates for stdin_program only
  description      manifest requirement text (title kept under library.title)
  test_cases       ALL manifest cases: {input, expected_output[, fixtures]}
  difficulty       manifest difficulty (author-assigned, 1-5)
  difficulty_derived  DIFFICULTY RULE (documented below) — fallback when a
                   manifest omits difficulty; recorded so the two can be compared
  source_program   absolute path of solution.tk
  source_sha256    sha256 of solution.tk bytes at spec time (drift detection)
  fixtures         sorted list of fixture paths used by any case (126.8), [] if none
  library          {id, category, manifest, title, stdlib_modules, input_format,
                    output_format, difficulty}

DIFFICULTY RULE (difficulty_derived): size band on raw solution.tk bytes —
  <500 -> 1, <1000 -> 2, <1800 -> 3, <3000 -> 4, else 5;  +1 when the program
  has >= 5 test cases (capped at 5).

Exclusions (printed, never written): manifest programs with no solution.tk, or
with zero test cases, or whose id repeats across manifests.
"""
import argparse, glob, hashlib, json, os, sys
from collections import Counter, OrderedDict

ROOT = os.path.expanduser("~/tk/toke-test-programs")
LIB = os.path.join(ROOT, "results", "library")
SOL = os.path.join(ROOT, "results", "solutions")
DEFAULT_OUT = os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04/shards/library_131.jsonl")

# manifest category -> short code. The code IS the manifest's own id prefix
# (AIA-001 ...), so task_id L-AIA-001 stays traceable to the library id and
# the L- prefix keeps L-DAT / L-NET / L-CRY distinct from D-DAT / D-NET / D-CRY.
CATEGORY_CODES = OrderedDict([
    ("ai-agents", "AIA"), ("calculators-finance", "FIN"), ("crypto-blockchain", "CRY"),
    ("data-processing", "DAT"), ("devtools", "DEV"), ("education", "EDU"),
    ("games", "GAM"), ("gazeta", "GAZ"), ("manufacturing-ml", "MFG"),
    ("media-content", "MED"), ("messaging", "MSG"), ("networking-rest", "NET"),
    ("scientific-math", "SCI"), ("security", "SEC"), ("social-media", "SOC"),
    ("system-tools", "SYS"),
])


def derived_difficulty(src_bytes, n_tests):
    d = 1 if src_bytes < 500 else 2 if src_bytes < 1000 else 3 if src_bytes < 1800 \
        else 4 if src_bytes < 3000 else 5
    if n_tests >= 5:
        d += 1
    return min(d, 5)


def load_manifests():
    for cf in sorted(glob.glob(os.path.join(LIB, "*.json"))):
        if cf.endswith("index.json"):
            continue
        d = json.load(open(cf))
        yield os.path.basename(cf), d["category"], d.get("title"), d["programs"]


def build_spec(manifest, cat, cat_title, p):
    code = CATEGORY_CODES[cat]
    pid = p["id"]
    if not pid.startswith(code + "-"):
        raise ValueError(f"{pid}: id prefix does not match category code {code}")
    sol = os.path.join(SOL, cat, pid, "solution.tk")
    src = open(sol, "rb").read()
    tcs = []
    fixture_paths = set()
    for tc in p["test_cases"]:
        entry = {"input": tc.get("input", "") or "",
                 "expected_output": tc.get("expected_output", "") or ""}
        if tc.get("fixtures"):
            entry["fixtures"] = tc["fixtures"]
            fixture_paths.update(tc["fixtures"].get("dirs") or [])
            fixture_paths.update((tc["fixtures"].get("files") or {}).keys())
        tcs.append(entry)
    return {
        "task_id": f"L-{pid}",
        "category": f"L-{code}",
        "task_type": "stdin_program",
        "build_flags": ["--allow-all"],
        "description": p["description"],
        "test_cases": tcs,
        "difficulty": p.get("difficulty") if p.get("difficulty") is not None
                      else derived_difficulty(len(src), len(tcs)),
        "difficulty_derived": derived_difficulty(len(src), len(tcs)),
        "source_program": sol,
        "source_sha256": hashlib.sha256(src).hexdigest(),
        "fixtures": sorted(fixture_paths),
        "library": {"id": pid, "category": cat, "manifest": manifest,
                    "title": p.get("title"), "stdlib_modules": p.get("stdlib_modules") or [],
                    "input_format": p.get("input_format"), "output_format": p.get("output_format"),
                    "difficulty": p.get("difficulty")},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    specs, excluded, seen = [], [], set()
    per_cat = Counter()
    agree = Counter()
    for manifest, cat, cat_title, programs in load_manifests():
        if cat not in CATEGORY_CODES:
            sys.exit(f"unmapped library category {cat!r} — extend CATEGORY_CODES")
        for p in programs:
            pid = p["id"]
            sol = os.path.join(SOL, cat, pid, "solution.tk")
            if pid in seen:
                excluded.append((cat, pid, "duplicate id across manifests"))
                continue
            seen.add(pid)
            if not os.path.exists(sol):
                excluded.append((cat, pid, "no solution.tk"))
                continue
            if not p.get("test_cases"):
                excluded.append((cat, pid, "no test cases"))
                continue
            s = build_spec(manifest, cat, cat_title, p)
            specs.append(s)
            per_cat[s["category"]] += 1
            agree["agree" if s["difficulty"] == s["difficulty_derived"] else "differ"] += 1
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        for s in specs:
            f.write(json.dumps(s) + "\n")
    print("category mapping (manifest -> code -> record dir):")
    for cat, code in CATEGORY_CODES.items():
        print(f"  {cat:22s} {code}  L-{code}  {per_cat.get('L-' + code, 0):4d}")
    print(f"specs written: {len(specs)} -> {args.out}")
    print(f"with fixtures: {sum(1 for s in specs if s['fixtures'])}")
    print(f"difficulty manifest-vs-derived: {dict(agree)}")
    print(f"excluded: {len(excluded)}")
    for cat, pid, why in excluded:
        print(f"  EXCLUDED {cat}/{pid}: {why}")


if __name__ == "__main__":
    main()
