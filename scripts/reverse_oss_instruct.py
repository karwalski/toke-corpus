#!/usr/bin/env python3
"""Reverse OSS-Instruct: corpus -> NL problem descriptions.

Reads existing toke corpus JSON entries and generates structured JSONL
records with natural-language problem descriptions, test cases, and
difficulty classifications.

In --dry-run mode, produces heuristic-based placeholders without calling
any external API.  With --provider, calls the specified teacher model
(future use: openai, anthropic, local).

Usage::

    python scripts/reverse_oss_instruct.py --dry-run --max-tasks 5
    python scripts/reverse_oss_instruct.py --corpus-dir corpus --output data/reverse_oss_instruct.jsonl
    python scripts/reverse_oss_instruct.py --dry-run --validate

Story 9.1.1 -- Reverse OSS-Instruct: corpus -> problem descriptions.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Toke language context for prompt templates
# ---------------------------------------------------------------------------

TOKE_LANGUAGE_CONTEXT = """\
Toke is a minimal systems language designed for token-efficient LLM output.
Key syntax (Profile 1):
- M= (module), F= (function), T= (struct), I= (import)
- `let x=mut.0` for mutable bindings, `<value` for return
- `lp(init;cond;step){body}` for loops
- `[a;b;c]` for arrays, `arr[i]` for indexing
- `;` separates statements (separator, not terminator)
- 56-character set, no underscores in identifiers
"""

TEACHER_PROMPT_TEMPLATE = """\
You are an expert programming instructor. Given the following toke source code,
produce a structured JSON object with these fields:

1. "problem_description": A clear 2-3 sentence natural language description of
   what this program does. Write it as a programming task/problem statement.
2. "test_cases": An array of at least 2 test cases, each with "input" and
   "expected_output" fields derived from analyzing the code logic.
3. "difficulty_tier": One of "beginner", "intermediate", or "advanced" based on
   code complexity (LOC, nesting depth, number of functions, use of
   structs/imports).

{toke_context}

Task ID: {task_id}

Source code:
```toke
{source_code}
```

{extra_context}

Respond with valid JSON only.
"""

# ---------------------------------------------------------------------------
# Complexity heuristics (used in dry-run mode)
# ---------------------------------------------------------------------------


def _count_nesting_depth(source: str) -> int:
    """Return maximum brace nesting depth."""
    depth = 0
    max_depth = 0
    for ch in source:
        if ch == "{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == "}":
            depth = max(depth - 1, 0)
    return max_depth


def _count_functions(source: str) -> int:
    """Count F= declarations."""
    return len(re.findall(r"\bF=", source))


def _has_structs(source: str) -> bool:
    return "T=" in source


def _has_imports(source: str) -> bool:
    return "I=" in source


def _has_loops(source: str) -> bool:
    return "lp(" in source


def _has_mutability(source: str) -> bool:
    return "mut." in source


def _line_count(source: str) -> int:
    """Approximate LOC from semicolons (toke uses ; as separator)."""
    return source.count(";") + 1


def classify_difficulty(source: str) -> str:
    """Classify difficulty tier using code complexity heuristics."""
    loc = _line_count(source)
    depth = _count_nesting_depth(source)
    funcs = _count_functions(source)
    has_struct = _has_structs(source)
    has_import = _has_imports(source)

    score = 0
    if loc > 15:
        score += 1
    if loc > 30:
        score += 1
    if depth > 2:
        score += 1
    if depth > 4:
        score += 1
    if funcs > 2:
        score += 1
    if has_struct:
        score += 1
    if has_import:
        score += 1

    if score <= 1:
        return "beginner"
    elif score <= 3:
        return "intermediate"
    else:
        return "advanced"


# ---------------------------------------------------------------------------
# Heuristic description generation (dry-run mode)
# ---------------------------------------------------------------------------

# Map category codes to human-readable descriptions
CATEGORY_DESCRIPTIONS = {
    "ARR": "array processing",
    "CND": "conditional logic",
    "ERR": "error handling",
    "MTH": "mathematical computation",
    "SRT": "sorting",
    "STR": "string processing",
    "FNC": "function composition",
    "REC": "recursion",
    "MOD": "module organization",
    "TYP": "type handling",
    "LP":  "loop-based iteration",
    "IO":  "input/output processing",
}


def _extract_category(task_id: str) -> str:
    """Extract category code from task_id like A-ARR-0001."""
    parts = task_id.split("-")
    if len(parts) >= 2:
        return parts[1] if len(parts[0]) == 1 else parts[0]
    return "UNKNOWN"


def _heuristic_description(task_id: str, source: str) -> str:
    """Generate a placeholder problem description from code analysis."""
    category = _extract_category(task_id)
    cat_desc = CATEGORY_DESCRIPTIONS.get(category, "general programming")

    features = []
    if _has_loops(source):
        features.append("iteration")
    if _has_mutability(source):
        features.append("mutable state")
    if _has_structs(source):
        features.append("struct definitions")
    if _has_imports(source):
        features.append("module imports")
    if _count_functions(source) > 1:
        features.append(f"{_count_functions(source)} functions")
    if "arr[" in source or ":[" in source:
        features.append("array indexing")

    feat_str = ", ".join(features) if features else "basic operations"

    module_match = re.search(r"M=(\w+)", source)
    module_name = module_match.group(1) if module_match else "program"

    return (
        f"Write a toke program that implements a {cat_desc} task. "
        f"The module '{module_name}' should use {feat_str}. "
        f"The solution demonstrates {cat_desc} patterns in the toke language."
    )


def _heuristic_test_cases(
    task_id: str, source: str, differential: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Generate test cases from differential testing data or heuristics."""
    cases: list[dict[str, str]] = []

    # If we have differential test output, use it
    if differential and differential.get("majority_output"):
        output_lines = str(differential["majority_output"]).split("\n")
        # First output line as case 1
        if len(output_lines) >= 1:
            cases.append({
                "input": "standard test input",
                "expected_output": output_lines[0].strip(),
            })
        # Second output line as case 2
        if len(output_lines) >= 2:
            cases.append({
                "input": "secondary test input",
                "expected_output": output_lines[1].strip(),
            })

    # Ensure at least 2 cases
    while len(cases) < 2:
        cases.append({
            "input": f"test case {len(cases) + 1}",
            "expected_output": "[requires teacher model inference]",
        })

    return cases


# ---------------------------------------------------------------------------
# Teacher-model prompt building
# ---------------------------------------------------------------------------


def build_prompt(task_id: str, source: str, extra_context: str = "") -> str:
    """Build the teacher-model prompt for a given source file."""
    return TEACHER_PROMPT_TEMPLATE.format(
        toke_context=TOKE_LANGUAGE_CONTEXT,
        task_id=task_id,
        source_code=source,
        extra_context=extra_context,
    )


def generate_description(
    task_id: str,
    source: str,
    differential: dict[str, Any] | None = None,
    provider: str = "dry-run",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Generate a structured description record for a toke source.

    In dry-run mode, uses heuristics.  With a provider, calls the
    teacher model API (not yet implemented -- raises NotImplementedError).

    Returns a dict with: problem_description, test_cases, difficulty_tier.
    """
    if dry_run or provider == "dry-run":
        return {
            "problem_description": _heuristic_description(task_id, source),
            "test_cases": _heuristic_test_cases(task_id, source, differential),
            "difficulty_tier": classify_difficulty(source),
        }

    # Future provider implementations
    if provider == "openai":
        raise NotImplementedError("OpenAI provider not yet implemented")
    elif provider == "anthropic":
        raise NotImplementedError("Anthropic provider not yet implemented")
    elif provider == "local":
        raise NotImplementedError("Local provider not yet implemented")
    else:
        raise ValueError(f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Corpus reading
# ---------------------------------------------------------------------------


def iter_corpus_entries(
    corpus_dir: Path, max_tasks: int | None = None
) -> list[dict[str, Any]]:
    """Read corpus JSON files and yield entry dicts.

    Walks all phase_*/category/ directories looking for .json files.
    """
    entries: list[dict[str, Any]] = []
    json_files = sorted(corpus_dir.rglob("*.json"))

    # Exclude manifest.json and schema.json at top level
    json_files = [
        f for f in json_files
        if f.name not in ("manifest.json", "schema.json")
    ]

    for path in json_files:
        if max_tasks is not None and len(entries) >= max_tasks:
            break
        try:
            with open(path) as fh:
                data = json.load(fh)
            # Must have tk_source to be useful
            if data.get("tk_source"):
                entries.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping {path}: {exc}", file=sys.stderr)

    return entries


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ("task_id", "problem_description", "toke_solution",
                   "test_cases", "difficulty_tier")
VALID_TIERS = ("beginner", "intermediate", "advanced")


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in record:
            errors.append(f"missing field: {field}")

    desc = record.get("problem_description", "")
    if isinstance(desc, str) and len(desc) < 20:
        errors.append(
            f"problem_description too short ({len(desc)} chars, need >= 20)"
        )

    cases = record.get("test_cases", [])
    if not isinstance(cases, list) or len(cases) < 2:
        errors.append(f"need >= 2 test_cases, got {len(cases) if isinstance(cases, list) else 0}")

    tier = record.get("difficulty_tier", "")
    if tier not in VALID_TIERS:
        errors.append(f"invalid difficulty_tier: {tier!r}")

    return errors


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_pipeline(args: argparse.Namespace) -> int:
    """Run the reverse OSS-Instruct pipeline. Returns exit code."""
    corpus_dir = Path(args.corpus_dir).resolve()
    if not corpus_dir.is_dir():
        print(f"ERROR: corpus directory not found: {corpus_dir}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    if output_path != Path("/dev/null"):
        output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Reading corpus from {corpus_dir} ...", file=sys.stderr)
    entries = iter_corpus_entries(corpus_dir, max_tasks=args.max_tasks)
    print(f"Found {len(entries)} corpus entries.", file=sys.stderr)

    if not entries:
        print("WARNING: no corpus entries found.", file=sys.stderr)
        return 0

    records: list[dict[str, Any]] = []
    validation_errors = 0

    for entry in entries:
        task_id = entry.get("task_id", entry.get("id", "unknown"))
        source = entry["tk_source"]

        # Parse differential data if available
        diff_raw = entry.get("differential")
        differential = None
        if isinstance(diff_raw, dict):
            differential = diff_raw
        elif isinstance(diff_raw, str):
            try:
                # Handle Python-repr style dicts from corpus
                differential = json.loads(diff_raw.replace("'", '"'))
            except (json.JSONDecodeError, ValueError):
                pass

        if args.dry_run:
            prompt = build_prompt(task_id, source)
            print(f"\n--- Prompt for {task_id} ---", file=sys.stderr)
            print(prompt[:500] + ("..." if len(prompt) > 500 else ""),
                  file=sys.stderr)

        desc_result = generate_description(
            task_id=task_id,
            source=source,
            differential=differential,
            provider=args.provider,
            dry_run=args.dry_run,
        )

        record = {
            "task_id": task_id,
            "problem_description": desc_result["problem_description"],
            "toke_solution": source,
            "test_cases": desc_result["test_cases"],
            "difficulty_tier": desc_result["difficulty_tier"],
        }

        if args.validate:
            errs = validate_record(record)
            if errs:
                validation_errors += 1
                print(
                    f"VALIDATION FAIL {task_id}: {'; '.join(errs)}",
                    file=sys.stderr,
                )

        records.append(record)

    # Write output
    with open(output_path, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    total = len(records)
    valid = total - validation_errors
    print(f"\nWrote {total} records to {output_path}", file=sys.stderr)

    if args.validate:
        rate = (valid / total * 100) if total else 0
        print(
            f"Validation: {valid}/{total} passed ({rate:.1f}%)",
            file=sys.stderr,
        )
        if rate < 80:
            print("WARNING: validation rate below 80% target", file=sys.stderr)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reverse OSS-Instruct: corpus -> NL problem descriptions",
    )
    parser.add_argument(
        "--corpus-dir",
        default=str(Path(__file__).resolve().parent.parent / "corpus"),
        help="Path to corpus directory (default: ../corpus/ relative to script)",
    )
    parser.add_argument(
        "--output",
        default=str(
            Path(__file__).resolve().parent.parent / "data" / "reverse_oss_instruct.jsonl"
        ),
        help="Output JSONL path (default: data/reverse_oss_instruct.jsonl)",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "local", "dry-run"],
        default="dry-run",
        help="Teacher model provider (default: dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate heuristic placeholders without calling any API",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate each generated record has all required fields",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Maximum number of corpus entries to process",
    )
    args = parser.parse_args()

    # If provider is not dry-run and --dry-run not set, warn
    if not args.dry_run and args.provider != "dry-run":
        print(
            f"NOTE: provider={args.provider} selected but not yet implemented. "
            f"Use --dry-run for heuristic mode.",
            file=sys.stderr,
        )

    return run_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
