#!/usr/bin/env python3
"""refine_corpus_llm.py — Story 57.16.1 + 57.16.4.

Multi-turn LLM refinement of toke training corpus entries.

For each training record:
  1. Present task prompt + toke syntax ref + library info to Sonnet
  2. Receive toke code, apply Phase 2 autofix
  3. Compile with tkc --check; if fail, send diagnostics back to LLM
  4. Iterate up to MAX_TURNS until compilation succeeds
  5. If compilable + has expected output: full compile, sandboxed run, compare
  6. If no expected output: ask LLM to generate test harness with f=main()
  7. Write refined record to output

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python3 scripts/refine_corpus_llm.py \
        --training-data data/refreshed/train.jsonl \
        --corpus data/corpus_default.jsonl \
        --output data/refined_100.jsonl \
        --max-records 100 \
        --diverse           # sample across categories
        --skip-categories FUZZ,MUT-let_to_mut

Parallel (EC2):
    python3 scripts/refine_corpus_llm.py \
        --training-data data/refreshed/train.jsonl \
        --corpus data/corpus_default.jsonl \
        --output data/refined_full.jsonl \
        --max-records 5000 --diverse \
        --workers 8 --resume
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import random
import re
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
MAX_TURNS = 10
COMPILE_TIMEOUT = 20
RUN_TIMEOUT = 5
TKC = os.environ.get("TKC", str(Path(__file__).resolve().parent.parent.parent / "toke" / "tkc"))

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from phase2_syntax_audit import autofix_source, detect_violations  # noqa: E402

# ---------------------------------------------------------------------------
# System prompt — comprehensive toke reference
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_PATH = (Path(__file__).resolve().parent.parent.parent
                       / "toke-model" / "corpus" / "system_prompt_phase2.txt")
SYSTEM_PROMPT_TEXT = _SYSTEM_PROMPT_PATH.read_text()

# Augment with additional library info and output conventions
SYSTEM_PROMPT_TEXT += """

# Additional library reference

## Output functions (for programs with f=main)
- `io.println(s)` — print a string to stdout with newline. Import: `i=io:std.io;`
- To print an integer: `io.println(str.fromint(n))` — Import: `i=str:std.str;`
- To print a float: `io.println(str.fromfloat(n))` — requires `i=str:std.str;`
- `log.info(s)` — structured JSON log (takes $str only, NOT integers)

## String conversion
- `str.fromint(n:i64):$str` — convert integer to string
- `str.fromfloat(n:f64):$str` — convert float to string
- `str.toint(s:$str):i64` — parse string to integer
- `str.len(s:$str):u64` — string length (returns u64)
- `str.concat(a:$str;b:$str):$str` — concatenation (or use + operator)
- `str.split(s:$str;sep:$str):@$str` — split string
- `str.contains(s:$str;sub:$str):bool` — substring check
- `str.trim(s:$str):$str` — trim whitespace
- `str.indexof(s:$str;sub:$str):i64` — find substring (-1 if not found)
- `str.slice(s:$str;start:i64;end:i64):$str` — substring extraction
- `str.replace(s:$str;old:$str;new:$str):$str` — replace all occurrences
- `str.startswith(s:$str;prefix:$str):bool` — prefix check
- `str.endswith(s:$str;suffix:$str):bool` — suffix check
- `str.charat(s:$str;i:i64):$str` — character at index

## Array operations
- Array literal: `@(1;2;3)` or `@()` for empty
- `arr.len` — length (property, NOT method call)
- `arr.get(i)` — element at index
- Array concatenation: `arr1 + arr2` or `@(arr1;elem)`

## Loop patterns
- ONLY form: `lp(init;cond;step){body};` — C-style three-clause loop
- Example: `lp(let i=0;i<n;i=i+1){io.println(str.fromint(i))};`
- Break early with `br;` inside an `if`: `lp(let i=0;i<100;i=i+1){if(arr.get(i)=target){br}};`
- The loop variable is scoped to the loop
- There is NO while-style `lp{...}` form — always use `lp(init;cond;step){body}`

## Mutable bindings
- `let x=mut.0;` — the `mut.` prefix on the VALUE makes it mutable. Then reassign: `x=x+1;`
- `let arr=mut.@();` — mutable empty array
- WRONG: `mut x=0;` — this does NOT compile. Always use `let x=mut.VALUE;`

## CRITICAL: these DO NOT exist in toke
- No `while` keyword — use `lp`
- No `for` keyword — use `lp`
- No `%` modulo operator — use `a - (a/b)*b`
- No `!=` — use `!(a=b)`
- No `==` — use `=` for both assignment and equality
- No ternary `?:` — use `if(cond){a}el{b}`
- No `&&` or `||` — toke has no short-circuit boolean operators
- No `null` or `nil` — use match on result types
- No `import` keyword — use `i=alias:path;`

# Your role in this conversation

You are reviewing and fixing toke programs. I will give you:
1. A task description (what the program should do)
2. The current toke source code
3. Any compiler errors

Fix the code to compile cleanly. If you need to ask about available library functions, ask.
Output ONLY the complete toke source code, starting with `m=`. No explanation, no fences.
"""

# System prompt as a cached block for Anthropic API
SYSTEM_PROMPT_CACHED = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT_TEXT,
        "cache_control": {"type": "ephemeral"},
    }
]


# ---------------------------------------------------------------------------
# Token / cost tracking
# ---------------------------------------------------------------------------

class UsageTracker:
    """Accumulates token usage across all API calls. Thread-safe."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0
        self.calls = 0

    def record(self, usage):
        """Record usage from an Anthropic API response."""
        with self._lock:
            self.calls += 1
            self.input_tokens += getattr(usage, "input_tokens", 0)
            self.output_tokens += getattr(usage, "output_tokens", 0)
            self.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0)
            self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0)

    def cost(self) -> dict:
        """Calculate cost at Sonnet 4.6 rates."""
        input_cost = self.input_tokens * 3 / 1_000_000
        output_cost = self.output_tokens * 15 / 1_000_000
        cache_write_cost = self.cache_creation_input_tokens * 3.75 / 1_000_000
        cache_read_cost = self.cache_read_input_tokens * 0.30 / 1_000_000
        total = input_cost + output_cost + cache_write_cost + cache_read_cost
        return {
            "input_cost": input_cost,
            "output_cost": output_cost,
            "cache_write_cost": cache_write_cost,
            "cache_read_cost": cache_read_cost,
            "total": total,
        }

    def summary(self) -> str:
        c = self.cost()
        return (
            f"API calls:           {self.calls}\n"
            f"Input tokens:        {self.input_tokens:,}\n"
            f"Output tokens:       {self.output_tokens:,}\n"
            f"Cache write tokens:  {self.cache_creation_input_tokens:,}\n"
            f"Cache read tokens:   {self.cache_read_input_tokens:,}\n"
            f"Cost — input:        ${c['input_cost']:.2f}\n"
            f"Cost — output:       ${c['output_cost']:.2f}\n"
            f"Cost — cache write:  ${c['cache_write_cost']:.2f}\n"
            f"Cost — cache read:   ${c['cache_read_cost']:.2f}\n"
            f"Cost — TOTAL:        ${c['total']:.2f}"
        )

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            **self.cost(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compile_check(source: str) -> tuple[bool, str]:
    """Run tkc --check, return (passed, diagnostics)."""
    fd, path = tempfile.mkstemp(suffix=".tk")
    with os.fdopen(fd, "w") as f:
        f.write(source)
    try:
        r = subprocess.run(
            [TKC, "--check", "--diag-json", path],
            capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        diag = (r.stdout + r.stderr).strip()
        return r.returncode == 0, diag
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    finally:
        os.unlink(path)


def full_compile(source: str) -> tuple[bool, str, str]:
    """Full compile to binary. Returns (passed, bin_path, diagnostics)."""
    fd, src_path = tempfile.mkstemp(suffix=".tk")
    with os.fdopen(fd, "w") as f:
        f.write(source)
    bin_path = src_path.replace(".tk", "_bin")
    try:
        r = subprocess.run(
            [TKC, src_path, "--out", bin_path],
            capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        diag = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            return True, bin_path, diag
        return False, "", diag
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, "", str(e)
    finally:
        os.unlink(src_path)


def _sandbox_preexec():
    """Set resource limits for sandboxed execution."""
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    except (ValueError, OSError):
        pass


def execute_sandboxed(bin_path: str) -> tuple[bool, int, str, str]:
    """Run binary with sandbox. Returns (ran, exit_code, stdout, stderr)."""
    try:
        r = subprocess.run(
            [bin_path],
            capture_output=True, text=True, timeout=RUN_TIMEOUT,
            preexec_fn=_sandbox_preexec,
            encoding="utf-8", errors="replace",
        )
        return True, r.returncode, r.stdout.rstrip("\n"), r.stderr
    except subprocess.TimeoutExpired:
        return True, -1, "", "timeout"
    except OSError as e:
        return False, -1, "", str(e)
    finally:
        try:
            os.unlink(bin_path)
        except OSError:
            pass


def extract_toke(response: str) -> str:
    """Extract toke source from LLM response."""
    m = re.search(r"```(?:toke|tk)?\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    lines = response.strip().split("\n")
    code_lines = []
    in_code = False
    for line in lines:
        if line.strip().startswith("m="):
            in_code = True
        if in_code:
            code_lines.append(line)
    return "\n".join(code_lines).strip() if code_lines else response.strip()


def phase2_clean(source: str) -> str:
    """Apply Phase 2 autofix if needed."""
    violations = detect_violations(source)
    if not violations:
        return source
    fixed, _, fixable = autofix_source(source)
    return fixed if fixable else source


def format_diagnostics(diag_text: str) -> str:
    """Format compiler diagnostics for the LLM — concise and actionable."""
    errors = []
    for line in diag_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                d = json.loads(line)
                stage = d.get("stage", "")
                msg = d.get("message", "")
                pos = d.get("pos", {})
                ln = pos.get("line", 0)
                col = pos.get("col", 0)
                errors.append(f"[{stage}] line {ln}:{col} — {msg}")
            except json.JSONDecodeError:
                errors.append(line[:200])
        elif "error:" in line.lower() or "undefined" in line.lower():
            errors.append(line[:200])
    return "\n".join(errors[:10]) if errors else diag_text[:500]


# ---------------------------------------------------------------------------
# Multi-turn refinement loop
# ---------------------------------------------------------------------------

def refine_record(
    client,
    task_prompt: str,
    current_source: str,
    expected_output: str | None,
    record_id: str,
    category: str,
    tracker: UsageTracker,
) -> dict:
    """Run multi-turn refinement for a single training record."""
    result = {
        "record_id": record_id,
        "category": category,
        "original_source": current_source,
        "refined_source": "",
        "turns": 0,
        "compile_passed": False,
        "ran": False,
        "output_match": False,
        "expected_output": expected_output or "",
        "actual_output": "",
        "harness_generated": False,
        "task_prompt": task_prompt,
        "conversation": [],
        "per_turn_usage": [],
    }

    initial_msg = (
        f"## Task\n{task_prompt}\n\n"
        f"## Current toke source (may have bugs)\n```\n{current_source}\n```\n\n"
        "Review this code. Fix any syntax errors, type errors, or semantic bugs. "
        "The code must compile cleanly with `tkc --check`. "
        "Output ONLY the corrected toke source, starting with `m=`."
    )

    if expected_output:
        initial_msg += (
            f"\n\n## Expected output\nWhen this program runs, it should print:\n"
            f"```\n{expected_output}\n```\n"
            "Make sure the program includes `f=main():i64{{...}};` that produces this output. "
            "Use `io.println(str.fromint(n))` to print integers, `io.println(s)` for strings. "
            "Import `i=io:std.io;` and `i=str:std.str;` as needed."
        )

    messages = [{"role": "user", "content": initial_msg}]

    for turn in range(MAX_TURNS):
        result["turns"] = turn + 1

        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM_PROMPT_CACHED,
                messages=messages,
            )
            response_text = resp.content[0].text

            # Track usage
            tracker.record(resp.usage)
            turn_usage = {
                "turn": turn + 1,
                "input_tokens": getattr(resp.usage, "input_tokens", 0),
                "output_tokens": getattr(resp.usage, "output_tokens", 0),
                "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0),
                "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
            }
            result["per_turn_usage"].append(turn_usage)

        except Exception as e:
            result["conversation"].append({"role": "error", "content": str(e)})
            break

        code = extract_toke(response_text)
        code = phase2_clean(code)

        result["conversation"].append({"role": "assistant", "content": code[:500]})

        passed, diag = compile_check(code)

        if passed:
            result["compile_passed"] = True
            result["refined_source"] = code
            print(f"    Turn {turn+1}: COMPILE OK")

            if "f=main(" not in code:
                if expected_output:
                    harness_msg = (
                        "Good, the library function compiles. Now add a `f=main():i64{...};` "
                        "that calls the function with test inputs to produce this expected output:\n"
                        f"```\n{expected_output}\n```\n"
                        "Use `io.println(str.fromint(result))` to print each integer result on its own line. "
                        "Import `i=io:std.io;` and `i=str:std.str;`. "
                        "Output the COMPLETE source including the library function AND the new main."
                    )
                else:
                    harness_msg = (
                        "Good, the library function compiles. Now add a `f=main():i64{...};` "
                        "that calls the function with representative test inputs and prints the results. "
                        "Use `io.println(str.fromint(result))` to print each integer result on its own line. "
                        "Import `i=io:std.io;` and `i=str:std.str;`. "
                        "Output the COMPLETE source including the library function AND the new main."
                    )
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content": harness_msg})
                result["harness_generated"] = True
                result["conversation"].append({"role": "user", "content": "requesting harness generation"})
                print(f"    Turn {turn+1}: compile OK (library), requesting harness...")
                continue

            comp_ok, bin_path, comp_diag = full_compile(code)
            if comp_ok and bin_path:
                ran, exit_code, stdout, stderr = execute_sandboxed(bin_path)
                result["ran"] = ran
                result["actual_output"] = stdout

                if expected_output and stdout == expected_output.rstrip("\n"):
                    result["output_match"] = True
                    print(f"    OUTPUT MATCH!")
                    break
                elif expected_output:
                    mismatch_msg = (
                        f"The code compiles and runs but produces wrong output.\n"
                        f"Expected:\n```\n{expected_output}\n```\n"
                        f"Actual:\n```\n{stdout if stdout else '(empty — no output printed)'}\n```\n"
                    )
                    if stderr:
                        mismatch_msg += f"Stderr:\n```\n{stderr[:300]}\n```\n"
                    if exit_code != 0:
                        mismatch_msg += f"Exit code: {exit_code} (non-zero means crash or error)\n"
                    mismatch_msg += (
                        "Fix the logic so the output matches exactly. "
                        "Remember: use `io.println(str.fromint(n))` for integers, "
                        "`io.println(s)` for strings. Import `i=io:std.io;` and `i=str:std.str;`.\n"
                        "Output ONLY the corrected toke source."
                    )
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": mismatch_msg})
                    result["conversation"].append({"role": "user", "content": f"output mismatch: expected {len(expected_output.split(chr(10)))} lines, got '{stdout[:80]}'"})
                    print(f"    Turn {turn+1}: output mismatch, continuing...")
                    continue
                else:
                    if stdout:
                        result["output_match"] = True
                        print(f"    Turn {turn+1}: ran OK, output: {stdout[:60]}")
                    else:
                        empty_msg = (
                            "The code compiles and runs but produces no output. "
                            "Add `io.println(...)` calls in `f=main()` to print the results. "
                            "Use `io.println(str.fromint(n))` for integers. "
                            "Import `i=io:std.io;` and `i=str:std.str;`.\n"
                            "Output ONLY the corrected toke source."
                        )
                        messages.append({"role": "assistant", "content": response_text})
                        messages.append({"role": "user", "content": empty_msg})
                        result["conversation"].append({"role": "user", "content": "ran but empty output"})
                        print(f"    Turn {turn+1}: ran but empty output, continuing...")
                        continue
                    break
            elif comp_diag:
                err_msg = (
                    f"The code passes `tkc --check` but full compilation failed:\n"
                    f"```\n{format_diagnostics(comp_diag)}\n```\n"
                    "Fix the issue. Output ONLY the corrected toke source."
                )
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content": err_msg})
                result["conversation"].append({"role": "user", "content": f"link error: {comp_diag[:100]}"})
                print(f"    Turn {turn+1}: link error, continuing...")
                continue
            else:
                print(f"    Turn {turn+1}: compile produced no binary, stopping")
                break
        else:
            formatted = format_diagnostics(diag)
            fix_msg = (
                f"Compilation failed with these errors:\n```\n{formatted}\n```\n"
                "Fix ALL errors. Remember:\n"
                "- No commas, use semicolons everywhere\n"
                "- No `==`, use `=` for equality\n"
                "- No `!=`, use `!(a=b)`\n"
                "- No `%`, use `a-(a/b)*b` for modulo\n"
                "- No `while`/`for`, use `lp`\n"
                "- Array literal is `@(e1;e2)`, NOT `@type{}`\n"
                "- `.len` is a property, not `.len()`\n"
                "Output ONLY the corrected toke source."
            )
            messages.append({"role": "assistant", "content": response_text})
            messages.append({"role": "user", "content": fix_msg})
            result["conversation"].append({"role": "user", "content": f"compile fail: {formatted[:150]}"})
            print(f"    Turn {turn+1}: compile fail, sending errors...")

    if not result["refined_source"]:
        result["refined_source"] = code if "code" in dir() else current_source

    return result


# ---------------------------------------------------------------------------
# Diverse sampling
# ---------------------------------------------------------------------------

def load_training_diverse(
    path: Path, max_records: int, offset: int,
    skip_categories: set[str] | None = None, seed: int = 42,
) -> list[dict]:
    """Load training records with diverse category sampling.

    Infers category from the user prompt heuristics or module name in the
    assistant source. Groups records by category, then round-robin samples
    to get even coverage.
    """
    all_records: list[dict] = []
    with open(path) as f:
        for i, line in enumerate(f):
            if i < offset:
                continue
            rec = json.loads(line)
            all_records.append(rec)

    # Infer category from module name in assistant source
    by_cat: dict[str, list[dict]] = collections.defaultdict(list)
    for rec in all_records:
        msgs = rec.get("messages", [])
        cat = "unknown"
        for m in msgs:
            if m["role"] == "assistant":
                src = m["content"]
                # Module name often encodes category: m=bifileftpad, m=acndbinsearch, etc.
                mm = re.match(r"m=([a-z]+)", src)
                if mm:
                    mod = mm.group(1)
                    # Try to infer category prefix
                    if mod.startswith("bifi"):
                        cat = "BIFI"
                    elif mod.startswith("mut"):
                        cat = "MUT"
                    elif mod.startswith("compose"):
                        cat = "COMPOSE"
                    elif mod.startswith("doc") or mod.startswith("exp"):
                        cat = "DOC"
                    elif mod.startswith("ec2"):
                        cat = "EC2"
                    elif any(mod.startswith(p) for p in ("acnd", "amth", "asrt", "astr", "aarr", "aerr")):
                        cat = "ALGO"
                    elif any(mod.startswith(p) for p in ("app", "bcmp", "comp")):
                        cat = "APP"
                    else:
                        cat = "OTHER"
                break
        rec["_category"] = cat
        if skip_categories and cat in skip_categories:
            continue
        by_cat[cat].append(rec)

    # Round-robin sample across categories
    rng = random.Random(seed)
    for cat_records in by_cat.values():
        rng.shuffle(cat_records)

    sampled: list[dict] = []
    cat_names = sorted(by_cat.keys())
    indices = {c: 0 for c in cat_names}
    while len(sampled) < max_records:
        added_any = False
        for c in cat_names:
            if indices[c] < len(by_cat[c]) and len(sampled) < max_records:
                sampled.append(by_cat[c][indices[c]])
                indices[c] += 1
                added_any = True
        if not added_any:
            break

    print(f"  Diverse sampling: {len(sampled)} records from {len(cat_names)} categories")
    for c in cat_names:
        n = min(indices[c], len(by_cat[c]))
        print(f"    {c:15s} {n:4d} / {len(by_cat[c]):5d}")
    return sampled


# ---------------------------------------------------------------------------
# Process-one helper (used by both sequential and parallel paths)
# ---------------------------------------------------------------------------

def _process_one(idx, rec, client, args, records, done_ids,
                 corpus_by_source, tracker):
    msgs = rec.get("messages", [])
    task_prompt = ""
    current_source = ""
    for msg in msgs:
        if msg["role"] == "user":
            task_prompt = msg["content"]
        elif msg["role"] == "assistant":
            current_source = msg["content"].strip()

    if not current_source:
        return None

    category = rec.get("_category", "unknown")
    record_id = f"train-{args.offset + idx}"

    if record_id in done_ids:
        return None

    corpus_rec = corpus_by_source.get(current_source)
    expected = None
    if corpus_rec:
        expected = corpus_rec.get("differential", {}).get("majority_output", "").rstrip("\n")

    print(f"\n[{idx+1}/{len(records)}] {record_id} ({category})")
    print(f"  Task: {task_prompt[:80]}...")
    print(f"  Source: {current_source[:60]}...")
    if expected:
        print(f"  Expected output: {expected[:60]}...")

    result = refine_record(
        client=client,
        task_prompt=task_prompt,
        current_source=current_source,
        expected_output=expected,
        record_id=record_id,
        category=category,
        tracker=tracker,
    )

    status = "PASS" if result["output_match"] else "COMPILE" if result["compile_passed"] else "FAIL"
    print(f"  Result: {status} in {result['turns']} turns")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--training-data", required=True, type=Path)
    parser.add_argument("--corpus", type=Path, default=None,
                        help="corpus_default.jsonl for expected outputs")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-records", type=int, default=10)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--diverse", action="store_true",
                        help="Sample evenly across categories instead of sequential")
    parser.add_argument("--skip-categories", type=str, default="",
                        help="Comma-separated categories to skip (e.g. FUZZ,MUT)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel workers (for EC2 batch runs)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip records already in output file")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    client = anthropic.Anthropic()
    tracker = UsageTracker()

    # Load completed record IDs for resume
    done_ids: set[str] = set()
    if args.resume and args.output.exists():
        with open(args.output) as f:
            for line in f:
                r = json.loads(line)
                done_ids.add(r["record_id"])
        print(f"Resume: {len(done_ids)} records already completed")

    # Load expected outputs from corpus
    corpus_by_source: dict[str, dict] = {}
    if args.corpus and args.corpus.exists():
        print(f"Loading corpus for expected outputs: {args.corpus}")
        with open(args.corpus) as f:
            for line in f:
                rec = json.loads(line)
                src = rec.get("tk_source", "").strip()
                mo = rec.get("differential", {}).get("majority_output", "")
                if src and mo:
                    corpus_by_source[src] = rec
        print(f"  Records with expected output: {len(corpus_by_source):,}")

    # Load training data
    print(f"Loading training data: {args.training_data}")
    skip_cats = set(args.skip_categories.split(",")) if args.skip_categories else set()

    if args.diverse:
        records = load_training_diverse(
            args.training_data, args.max_records, args.offset,
            skip_categories=skip_cats, seed=args.seed,
        )
    else:
        records = []
        with open(args.training_data) as f:
            for i, line in enumerate(f):
                if i < args.offset:
                    continue
                if len(records) >= args.max_records:
                    break
                rec = json.loads(line)
                rec["_category"] = "sequential"
                records.append(rec)

    print(f"  Records to process: {len(records)}")

    # -----------------------------------------------------------------------
    # Process records (sequential or parallel)
    # -----------------------------------------------------------------------

    def process_one(idx: int, rec: dict) -> dict | None:
        return _process_one(idx, rec, client, args, records, done_ids,
                            corpus_by_source, tracker)

    results = []
    t0 = time.time()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    out_mode = "a" if args.resume and done_ids else "w"
    import threading
    write_lock = threading.Lock()

    if args.workers > 1:
        # --- Parallel execution ---
        # Each worker gets its own Anthropic client to avoid connection contention
        def worker_fn(idx_rec):
            idx, rec = idx_rec
            # Per-thread client for connection isolation
            thread_client = anthropic.Anthropic()
            return _process_one(idx, rec, thread_client, args, records, done_ids,
                                corpus_by_source, tracker)

        print(f"\nRunning with {args.workers} parallel workers")
        with open(args.output, out_mode) as fout:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(worker_fn, (idx, rec)): idx
                           for idx, rec in enumerate(records)}
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result is None:
                        continue
                    with write_lock:
                        results.append(result)
                        fout.write(json.dumps(result) + "\n")
                        fout.flush()
    else:
        # --- Sequential execution ---
        with open(args.output, out_mode) as fout:
            for idx, rec in enumerate(records):
                result = process_one(idx, rec)
                if result is None:
                    continue
                results.append(result)
                fout.write(json.dumps(result) + "\n")
                fout.flush()

    elapsed = time.time() - t0

    # Summary stats
    total = len(results)
    compiled = sum(1 for r in results if r["compile_passed"])
    matched = sum(1 for r in results if r["output_match"])
    ran = sum(1 for r in results if r["ran"])
    harness = sum(1 for r in results if r["harness_generated"])
    avg_turns = sum(r["turns"] for r in results) / max(total, 1)

    # Per-category breakdown
    cat_stats: dict[str, dict] = collections.defaultdict(lambda: {"total": 0, "compiled": 0, "matched": 0})
    for r in results:
        cat = r.get("category", "unknown")
        cat_stats[cat]["total"] += 1
        if r["compile_passed"]:
            cat_stats[cat]["compiled"] += 1
        if r["output_match"]:
            cat_stats[cat]["matched"] += 1

    print(f"\n{'='*60}")
    print(f"Refinement Results")
    print(f"{'='*60}")
    print(f"Total:           {total}")
    print(f"Compile passed:  {compiled} ({100*compiled/max(total,1):.0f}%)")
    print(f"Ran:             {ran}")
    print(f"Output matched:  {matched} ({100*matched/max(total,1):.0f}%)")
    print(f"Harness gen:     {harness}")
    print(f"Avg turns:       {avg_turns:.1f}")
    print(f"Elapsed:         {elapsed:.1f}s ({elapsed/max(total,1):.1f}s/record)")

    print(f"\nPer category:")
    print(f"  {'Category':15s} {'Total':>6s} {'Compile':>8s} {'Output':>8s}")
    for cat in sorted(cat_stats.keys()):
        s = cat_stats[cat]
        print(f"  {cat:15s} {s['total']:6d} {s['compiled']:8d} {s['matched']:8d}")

    print(f"\n{tracker.summary()}")

    # Write summary alongside results
    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": total,
        "compiled": compiled,
        "matched": matched,
        "ran": ran,
        "harness": harness,
        "avg_turns": round(avg_turns, 1),
        "elapsed_s": round(elapsed, 1),
        "per_category": dict(cat_stats),
        "usage": tracker.to_dict(),
    }
    summary_path = args.output.with_suffix(".summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary: {summary_path}")
    print(f"Results: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
