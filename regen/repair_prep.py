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
from build_prompt import (expected_stdout_lines,   # noqa: E402  (131.74)
                          ERR_CONVENTION_COMMON,
                          ERR_CONVENTION_FULL,
                          ERR_CONVENTION_SINGLE)

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
    """131.74: the test lock is rendered by the GATE'S OWN renderer, through
    the single implementation in build_prompt.expected_stdout_lines
    (validate.render_expected for full_program, driver.expected_lines for
    single_function).  Before this story the full_program branch json.dumps'd
    the raw expectation and printed no line-form at all — a worker repairing
    an output mismatch was shown `-> expected "localhost"` and judged against
    the line `localhost` — while the single_function branch showed the raw
    JSON *and* the gate's lines.  Do not hand-roll a renderer here;
    tests/test_prompt_gate_agreement.py fails if this stops delegating."""
    ttype = spec.get("task_type", "full_program")
    tcs = spec.get("test_cases") or []
    lines = [f"REPAIR TASK ({spec.get('category')}): the toke program below was "
             f"accepted at compile time but FAILS execution.",
             "",
             f"Task: {spec.get('description', '')}",
             ""]
    if ttype == "stdin_program":
        lines.append("main() is run once per test case with the input on stdin; the "
                     "whole stdout must match exactly (exit 0):")
        for i, tc in enumerate(drv.stdin_cases(spec)):
            lines.append(f"  case {i}: stdin={json.dumps(tc['input'])} -> stdout="
                         f"{json.dumps(tc['expected_output'])}")
    else:
        single = ttype == "single_function"
        if single:
            lines.append("The validation harness appends a main() that calls your target "
                         "function on each test input and prints one line per result "
                         "(bool prints as 1/0; an array result prints one line per "
                         "element). Your fix must make these EXACT stdout lines come "
                         "out, in order:")
        else:
            lines.append("main() must print, with io.println, exactly the stdout line(s) "
                         "shown for each test case, in order — byte for byte, no extra "
                         "lines, exit 0:")
        has_err = False
        for tc in tcs:
            if drv.err_name(tc.get("expected")) is not None:
                has_err = True
            lines.append(f"  inputs={json.dumps(tc.get('inputs'))} -> stdout "
                         f"{json.dumps(expected_stdout_lines(spec, tc))}")
        if has_err:
            lines.append("")
            lines.append(ERR_CONVENTION_COMMON +
                         (ERR_CONVENTION_SINGLE if single else ERR_CONVENTION_FULL))
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
