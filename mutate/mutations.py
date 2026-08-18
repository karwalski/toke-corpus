"""Mutation engine for toke source code.

Applies mechanical text transforms to toke source, producing semantically
varied but syntactically valid mutations. Works directly on source text
using regex and string manipulation.
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches a function declaration: F=name(params):rettype{
_FUNC_RE = re.compile(
    r"(?P<prefix>[FfMm]=)"
    r"(?P<name>\w+)"
    r"\((?P<params>[^)]*)\)"
    r":(?P<ret>\w+)"
    r"\{"
)

# Matches a let binding: let varname=expr; or let varname=mut.expr;
_LET_RE = re.compile(r"let\s+(?P<var>[a-z]\w*)\s*=\s*(?P<rhs>[^;]+);")

# Matches a mutable let binding: let varname=mut.expr;
_LET_MUT_RE = re.compile(r"let\s+(?P<var>[a-z]\w*)\s*=\s*mut\.(?P<rhs>[^;]+);")

# Matches an immutable let binding (no mut.)
_LET_IMMUT_RE = re.compile(r"let\s+(?P<var>[a-z]\w*)\s*=\s*(?!mut\.)(?P<rhs>[^;]+);")

# Matches numeric literals (integers)
_NUM_LITERAL_RE = re.compile(r"(?<![a-zA-Z0-9_\.])(?P<num>-?\d+)(?![a-zA-Z0-9_\.])")

# Matches a return expression with a binary op: <a OP b
_RETURN_BINOP_RE = re.compile(
    r"<(?P<left>[a-zA-Z_]\w*)"
    r"(?P<op>[+\-*/])"
    r"(?P<right>[a-zA-Z_]\w*(?:[+\-*/]\w+)*)"
)

# Matches type annotations in params: name:type
_PARAM_TYPE_RE = re.compile(r"(?P<name>[a-z]\w*):(?P<type>i64|u64)")

# Matches return type annotation
_RET_TYPE_RE = re.compile(r"\):(?P<type>i64|u64)\{")


@dataclass
class Mutation:
    """A single mutation result."""

    mutated_source: str
    mutation_type: str
    mutation_details: str


class MutationEngine:
    """Applies mechanical text mutations to toke source code.

    Each mutation method returns a list of Mutation objects. The main
    ``mutate`` method aggregates results from all mutation strategies.
    """

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mutate(self, source: str) -> list[tuple[str, str, str]]:
        """Apply all mutation strategies to *source*.

        Returns a list of ``(mutated_source, mutation_type, mutation_details)``
        tuples.
        """
        results: list[tuple[str, str, str]] = []
        for m in self._all_mutations(source):
            results.append((m.mutated_source, m.mutation_type, m.mutation_details))
        return results

    # ------------------------------------------------------------------
    # Internal: collect all mutations
    # ------------------------------------------------------------------

    def _all_mutations(self, source: str) -> list[Mutation]:
        mutations: list[Mutation] = []
        mutations.extend(self._variable_renaming(source))
        mutations.extend(self._type_widening(source))
        mutations.extend(self._let_to_mut(source))
        mutations.extend(self._expression_extraction(source))
        mutations.extend(self._constant_variation(source))
        return mutations

    # ------------------------------------------------------------------
    # 1. Variable renaming
    # ------------------------------------------------------------------

    # Candidate rename maps — we pick one alternative for each variable.
    _RENAME_CANDIDATES: dict[str, list[str]] = {
        "total": ["acc", "sum", "result"],
        "acc": ["total", "accum", "result"],
        "sum": ["total", "acc", "result"],
        "result": ["res", "out", "ret"],
        "res": ["result", "out", "ret"],
        "i": ["j", "k", "idx"],
        "j": ["i", "k", "idx"],
        "k": ["i", "j", "idx"],
        "idx": ["i", "j", "k"],
        "x": ["y", "z", "v"],
        "y": ["x", "z", "w"],
        "z": ["x", "y", "w"],
        "n": ["m", "num", "val"],
        "m": ["n", "num", "val"],
        "a": ["b", "p", "v"],
        "b": ["a", "q", "w"],
        "c": ["d", "r", "u"],
        "v": ["u", "w", "val"],
        "p": ["q", "pt", "pos"],
        "q": ["p", "pt", "pos"],
        "val": ["v", "num", "tmp"],
        "num": ["val", "n", "count"],
        "count": ["cnt", "num", "total"],
        "cnt": ["count", "num", "total"],
        "tmp": ["temp", "t", "scratch"],
        "temp": ["tmp", "t", "scratch"],
        "score": ["pts", "val", "grade"],
        "name": ["label", "tag", "nm"],
        "msg": ["txt", "str", "note"],
        "arr": ["list", "vec", "seq"],
        "first": ["head", "fst", "front"],
        "last": ["tail", "lst", "back"],
        "e": ["d", "f", "g"],
        "d": ["e", "f", "g"],
    }

    def _variable_renaming(self, source: str) -> list[Mutation]:
        """Rename local variables consistently throughout the source."""
        mutations: list[Mutation] = []

        # Find all let-bound variable names
        let_vars = _LET_RE.findall(source)

        var_names: list[str] = []
        for var, _rhs in let_vars:
            if var not in var_names:
                var_names.append(var)

        # Also find function parameter names
        for match in _FUNC_RE.finditer(source):
            params_str = match.group("params")
            if params_str:
                for param_match in re.finditer(r"([a-z]\w*):", params_str):
                    pname = param_match.group(1)
                    if pname not in var_names:
                        var_names.append(pname)

        # Collect function and module names so we do NOT rename them
        reserved: set[str] = set()
        for match in _FUNC_RE.finditer(source):
            reserved.add(match.group("name"))
        # Module name
        mod_match = re.search(r"[Mm]=(\w+);", source)
        if mod_match:
            reserved.add(mod_match.group(1))
        # Type names
        for tmatch in re.finditer(r"[Tt]=(\w+)", source):
            reserved.add(tmatch.group(1))

        for var in var_names:
            if var in reserved:
                continue

            candidates = self._RENAME_CANDIDATES.get(var)
            if not candidates:
                # Generate a generic rename
                candidates = [var + "r", var + "v"]

            # Pick the first candidate that does not collide
            existing_names = set(var_names) | reserved
            new_name = None
            for c in candidates:
                if c not in existing_names:
                    new_name = c
                    break

            if new_name is None:
                continue

            # Rename: we need to replace whole-word occurrences of var with
            # new_name, but NOT inside string literals or as part of longer
            # identifiers.
            renamed = self._rename_variable(source, var, new_name)
            if renamed != source:
                mutations.append(Mutation(
                    mutated_source=renamed,
                    mutation_type="variable_rename",
                    mutation_details=f"{var} -> {new_name}",
                ))

        return mutations

    @staticmethod
    def _rename_variable(source: str, old: str, new: str) -> str:
        """Replace whole-word occurrences of *old* with *new* in source.

        Avoids renaming inside string literals and does not match partial
        identifiers.
        """
        # Split by string literals to avoid renaming inside them
        parts = re.split(r'("(?:[^"\\]|\\.)*")', source)
        pattern = re.compile(r"(?<![a-zA-Z0-9_])" + re.escape(old) + r"(?![a-zA-Z0-9_])")
        result_parts: list[str] = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # Inside a string literal — keep as-is
                result_parts.append(part)
            else:
                result_parts.append(pattern.sub(new, part))
        return "".join(result_parts)

    # ------------------------------------------------------------------
    # 2. Type widening  (i64 -> u64)
    # ------------------------------------------------------------------

    def _type_widening(self, source: str) -> list[Mutation]:
        """Widen i64 types to u64 in functions that don't use negative values."""
        mutations: list[Mutation] = []

        # Check for negative values in the source — skip if present
        if self._has_negative_values(source):
            return mutations

        if "i64" not in source:
            return mutations

        widened = source.replace("i64", "u64")
        if widened != source:
            mutations.append(Mutation(
                mutated_source=widened,
                mutation_type="type_widening",
                mutation_details="i64 -> u64 (all occurrences)",
            ))

        return mutations

    @staticmethod
    def _has_negative_values(source: str) -> bool:
        """Return True if the source contains negative numeric literals or subtraction from zero."""
        # Negative literals like -1, -42
        if re.search(r"(?<![a-zA-Z0-9_])-([ ]*)\d+", source):
            # Check if it is actually a negative literal vs subtraction
            # Look for patterns like <0-n (negation), or explicit negative numbers
            pass

        # Check for 0-n pattern (negation idiom) or explicit minus-prefixed numbers
        # in let bindings or returns
        if re.search(r"<\s*0\s*-", source):
            return True
        if re.search(r"=\s*0\s*-", source):
            return True
        # Look for negative number literals in let bindings
        if re.search(r"let\s+\w+=\s*-\d+", source):
            return True
        # negate function patterns
        if re.search(r"negate", source, re.IGNORECASE):
            return True

        return False

    # ------------------------------------------------------------------
    # 3. Let -> mut promotion
    # ------------------------------------------------------------------

    def _let_to_mut(self, source: str) -> list[Mutation]:
        """Promote immutable let bindings to mutable (add unused mutability)."""
        mutations: list[Mutation] = []

        for match in _LET_IMMUT_RE.finditer(source):
            var = match.group("var")
            rhs = match.group("rhs")
            old_binding = match.group(0)
            new_binding = f"let {var}=mut.{rhs};"

            mutated = source.replace(old_binding, new_binding, 1)
            if mutated != source:
                mutations.append(Mutation(
                    mutated_source=mutated,
                    mutation_type="let_to_mut",
                    mutation_details=f"let {var}={rhs} -> let {var}=mut.{rhs}",
                ))

        return mutations

    # ------------------------------------------------------------------
    # 4. Expression extraction
    # ------------------------------------------------------------------

    def _expression_extraction(self, source: str) -> list[Mutation]:
        """Extract a sub-expression from a return into a let binding."""
        mutations: list[Mutation] = []

        for match in _RETURN_BINOP_RE.finditer(source):
            left = match.group("left")
            op = match.group("op")
            right = match.group("right")
            full_return = match.group(0)

            # We extract the right-hand sub-expression into a temp variable.
            # For <a+b*c we extract b*c into temp.
            # For simple <a+b we extract b into temp (still valid, just trivial).
            # Check if right has another operator — prefer extracting compound
            # sub-expressions.
            sub_ops = re.findall(r"[+\-*/]", right)
            if not sub_ops:
                # right is a single variable — extract left OP right into temp
                extracted_expr = f"{left}{op}{right}"
                new_return = "<temp"
                indent = self._get_indent(source, match.start())
                new_lines = f"let temp={extracted_expr};\n{indent}{new_return}"
                mutated = source[:match.start()] + new_lines + source[match.end():]
            else:
                # right contains operators — extract right into temp
                extracted_expr = right
                new_return = f"<{left}{op}temp"
                indent = self._get_indent(source, match.start())
                new_lines = f"let temp={extracted_expr};\n{indent}{new_return}"
                mutated = source[:match.start()] + new_lines + source[match.end():]

            if mutated != source:
                mutations.append(Mutation(
                    mutated_source=mutated,
                    mutation_type="expression_extraction",
                    mutation_details=f"extracted '{extracted_expr}' into let temp",
                ))

        return mutations

    @staticmethod
    def _get_indent(source: str, pos: int) -> str:
        """Return the leading whitespace of the line containing *pos*."""
        line_start = source.rfind("\n", 0, pos)
        if line_start == -1:
            line_start = 0
        else:
            line_start += 1
        indent = ""
        for ch in source[line_start:pos]:
            if ch in (" ", "\t"):
                indent += ch
            else:
                break
        return indent

    # ------------------------------------------------------------------
    # 5. Constant variation
    # ------------------------------------------------------------------

    def _constant_variation(self, source: str) -> list[Mutation]:
        """Perturb numeric literals slightly to change semantics."""
        mutations: list[Mutation] = []

        matches = list(_NUM_LITERAL_RE.finditer(source))
        if not matches:
            return mutations

        # Collect all unique numeric literals with their values
        seen: set[str] = set()
        for match in matches:
            num_str = match.group("num")
            if num_str in seen:
                continue
            seen.add(num_str)

            num_val = int(num_str)

            # Skip 0 and 1 — too fundamental to perturb safely
            if num_val in (0, 1, -1):
                continue

            # Skip numbers that appear in loop bounds / array indices
            # (these are structural), but we still generate the mutation.
            new_val = num_val + 1

            # Replace the first occurrence of this literal
            new_source = self._replace_literal(source, num_str, str(new_val))
            if new_source != source:
                mutations.append(Mutation(
                    mutated_source=new_source,
                    mutation_type="constant_variation",
                    mutation_details=f"{num_str} -> {new_val}",
                ))

        return mutations

    @staticmethod
    def _replace_literal(source: str, old_lit: str, new_lit: str) -> str:
        """Replace a numeric literal (whole-word) in source."""
        pattern = re.compile(
            r"(?<![a-zA-Z_\.])" + re.escape(old_lit) + r"(?![a-zA-Z_\.])"
        )
        # Replace only the first occurrence
        return pattern.sub(new_lit, source, count=1)


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def batch_mutate(
    input_dir: str | Path,
    output_path: str | Path,
    seed: int | None = None,
) -> int:
    """Process all JSON corpus entries in *input_dir* and write mutations to
    *output_path* as JSONL.

    Each input JSON file must have a ``tk_source`` field (or a ``source``
    field as fallback). Returns the number of mutation records written.
    """
    input_dir = Path(input_dir)
    output_path = Path(output_path)
    engine = MutationEngine(seed=seed)
    count = 0

    # Gather input files — support both .json and .jsonl
    json_files = sorted(input_dir.glob("*.json"))
    jsonl_files = sorted(input_dir.glob("*.jsonl"))

    entries: list[dict] = []
    for jf in json_files:
        with open(jf) as f:
            data = json.load(f)
            if isinstance(data, list):
                entries.extend(data)
            else:
                entries.append(data)

    for jf in jsonl_files:
        with open(jf) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))

    with open(output_path, "w") as out:
        for entry in entries:
            source_id = entry.get("id", entry.get("source_id", "unknown"))
            source = entry.get("tk_source", entry.get("source", ""))
            if not source:
                continue

            for mutated_source, mutation_type, mutation_details in engine.mutate(source):
                record = {
                    "source_id": source_id,
                    "original_source": source,
                    "mutated_source": mutated_source,
                    "mutation_type": mutation_type,
                    "mutation_details": mutation_details,
                }
                out.write(json.dumps(record) + "\n")
                count += 1

    return count
