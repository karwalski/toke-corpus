"""Error injection and repair pair generator for the toke corpus.

Story 9.1.3 -- Deliberately introduces bugs into valid toke programs
to create (broken_source, diagnostic, fixed_source) triples for
training error-recovery models.
"""

from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Types that can be swapped for type-mismatch injection
_TYPES = ["i64", "u64", "f64", "bool", "$str", "$void"]

# Arithmetic / comparison operators eligible for swapping
_ARITH_OPS = ["+", "-", "*"]
_CMP_OPS = ["<", ">", "="]

# All swappable operators (single-char only to keep edits atomic)
_ALL_OPS = _ARITH_OPS + _CMP_OPS


@dataclass
class Injection:
    """One injected error."""

    broken_source: str
    injection_type: str
    injection_details: str


# ---------------------------------------------------------------------------
# ErrorInjector
# ---------------------------------------------------------------------------


class ErrorInjector:
    """Produces single-error mutations of valid toke source code."""

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject(self, source: str) -> list[Injection]:
        """Return every applicable single-error injection for *source*.

        Each entry is an ``Injection`` with broken_source, injection_type,
        and injection_details.  The caller can recover the fixed source
        because it is always the original *source*.
        """
        results: list[Injection] = []
        for method in [
            self._type_mismatch,
            self._missing_semicolon,
            self._wrong_operator,
            self._immutable_reassignment,
            self._undefined_variable,
            self._wrong_argument_count,
        ]:
            results.extend(method(source))
        return results

    # ------------------------------------------------------------------
    # 1. Type mismatch
    # ------------------------------------------------------------------

    def _type_mismatch(self, source: str) -> list[Injection]:
        results: list[Injection] = []
        # Match type annotations like  name:i64  or  ):i64{
        # We look for   :TYPE   where TYPE is one of _TYPES
        pattern = re.compile(
            r"(?<=[:\)])(" + "|".join(re.escape(t) for t in _TYPES) + r")(?=[;{),\s]|$)"
        )
        for m in pattern.finditer(source):
            original_type = m.group(1)
            candidates = [t for t in _TYPES if t != original_type]
            replacement = self._rng.choice(candidates)
            broken = source[: m.start(1)] + replacement + source[m.end(1) :]
            # Only emit if the change produces exactly one diff
            if broken != source:
                results.append(
                    Injection(
                        broken_source=broken,
                        injection_type="type_mismatch",
                        injection_details=f"Changed {original_type} to {replacement} at pos {m.start(1)}",
                    )
                )
        return results

    # ------------------------------------------------------------------
    # 2. Missing semicolon
    # ------------------------------------------------------------------

    def _missing_semicolon(self, source: str) -> list[Injection]:
        results: list[Injection] = []
        # Find all semicolons.  We skip:
        #   - the very last semicolon (often closes the top-level function)
        #   - semicolons inside param lists  (name:type;name:type)
        # Strategy: remove each ; that is followed by something other than
        # end-of-string or '}', and that is not between '(' and ')'.
        for m in re.finditer(r";", source):
            pos = m.start()
            # Skip the final ; in the source
            if pos == len(source) - 1:
                continue
            # Skip if inside parentheses (parameter separator)
            depth = 0
            in_parens = False
            for i in range(pos):
                if source[i] == "(":
                    depth += 1
                elif source[i] == ")":
                    depth -= 1
            if depth > 0:
                continue
            # Skip if the next non-whitespace char is end-of-string
            rest = source[pos + 1 :].lstrip()
            if not rest:
                continue
            broken = source[:pos] + source[pos + 1 :]
            results.append(
                Injection(
                    broken_source=broken,
                    injection_type="missing_semicolon",
                    injection_details=f"Removed semicolon at pos {pos}",
                )
            )
        return results

    # ------------------------------------------------------------------
    # 3. Wrong operator
    # ------------------------------------------------------------------

    def _wrong_operator(self, source: str) -> list[Injection]:
        results: list[Injection] = []
        # Find arithmetic and comparison operators that are NOT part of
        # declaration syntax (=) or return (<).
        # We look for operators surrounded by word/digit chars.
        pattern = re.compile(r"(?<=[\w\d)])([+\-*<>=])(?=[\w\d(])")
        for m in pattern.finditer(source):
            op = m.group(1)
            pos = m.start(1)
            # Skip '=' when it is part of declaration (let x=..., m=..., f=...)
            # or assignment.  We only want '=' used as equality comparison
            # inside if().
            if op == "=":
                # Check if inside an if() or lp() condition
                # Simple heuristic: preceded by something like 'if(' context
                before = source[:pos]
                # Skip if this looks like assignment (preceded by 'let ...' or
                # start of line or after ';')
                stripped = before.rstrip()
                if re.search(r"(?:let\s+\w+|^[mMfFtT]|mut\.)$", stripped):
                    continue
                # Skip if after ';' with just a var name (assignment)
                if re.search(r";\s*\w+$", stripped):
                    continue
            if op == "<":
                # Skip return operator: '<' at start or after '{' or after ';'
                before = source[:pos].rstrip()
                if not before or before[-1] in "{;":
                    continue
            # Pick a different operator from the same family
            if op in _ARITH_OPS:
                candidates = [o for o in _ARITH_OPS if o != op]
            elif op in _CMP_OPS:
                candidates = [o for o in _CMP_OPS if o != op]
            else:
                continue
            replacement = self._rng.choice(candidates)
            broken = source[:pos] + replacement + source[pos + 1 :]
            if broken != source:
                results.append(
                    Injection(
                        broken_source=broken,
                        injection_type="wrong_operator",
                        injection_details=f"Changed '{op}' to '{replacement}' at pos {pos}",
                    )
                )
        return results

    # ------------------------------------------------------------------
    # 4. Immutable reassignment
    # ------------------------------------------------------------------

    def _immutable_reassignment(self, source: str) -> list[Injection]:
        results: list[Injection] = []
        # Find   let name=VALUE;   where VALUE does NOT start with 'mut.'
        pattern = re.compile(r"let\s+(\w+)\s*=\s*(?!mut\.)([^;]+);")
        for m in pattern.finditer(source):
            var_name = m.group(1)
            # Insert a reassignment right after this let statement
            insert_pos = m.end()  # just after the ';'
            reassignment = f"{var_name}=99;"
            broken = source[:insert_pos] + reassignment + source[insert_pos:]
            results.append(
                Injection(
                    broken_source=broken,
                    injection_type="immutable_reassignment",
                    injection_details=f"Added '{reassignment}' after immutable let {var_name} at pos {insert_pos}",
                )
            )
        return results

    # ------------------------------------------------------------------
    # 5. Undefined variable
    # ------------------------------------------------------------------

    def _undefined_variable(self, source: str) -> list[Injection]:
        results: list[Injection] = []
        # Find variable declarations: let name= or function params name:type
        # Then find usages of that variable and rename ONE usage.
        declared: list[tuple[str, int]] = []

        # let declarations
        for m in re.finditer(r"let\s+(\w+)\s*=", source):
            declared.append((m.group(1), m.end()))

        # function params: name:type  inside (...)
        for m in re.finditer(r"(\w+)\s*:\s*(?:" + "|".join(re.escape(t) for t in _TYPES + ["Str", "@("]) + r")", source):
            name = m.group(1)
            # Skip if name is a keyword or type
            if name in ("let", "if", "el", "lp", "br", "mut", "as"):
                continue
            declared.append((name, m.end()))

        seen_vars: set[str] = set()
        for var_name, decl_end in declared:
            if var_name in seen_vars:
                continue
            if len(var_name) < 2:
                continue  # too short to meaningfully typo
            seen_vars.add(var_name)

            # Find usages of this variable AFTER its declaration
            usage_pattern = re.compile(r"(?<![.\w])" + re.escape(var_name) + r"(?!\w*\s*[:=](?!=))")
            usages = list(usage_pattern.finditer(source, decl_end))
            if not usages:
                continue

            # Pick the first usage for determinism
            usage = usages[0]
            # Create a typo: drop the last character
            typo = var_name[:-1] + self._rng.choice("xzq")
            broken = source[: usage.start()] + typo + source[usage.end() :]
            if broken != source:
                results.append(
                    Injection(
                        broken_source=broken,
                        injection_type="undefined_variable",
                        injection_details=f"Renamed usage of '{var_name}' to '{typo}' at pos {usage.start()}",
                    )
                )
        return results

    # ------------------------------------------------------------------
    # 6. Wrong argument count
    # ------------------------------------------------------------------

    def _wrong_argument_count(self, source: str) -> list[Injection]:
        results: list[Injection] = []
        # Find function definitions to know their names
        func_pattern = re.compile(r"[fF]=(\w+)\(")
        func_names = {m.group(1) for m in func_pattern.finditer(source)}

        # Find function calls (not definitions):  name(args)
        # A call is name( that is NOT preceded by f= or F=
        call_pattern = re.compile(r"(?<![fF=])(?<!=)\b(\w+)\(([^)]*)\)")
        for m in call_pattern.finditer(source):
            fname = m.group(1)
            args_str = m.group(2)

            # Skip keywords that look like calls
            if fname in ("if", "lp", "let", "el", "br", "mut", "as"):
                continue
            # Only inject if this is a known user-defined function
            if fname not in func_names:
                continue
            # Must have at least one arg to drop or to add to
            if not args_str.strip():
                continue

            args = args_str.split(";")
            full_match_start = m.start()
            args_start = m.start(2)
            args_end = m.end(2)

            if len(args) >= 2:
                # Drop the last argument
                new_args = ";".join(args[:-1])
                broken = source[:args_start] + new_args + source[args_end:]
                results.append(
                    Injection(
                        broken_source=broken,
                        injection_type="wrong_argument_count",
                        injection_details=f"Removed last arg from {fname}() call at pos {full_match_start}, {len(args)} -> {len(args)-1}",
                    )
                )
            else:
                # Add a dummy extra argument
                new_args = args_str + ";0"
                broken = source[:args_start] + new_args + source[args_end:]
                results.append(
                    Injection(
                        broken_source=broken,
                        injection_type="wrong_argument_count",
                        injection_details=f"Added extra arg to {fname}() call at pos {full_match_start}, {len(args)} -> {len(args)+1}",
                    )
                )
        return results


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------


def batch_inject(input_dir: str | Path, output_path: str | Path) -> int:
    """Process every JSON corpus file in *input_dir* and write JSONL to *output_path*.

    Returns the number of injection pairs written.
    """
    input_dir = Path(input_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    injector = ErrorInjector(seed=42)
    count = 0

    json_files = sorted(input_dir.rglob("*.json"))
    with open(output_path, "w", encoding="utf-8") as out:
        for json_file in json_files:
            if json_file.name in ("manifest.json", "schema.json"):
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            source = data.get("tk_source")
            if not source:
                continue

            source_id = data.get("id", json_file.stem)
            validation = data.get("validation", {})
            if validation.get("compiler_exit_code", 1) != 0:
                continue  # skip programs that don't compile

            injections = injector.inject(source)
            for inj in injections:
                record = {
                    "source_id": source_id,
                    "broken_source": inj.broken_source,
                    "fixed_source": source,
                    "injection_type": inj.injection_type,
                    "injection_details": inj.injection_details,
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

    return count


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inject errors into toke corpus")
    parser.add_argument("input_dir", help="Directory containing corpus JSON files")
    parser.add_argument("output", help="Output JSONL file path")
    args = parser.parse_args()

    n = batch_inject(args.input_dir, args.output)
    print(f"Wrote {n} injection pairs to {args.output}")
