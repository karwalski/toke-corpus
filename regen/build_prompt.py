#!/usr/bin/env python3
"""Build a worker generation prompt for one task spec.

Usage: build_prompt.py <spec.json line or file> [--card syntax_card.md]
Emits the full prompt on stdout. Three task types:

  full_program    - complete program with main() exercising the function on the
                    spec's test inputs, printing one result per line
  single_function - write the target function given helper context signatures;
                    harness wraps it with a main() for validation
  migrate_fix     - given legacy toke source + current tkc diagnostics, produce
                    an equivalent program in current syntax
  stdin_program   - (131.18) complete program driven by stdin; each test case is
                    one run whose whole stdout must equal expected_output
"""
import json, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import driver as drv                            # noqa: E402  (131.72)
from validate import render_expected            # noqa: E402  (131.72)

# ---------------------------------------------------------------------------
# 131.72: the prompt MUST ask for exactly what the acceptance gate accepts.
#
# Until this story the prompt dumped a test case's expectation as raw JSON
# (`expected output: {"err": "NotFound"}`) while the gate compares stdout
# against `validate.render_expected` (`err:notfound`) for full_program and
# `driver.expected_lines` for single_function.  A worker was therefore told to
# print a marker string and then failed for printing it — and `str` / `float`
# / `list` / `None` expectations diverged from the gate in the same way
# (`"alice"` vs `alice`, `2.0` vs `2`, `null` vs `None`).
#
# There is exactly ONE renderer per task type and it is the gate's own: never
# hand-roll a second one here, it will drift.  tests/test_prompt_gate_agreement.py
# fails if this file stops delegating.
# ---------------------------------------------------------------------------

ERR_CONVENTION_COMMON = (
    "  The `err:<variant>` lines above are the harness's ERROR convention, not text to "
    "hard-code: `<variant>` is the tag of the error-type variant the function returns, "
    "lower-cased with every non-alphanumeric character dropped (`$ParseFailed` -> "
    "`err:parsefailed`). Declare the spec's `t=$errtype{...}` and RETURN the real "
    "`$variant` from the `T!$errtype` union for those inputs. Returning (or printing) "
    "the marker text itself — `{'err': 'ParseFailed'}`, `\"err:parsefailed\"` from a "
    "plain `str` function, or any equivalent in a helper or a trailing wrapper — is a "
    "hard rejection.")

ERR_CONVENTION_FULL = (
    "\n  main() prints that line by matching the union: "
    "`mt f(x) {$ok:v \"\\(v)\";$err:e mt e {$parsefailed:w \"err:parsefailed\";...}}`.")

ERR_CONVENTION_SINGLE = (
    "\n  You write no main(): the harness appends one that matches your `T!$errtype` "
    "result and prints the `err:<variant>` line from the variant you returned.")


def expected_stdout_lines(spec, tc):
    """The exact stdout line(s) the acceptance gate expects for ONE test case,
    computed by the gate's own renderer for the spec's task type:

      full_program    validate.render_expected (run_shard.run_test_cases,
                      audit.audit_one) — one line, split on embedded newlines
      single_function driver.expected_lines (audit.audit_one via
                      driver.append_main) — array results are one line per
                      element and bools print 1/0, not true/false

    Single source of truth: this function delegates, it never formats."""
    if spec.get("task_type", "full_program") == "single_function":
        return drv.expected_lines({"test_cases": [tc]})
    return render_expected(tc.get("expected")).split("\n")


def format_test_cases(spec):
    """131.72: render each case as the stdout the gate will demand, never as
    the raw expectation value."""
    tcs = spec.get("test_cases") or []
    if not tcs:
        return ""
    single = spec.get("task_type", "full_program") == "single_function"
    if single:
        head = ("Test cases. You write no main(): the harness appends one that calls the "
                "target function on each input in order and prints one line per result "
                "(bool prints 1/0; an array result prints one line per element). Your "
                "function must make these EXACT stdout lines come out, in order:")
    else:
        head = ("Test cases. main() must exercise these inputs IN ORDER and print, with "
                "io.println, exactly the stdout line(s) shown for each — byte for byte, "
                "in order, with no extra lines and exit 0:")
    lines = ["", head]
    has_err = False
    for tc in tcs:
        if drv.err_name(tc.get("expected")) is not None:
            has_err = True
        want = expected_stdout_lines(spec, tc)
        lines.append(f"  inputs={json.dumps(tc.get('inputs'))} -> stdout {json.dumps(want)}")
    if has_err:
        lines.append("")
        lines.append(ERR_CONVENTION_COMMON +
                     (ERR_CONVENTION_SINGLE if single else ERR_CONVENTION_FULL))
    return "\n".join(lines)


def build(spec, card):
    desc = spec.get("description_v03") or spec.get("description", "")
    ttype = spec.get("task_type", "full_program")
    sig_in = spec.get("input_types_v03") or spec.get("input_types", [])
    sig_out = spec.get("output_type_v03") or spec.get("output_type", "")
    ctx = spec.get("domain_context_v03") or ""

    parts = [card, "", "---", ""]

    if ttype == "full_program":
        parts.append(f"TASK ({spec.get('category','?')}): {desc}")
        if sig_in or sig_out:
            parts.append(f"Target signature: inputs {sig_in} -> {sig_out}")
        parts.append(format_test_cases(spec))
        parts.append("")
        parts.append("Write a COMPLETE toke program: module declaration, any imports, the "
                      "function(s), and f=main():i64 that runs the test inputs and prints "
                      "each result with io.println. Return 0 from main.")
    elif ttype == "single_function":
        parts.append(f"TASK ({spec.get('category','?')}): {desc}")
        if sig_in or sig_out:
            parts.append(f"Target signature: inputs {sig_in} -> {sig_out}")
        if ctx:
            parts.append("")
            parts.append("Existing helper functions available in this module (already defined; "
                          "call them if useful, do NOT redefine them):")
            parts.append(f"  {ctx}")
        # 131.72: the driver executes these cases at audit time, so the worker
        # is shown them — rendered by driver.expected_lines, the gate itself.
        parts.append(format_test_cases(spec))
        parts.append("")
        parts.append("Write ONLY the single target function declaration (f=...{...};) plus any "
                      "t=$... type declarations it needs. No module line, no imports, no main. "
                      "The harness will assemble the full module around your function.")
    elif ttype == "stdin_program":
        # 131.18: library-style task — the program reads its input from stdin
        # and its WHOLE stdout is compared against expected_output per case
        parts.append(f"TASK ({spec.get('category','?')}): {desc}")
        lib = spec.get("library") or {}
        if lib.get("input_format"):
            parts.append(f"Input format (stdin): {lib['input_format']}")
        if lib.get("output_format"):
            parts.append(f"Output format (stdout): {lib['output_format']}")
        if lib.get("stdlib_modules"):
            parts.append(f"Stdlib modules used by the reference: {', '.join(lib['stdlib_modules'])}")
        tcs = spec.get("test_cases") or []
        if tcs:
            parts.append("")
            parts.append("Test cases (each is a separate run: stdin -> exact expected stdout, exit 0):")
            for i, tc in enumerate(tcs):
                parts.append(f"  case {i}: stdin={json.dumps(tc.get('input', ''))}")
                parts.append(f"          stdout={json.dumps(tc.get('expected_output', ''))}")
                if tc.get("fixtures"):
                    parts.append(f"          fixtures={json.dumps(tc['fixtures'])[:400]}")
        parts.append("")
        parts.append("Write a COMPLETE toke program: module declaration, imports, the "
                      "function(s), and f=main():i64 that reads stdin, prints exactly the "
                      "expected output with io.println, and returns 0.")
    elif ttype == "migrate_fix":
        parts.append("TASK: migrate legacy toke source to current v0.3 syntax (tkc 2.8.0).")
        parts.append("")
        parts.append("Legacy source (compiled under an older toke; now fails):")
        parts.append(spec["legacy_source"])
        parts.append("")
        parts.append("Current compiler diagnostics:")
        parts.append(spec.get("legacy_diagnostics", "(none captured)"))
        parts.append("")
        parts.append("Rewrite the program so it compiles under current tkc, preserving the "
                      "original behaviour exactly. Apply current syntax throughout (==, string "
                      "interpolation or s.concat instead of +, no underscores, etc.). Output the "
                      "complete migrated program.")
    else:
        raise ValueError(f"unknown task_type {ttype}")

    parts.append("")
    parts.append("Output ONLY raw toke source. No markdown fences, no commentary.")
    return "\n".join(p for p in parts if p is not None)


def main():
    src = sys.argv[1]
    card_path = os.path.join(HERE, "syntax_card.md")
    if os.path.isfile(src):
        spec = json.load(open(src))
    else:
        spec = json.loads(src)
    print(build(spec, open(card_path).read()))


if __name__ == "__main__":
    main()
