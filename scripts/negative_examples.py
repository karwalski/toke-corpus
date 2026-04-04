#!/usr/bin/env python3
"""Generate negative (broken) program variants for contrastive learning.

Extends the mutation catalogue from 9.1.4 (execution_feedback.py) with more
subtle, semantically meaningful errors: off-by-one, logic inversion, missing
return, wrong operator, scope errors, type confusion, and semicolon errors.

Each negative example is a contrastive pair of (broken_source, fixed_source)
with diagnostics and difficulty rating, targeting 10-15% of the training corpus.

Usage::

    python scripts/negative_examples.py \\
        --corpus-dir corpus/ \\
        --output data/negative_examples.jsonl \\
        --target-ratio 0.12 --max-entries 20 --seed 42

Story 10.7.5 -- Negative examples in training corpus (10-15%).
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
- `if(cond){...}` / `el{...}` for conditionals
- 56-character set, no underscores in identifiers
"""


# ---------------------------------------------------------------------------
# Mutation catalogue — subtle/semantic errors
# ---------------------------------------------------------------------------
# Each mutator returns (mutated_source, mutation_description, error_code,
# difficulty) or None if the mutation is not applicable.

# Difficulty levels: "easy", "medium", "hard"


def _mutate_off_by_one_lt_to_lte(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Change `<` (comparison) to `<=` — off-by-one boundary error."""
    # Find comparison `<` but not `<=` and not `<` used as return
    matches = list(re.finditer(r"(?<![<])(<)(?!=)(?![a-zA-Z])", src))
    # Filter out return statements: `<value` at start or after `{` or `;`
    comparison_matches = []
    for m in matches:
        pos = m.start()
        # Look backward for context — returns are `<value` after { or ; or start
        before = src[:pos].rstrip()
        if before and before[-1] in "{;(":
            # This is likely a return `<value`, skip
            continue
        if pos == 0:
            continue
        comparison_matches.append(m)
    if not comparison_matches:
        return None
    m = rng.choice(comparison_matches)
    broken = src[: m.start()] + "<=" + src[m.end() :]
    return broken, "off-by-one: changed < to <= at position {}".format(m.start()), "E5001", "hard"


def _mutate_off_by_one_index(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Change `i+1` to `i` or `i-1` to `i` — off-by-one index error."""
    matches = list(re.finditer(r"([a-z][a-z0-9]*)\s*([+-])\s*1", src))
    if not matches:
        return None
    m = rng.choice(matches)
    var_name = m.group(1)
    broken = src[: m.start()] + var_name + src[m.end() :]
    return (
        broken,
        "off-by-one: removed {} 1 from '{}' at position {}".format(
            m.group(2), m.group(0), m.start()
        ),
        "E5001",
        "hard",
    )


def _mutate_logic_inversion_branches(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Swap if/el branch bodies — logic inversion."""
    # Find if(...){...}el{...} patterns
    matches = list(re.finditer(
        r"(if\s*\([^)]+\)\s*\{)([^}]*)\}(\s*el\s*\{)([^}]*)\}",
        src,
    ))
    if not matches:
        return None
    m = rng.choice(matches)
    if_prefix = m.group(1)
    if_body = m.group(2)
    el_prefix = m.group(3)
    el_body = m.group(4)
    # Swap bodies
    swapped = if_prefix + el_body + "}" + el_prefix + if_body + "}"
    broken = src[: m.start()] + swapped + src[m.end() :]
    if broken == src:
        return None
    return broken, "logic inversion: swapped if/el branch bodies", "E5002", "hard"


def _mutate_logic_negate_condition(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Negate a condition in an if statement."""
    matches = list(re.finditer(r"if\s*\(([^)]+)\)", src))
    if not matches:
        return None
    m = rng.choice(matches)
    cond = m.group(1)
    # Simple negation strategies
    if "=" in cond and "!" not in cond:
        negated = cond.replace("=", "!=", 1)
    elif "<" in cond:
        negated = cond.replace("<", ">=", 1)
    elif ">" in cond:
        negated = cond.replace(">", "<=", 1)
    elif "!=" in cond:
        negated = cond.replace("!=", "=", 1)
    else:
        return None
    if negated == cond:
        return None
    broken = src[: m.start()] + "if(" + negated + ")" + src[m.end() :]
    return broken, "logic inversion: negated condition '{}' to '{}'".format(cond, negated), "E5002", "medium"


def _mutate_missing_return(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Remove a `<` return statement."""
    # Find return statements: `<value` or `<expr`
    matches = list(re.finditer(r"<([^=<][^;{}]*)", src))
    if not matches:
        return None
    m = rng.choice(matches)
    # Remove the return marker `<` but keep the expression as a dead statement
    broken = src[: m.start()] + m.group(1) + src[m.end() :]
    return broken, "missing return: removed < at position {}".format(m.start()), "E5003", "medium"


def _mutate_wrong_operator_arith(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Swap arithmetic operator: + to -, * to /, etc."""
    swaps = {"+": "-", "-": "+", "*": "/", "/": "*"}
    # Find arithmetic operators not inside comments or strings
    matches = list(re.finditer(r"(?<=[a-z0-9)\]])\s*([+\-*/])\s*(?=[a-z0-9(])", src))
    if not matches:
        return None
    m = rng.choice(matches)
    op = m.group(1)
    if op not in swaps:
        return None
    new_op = swaps[op]
    # Replace only the operator character within the match
    op_pos = m.start(1)
    broken = src[:op_pos] + new_op + src[op_pos + 1 :]
    return (
        broken,
        "wrong operator: changed '{}' to '{}' at position {}".format(op, new_op, op_pos),
        "E5004",
        "medium",
    )


def _mutate_scope_use_before_decl(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Insert a use of a variable before its declaration — scope error."""
    matches = list(re.finditer(r"let\s+([a-z][a-z0-9]*)\s*=", src))
    if not matches:
        return None
    m = rng.choice(matches)
    var_name = m.group(1)
    # Insert a usage of the variable before the let statement
    insert_pos = m.start()
    usage = var_name + "+0;"
    broken = src[:insert_pos] + usage + src[insert_pos:]
    return (
        broken,
        "scope error: used '{}' before declaration at position {}".format(var_name, insert_pos),
        "E3011",
        "easy",
    )


def _mutate_type_confusion(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Replace an integer literal with a string literal — type confusion."""
    matches = list(re.finditer(r"(?<=[=;(,+\-*/ ])\d+(?=[;),+\-*/ \]])", src))
    if not matches:
        return None
    m = rng.choice(matches)
    num = m.group(0)
    broken = src[: m.start()] + '"' + num + '"' + src[m.end() :]
    return (
        broken,
        "type confusion: replaced integer {} with string \"{}\" at position {}".format(
            num, num, m.start()
        ),
        "E4010",
        "easy",
    )


def _mutate_semicolon_missing(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Remove a semicolon — missing separator."""
    positions = [i for i, ch in enumerate(src) if ch == ";"]
    if not positions:
        return None
    pos = rng.choice(positions)
    broken = src[:pos] + src[pos + 1 :]
    return broken, "semicolon error: removed semicolon at position {}".format(pos), "E2001", "easy"


def _mutate_semicolon_extra(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Add an extra semicolon after a closing brace — extra separator."""
    positions = [i for i, ch in enumerate(src) if ch == "}"]
    if not positions:
        return None
    pos = rng.choice(positions)
    broken = src[: pos + 1] + ";" + src[pos + 1 :]
    return (
        broken,
        "semicolon error: added extra semicolon after }} at position {}".format(pos),
        "E2001",
        "easy",
    )


def _mutate_semicolon_misplaced(
    src: str, rng: random.Random,
) -> tuple[str, str, str, str] | None:
    """Move a semicolon to the wrong position — misplaced separator."""
    positions = [i for i, ch in enumerate(src) if ch == ";"]
    if len(positions) < 2:
        return None
    pos = rng.choice(positions)
    # Remove the semicolon and insert it one position to the left
    src_no_semi = src[:pos] + src[pos + 1 :]
    insert_pos = max(0, pos - 2)
    broken = src_no_semi[:insert_pos] + ";" + src_no_semi[insert_pos:]
    if broken == src:
        return None
    return (
        broken,
        "semicolon error: misplaced semicolon from position {} to {}".format(pos, insert_pos),
        "E2001",
        "medium",
    )


# ---------------------------------------------------------------------------
# Mutation categories
# ---------------------------------------------------------------------------

OFF_BY_ONE_MUTATORS = [
    _mutate_off_by_one_lt_to_lte,
    _mutate_off_by_one_index,
]

LOGIC_MUTATORS = [
    _mutate_logic_inversion_branches,
    _mutate_logic_negate_condition,
]

RETURN_MUTATORS = [
    _mutate_missing_return,
]

OPERATOR_MUTATORS = [
    _mutate_wrong_operator_arith,
]

SCOPE_MUTATORS = [
    _mutate_scope_use_before_decl,
]

TYPE_MUTATORS = [
    _mutate_type_confusion,
]

SEMICOLON_MUTATORS = [
    _mutate_semicolon_missing,
    _mutate_semicolon_extra,
    _mutate_semicolon_misplaced,
]

ALL_MUTATORS = (
    OFF_BY_ONE_MUTATORS
    + LOGIC_MUTATORS
    + RETURN_MUTATORS
    + OPERATOR_MUTATORS
    + SCOPE_MUTATORS
    + TYPE_MUTATORS
    + SEMICOLON_MUTATORS
)

MUTATION_CATEGORY_MAP: dict[str, str] = {}
for _fn in OFF_BY_ONE_MUTATORS:
    MUTATION_CATEGORY_MAP[_fn.__name__] = "off_by_one"
for _fn in LOGIC_MUTATORS:
    MUTATION_CATEGORY_MAP[_fn.__name__] = "logic_inversion"
for _fn in RETURN_MUTATORS:
    MUTATION_CATEGORY_MAP[_fn.__name__] = "missing_return"
for _fn in OPERATOR_MUTATORS:
    MUTATION_CATEGORY_MAP[_fn.__name__] = "wrong_operator"
for _fn in SCOPE_MUTATORS:
    MUTATION_CATEGORY_MAP[_fn.__name__] = "scope_error"
for _fn in TYPE_MUTATORS:
    MUTATION_CATEGORY_MAP[_fn.__name__] = "type_confusion"
for _fn in SEMICOLON_MUTATORS:
    MUTATION_CATEGORY_MAP[_fn.__name__] = "semicolon_error"


# ---------------------------------------------------------------------------
# Diagnostic simulation
# ---------------------------------------------------------------------------

ERROR_DESCRIPTIONS = {
    "E2001": "syntax error: unexpected token or missing delimiter",
    "E3011": "name resolution error: undefined or duplicate identifier",
    "E4010": "type error: incompatible types in expression or declaration",
    "E5001": "semantic error: off-by-one boundary or index error",
    "E5002": "semantic error: incorrect control flow or logic",
    "E5003": "semantic error: missing return value",
    "E5004": "semantic error: wrong arithmetic or comparison operator",
}


def simulate_diagnostic(
    error_code: str, description: str, broken_source: str,
) -> dict[str, Any]:
    """Build a diagnostic dict mimicking tkc compiler output."""
    return {
        "code": error_code,
        "severity": "error",
        "message": "{}: {}".format(
            error_code, ERROR_DESCRIPTIONS.get(error_code, "unknown error"),
        ),
        "detail": description,
        "source_length": len(broken_source),
    }


# ---------------------------------------------------------------------------
# Corpus reader (shared pattern with execution_feedback.py)
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
# Quality filter
# ---------------------------------------------------------------------------

def passes_quality_filter(broken_source: str, fixed_source: str, error_code: str) -> bool:
    """Only keep mutations that would fail to compile OR produce wrong output.

    Syntactic errors (E2001, E3011, E4010) always fail compilation.
    Semantic errors (E5001-E5004) produce wrong output but may compile.
    In both cases the mutation is valid for contrastive learning as long as
    the broken source differs from the fixed source.
    """
    if broken_source == fixed_source:
        return False
    # Syntactic/type/scope errors -> would fail compilation
    if error_code in ("E2001", "E3011", "E4010"):
        return True
    # Semantic errors -> would produce wrong output (different program behavior)
    if error_code.startswith("E5"):
        return True
    return False


# ---------------------------------------------------------------------------
# Pair generation
# ---------------------------------------------------------------------------

def generate_negative_examples(
    entries: list[dict[str, Any]],
    target_ratio: float,
    max_entries: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Generate negative examples targeting the specified ratio of the corpus."""
    corpus_size = len(entries)
    target_count = int(corpus_size * target_ratio) if max_entries == 0 else min(
        max_entries, int(corpus_size * target_ratio),
    )
    if max_entries > 0:
        target_count = max_entries

    examples: list[dict[str, Any]] = []
    # Shuffle entries so we sample broadly
    shuffled = list(entries)
    rng.shuffle(shuffled)

    attempts = 0
    entry_idx = 0

    while len(examples) < target_count and attempts < target_count * 10:
        entry = shuffled[entry_idx % len(shuffled)]
        entry_idx += 1
        attempts += 1

        task_id = entry.get("task_id", entry.get("id", "unknown"))
        original = entry["tk_source"]

        # Pick a random mutator
        mutator = rng.choice(ALL_MUTATORS)
        result = mutator(original, rng)
        if result is None:
            continue
        broken_source, mutation_desc, error_code, difficulty = result

        # Quality filter
        if not passes_quality_filter(broken_source, original, error_code):
            continue

        mutation_type = MUTATION_CATEGORY_MAP.get(mutator.__name__, "unknown")
        diagnostic = simulate_diagnostic(error_code, mutation_desc, broken_source)

        pair_id = hashlib.sha256(
            "{}:{}:{}".format(task_id, mutator.__name__, broken_source).encode(),
        ).hexdigest()[:12]

        examples.append({
            "pair_id": "neg-{}-{}".format(task_id, pair_id),
            "task_id": task_id,
            "broken_source": broken_source,
            "fixed_source": original,
            "mutation_type": mutation_type,
            "diagnostics": [diagnostic],
            "difficulty": difficulty,
            "contrastive_pair": True,
        })

    return examples


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def print_stats(
    examples: list[dict[str, Any]], corpus_size: int,
) -> None:
    """Print summary statistics to stderr."""
    total = len(examples)
    by_type: dict[str, int] = collections.Counter()
    by_difficulty: dict[str, int] = collections.Counter()

    for ex in examples:
        by_type[ex["mutation_type"]] += 1
        by_difficulty[ex["difficulty"]] += 1

    ratio = total / corpus_size if corpus_size > 0 else 0.0

    print("\n--- Negative Examples Statistics ---", file=sys.stderr)
    print("Corpus size: {}".format(corpus_size), file=sys.stderr)
    print("Negative examples generated: {}".format(total), file=sys.stderr)
    print("Ratio: {:.1%}".format(ratio), file=sys.stderr)
    print("\nBreakdown by mutation type:", file=sys.stderr)
    for mtype in sorted(by_type):
        print("  {}: {}".format(mtype, by_type[mtype]), file=sys.stderr)
    print("\nBreakdown by difficulty:", file=sys.stderr)
    for diff in ["easy", "medium", "hard"]:
        print("  {}: {}".format(diff, by_difficulty.get(diff, 0)), file=sys.stderr)
    print("", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Generate negative (broken) program variants for contrastive learning.",
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
        default=Path("data/negative_examples.jsonl"),
        help="Output JSONL path (default: %(default)s)",
    )
    ap.add_argument(
        "--target-ratio",
        type=float,
        default=0.12,
        help="Target ratio of negative examples to corpus size (default: 0.12)",
    )
    ap.add_argument(
        "--max-entries",
        type=int,
        default=0,
        help="Maximum number of negative examples (0 = use target-ratio, default: 0)",
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

    print(
        "Generating negative examples (target ratio: {:.0%}, max: {}) ...".format(
            args.target_ratio,
            args.max_entries if args.max_entries > 0 else "unlimited",
        ),
        file=sys.stderr,
    )

    # Generate negative examples
    examples = generate_negative_examples(
        entries, args.target_ratio, args.max_entries, rng,
    )

    # Write output
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(
        "Wrote {} negative examples to {}".format(len(examples), output_path),
        file=sys.stderr,
    )
    print_stats(examples, len(entries))


if __name__ == "__main__":
    main()
