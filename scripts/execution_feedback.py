#!/usr/bin/env python3
"""Generate repair pairs by mutating valid toke programs.

Reads validated corpus entries, applies 1-3 mutations per program to produce
broken variants, and records (broken_source, diagnostics) -> fixed_source
pairs in JSONL format suitable for RLEF training.

Usage::

    python scripts/execution_feedback.py \\
        --corpus-dir corpus/ \\
        --output data/execution_feedback.jsonl \\
        --max-tasks 10 --seed 42

Story 9.1.4 -- Execution feedback annotation.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Toke syntax reference
# ---------------------------------------------------------------------------

TOKE_SYNTAX = """\
Toke syntax reference (Profile 1):
- M= (module), F= (function), T= (struct), I= (import)
- `let x=mut.0` for mutable bindings, `<value` for return
- `lp(init;cond;step){body}` for loops
- `[a;b;c]` for arrays, `arr[i]` for indexing
- `;` as statement separator (not terminator)
- 56-character set, no underscores in identifiers
"""

# ---------------------------------------------------------------------------
# Mutation catalogue
# ---------------------------------------------------------------------------

# Each mutator returns (mutated_source, mutation_description, error_code) or
# None if the mutation is not applicable to the given source.


def _mutate_remove_semicolon(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Remove a random semicolon."""
    positions = [i for i, ch in enumerate(src) if ch == ";"]
    if not positions:
        return None
    pos = rng.choice(positions)
    broken = src[:pos] + src[pos + 1:]
    return broken, "removed semicolon at position {}".format(pos), "E2001"


def _mutate_swap_brace_paren(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Swap a `{` with `(`."""
    positions = [i for i, ch in enumerate(src) if ch == "{"]
    if not positions:
        return None
    pos = rng.choice(positions)
    broken = src[:pos] + "(" + src[pos + 1:]
    return broken, "swapped {{ with ( at position {}".format(pos), "E2001"


def _mutate_remove_closing_brace(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Remove a closing `}`."""
    positions = [i for i, ch in enumerate(src) if ch == "}"]
    if not positions:
        return None
    pos = rng.choice(positions)
    broken = src[:pos] + src[pos + 1:]
    return broken, "removed closing }} at position {}".format(pos), "E2001"


def _mutate_rename_variable(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Rename a variable to an undefined name."""
    # Find `let <name>=` bindings
    matches = list(re.finditer(r"let\s+([a-z][a-z0-9]*)\s*=", src))
    if not matches:
        return None
    m = rng.choice(matches)
    var_name = m.group(1)
    # Replace the *usage* of the variable (not the declaration) with a bogus name
    bogus = "zzzundef"
    # Find occurrences of the variable after the declaration
    after_decl = src[m.end():]
    if var_name not in after_decl:
        return None
    # Replace first usage after declaration
    replaced = after_decl.replace(var_name, bogus, 1)
    broken = src[:m.end()] + replaced
    return broken, "renamed variable '{}' to undefined '{}'".format(var_name, bogus), "E3011"


def _mutate_duplicate_let(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Duplicate a `let` binding to create a redeclaration error."""
    matches = list(re.finditer(r"(let\s+[a-z][a-z0-9]*\s*=[^;{}<]+)", src))
    if not matches:
        return None
    m = rng.choice(matches)
    binding = m.group(1)
    # Insert a duplicate right before the original
    broken = src[:m.start()] + binding + ";" + src[m.start():]
    return broken, "duplicated binding '{}'".format(binding.strip()), "E3011"


def _mutate_type_swap(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Change `i64` to `str` in a declaration."""
    if "i64" not in src:
        return None
    positions = [m.start() for m in re.finditer(r"i64", src)]
    if not positions:
        return None
    pos = rng.choice(positions)
    broken = src[:pos] + "str" + src[pos + 3:]
    return broken, "changed i64 to str at position {}".format(pos), "E4010"


def _mutate_remove_cast(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Remove a type cast (`:i64`, `:str`, etc.)."""
    matches = list(re.finditer(r":[a-z][a-z0-9]*(?=[\s;,)\]{}]|$)", src))
    if not matches:
        return None
    m = rng.choice(matches)
    broken = src[:m.start()] + src[m.end():]
    return broken, "removed type annotation '{}' at position {}".format(m.group(), m.start()), "E4010"


def _mutate_keyword_loop(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Replace `lp` with `loop`."""
    if "lp(" not in src:
        return None
    broken = src.replace("lp(", "loop(", 1)
    return broken, "replaced keyword 'lp' with invalid 'loop'", "E1003"


def _mutate_keyword_else(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Replace `el` with `else`."""
    if "el{" not in src and "el {" not in src:
        return None
    broken = src.replace("el{", "else{", 1).replace("el {", "else {", 1)
    return broken, "replaced keyword 'el' with invalid 'else'", "E1003"


def _mutate_comma_for_semicolon(src: str, rng: random.Random) -> tuple[str, str, str] | None:
    """Replace a semicolon with a comma."""
    positions = [i for i, ch in enumerate(src) if ch == ";"]
    if not positions:
        return None
    pos = rng.choice(positions)
    broken = src[:pos] + "," + src[pos + 1:]
    return broken, "replaced ; with , at position {}".format(pos), "E1003"


# Organised by mutation category
SYNTAX_MUTATORS = [
    _mutate_remove_semicolon,
    _mutate_swap_brace_paren,
    _mutate_remove_closing_brace,
]

NAME_MUTATORS = [
    _mutate_rename_variable,
    _mutate_duplicate_let,
]

TYPE_MUTATORS = [
    _mutate_type_swap,
    _mutate_remove_cast,
]

KEYWORD_MUTATORS = [
    _mutate_keyword_loop,
    _mutate_keyword_else,
    _mutate_comma_for_semicolon,
]

ALL_MUTATORS = SYNTAX_MUTATORS + NAME_MUTATORS + TYPE_MUTATORS + KEYWORD_MUTATORS

MUTATION_TYPE_MAP: dict[str, str] = {}
for _fn in SYNTAX_MUTATORS:
    MUTATION_TYPE_MAP[_fn.__name__] = "syntax"
for _fn in NAME_MUTATORS:
    MUTATION_TYPE_MAP[_fn.__name__] = "name"
for _fn in TYPE_MUTATORS:
    MUTATION_TYPE_MAP[_fn.__name__] = "type"
for _fn in KEYWORD_MUTATORS:
    MUTATION_TYPE_MAP[_fn.__name__] = "keyword"


# ---------------------------------------------------------------------------
# Diagnostic simulation
# ---------------------------------------------------------------------------

ERROR_DESCRIPTIONS = {
    "E1003": "invalid character or keyword not in toke 56-char set",
    "E2001": "syntax error: unexpected token or missing delimiter",
    "E3011": "name resolution error: undefined or duplicate identifier",
    "E4010": "type error: incompatible types in expression or declaration",
}


def simulate_diagnostic(error_code: str, description: str, broken_source: str) -> dict[str, Any]:
    """Build a diagnostic dict mimicking tkc compiler output."""
    return {
        "code": error_code,
        "severity": "error",
        "message": "{}: {}".format(error_code, ERROR_DESCRIPTIONS.get(error_code, "unknown error")),
        "detail": description,
        "source_length": len(broken_source),
    }


# ---------------------------------------------------------------------------
# Corpus reader
# ---------------------------------------------------------------------------

def iter_corpus_entries(corpus_dir: Path) -> list[dict[str, Any]]:
    """Walk corpus directory tree and yield valid entries with tk_source."""
    entries: list[dict[str, Any]] = []
    for json_path in sorted(corpus_dir.rglob("*.json")):
        if json_path.name in ("manifest.json", "schema.json"):
            continue
        try:
            data = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        if "tk_source" not in data or not data["tk_source"].strip():
            continue
        # Only use entries that actually compiled successfully
        validation = data.get("validation", {})
        if validation.get("compiler_exit_code", 1) != 0:
            continue
        entries.append(data)
    return entries


# ---------------------------------------------------------------------------
# Pair generation
# ---------------------------------------------------------------------------

def generate_pairs(
    entries: list[dict[str, Any]],
    mutations_per_task: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate repair pairs for the given corpus entries."""
    pairs: list[dict[str, Any]] = []

    for entry in entries:
        task_id = entry.get("task_id", entry.get("id", "unknown"))
        original = entry["tk_source"]

        # Shuffle mutators and try to produce the requested number of mutations
        available = list(ALL_MUTATORS)
        rng.shuffle(available)

        generated = 0
        for mutator in available:
            if generated >= mutations_per_task:
                break
            result = mutator(original, rng)
            if result is None:
                continue
            broken_source, mutation_desc, error_code = result

            # Skip if the mutation produced no change
            if broken_source == original:
                continue

            pair_id = hashlib.sha256(
                "{}:{}:{}".format(task_id, mutator.__name__, broken_source).encode()
            ).hexdigest()[:12]

            mutation_type = MUTATION_TYPE_MAP.get(mutator.__name__, "unknown")
            diagnostic = simulate_diagnostic(error_code, mutation_desc, broken_source)

            pairs.append({
                "pair_id": "ef-{}-{}".format(task_id, pair_id),
                "task_id": task_id,
                "mutation_type": mutation_type,
                "mutation_description": mutation_desc,
                "broken_source": broken_source,
                "diagnostics": [diagnostic],
                "error_codes": [error_code],
                "fixed_source": original,
                "reward": 0.0,
            })

            # Also emit the fixed version as a positive RLEF example
            pairs.append({
                "pair_id": "ef-{}-{}-fix".format(task_id, pair_id),
                "task_id": task_id,
                "mutation_type": mutation_type,
                "mutation_description": "correct original for: {}".format(mutation_desc),
                "broken_source": broken_source,
                "diagnostics": [],
                "error_codes": [],
                "fixed_source": original,
                "reward": 1.0,
            })

            generated += 1

    return pairs


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def print_stats(pairs: list[dict[str, Any]]) -> None:
    """Print summary statistics to stderr."""
    total = len(pairs)
    by_type: dict[str, int] = collections.Counter()
    by_code: dict[str, int] = collections.Counter()

    for p in pairs:
        by_type[p["mutation_type"]] += 1
        for code in p["error_codes"]:
            by_code[code] += 1

    print("\n--- Execution Feedback Statistics ---", file=sys.stderr)
    print("Total pairs generated: {}".format(total), file=sys.stderr)
    print("  (broken variants: {}, fixed variants: {})".format(
        sum(1 for p in pairs if p["reward"] == 0.0),
        sum(1 for p in pairs if p["reward"] == 1.0),
    ), file=sys.stderr)
    print("\nBreakdown by mutation type:", file=sys.stderr)
    for mtype in sorted(by_type):
        print("  {}: {}".format(mtype, by_type[mtype]), file=sys.stderr)
    print("\nBreakdown by error code:", file=sys.stderr)
    for code in sorted(by_code):
        desc = ERROR_DESCRIPTIONS.get(code, "")
        print("  {} ({}): {}".format(code, desc, by_code[code]), file=sys.stderr)
    print("", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Generate execution feedback repair pairs from toke corpus."
    )
    ap.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("/Users/matthew.watt/tk/toke-corpus/corpus/"),
        help="Directory containing corpus JSON files (default: %(default)s)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("data/execution_feedback.jsonl"),
        help="Output JSONL path (default: %(default)s)",
    )
    ap.add_argument(
        "--max-tasks",
        type=int,
        default=0,
        help="Limit to first N tasks (0 = unlimited, default: 0)",
    )
    ap.add_argument(
        "--mutations-per-task",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help="Number of mutations per task (1-3, default: 2)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    rng = random.Random(args.seed)

    # Read corpus
    print("Reading corpus from {} ...".format(args.corpus_dir), file=sys.stderr)
    entries = iter_corpus_entries(args.corpus_dir)
    print("Found {} valid entries".format(len(entries)), file=sys.stderr)

    if not entries:
        print("ERROR: no valid corpus entries found", file=sys.stderr)
        sys.exit(1)

    # Sort for deterministic ordering, then shuffle with seed
    entries.sort(key=lambda e: e.get("id", ""))
    rng.shuffle(entries)

    if args.max_tasks > 0:
        entries = entries[:args.max_tasks]
    print("Processing {} tasks with {} mutations each ...".format(
        len(entries), args.mutations_per_task), file=sys.stderr)

    # Generate pairs
    pairs = generate_pairs(entries, args.mutations_per_task, rng)

    # Write output
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print("Wrote {} pairs to {}".format(len(pairs), output_path), file=sys.stderr)
    print_stats(pairs)


if __name__ == "__main__":
    main()
