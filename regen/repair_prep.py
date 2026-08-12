#!/usr/bin/env python3
"""Epic 129.4/129.5 — prepare the repair + compaction wave.

Reads audit/repair_queue.json + audit/compaction_queue.json, writes
work/repair_129/{specs,prompts,batches,gen}. Worker contract: read the prompt,
write the corrected module to gen/fix_<task_id>.tk (same shape as the current
source — single_function modules have NO main), self-check with
check_repair.py, ≤3 attempts. Banking is main-thread only (bank_repairs.py).
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit import load_specs, style_mandate      # noqa: E402
import driver as drv                              # noqa: E402

CORPUS = "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04"
WD = os.path.join(CORPUS, "work", "repair_129")
BATCH_SIZE = 20


def audit_rows():
    rows = {}
    for line in open(os.path.join(CORPUS, "audit", "audit_corpus.jsonl")):
        r = json.loads(line)
        rows[r["task_id"]] = r
    return rows


def repair_prompt(spec, rec, row):
    tcs = spec.get("test_cases") or []
    lines = [f"REPAIR TASK ({spec.get('category')}): the toke program below was "
             f"accepted at compile time but FAILS execution.",
             "",
             f"Task: {spec.get('description', '')}",
             ""]
    if spec.get("task_type") == "single_function":
        want = drv.expected_lines(spec)
        lines.append("The validation harness appends a main() that calls your target "
                     "function on each test input and prints one line per result "
                     "(bool prints as 1/0). Expected lines, in order:")
        for tc, _ in zip(tcs, range(99)):
            lines.append(f"  inputs={json.dumps(tc.get('inputs'))} -> expected "
                         f"{json.dumps(tc.get('expected'))}")
        lines.append(f"  (as printed lines: {json.dumps(want)})")
    else:
        lines.append("main() must print exactly one line per test case, in order:")
        for tc in tcs:
            lines.append(f"  inputs={json.dumps(tc.get('inputs'))} -> expected "
                         f"{json.dumps(tc.get('expected'))}")
    lines += ["",
              f"Audit failure: {row.get('reason') or ('build failed (E9003-class codegen)' if not row.get('build') else 'output mismatch')}",
              "",
              "Current source:",
              rec["tk_source"],
              ""]
    return "\n".join(lines)


def compact_prompt(spec, rec, row):
    m = row.get("metrics") or {}
    flags = []
    if (row.get("idiom") or 1.0) < 0.6:
        flags.append(f"idiom score {row['idiom']} < 0.6 floor ({', '.join(row.get('idiom_notes') or [])})")
    if m.get("max_depth", 0) > 3:
        flags.append(f"control nesting depth {m['max_depth']} (target <=3: expression-if, early-exit, && / ||)")
    if m.get("max_func_bytes", 0) > 600:
        flags.append(f"largest function {m['max_func_bytes']} bytes (target <=600: split into small named sub-functions)")
    lines = [f"COMPACTION TASK ({spec.get('category')}): the toke program below is "
             f"CORRECT (passes all checks) but violates the quality rubric:",
             ""]
    lines += [f"- {f}" for f in flags]
    mandate = style_mandate(spec)
    if mandate:
        lines += ["", f"NOTE the task description mandates: \"{mandate}\" — honour it; "
                      "only fix the flags that do not contradict it."]
    lines += ["",
              f"Task: {spec.get('description', '')}",
              "",
              "Rewrite it: least code possible, small composable functions, no deep "
              "nesting, preserve the EXACT same behaviour and printed output. "
              "Keep the same module shape (single_function modules have no main).",
              "",
              "Current source:",
              rec["tk_source"],
              ""]
    return "\n".join(lines)


def main():
    specs = load_specs(CORPUS)
    rows = audit_rows()
    repair = json.load(open(os.path.join(CORPUS, "audit", "repair_queue.json")))
    compact = json.load(open(os.path.join(CORPUS, "audit", "compaction_queue.json")))
    for d in ("specs", "prompts", "batches", "gen"):
        os.makedirs(os.path.join(WD, d), exist_ok=True)
    done = set()
    bank_ledger = os.path.join(WD, "bank_ledger.jsonl")
    if os.path.exists(bank_ledger):
        for line in open(bank_ledger):
            e = json.loads(line)
            if e["status"] == "banked":
                done.add(e["task_id"])
    todo = []
    for kind, ids in (("repair", repair), ("compact", compact)):
        for tid in ids:
            if tid in done or tid not in specs:
                continue
            spec, row = specs[tid], rows[tid]
            rec = json.load(open(os.path.join(CORPUS, row["category"], tid + ".json")))
            with open(os.path.join(WD, "specs", tid + ".json"), "w") as f:
                json.dump(spec, f)
            prompt = (repair_prompt if kind == "repair" else compact_prompt)(spec, rec, row)
            with open(os.path.join(WD, "prompts", tid + ".txt"), "w") as f:
                f.write(prompt)
            todo.append(tid)
    for n, i in enumerate(range(0, len(todo), BATCH_SIZE)):
        with open(os.path.join(WD, "batches", f"batch_{n:03d}.json"), "w") as f:
            json.dump({"batch": n, "task_ids": todo[i:i + BATCH_SIZE]}, f)
    print(json.dumps({"prepared": len(todo), "already_banked": len(done),
                      "batches": (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE,
                      "workdir": WD}))


if __name__ == "__main__":
    main()
