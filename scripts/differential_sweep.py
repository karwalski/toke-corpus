#!/usr/bin/env python3
"""differential_sweep.py — Stories 57.14.1 + 57.14.2.

Run toke differential testing over corpus_default.jsonl:
  1. Cross-reference each record's tk_source against majority_output
  2. Compile with tkc (--check or full compile depending on f=main presence)
  3. For records with f=main(): compile, run, compare output
  4. For library-style: auto-generate a minimal test harness where feasible
  5. Categorise failures: lex, parse, typecheck, codegen, link, runtime, output_mismatch
  6. Report statistics and write detailed results

Auto-harness generation:
  For library functions with simple signatures (e.g. f=foo(a:i64;b:i64):i64),
  the script parses the Python reference main() to extract test calls, translates
  them to toke syntax, and wraps the function with a generated f=main().

Usage:
    python3 scripts/differential_sweep.py \
        --corpus data/corpus_default.jsonl \
        --training-data data/train.jsonl \
        --tkc /path/to/tkc \
        --output data/differential_sweep_results.jsonl \
        --workers 4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from multiprocessing import Pool
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COMPILE_TIMEOUT = 20  # seconds
RUN_TIMEOUT = 5

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SweepResult:
    entry_id: str
    in_training: bool
    has_main: bool
    has_expected_output: bool
    harness_generated: bool = False
    compile_check_passed: bool = False
    compile_passed: bool = False
    ran: bool = False
    exit_zero: bool = False
    output_match: bool = False
    failure_stage: str = ""  # lex, parse, typecheck, codegen, link, runtime, output_mismatch, ok
    failure_category: str = ""  # operator_precedence, overflow, algorithm, syntax, type_error, etc.
    error_codes: list[str] = field(default_factory=list)
    expected_output: str = ""
    actual_output: str = ""
    diagnostics: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_temp(src: str, suffix: str = ".tk") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        f.write(src)
    return path


def _cleanup(*paths: str) -> None:
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


def _classify_error_stage(diag_text: str, error_codes: list[str]) -> str:
    """Classify the failure stage from diagnostics."""
    for code in error_codes:
        if code.startswith("E1"):
            return "lex"
        if code.startswith("E2"):
            return "parse"
        if code.startswith("E3") or code.startswith("E4"):
            return "typecheck"
        if code.startswith("E9"):
            return "codegen"
    if "undefined reference" in diag_text or "linker" in diag_text.lower():
        return "link"
    if "lex" in diag_text.lower():
        return "lex"
    if "parse" in diag_text.lower():
        return "parse"
    return "unknown"


def _classify_failure_category(result: SweepResult, tk_source: str) -> str:
    """Heuristic classification of failure root cause."""
    diag = result.diagnostics.lower()
    codes = result.error_codes

    # Lex-level: character set violations
    if any(c.startswith("E1003") for c in codes):
        return "illegal_character"

    # Parse-level
    if any(c.startswith("E2") for c in codes):
        if "==" in tk_source:
            return "double_equals"
        if "," in re.sub(r'"[^"]*"', '', tk_source):
            return "comma_separator"
        return "syntax_error"

    # Type errors
    if any(c.startswith("E3") or c.startswith("E4") for c in codes):
        return "type_error"

    # Codegen/link
    if result.failure_stage in ("codegen", "link"):
        if "undefined" in diag:
            return "undefined_symbol"
        return "codegen_error"

    # Output mismatch — try to categorise
    if result.failure_stage == "output_mismatch":
        expected = result.expected_output.strip().split("\n")
        actual = result.actual_output.strip().split("\n")
        if len(expected) != len(actual):
            return "wrong_line_count"
        # Check for off-by-one / boundary issues
        try:
            for e, a in zip(expected, actual):
                ev, av = int(e), int(a)
                if abs(ev - av) == 1:
                    return "off_by_one"
                if av == 0 and ev != 0:
                    return "returns_zero"
        except ValueError:
            pass
        return "algorithm_bug"

    return "other"


# ---------------------------------------------------------------------------
# Test harness generation for library functions
# ---------------------------------------------------------------------------

_FUNC_SIG_RE = re.compile(
    r'f=([a-z][a-z0-9]*)\(([^)]*)\):([^{]+)\{'
)

_SIMPLE_TYPES = {"i64", "i32", "u64", "u32", "f64", "f32", "bool"}


def _parse_toke_signatures(tk_source: str) -> list[dict]:
    """Extract function signatures from toke source (excluding main)."""
    sigs = []
    for m in _FUNC_SIG_RE.finditer(tk_source):
        name = m.group(1)
        if name == "main":
            continue
        params_str = m.group(2)
        ret_type = m.group(3).strip()
        params = []
        if params_str.strip():
            for p in params_str.split(";"):
                p = p.strip()
                if ":" in p:
                    pname, ptype = p.split(":", 1)
                    params.append({"name": pname.strip(), "type": ptype.strip()})
        sigs.append({"name": name, "params": params, "return_type": ret_type})
    return sigs


def _parse_python_main_calls(py_source: str, func_name: str) -> list[list[str]]:
    """Extract argument lists from Python main() calls to func_name.

    Returns list of argument lists as string values.
    e.g. for print(sum([1,2,3])) returns [["@(1;2;3)"]]
    """
    if not py_source:
        return []

    calls = []
    # Find lines like: print(func_name(...))
    pattern = re.compile(
        rf'print\s*\(\s*{re.escape(func_name)}\s*\((.+?)\)\s*\)',
        re.DOTALL
    )
    for m in pattern.finditer(py_source):
        args_str = m.group(1)
        # Simple translation of Python literals to toke
        toke_args = _translate_py_args(args_str)
        if toke_args is not None:
            calls.append(toke_args)

    return calls


def _translate_py_args(args_str: str) -> list[str] | None:
    """Translate Python argument string to toke arguments.

    Returns list of toke expression strings, or None if too complex.
    """
    # Split by comma at top level (respecting brackets/parens)
    parts = _split_top_level(args_str, ",")
    toke_parts = []
    for part in parts:
        part = part.strip()
        translated = _translate_py_value(part)
        if translated is None:
            return None
        toke_parts.append(translated)
    return toke_parts


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split string by separator, respecting nested brackets/parens."""
    parts = []
    depth = 0
    current = []
    for c in s:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(c)
    if current:
        parts.append("".join(current))
    return parts


def _translate_py_value(val: str) -> str | None:
    """Translate a single Python value to toke syntax."""
    val = val.strip()

    # Integer literal
    if re.match(r'^-?\d+$', val):
        return val

    # Float literal
    if re.match(r'^-?\d+\.\d+$', val):
        return val

    # Boolean
    if val == "True":
        return "true"
    if val == "False":
        return "false"

    # String literal
    if (val.startswith('"') and val.endswith('"')) or \
       (val.startswith("'") and val.endswith("'")):
        # Use double quotes for toke
        inner = val[1:-1]
        return f'"{inner}"'

    # List literal -> toke array
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return "@()"
        elements = _split_top_level(inner, ",")
        toke_elems = []
        for e in elements:
            te = _translate_py_value(e.strip())
            if te is None:
                return None
            toke_elems.append(te)
        return "@(" + ";".join(toke_elems) + ")"

    # Too complex
    return None


def _wrap_print(expr: str, ret_type: str) -> str:
    """Wrap an expression in the appropriate print call for its type.

    Integers need str.fromint() conversion before io.println().
    Strings and $str can be printed directly.
    Arrays need custom formatting.
    """
    if ret_type in ("$str", "str", "Str"):
        return f"io.println({expr})"
    if ret_type in ("i64", "i32", "u64", "u32", "i8", "i16", "u8", "u16"):
        return f"io.println(str.fromint({expr}))"
    if ret_type in ("f64", "f32"):
        return f"io.println(str.fromfloat({expr}))"
    if ret_type == "bool":
        return f'io.println(if({expr}){{"true"}}el{{"false"}})'
    # Default: try str.fromint
    return f"io.println(str.fromint({expr}))"


def generate_harness(tk_source: str, py_source: str) -> str | None:
    """Generate a toke test harness wrapping library function with main().

    Returns the full toke source with appended f=main(), or None if
    we can't safely generate one.
    """
    sigs = _parse_toke_signatures(tk_source)
    if not sigs:
        return None

    # Use the first non-main function
    sig = sigs[0]
    func_name = sig["name"]
    ret_type = sig["return_type"]

    # Only generate harnesses for functions returning simple scalar types or strings.
    # Array returns need custom formatting which we can't reliably generate.
    allowed = _SIMPLE_TYPES | {"$str", "str", "Str"}
    if ret_type not in allowed:
        return None

    # Parse Python main() to extract test calls
    calls = _parse_python_main_calls(py_source, func_name)
    if not calls:
        return None

    # Generate toke main
    lines = []
    lines.append("f=main():i64{")

    for args in calls:
        arg_str = ";".join(args)
        call_expr = f"{func_name}({arg_str})"
        lines.append(f"  {_wrap_print(call_expr, ret_type)};")

    lines.append("  <0")
    lines.append("}")

    harness_main = "\n".join(lines)

    # Ensure required imports exist
    full_source = tk_source.rstrip(";").rstrip()
    needed_imports = []
    if "i=io:" not in full_source:
        needed_imports.append("i=io:std.io")
    if "i=str:" not in full_source and ret_type in _SIMPLE_TYPES:
        needed_imports.append("i=str:std.str")

    if needed_imports:
        # Insert imports after module declaration
        parts = full_source.split(";", 1)
        if len(parts) == 2:
            import_str = ";".join(needed_imports)
            full_source = parts[0] + ";" + import_str + ";" + parts[1]
        else:
            full_source = full_source + ";" + ";".join(needed_imports)

    return full_source + ";\n" + harness_main + ";\n"


# ---------------------------------------------------------------------------
# Core testing
# ---------------------------------------------------------------------------

def test_record(entry: dict, tkc_path: str, training_sources: set[str]) -> SweepResult:
    """Test a single corpus record."""
    tk_source = entry.get("tk_source", "")
    entry_id = entry.get("id", entry.get("task_id", "unknown"))
    mo = entry.get("differential", {}).get("majority_output", "")
    has_main = "f=main(" in tk_source
    in_training = tk_source.strip() in training_sources

    result = SweepResult(
        entry_id=entry_id,
        in_training=in_training,
        has_main=has_main,
        has_expected_output=bool(mo and mo.strip()),
        expected_output=mo.rstrip("\n") if mo else "",
    )

    if not mo or not mo.strip():
        result.failure_stage = "no_expected"
        return result

    # Determine source to compile
    compile_source = tk_source
    if not has_main:
        # Try to generate harness
        py_src = entry.get("references", {}).get("python_source", "")
        harness = generate_harness(tk_source, py_src)
        if harness:
            compile_source = harness
            result.harness_generated = True
            result.has_main = True
        else:
            # Can't run without main — just do compile check
            src_path = _write_temp(tk_source)
            try:
                comp = subprocess.run(
                    [tkc_path, "--check", "--diag-json", src_path],
                    capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
                    encoding="utf-8", errors="replace",
                )
                result.compile_check_passed = comp.returncode == 0
                if not result.compile_check_passed:
                    result.diagnostics = (comp.stdout + comp.stderr)[:2000]
                    result.error_codes = _extract_error_codes(comp.stdout + comp.stderr)
                    result.failure_stage = _classify_error_stage(result.diagnostics, result.error_codes)
                else:
                    result.failure_stage = "no_harness"
            except (subprocess.TimeoutExpired, OSError):
                result.failure_stage = "timeout"
            finally:
                _cleanup(src_path)
            return result

    # Full compile + run
    src_path = _write_temp(compile_source)
    bin_path = src_path.replace(".tk", "")
    try:
        comp = subprocess.run(
            [tkc_path, src_path, "--out", bin_path],
            capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        result.compile_check_passed = True  # got past --check implicitly
        if comp.returncode != 0:
            result.diagnostics = (comp.stdout + comp.stderr)[:2000]
            result.error_codes = _extract_error_codes(comp.stdout + comp.stderr)
            result.failure_stage = _classify_error_stage(result.diagnostics, result.error_codes)
            result.failure_category = _classify_failure_category(result, compile_source)
            return result

        result.compile_passed = True

        # Run
        try:
            run = subprocess.run(
                [bin_path],
                capture_output=True, text=True, timeout=RUN_TIMEOUT,
                encoding="utf-8", errors="replace",
            )
            result.ran = True
            result.exit_zero = run.returncode == 0
            result.actual_output = run.stdout.rstrip("\n")

            if result.actual_output == result.expected_output:
                result.output_match = True
                result.failure_stage = "ok"
            else:
                result.failure_stage = "output_mismatch"
                result.failure_category = _classify_failure_category(result, compile_source)
        except subprocess.TimeoutExpired:
            result.ran = True
            result.failure_stage = "runtime_timeout"
        except OSError as e:
            result.failure_stage = f"runtime_error: {e}"
    except subprocess.TimeoutExpired:
        result.failure_stage = "compile_timeout"
    except OSError as e:
        result.failure_stage = f"compile_error: {e}"
    finally:
        _cleanup(src_path, bin_path)

    if not result.failure_category and result.failure_stage not in ("ok", "no_expected"):
        result.failure_category = _classify_failure_category(result, compile_source)

    return result


def _extract_error_codes(text: str) -> list[str]:
    codes = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                code = d.get("error_code", "")
                if code and code not in codes:
                    codes.append(code)
            except json.JSONDecodeError:
                pass
    return codes


# ---------------------------------------------------------------------------
# Worker for multiprocessing
# ---------------------------------------------------------------------------

_worker_tkc: str = ""
_worker_training: set = set()


def _init_worker(tkc_path: str, training_sources: set[str]) -> None:
    global _worker_tkc, _worker_training
    _worker_tkc = tkc_path
    _worker_training = training_sources


def _process_entry(entry: dict) -> dict:
    result = test_record(entry, _worker_tkc, _worker_training)
    return asdict(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--training-data", type=Path, default=None)
    parser.add_argument("--tkc", type=str, default="tkc")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-records", type=int, default=None)
    parser.add_argument("--training-only", action="store_true",
                        help="Only test records that appear in training data")
    args = parser.parse_args()

    # Verify tkc
    try:
        v = subprocess.run([args.tkc, "--version"], capture_output=True, text=True, timeout=5)
        print(f"tkc: {v.stdout.strip()}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print(f"ERROR: tkc not found at {args.tkc}", file=sys.stderr)
        return 1

    # Load training sources
    training_sources: set[str] = set()
    if args.training_data and args.training_data.exists():
        print(f"Loading training data: {args.training_data}")
        with open(args.training_data) as f:
            for line in f:
                rec = json.loads(line.strip())
                for msg in rec.get("messages", []):
                    if msg.get("role") == "assistant":
                        training_sources.add(msg["content"].strip())
        print(f"  Training sources: {len(training_sources):,}")

    # Load corpus
    print(f"Loading corpus: {args.corpus}")
    entries = []
    with open(args.corpus) as f:
        for line in f:
            entry = json.loads(line.strip())
            if args.training_only and entry.get("tk_source", "").strip() not in training_sources:
                continue
            entries.append(entry)
            if args.max_records and len(entries) >= args.max_records:
                break

    print(f"  Records to test: {len(entries):,}")

    # Run sweep
    t0 = time.time()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    results = []
    with Pool(args.workers, initializer=_init_worker,
              initargs=(args.tkc, training_sources)) as pool:
        for i, result_dict in enumerate(pool.imap(_process_entry, entries, chunksize=10)):
            results.append(result_dict)
            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                print(f"  {i+1}/{len(entries)} ({rate:.1f}/s)", file=sys.stderr)

    elapsed = time.time() - t0

    # Write results
    with open(args.output, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    # Statistics
    total = len(results)
    in_training = sum(1 for r in results if r["in_training"])
    has_expected = sum(1 for r in results if r["has_expected_output"])
    harness_gen = sum(1 for r in results if r["harness_generated"])
    compile_ok = sum(1 for r in results if r["compile_passed"])
    ran = sum(1 for r in results if r["ran"])
    output_ok = sum(1 for r in results if r["output_match"])

    print(f"\n{'='*60}")
    print(f"Differential Sweep Results")
    print(f"{'='*60}")
    print(f"Total records:         {total:,}")
    print(f"In training set:       {in_training:,}")
    print(f"Has expected output:   {has_expected:,}")
    print(f"Harness generated:     {harness_gen:,}")
    print(f"Full compile passed:   {compile_ok:,}")
    print(f"Ran successfully:      {ran:,}")
    print(f"Output matched:        {output_ok:,} ({100*output_ok/max(has_expected,1):.1f}% of testable)")
    print(f"Elapsed:               {elapsed:.1f}s")

    # Failure stage breakdown
    stages = Counter(r["failure_stage"] for r in results)
    print(f"\nFailure stage breakdown:")
    for stage, count in stages.most_common():
        pct = 100 * count / total
        print(f"  {stage:25s}  {count:6,}  ({pct:.1f}%)")

    # Failure category breakdown (for non-ok entries)
    cats = Counter(r["failure_category"] for r in results
                   if r["failure_category"])
    if cats:
        print(f"\nFailure category breakdown:")
        for cat, count in cats.most_common():
            print(f"  {cat:25s}  {count:6,}")

    # Training-specific stats
    if in_training > 0:
        train_results = [r for r in results if r["in_training"]]
        train_expected = sum(1 for r in train_results if r["has_expected_output"])
        train_ok = sum(1 for r in train_results if r["output_match"])
        train_compile = sum(1 for r in train_results if r["compile_passed"])
        print(f"\nTraining data subset:")
        print(f"  Total:               {in_training:,}")
        print(f"  Has expected output:  {train_expected:,}")
        print(f"  Compile passed:       {train_compile:,}")
        print(f"  Output matched:       {train_ok:,} ({100*train_ok/max(train_expected,1):.1f}%)")

        train_stages = Counter(r["failure_stage"] for r in train_results)
        print(f"  Failure stages:")
        for stage, count in train_stages.most_common():
            print(f"    {stage:25s}  {count:6,}")

    print(f"\nResults written to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
