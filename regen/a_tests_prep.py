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

CORPUS = "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04"
WD = os.path.join(CORPUS, "work", "a_tests_129")
BATCH_SIZE = 20
_BASE = re.compile(r"^(A-[A-Z]+-\d+)v\d+$")


def main():
    specs = load_specs(CORPUS)
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
    for d in ("specs", "prompts", "batches", "gen"):
        os.makedirs(os.path.join(WD, d), exist_ok=True)
    done = set()
    bank_dir = os.path.join(CORPUS, "audit", "a_tests")
    if os.path.isdir(bank_dir):
        done = {os.path.splitext(f)[0] for f in os.listdir(bank_dir)}
    todo = sorted(b for b in bases if b not in done)
    for b in todo:
        s, _ = bases[b]
        with open(os.path.join(WD, "specs", b + ".json"), "w") as f:
            json.dump(s, f)
        prompt = "\n".join([
            f"BASE TASK {b} ({s['category']}, difficulty {s.get('difficulty')}):",
            "",
            s.get("description", ""),
            "",
            f"input_types: {json.dumps(s.get('input_types_v03') or s.get('input_types'))}",
            f"output_type: {json.dumps(s.get('output_type_v03') or s.get('output_type'))}",
            "",
            "Author 3-5 test cases that pin the function's semantics (include one "
            "edge case: empty/zero/negative/boundary as applicable). Derive a Python "
            "reference implementation of EXACTLY the described behaviour, run it on "
            "your chosen inputs, and record the actual outputs as expected values.",
        ])
        with open(os.path.join(WD, "prompts", b + ".txt"), "w") as f:
            f.write(prompt)
    for n, i in enumerate(range(0, len(todo), BATCH_SIZE)):
        with open(os.path.join(WD, "batches", f"batch_{n:03d}.json"), "w") as f:
            json.dump({"batch": n, "base_ids": todo[i:i + BATCH_SIZE]}, f)
    print(json.dumps({"bases": len(bases), "already_banked": len(done),
                      "prepared": len(todo),
                      "batches": (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE,
                      "workdir": WD}))


if __name__ == "__main__":
    main()
