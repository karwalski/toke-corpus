#!/usr/bin/env python3
"""Epic 129.7 — prepare the A-category test-case authoring wave.

All 10,116 A-category specs have empty test_cases at source. They resolve to
1,489 base tasks (A-CAT-NNNN, variants differ only in naming/style mandates).
This wave authors test cases per BASE: the worker derives a Python reference
implementation from the task description, executes it on 3-5 chosen inputs,
and emits {base, python_ref, test_cases}. Banking (bank_a_tests.py) RE-RUNS
the reference independently — expected values are execution-verified, never
trusted from the worker.

Writes work/a_tests_129/{specs,prompts,batches}; workers write gen/tests_<base>.json.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit import load_specs                      # noqa: E402
from driver import effective_input_types          # noqa: E402  (131.44)

CORPUS = "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04"
WD = os.path.join(CORPUS, "work", "a_tests_129")
BATCH_SIZE = 20
_BASE = re.compile(r"^(A-[A-Z]+-\d+)v\d+$")


def representative_bases(specs):
    """base -> (spec, named) for every A-category base with no source
    test_cases (129.7 rule); prefers a variant whose description names the
    full signature."""
    bases = {}
    for tid, s in specs.items():
        if s.get("test_cases") or not s["category"].startswith("A-"):
            continue
        if s.get("task_type") == "migrate_fix":
            continue  # behaviour-equivalence tasks; compile-only (129.8 note)
        m = _BASE.match(tid)
        b = m.group(1) if m else tid  # non-variant ids are their own base
        # prefer a representative whose description names the full signature
        named = bool(re.search(r"f=[a-z0-9]+\([^)]*\):\S+", s.get("description", "")))
        cur = bases.get(b)
        if cur is None or (named and not cur[1]):
            bases[b] = (s, named)
    return bases


def prompt_for(base, s, reauthor_reason=None):
    # 131.44 (c): show the NORMALISED input types (`@u64;u64`), never the
    # sampler-mangled field (`['@(u64']`) that made the 129.7 workers author
    # 52 bases at arity 1 with the real arguments packed into one list
    types = effective_input_types(s)
    lines = [
        f"BASE TASK {base} ({s['category']}, difficulty {s.get('difficulty')}):",
        "",
        s.get("description", ""),
        "",
        f"input_types: {json.dumps(types)}   ({len(types)} parameter{'s' if len(types) != 1 else ''}, "
        "in signature order — each test case's `inputs` is a list of exactly that many values; "
        "never pack several arguments into one list)",
        f"output_type: {json.dumps(s.get('output_type_v03') or s.get('output_type'))}",
        "",
        "Author 3-5 test cases that pin the function's semantics (include one "
        "edge case: empty/zero/negative/boundary as applicable). Derive a Python "
        "reference implementation of EXACTLY the described behaviour, run it on "
        "your chosen inputs, and record the actual outputs as expected values.",
    ]
    if reauthor_reason:
        lines[1:1] = ["RE-AUTHOR (131.44): the banked test file for this base was rejected — "
                      + reauthor_reason + ". Author it afresh against the signature below.", ""]
    return "\n".join(lines)


def flagged_banked(bases, bank_dir):
    """131.44 (c): banked a_tests that verify_a_tests.verify rejects against
    the base's representative spec (wrong arity / type / ref mismatch).
    Returns {base: reason}."""
    from verify_a_tests import verify
    out = {}
    for fn in sorted(os.listdir(bank_dir)):
        if not fn.endswith(".json"):
            continue
        base = fn[:-5]
        if base not in bases:
            continue
        doc = json.load(open(os.path.join(bank_dir, fn)))
        errs = verify(base, doc, bases[base][0])
        if errs:
            out[base] = "; ".join(errs[:2])
    return out


def prepare(bases, todo, wd, batch_size=BATCH_SIZE, reasons=None, mode="author"):
    reasons = reasons or {}
    for d in ("specs", "prompts", "batches", "gen"):
        os.makedirs(os.path.join(wd, d), exist_ok=True)
    for b in todo:
        s, _ = bases[b]
        with open(os.path.join(wd, "specs", b + ".json"), "w") as f:
            json.dump(s, f)
        with open(os.path.join(wd, "prompts", b + ".txt"), "w") as f:
            f.write(prompt_for(b, s, reasons.get(b)))
    for n, i in enumerate(range(0, len(todo), batch_size)):
        ids = todo[i:i + batch_size]
        doc = {"batch": n, "base_ids": ids, "mode": mode}
        if reasons:
            doc["reasons"] = {b: reasons[b] for b in ids if b in reasons}
        with open(os.path.join(wd, "batches", f"batch_{n:03d}.json"), "w") as f:
            json.dump(doc, f, indent=1)
    return (len(todo) + batch_size - 1) // batch_size


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=WD)
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--reauthor", action="store_true",
                    help="131.44 (c): prepare the bases whose BANKED a_tests fail "
                         "verify_a_tests.verify (wrong arity etc.) for re-authoring; "
                         "prep only — nothing is launched or banked")
    args = ap.parse_args()
    specs = load_specs(CORPUS)
    bases = representative_bases(specs)
    bank_dir = os.path.join(CORPUS, "audit", "a_tests")
    done = set()
    if os.path.isdir(bank_dir):
        done = {os.path.splitext(f)[0] for f in os.listdir(bank_dir)}
    if args.reauthor:
        reasons = flagged_banked(bases, bank_dir)
        todo = sorted(reasons)
        nb = prepare(bases, todo, args.workdir, args.batch_size, reasons, mode="reauthor")
        print(json.dumps({"bases": len(bases), "banked": len(done), "flagged": len(todo),
                          "prepared": len(todo), "batches": nb, "workdir": args.workdir,
                          "base_ids": todo}))
        return
    todo = sorted(b for b in bases if b not in done)
    nb = prepare(bases, todo, args.workdir, args.batch_size)
    print(json.dumps({"bases": len(bases), "already_banked": len(done),
                      "prepared": len(todo), "batches": nb, "workdir": args.workdir}))


if __name__ == "__main__":
    main()
