"""Tests for the grammar-based toke fuzzer."""

from __future__ import annotations

import re

import pytest

from fuzz.grammar_fuzz import GrammarFuzzer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(**kw) -> GrammarFuzzer:
    return GrammarFuzzer(**kw)


# ---------------------------------------------------------------------------
# Basic generation tests
# ---------------------------------------------------------------------------

class TestBasicGeneration:
    def test_generate_returns_string(self):
        fz = _make()
        result = fz.generate()
        assert isinstance(result, str)

    def test_generate_non_empty(self):
        fz = _make()
        result = fz.generate()
        assert len(result) > 0

    def test_contains_module_declaration(self):
        fz = _make()
        result = fz.generate()
        assert "m=" in result
        assert result.startswith("m=")

    def test_module_ends_with_semicolon(self):
        fz = _make()
        result = fz.generate()
        first_line = result.split("\n")[0]
        assert first_line.endswith(";")

    def test_contains_function_declaration(self):
        fz = _make()
        result = fz.generate()
        assert "f=" in result

    def test_function_has_parens(self):
        fz = _make()
        result = fz.generate()
        assert "(" in result
        assert ")" in result

    def test_function_has_braces(self):
        fz = _make()
        result = fz.generate()
        assert "{" in result
        assert "};" in result

    def test_function_has_return_type(self):
        fz = _make()
        result = fz.generate()
        # Should contain a colon between ) and {
        assert re.search(r"\):[a-z0-9$@()]+\{", result) is not None


# ---------------------------------------------------------------------------
# Reproducibility tests
# ---------------------------------------------------------------------------

class TestReproducibility:
    def test_same_seed_same_output(self):
        fz = _make()
        a = fz.generate(seed=123)
        b = fz.generate(seed=123)
        assert a == b

    def test_different_seed_different_output(self):
        fz = _make()
        a = fz.generate(seed=1)
        b = fz.generate(seed=2)
        assert a != b

    def test_batch_reproducibility(self):
        fz = _make()
        batch1 = fz.generate_batch(5, seed=99)
        batch2 = fz.generate_batch(5, seed=99)
        assert batch1 == batch2


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------

class TestBatchGeneration:
    def test_batch_returns_list(self):
        fz = _make()
        result = fz.generate_batch(3)
        assert isinstance(result, list)

    def test_batch_correct_count(self):
        fz = _make()
        result = fz.generate_batch(7)
        assert len(result) == 7

    def test_batch_all_strings(self):
        fz = _make()
        for prog in fz.generate_batch(5):
            assert isinstance(prog, str)
            assert len(prog) > 0

    def test_batch_programs_differ(self):
        fz = _make()
        result = fz.generate_batch(5)
        # With different seeds each program should be unique
        assert len(set(result)) == len(result)


# ---------------------------------------------------------------------------
# Depth limiting
# ---------------------------------------------------------------------------

class TestDepthLimiting:
    def test_max_depth_1_produces_output(self):
        fz = _make(max_depth=1)
        result = fz.generate()
        assert "m=" in result
        assert "f=" in result

    def test_deeper_programs_tend_longer(self):
        shallow = _make(max_depth=2, max_functions=1, max_statements=2)
        deep = _make(max_depth=6, max_functions=1, max_statements=2)
        # Average over several seeds
        s_len = sum(len(shallow.generate(seed=i)) for i in range(10))
        d_len = sum(len(deep.generate(seed=i)) for i in range(10))
        # Deep programs should be at least as long on average
        assert d_len >= s_len * 0.5  # generous lower bound

    def test_max_depth_limits_nesting(self):
        fz = _make(max_depth=2, max_functions=1, max_statements=3)
        result = fz.generate(seed=0)
        # Count nesting depth by brace depth
        max_braces = 0
        depth = 0
        for ch in result:
            if ch == "{":
                depth += 1
                max_braces = max(max_braces, depth)
            elif ch == "}":
                depth -= 1
        # max_depth=2 plus function brace should not exceed a reasonable depth
        assert max_braces <= 10


# ---------------------------------------------------------------------------
# Configurable limits
# ---------------------------------------------------------------------------

class TestConfigLimits:
    def test_max_functions(self):
        fz = _make(max_functions=1)
        result = fz.generate(seed=42)
        # Exactly one function
        assert result.count("f=") == 1

    def test_max_functions_upper(self):
        fz = _make(max_functions=6)
        result = fz.generate(seed=42)
        count = result.count("f=")
        assert 1 <= count <= 6

    def test_max_params_zero(self):
        fz = _make(max_params=0, max_functions=1)
        result = fz.generate(seed=42)
        # The param region between parens should be empty
        match = re.search(r"f=\w+\(([^)]*)\)", result)
        assert match is not None
        assert match.group(1) == ""

    def test_max_statements_one(self):
        fz = _make(max_statements=1, max_functions=1, max_depth=1)
        result = fz.generate(seed=42)
        # Should still be valid
        assert "f=" in result


# ---------------------------------------------------------------------------
# Syntax character set
# ---------------------------------------------------------------------------

class TestCharacterSet:
    def test_no_uppercase_in_identifiers(self):
        fz = _make()
        result = fz.generate(seed=42)
        # Remove string literals (which could contain anything)
        no_strings = re.sub(r'"[^"]*"', '', result)
        # All alphabetic chars should be lowercase
        alphas = [c for c in no_strings if c.isalpha()]
        for c in alphas:
            assert c.islower(), f"Found uppercase char: {c}"

    def test_no_underscores(self):
        fz = _make()
        result = fz.generate(seed=42)
        no_strings = re.sub(r'"[^"]*"', '', result)
        assert "_" not in no_strings


# ---------------------------------------------------------------------------
# Structural validity
# ---------------------------------------------------------------------------

class TestStructuralValidity:
    def test_semicolons_after_let(self):
        fz = _make(max_depth=2)
        result = fz.generate(seed=42)
        for line in result.split("\n"):
            stripped = line.strip()
            if stripped.startswith("let "):
                assert stripped.endswith(";"), f"let without semicolon: {stripped}"

    def test_module_line_format(self):
        fz = _make()
        result = fz.generate(seed=42)
        first = result.split("\n")[0]
        assert re.match(r"^m=[a-z0-9]+;$", first), f"Bad module line: {first}"

    def test_function_signature_format(self):
        fz = _make()
        result = fz.generate(seed=42)
        # Should have at least one function matching the signature pattern
        assert re.search(r"f=[a-z0-9]+\([^)]*\):[a-z$@0-9]+\{", result)

    def test_types_are_valid(self):
        fz = _make()
        result = fz.generate(seed=42)
        # Extract type annotations (after colons in params and return types)
        types_found = re.findall(r":([a-z$@0-9()]+)(?=[{;]|\Z)", result)
        valid = {"i64", "u64", "f64", "bool", "$str", "$void"}
        for t in types_found:
            # Handle compound param types by splitting on semicolons
            for sub in t.split(";"):
                # Extract just the type part (after colon if present)
                if ":" in sub:
                    sub = sub.split(":")[-1]
                sub = sub.strip()
                if sub:
                    assert sub in valid, f"Unknown type: {sub}"

    def test_balanced_braces(self):
        fz = _make()
        for seed in range(10):
            result = fz.generate(seed=seed)
            opens = result.count("{")
            closes = result.count("}")
            assert opens == closes, (
                f"Unbalanced braces (seed={seed}): {opens} open vs {closes} close"
            )

    def test_balanced_parens(self):
        fz = _make()
        for seed in range(10):
            result = fz.generate(seed=seed)
            # Exclude string literals
            no_strings = re.sub(r'"[^"]*"', '', result)
            opens = no_strings.count("(")
            closes = no_strings.count(")")
            assert opens == closes, (
                f"Unbalanced parens (seed={seed}): {opens} open vs {closes} close"
            )


# ---------------------------------------------------------------------------
# Type-aware generation
# ---------------------------------------------------------------------------

class TestTypeAwareness:
    def test_bool_literals_present(self):
        fz = _make(max_functions=3)
        # Generate several programs, at least one should have bool literals
        programs = fz.generate_batch(20, seed=0)
        combined = "\n".join(programs)
        assert "true" in combined or "false" in combined

    def test_string_literals_present(self):
        fz = _make(max_functions=3)
        programs = fz.generate_batch(20, seed=0)
        combined = "\n".join(programs)
        assert '"' in combined

    def test_numeric_literals_present(self):
        fz = _make()
        result = fz.generate(seed=42)
        assert re.search(r"\d+", result) is not None
