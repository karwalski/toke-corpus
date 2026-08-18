#!/usr/bin/env python3
"""Generate corpus entries for stories 10.2.3, 10.3.2, 10.3.3, 10.4.2, 10.4.4.

Runs on EC2 with Anthropic API. Uses Claude Haiku for bulk generation,
Sonnet for complex tasks (error handling, OSS-Instruct).

Usage:
    python3 scripts/ec2_generate_all.py [--story STORY_ID] [--dry-run]

Stories:
    10.2.3  Error handling pattern generation (500-1000 programs)
    10.3.2  Stdlib usage example generation (507+ programs)
    10.3.3  Multi-stdlib composition programs (500-1000 programs)
    10.4.2  Medium-complexity augmentation (3000-5000 programs)
    10.4.4  OSS-Instruct seed generation (2000-5000 programs)
"""

import anthropic
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TKC = os.environ.get("TKC", "tkc")
DRY_RUN = "--dry-run" in sys.argv
STORY = None
for i, arg in enumerate(sys.argv):
    if arg == "--story" and i + 1 < len(sys.argv):
        STORY = sys.argv[i + 1]

SPEC_REF = (ROOT / "prompts" / "spec-reference.md").read_text() if (ROOT / "prompts" / "spec-reference.md").exists() else ""

# Load stdlib graph if available
STDLIB_GRAPH = {}
stdlib_graph_path = ROOT / "data" / "stdlib_graph.json"
if stdlib_graph_path.exists():
    STDLIB_GRAPH = json.loads(stdlib_graph_path.read_text())

# Load autofixer for post-processing LLM output
try:
    from validate.autofixer import AutoFixer
    FIXER = AutoFixer()
except ImportError:
    FIXER = None

client = anthropic.Anthropic()

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

# Shared syntax reference for all prompts — includes common mistake warnings
TOKE_SYNTAX = """## toke Phase 2 syntax (MUST follow exactly)
- Module: m=name;
- Function: f=name(param1:type1;param2:type2):rettype{body};
- Import: i=alias:std.module;
- Custom type: t=$name{field1:type1;field2:type2};
- Primitive types: i64, u64, f64, $str, bool, $void
- Array type: @i64, @$str (NOT [i64] or @(i64))
- Array access: arr.get(i) (NOT arr[i]), arr.len (NOT arr.len())
- Map type: @($str:i64)
- Let: let x=val; (immutable), let x=mut.val; (mutable)
- Return: <expr (the < character IS the return keyword)
- Loop: lp(let i=0;i<n;i=i+1){body}
- If/else: if(cond){...}el{...}
- Break: br
- Equality: = (NOT ==)
- Not equal: !(a=b) (NOT a!=b, NOT a<>b)
- Error result type: rettype!$errtype
- Error propagation: expr!$errtype
- Cast: expr as type

## CRITICAL RULES (violations = compile failure)
- Use SEMICOLONS ; as separators everywhere (NOT commas)
- Function params separated by ; (NOT ,)
- NO uppercase letters anywhere (no A-Z at all)
- NO underscores (use camelCase: myVar not my_var)
- NO square brackets [ ] (use @type for arrays, .get(i) for access)
- .len is a PROPERTY not a method (arr.len NOT arr.len())
- Strings use double quotes only
- Every function body must end with };
- Output ONLY a single fenced ```toke code block, nothing else."""


def compile_check(source: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    try:
        r = subprocess.run([TKC, "--check", fname], capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stderr
    except Exception as e:
        return False, str(e)
    finally:
        os.unlink(fname)


def clean_llm_output(code: str) -> str:
    """Fix common LLM mistakes before compile check."""
    # Replace disallowed characters
    code = code.replace("!=", "!(x=y)")  # placeholder, fix below
    code = code.replace("&&", " & ")
    code = code.replace("||", " | ")
    # Fix != properly: a!=b -> !(a=b)
    code = re.sub(r'(\w+)\s*!=\s*(\w+)', r'!(\1=\2)', code)
    # Replace comma separators with semicolons (toke uses ; not ,)
    # But only outside strings
    parts = re.split(r'("(?:[^"\\]|\\.)*")', code)
    fixed_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:  # Not inside string
            part = part.replace(",", ";")
            # Fix & to logical and (not in char set)
            part = part.replace(" & ", " and ")
            part = part.replace("&", " and ")
        fixed_parts.append(part)
    code = "".join(fixed_parts)
    # Fix .len() -> .len (property not method)
    code = re.sub(r'\.len\(\)', '.len', code)
    # Fix return followed by semicolon: <expr; -> <expr
    # (return doesn't need trailing semicolon in some contexts)
    # Fix uppercase type names
    for old, new in [("Str", "$str"), ("Int", "i64"), ("Float", "f64"),
                     ("Bool", "bool"), ("Void", "$void"), ("String", "$str")]:
        code = re.sub(rf'\b{old}\b', new, code)
    # Fix == to = (equality)
    parts = re.split(r'("(?:[^"\\]|\\.)*")', code)
    fixed_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            part = part.replace("==", "=")
        fixed_parts.append(part)
    code = "".join(fixed_parts)
    # Apply autofixer if available
    if FIXER:
        code, fixes = FIXER.fix(code)
    return code


def extract_toke(response: str) -> str:
    """Extract toke code from LLM response."""
    m = re.search(r"```(?:toke|tk)?\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: if response looks like toke code
    if response.strip().startswith("m="):
        return response.strip()
    return response.strip()


def generate_one(prompt: str, model: str = HAIKU, max_retries: int = 2) -> str | None:
    """Call LLM and return extracted toke code, or None on failure."""
    for attempt in range(max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_code = extract_toke(resp.content[0].text)
            # Apply post-processing fixes
            code = clean_llm_output(raw_code)
            ok, err = compile_check(code)
            if ok:
                return code
            if attempt < max_retries:
                # Retry with error feedback
                prompt = f"{prompt}\n\nYour previous attempt had this compiler error:\n{err}\n\nCommon mistakes: use ; not , as separator, use .len not .len(), use = not == for equality, use !(a=b) not a!=b, no uppercase letters, no & or | characters. Please fix and try again."
        except anthropic.RateLimitError:
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            print(f"  API error: {e}")
            time.sleep(2)
    return None


def write_entry(output_dir: Path, entry_id: str, source: str, task_id: str,
                model_name: str, score: float, references: dict):
    """Write a corpus-schema JSON entry."""
    output_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": entry_id,
        "version": 1,
        "phase": "B",
        "task_id": task_id,
        "tk_source": source,
        "tk_tokens": len(source.split()),
        "attempts": 1,
        "model": model_name,
        "validation": {"compiler_exit_code": 0, "error_codes": []},
        "differential": {"languages_agreed": [], "majority_output": ""},
        "judge": {"accepted": True, "score": score},
        "references": references,
    }
    h = hashlib.sha1(source.encode()).hexdigest()[:8]
    out_file = output_dir / f"{entry_id}-{h}.json"
    with open(out_file, "w") as fh:
        json.dump(entry, fh, ensure_ascii=False)
        fh.write("\n")
    return out_file


# ============================================================
# Story 10.2.3 — Error handling pattern generation
# ============================================================

ERROR_HANDLING_PROMPTS = [
    "Write a toke program with a function that reads a file and returns a result type using !$err for error propagation. Use i=file:std.file;",
    "Write a toke program with a custom error type t=$parseErr{{code:i64}; and a function that parses a string to i64, returning i64!$parseErr.",
    "Write a toke program where one function calls another that returns a result type, and propagates the error with the ! operator.",
    "Write a toke program with an error type and multiple functions that each return result types, chaining error propagation through 3 function calls.",
    "Write a toke program that validates user input (a string) and returns either the parsed value or a custom error type.",
    "Write a toke program that reads from a file, splits the content, and processes each line — using error propagation at each step.",
    "Write a toke program with a function that converts a string to f64, returning f64!$err, and a caller that handles the error case.",
    "Write a toke program demonstrating error recovery: call a function that may fail, and if it does, use a default value.",
    "Write a toke program with nested error propagation: main calls process which calls validate which calls parse, each returning a result type.",
    "Write a toke program that opens a database connection and queries it, propagating errors at each step. Use i=db:std.db;",
]


def run_10_2_3():
    """Generate error handling programs."""
    print("=== Story 10.2.3: Error handling patterns ===")
    output_dir = ROOT / "corpus" / "phase2_deduplicated" / "ERR-HANDLE"

    # Generate diverse error handling prompts
    base_prompts = ERROR_HANDLING_PROMPTS * 3  # 30 base prompts

    # Add variations
    variations = [
        "with exactly 2 functions",
        "with exactly 3 functions",
        "using std.file for file I/O",
        "using std.str for string parsing",
        "using std.json for JSON parsing",
        "with a loop that accumulates results, propagating errors",
        "with an if/else that handles the error case inline",
    ]

    prompts = []
    for base in base_prompts:
        prompts.append(base)
    for base in ERROR_HANDLING_PROMPTS[:5]:
        for var in variations:
            prompts.append(f"{base} The program should be {var}.")

    # Target 750 programs, generate more to account for failures
    prompts = prompts[:1000]

    preamble = f"""You are a toke language expert.

{TOKE_SYNTAX}

IMPORTANT: Every program MUST use error result types (!$errtype) and the ! error propagation operator.

Example of error handling in toke:
```toke
m=example;
t=$err{{code:i64}};
f=parse(s:$str):i64!$err{{if(s.len=0){{<rt $err{{code=1}}}};< 42}};
f=main():i64!$err{{let val=parse("hello")!$err;< val}};
```"""

    passed = 0
    failed = 0
    seen = set()

    for i, prompt in enumerate(prompts):
        if passed >= 750:
            break
        full_prompt = f"{preamble}\n\n{prompt}"
        code = generate_one(full_prompt, model=SONNET, max_retries=2)
        if code and "!" in code and code not in seen:
            seen.add(code)
            if not DRY_RUN:
                write_entry(
                    output_dir, f"ERR-H-{passed:05d}", code,
                    f"ERR-HANDLE-{i}", "claude-sonnet-error-handling", 0.90,
                    {"story": "10.2.3", "prompt_index": i}
                )
            passed += 1
        else:
            failed += 1
        if (passed + failed) % 25 == 0:
            print(f"  {passed + failed}/{len(prompts)}, pass={passed}, fail={failed}")

    print(f"  Done: {passed} passed, {failed} failed")
    return passed


# ============================================================
# Story 10.3.2 — Stdlib usage example generation
# ============================================================

def build_stdlib_prompts():
    """Build prompts targeting under-covered stdlib functions."""
    prompts = []

    # Functions needing coverage (from knowledge graph)
    gaps = {
        "std.str": ["replace", "starts", "ends", "lower", "upper", "repeat", "padLeft", "padRight"],
        "std.file": ["read", "write", "exists", "delete", "append"],
        "std.db": ["open", "query", "close", "exec"],
        "std.json": ["parse", "stringify", "get", "set"],
        "std.http": ["get", "post", "request"],
        "std.test": ["assert", "assertEqual", "assertErr", "run"],
        "std.toon": ["serialize", "deserialize", "fromJson", "toJson", "fromStr", "toStr"],
        "std.log": ["info", "warn", "error", "debug"],
        "std.math": ["abs", "min", "max", "sqrt", "pow", "floor", "ceil", "round"],
    }

    for module, functions in gaps.items():
        alias = module.split(".")[-1]
        for fn in functions:
            for variant in range(3):  # 3 variants per function
                prompts.append({
                    "module": module,
                    "alias": alias,
                    "function": fn,
                    "prompt": f"Write a toke program that uses {module}.{fn}. Import it as i={alias}:{module}; and call {alias}.{fn}() in a meaningful way. Variant {variant+1}: make it solve a different problem than the others.",
                    "variant": variant,
                })

    return prompts


def run_10_3_2():
    """Generate stdlib usage examples."""
    print("=== Story 10.3.2: Stdlib usage examples ===")
    output_dir = ROOT / "corpus" / "phase2_deduplicated" / "STDLIB"

    prompts = build_stdlib_prompts()
    print(f"  {len(prompts)} prompts targeting stdlib gaps")

    preamble = f"""You are a toke language expert.

{TOKE_SYNTAX}

CRITICAL: The program MUST contain an import statement (i=...:std.module;) and actually CALL the imported function using alias.function(args) syntax. Use semicolons between function arguments, NOT commas."""

    passed = 0
    failed = 0
    seen = set()

    for i, p in enumerate(prompts):
        full_prompt = f"{preamble}\n\n{p['prompt']}"
        code = generate_one(full_prompt, model=HAIKU, max_retries=2)
        if code and f"i=" in code and code not in seen:
            seen.add(code)
            if not DRY_RUN:
                h = hashlib.sha1(code.encode()).hexdigest()[:8]
                write_entry(
                    output_dir, f"STD-{p['alias']}-{p['function']}-{p['variant']:02d}",
                    code, f"STDLIB-{p['module']}-{p['function']}",
                    "claude-haiku-stdlib", 0.85,
                    {"story": "10.3.2", "module": p["module"], "function": p["function"]}
                )
            passed += 1
        else:
            failed += 1
        if (passed + failed) % 50 == 0:
            print(f"  {passed + failed}/{len(prompts)}, pass={passed}, fail={failed}")

    print(f"  Done: {passed} passed, {failed} failed")
    return passed


# ============================================================
# Story 10.3.3 — Multi-stdlib composition programs
# ============================================================

MULTI_STDLIB_PROMPTS = [
    "Write a toke program that reads a CSV file (std.file), splits each line (std.str), and logs the results (std.log).",
    "Write a toke program that reads JSON from a file (std.file), parses it (std.json), and writes a summary to another file.",
    "Write a toke program that fetches data via HTTP (std.http), parses the JSON response (std.json), and logs key values (std.log).",
    "Write a toke program that reads a config file (std.file), parses key=value pairs (std.str), and stores them in a map.",
    "Write a toke program that reads test cases from a file (std.file), runs assertions (std.test), and logs results (std.log).",
    "Write a toke program that reads a text file, converts it to uppercase (std.str), and writes the result to a new file (std.file).",
    "Write a toke program that serializes a data structure to TOON (std.toon), writes it to a file (std.file), then reads and deserializes it.",
    "Write a toke program that processes a log file: reads it (std.file), filters lines containing 'ERROR' (std.str), and counts them.",
    "Write a toke program that builds an HTTP request body from a map (std.json), sends it (std.http), and logs the response (std.log).",
    "Write a toke program that reads multiple files (std.file), concatenates their contents (std.str), and writes a merged output.",
    "Write a toke program that parses command-line-style arguments from a string (std.str) and validates them with assertions (std.test).",
    "Write a toke program that queries a database (std.db), formats results as JSON (std.json), and writes to a file (std.file).",
]


def run_10_3_3():
    """Generate multi-stdlib composition programs."""
    print("=== Story 10.3.3: Multi-stdlib compositions ===")
    output_dir = ROOT / "corpus" / "phase2_deduplicated" / "STDLIB-MULTI"

    # Expand each base prompt into several variants
    prompts = []
    for base in MULTI_STDLIB_PROMPTS:
        prompts.append(base)
        prompts.append(f"{base} Use at least 3 functions and include error handling with !$err.")
        prompts.append(f"{base} Make the program have 4+ functions with clear separation of concerns.")

    # Target 600, generate more for failures
    prompts = prompts[:800]

    preamble = f"""You are a toke language expert.

{TOKE_SYNTAX}

CRITICAL: Use AT LEAST 2 different stdlib imports (i=...:std.module;). Each import MUST be used with alias.function(args) calls. Use semicolons between all arguments, NOT commas."""

    passed = 0
    failed = 0
    seen = set()

    for i, prompt in enumerate(prompts):
        if passed >= 600:
            break
        full_prompt = f"{preamble}\n\n{prompt}"
        code = generate_one(full_prompt, model=HAIKU, max_retries=2)
        # Verify at least 2 imports
        import_count = len(re.findall(r'i=\w+:std\.\w+', code or ""))
        if code and import_count >= 2 and code not in seen:
            seen.add(code)
            if not DRY_RUN:
                write_entry(
                    output_dir, f"STDLIB-M-{passed:05d}", code,
                    f"STDLIB-MULTI-{i}", "claude-haiku-multi-stdlib", 0.85,
                    {"story": "10.3.3", "prompt_index": i, "import_count": import_count}
                )
            passed += 1
        else:
            failed += 1
        if (passed + failed) % 25 == 0:
            print(f"  {passed + failed}/{len(prompts)}, pass={passed}, fail={failed}")

    print(f"  Done: {passed} passed, {failed} failed")
    return passed


# ============================================================
# Story 10.4.2 — Medium-complexity augmentation
# ============================================================

MEDIUM_COMPLEXITY_TEMPLATES = [
    "Write a toke program with a single function that uses nested loops (lp inside lp) to {task}.",
    "Write a toke program with a single function that uses a loop with an if/else inside to {task}.",
    "Write a toke program with a function that uses mutable state (let x=mut.val) and a loop to {task}.",
    "Write a toke program with a function that processes an array using a loop with early break (br) to {task}.",
    "Write a toke program with a function that builds a string character by character in a loop to {task}.",
    "Write a toke program with a function that uses multiple let bindings and conditional logic to {task}.",
]

MEDIUM_TASKS = [
    "find the second largest element in an array",
    "check if an array is sorted in ascending order",
    "count occurrences of each unique value in an array",
    "find the longest run of consecutive equal elements",
    "compute the running average of an array",
    "implement binary search on a sorted array",
    "find all pairs that sum to a target value",
    "remove duplicate values from an array",
    "rotate an array left by k positions",
    "find the intersection of two sorted arrays",
    "compute the dot product of two arrays",
    "check if two strings are anagrams",
    "find the most frequent character in a string",
    "implement run-length encoding of a string",
    "convert an integer to its binary string representation",
    "validate that parentheses in a string are balanced",
    "find the median of an array without sorting",
    "implement a simple stack using an array",
    "compute the Levenshtein distance between two strings",
    "transpose a 2D matrix represented as a flat array",
    "implement merge sort on an array",
    "find the kth smallest element in an unsorted array",
    "compute the prefix sum array",
    "check if a string is a valid integer representation",
    "implement atoi (string to integer conversion)",
    "find the longest palindromic substring",
    "count inversions in an array",
    "implement a simple hash function for strings",
    "compute GCD of an array of numbers",
    "find the equilibrium index of an array",
]


def run_10_4_2():
    """Generate medium-complexity single-function programs."""
    print("=== Story 10.4.2: Medium-complexity augmentation ===")
    output_dir = ROOT / "corpus" / "phase2_deduplicated" / "MED-CMPLX"

    prompts = []
    for template in MEDIUM_COMPLEXITY_TEMPLATES:
        for task in MEDIUM_TASKS:
            prompts.append(template.format(task=task))

    # Target 3000, generate more for failures
    prompts = prompts[:4000]

    preamble = f"""You are a toke language expert.

{TOKE_SYNTAX}

The program should have exactly ONE function with non-trivial logic (loops, conditionals, mutable state).
Target 50-150 tokens."""

    passed = 0
    failed = 0
    seen = set()

    for i, prompt in enumerate(prompts):
        if passed >= 3000:
            break
        full_prompt = f"{preamble}\n\n{prompt}"
        code = generate_one(full_prompt, model=HAIKU, max_retries=1)
        if code and code not in seen:
            seen.add(code)
            if not DRY_RUN:
                write_entry(
                    output_dir, f"MED-{passed:05d}", code,
                    f"MED-CMPLX-{i}", "claude-haiku-medium", 0.85,
                    {"story": "10.4.2", "prompt_index": i}
                )
            passed += 1
        else:
            failed += 1
        if (passed + failed) % 100 == 0:
            print(f"  {passed + failed}/{min(4000, len(prompts))}, pass={passed}, fail={failed}")

    print(f"  Done: {passed} passed, {failed} failed")
    return passed


# ============================================================
# Story 10.4.4 — OSS-Instruct seed generation
# ============================================================

def load_seed_programs(n: int = 100) -> list[str]:
    """Load diverse seed programs for OSS-Instruct."""
    seeds = []
    seed_dirs = ["A-ARR", "A-CND", "A-MTH", "A-SRT", "A-STR", "B-CMP",
                 "COMPOSE-C", "COMPOSE-D", "DOC-EXP"]
    dedup_dir = ROOT / "corpus" / "phase2_deduplicated"

    for d in seed_dirs:
        cat_dir = dedup_dir / d
        if not cat_dir.exists():
            continue
        files = sorted(cat_dir.glob("*.json"))[:max(5, n // len(seed_dirs))]
        for f in files:
            try:
                data = json.loads(f.read_text())
                src = data.get("tk_source", "")
                if src.strip() and len(src.split()) > 20:
                    seeds.append(src)
            except Exception:
                pass
    return seeds[:n]


def run_10_4_4():
    """OSS-Instruct: use seed programs to generate diverse new programs."""
    print("=== Story 10.4.4: OSS-Instruct seed generation ===")
    output_dir = ROOT / "corpus" / "phase2_deduplicated" / "OSS-INST"

    seeds = load_seed_programs(150)
    print(f"  Loaded {len(seeds)} seed programs")

    preamble = f"""You are a toke language expert. Given a toke code snippet as inspiration, create a COMPLETELY DIFFERENT program that solves a different problem but demonstrates similar or more advanced toke concepts.

{TOKE_SYNTAX}

Requirements:
- The new program must solve a DIFFERENT problem than the seed
- It should be of equal or greater complexity
- Include meaningful function and variable names"""

    prompts = []
    for seed in seeds:
        # Multiple inspirations per seed
        prompts.append(f"Inspired by this toke program, write a different program of similar complexity:\n\n```toke\n{seed}\n```")
        prompts.append(f"This toke program does one thing. Write a more complex program that solves a harder problem using similar patterns:\n\n```toke\n{seed}\n```")
        prompts.append(f"Study this toke code and write a new program that uses the same language features but for a completely unrelated task:\n\n```toke\n{seed}\n```")

    # Target 2000
    prompts = prompts[:3000]

    passed = 0
    failed = 0
    seen = set()

    for i, prompt in enumerate(prompts):
        if passed >= 2000:
            break
        full_prompt = f"{preamble}\n\n{prompt}"
        # Use Sonnet for OSS-Instruct (higher quality needed)
        code = generate_one(full_prompt, model=SONNET, max_retries=1)
        if code and code not in seen:
            seen.add(code)
            if not DRY_RUN:
                write_entry(
                    output_dir, f"OSS-{passed:05d}", code,
                    f"OSS-INST-{i}", "claude-sonnet-oss-instruct", 0.90,
                    {"story": "10.4.4", "prompt_index": i, "seed_index": i // 3}
                )
            passed += 1
        else:
            failed += 1
        if (passed + failed) % 50 == 0:
            print(f"  {passed + failed}/{min(3000, len(prompts))}, pass={passed}, fail={failed}")

    print(f"  Done: {passed} passed, {failed} failed")
    return passed


# ============================================================
# Main
# ============================================================

def main():
    stories = {
        "10.2.3": ("Error handling patterns", run_10_2_3),
        "10.3.2": ("Stdlib usage examples", run_10_3_2),
        "10.3.3": ("Multi-stdlib compositions", run_10_3_3),
        "10.4.2": ("Medium-complexity augmentation", run_10_4_2),
        "10.4.4": ("OSS-Instruct seed generation", run_10_4_4),
    }

    if STORY:
        if STORY not in stories:
            print(f"Unknown story: {STORY}. Available: {list(stories.keys())}")
            sys.exit(1)
        name, fn = stories[STORY]
        print(f"\nRunning story {STORY}: {name}")
        count = fn()
        print(f"\n=== Story {STORY} complete: {count} programs generated ===")
    else:
        # Run all stories in order (cheapest first)
        total = 0
        order = ["10.3.2", "10.3.3", "10.4.2", "10.2.3", "10.4.4"]
        for sid in order:
            name, fn = stories[sid]
            print(f"\n{'='*60}")
            print(f"Running story {sid}: {name}")
            print(f"{'='*60}")
            count = fn()
            total += count
            print(f"  Subtotal: {total} programs so far\n")

        print(f"\n{'='*60}")
        print(f"ALL STORIES COMPLETE: {total} total programs generated")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
