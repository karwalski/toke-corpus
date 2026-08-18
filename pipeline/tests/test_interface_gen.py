"""Tests for interface-first generation pipeline — Story 9.2.3.

Covers InterfaceGenerator, BodyGenerator, and the batch runner.
All subprocess calls are mocked; no real tkc binary required.
"""
from __future__ import annotations

import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pipeline.interface_gen import (
    ALL_CATEGORIES,
    ARRAY_TYPES,
    MAP_TYPES,
    PRIMITIVE_TYPES,
    InterfaceGenerator,
    InterfaceSpec,
)
from pipeline.body_gen import BodyGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All valid toke types that can appear in signatures
VALID_TYPE_PATTERNS = (
    # Primitives
    r"i64", r"u64", r"f64", r"\$str", r"bool", r"\$void",
    # Arrays: @(T)
    r"@\(\w+\)",
    r"@\(\$\w+\)",
    # Maps: @(K:V)
    r"@\(\w+:\w+\)",
    r"@\(\$\w+:\$?\w+\)",
)

VALID_TYPE_RE = re.compile(
    r"^(?:" + "|".join(VALID_TYPE_PATTERNS) + r")$"
)


def _is_valid_toke_type(t: str) -> bool:
    """Check if a string is a valid toke Phase 2 type."""
    return bool(VALID_TYPE_RE.match(t))


def _extract_types_from_interface(iface: str) -> list[str]:
    """Extract all type annotations from an interface string."""
    types: list[str] = []
    for match in re.finditer(r"f=\w+\(([^)]*)\):([^;{]+)", iface):
        params_str, ret = match.group(1), match.group(2)
        types.append(ret.strip())
        if params_str.strip():
            for param in params_str.split(";"):
                if ":" in param:
                    _, ptype = param.split(":", 1)
                    types.append(ptype.strip())
    return types


# ---------------------------------------------------------------------------
# InterfaceGenerator — basic generation
# ---------------------------------------------------------------------------

class TestInterfaceGeneratorBasic:
    """Basic generation and syntax tests."""

    def test_generates_nonempty_string(self) -> None:
        gen = InterfaceGenerator()
        result = gen.generate_interface("A-MTH", 1, 42)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_starts_with_module_declaration(self) -> None:
        gen = InterfaceGenerator()
        result = gen.generate_interface("A-STR", 2, 100)
        assert re.match(r"^m=\w+;", result), f"Expected module decl, got: {result[:50]}"

    def test_contains_function_signature(self) -> None:
        gen = InterfaceGenerator()
        result = gen.generate_interface("A-MTH", 1, 42)
        assert re.search(r"f=\w+\([^)]*\):\w[\w$@().:]*;", result), (
            f"No function signature found in:\n{result}"
        )

    def test_function_signatures_end_with_semicolon(self) -> None:
        """Interface signatures must end with ; (no body)."""
        gen = InterfaceGenerator()
        result = gen.generate_interface("A-ARR", 3, 77)
        func_lines = [ln for ln in result.splitlines() if ln.startswith("f=")]
        assert len(func_lines) >= 1
        for line in func_lines:
            assert line.endswith(";"), f"Signature should end with semicolon: {line}"

    def test_no_function_bodies(self) -> None:
        """Interface should NOT contain function bodies (braces)."""
        gen = InterfaceGenerator()
        result = gen.generate_interface("A-CND", 2, 55)
        func_lines = [ln for ln in result.splitlines() if ln.startswith("f=")]
        for line in func_lines:
            assert "{" not in line, f"Interface should not have body: {line}"


# ---------------------------------------------------------------------------
# InterfaceGenerator — type validity
# ---------------------------------------------------------------------------

class TestInterfaceGeneratorTypes:
    """All types in generated interfaces must be valid toke types."""

    def test_all_types_valid_simple(self) -> None:
        gen = InterfaceGenerator()
        result = gen.generate_interface("A-MTH", 1, 42)
        types = _extract_types_from_interface(result)
        for t in types:
            assert _is_valid_toke_type(t), f"Invalid toke type: {t!r}"

    def test_all_types_valid_complex(self) -> None:
        gen = InterfaceGenerator()
        result = gen.generate_interface("A-ARR", 5, 99)
        types = _extract_types_from_interface(result)
        for t in types:
            assert _is_valid_toke_type(t), f"Invalid toke type: {t!r}"

    def test_array_types_at_higher_difficulty(self) -> None:
        """Difficulty 4-5 should sometimes produce array/map types."""
        gen = InterfaceGenerator()
        found_complex = False
        for seed in range(100, 150):
            result = gen.generate_interface("A-ARR", 5, seed)
            types = _extract_types_from_interface(result)
            if any("@(" in t for t in types):
                found_complex = True
                break
        assert found_complex, "Expected some array/map types at difficulty 5"


# ---------------------------------------------------------------------------
# InterfaceGenerator — difficulty scaling
# ---------------------------------------------------------------------------

class TestInterfaceGeneratorDifficulty:
    """Difficulty tier affects function count and complexity."""

    def test_difficulty_1_single_function(self) -> None:
        gen = InterfaceGenerator()
        spec = gen.generate_spec("A-MTH", 1, 42)
        assert len(spec.functions) == 1

    def test_difficulty_2_single_function(self) -> None:
        gen = InterfaceGenerator()
        spec = gen.generate_spec("A-STR", 2, 42)
        assert len(spec.functions) == 1

    def test_difficulty_3_multiple_functions(self) -> None:
        gen = InterfaceGenerator()
        spec = gen.generate_spec("A-ARR", 3, 42)
        assert 2 <= len(spec.functions) <= 3

    def test_difficulty_5_many_functions(self) -> None:
        gen = InterfaceGenerator()
        spec = gen.generate_spec("A-ERR", 5, 42)
        assert len(spec.functions) >= 3

    def test_error_types_absent_at_low_difficulty(self) -> None:
        gen = InterfaceGenerator()
        spec = gen.generate_spec("A-MTH", 1, 42)
        assert len(spec.error_types) == 0

    def test_error_types_possible_at_high_difficulty(self) -> None:
        gen = InterfaceGenerator()
        found_errors = False
        for seed in range(100, 200):
            spec = gen.generate_spec("A-ERR", 5, seed)
            if len(spec.error_types) > 0:
                found_errors = True
                break
        assert found_errors, "Expected error types at difficulty 5"


# ---------------------------------------------------------------------------
# InterfaceGenerator — determinism
# ---------------------------------------------------------------------------

class TestInterfaceGeneratorDeterminism:
    """Same (category, difficulty, seed) must produce identical output."""

    def test_same_seed_same_output(self) -> None:
        gen = InterfaceGenerator()
        a = gen.generate_interface("A-MTH", 3, 42)
        b = gen.generate_interface("A-MTH", 3, 42)
        assert a == b

    def test_different_seed_different_output(self) -> None:
        gen = InterfaceGenerator()
        a = gen.generate_interface("A-MTH", 3, 42)
        b = gen.generate_interface("A-MTH", 3, 43)
        assert a != b


# ---------------------------------------------------------------------------
# InterfaceGenerator — categories
# ---------------------------------------------------------------------------

class TestInterfaceGeneratorCategories:
    """All categories produce valid interfaces."""

    @pytest.mark.parametrize("category", ALL_CATEGORIES)
    def test_all_categories_generate(self, category: str) -> None:
        gen = InterfaceGenerator()
        result = gen.generate_interface(category, 3, 42)
        assert result.startswith("m=")
        assert "f=" in result

    def test_invalid_category_raises(self) -> None:
        gen = InterfaceGenerator()
        with pytest.raises(ValueError, match="Unknown category"):
            gen.generate_interface("X-BAD", 1, 42)

    def test_invalid_difficulty_raises(self) -> None:
        gen = InterfaceGenerator()
        with pytest.raises(ValueError, match="Difficulty"):
            gen.generate_interface("A-MTH", 0, 42)
        with pytest.raises(ValueError, match="Difficulty"):
            gen.generate_interface("A-MTH", 6, 42)


# ---------------------------------------------------------------------------
# InterfaceGenerator — rendered syntax
# ---------------------------------------------------------------------------

class TestInterfaceRenderedSyntax:
    """Rendered interface follows toke syntax rules."""

    def test_no_underscores_in_identifiers(self) -> None:
        gen = InterfaceGenerator()
        for seed in range(42, 62):
            result = gen.generate_interface("A-STR", 3, seed)
            # Check identifiers (module name, function names, param names)
            for match in re.finditer(r"(?:m=|f=)(\w+)", result):
                name = match.group(1)
                assert "_" not in name, f"Underscore in identifier: {name}"

    def test_error_type_syntax(self) -> None:
        """Error types should use e=Name{variant1;variant2;} syntax."""
        gen = InterfaceGenerator()
        for seed in range(100, 200):
            spec = gen.generate_spec("A-ERR", 5, seed)
            if spec.error_types:
                rendered = gen.render(spec)
                assert re.search(r"e=\w+\{[\w;]+\}", rendered), (
                    f"Bad error type syntax in:\n{rendered}"
                )
                break


# ---------------------------------------------------------------------------
# BodyGenerator — prompt generation
# ---------------------------------------------------------------------------

class TestBodyGeneratorPrompt:
    """Body prompt must reference the interface signatures."""

    def test_prompt_contains_interface(self) -> None:
        gen = InterfaceGenerator()
        body = BodyGenerator()
        iface = gen.generate_interface("A-MTH", 1, 42)
        prompt = body.generate_body_prompt(iface)
        assert iface.strip() in prompt

    def test_prompt_contains_syntax_rules(self) -> None:
        body = BodyGenerator()
        prompt = body.generate_body_prompt("m=test;\nf=add(a:i64;b:i64):i64;")
        assert "m=modname;" in prompt or "Module:" in prompt
        assert "<expr" in prompt or "Return:" in prompt


# ---------------------------------------------------------------------------
# BodyGenerator — response parsing
# ---------------------------------------------------------------------------

class TestBodyGeneratorParsing:
    """Response parsing merges bodies with interfaces correctly."""

    def test_extracts_code_block(self) -> None:
        body = BodyGenerator()
        iface = "m=test;\nf=add(a:i64;b:i64):i64;"
        response = "Here is the code:\n```toke\nm=test;\nf=add(a:i64;b:i64):i64{\n<a+b\n}\n```"
        result = body.parse_response(response, iface)
        assert "f=add(" in result
        assert "<a+b" in result

    def test_missing_function_gets_stub(self) -> None:
        body = BodyGenerator()
        iface = "m=test;\nf=add(a:i64;b:i64):i64;\nf=sub(a:i64;b:i64):i64;"
        # Response only has add, not sub
        response = "```toke\nm=test;\nf=add(a:i64;b:i64):i64{\n<a+b\n}\n```"
        result = body.parse_response(response, iface)
        assert "f=sub(" in result, "Missing function should get a stub"

    def test_module_prepended_if_missing(self) -> None:
        body = BodyGenerator()
        iface = "m=test;\nf=add(a:i64;b:i64):i64;"
        response = "```toke\nf=add(a:i64;b:i64):i64{\n<a+b\n}\n```"
        result = body.parse_response(response, iface)
        assert result.startswith("m=test;")

    def test_fallback_when_no_code_block(self) -> None:
        body = BodyGenerator()
        iface = "m=test;\nf=add(a:i64;b:i64):i64;"
        response = "m=test;\nf=add(a:i64;b:i64):i64{\n<a+b\n}"
        result = body.parse_response(response, iface)
        assert "f=add(" in result


# ---------------------------------------------------------------------------
# BodyGenerator — validation (mocked)
# ---------------------------------------------------------------------------

class TestBodyGeneratorValidation:
    """Validation calls tkc --check and interprets results."""

    @patch("pipeline.body_gen.subprocess.run")
    def test_validate_pass(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        body = BodyGenerator()
        passed, diag = body.validate("m=test;\nf=main():i64{\n<42\n}")
        assert passed is True
        assert diag == ""

    @patch("pipeline.body_gen.subprocess.run")
    def test_validate_fail(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="E1001: type mismatch")
        body = BodyGenerator()
        passed, diag = body.validate("m=test;\nf=bad():i64{\n<true\n}")
        assert passed is False
        assert "E1001" in diag

    @patch("pipeline.body_gen.subprocess.run")
    def test_validate_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tkc", timeout=10)
        body = BodyGenerator()
        passed, diag = body.validate("m=test;\nf=hang():i64{\n<0\n}")
        assert passed is False
        assert "timed out" in diag.lower() or "timeout" in diag.lower()


# ---------------------------------------------------------------------------
# Batch runner (generate_batch)
# ---------------------------------------------------------------------------

class TestBatchRunner:
    """Test the batch generation helper from the runner script."""

    def test_generate_batch_count(self) -> None:
        from scripts.run_interface_gen import generate_batch
        batch = generate_batch(10, seed=42)
        assert len(batch) == 10

    def test_generate_batch_deterministic(self) -> None:
        from scripts.run_interface_gen import generate_batch
        a = generate_batch(5, seed=42)
        b = generate_batch(5, seed=42)
        assert a == b

    def test_generate_batch_cycles_categories(self) -> None:
        from scripts.run_interface_gen import generate_batch
        batch = generate_batch(len(ALL_CATEGORIES), seed=42)
        cats = [entry[1] for entry in batch]
        assert cats == ALL_CATEGORIES
