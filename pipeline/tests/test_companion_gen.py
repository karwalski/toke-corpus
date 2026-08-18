"""Tests for companion-file generation and companion-to-code pipeline (Story 9.2.4)."""
from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from pipeline.companion_gen import CATEGORIES, CompanionGenerator
from pipeline.companion_to_code import CompanionToCodePipeline


# ---------------------------------------------------------------------------
# CompanionGenerator tests
# ---------------------------------------------------------------------------


class TestCompanionGenerator:
    """Tests for CompanionGenerator."""

    def setup_method(self) -> None:
        self.gen = CompanionGenerator()

    # 1. Valid markdown output
    def test_generates_valid_markdown(self) -> None:
        _, md = self.gen.generate_companion("math", 1, 100)
        assert md.startswith("# ")
        assert "## Functions" in md

    # 2. Module name returned
    def test_returns_module_name(self) -> None:
        name, _ = self.gen.generate_companion("math", 1, 42)
        assert isinstance(name, str)
        assert len(name) > 0
        assert name == "math042"

    # 3. Companion mentions toke function signatures (f=...)
    def test_companion_mentions_function_signatures(self) -> None:
        _, md = self.gen.generate_companion("string", 2, 50)
        assert re.search(r"f=\w+\(", md), "Expected toke function signature with f="

    # 4. Companion mentions toke types
    def test_companion_mentions_toke_types(self) -> None:
        _, md = self.gen.generate_companion("math", 1, 10)
        assert any(t in md for t in ["i64", "u64", "f64", "$str", "bool"])

    # 5. Determinism — same seed produces same output
    def test_determinism_same_seed(self) -> None:
        name1, md1 = self.gen.generate_companion("math", 3, 999)
        name2, md2 = self.gen.generate_companion("math", 3, 999)
        assert name1 == name2
        assert md1 == md2

    # 6. Different seeds produce different output
    def test_different_seeds_differ(self) -> None:
        _, md1 = self.gen.generate_companion("math", 3, 100)
        _, md2 = self.gen.generate_companion("math", 3, 200)
        assert md1 != md2

    # 7. Difficulty 1-2 produces single function
    def test_low_difficulty_single_function(self) -> None:
        _, md = self.gen.generate_companion("array", 1, 10)
        func_headings = re.findall(r"^### f=", md, re.MULTILINE)
        assert len(func_headings) == 1

    # 8. Difficulty 3-5 produces multiple functions
    def test_high_difficulty_multi_function(self) -> None:
        _, md = self.gen.generate_companion("math", 4, 10)
        func_headings = re.findall(r"^### f=", md, re.MULTILINE)
        assert len(func_headings) >= 2

    # 9. Difficulty 3+ includes examples section
    def test_high_difficulty_includes_examples(self) -> None:
        _, md = self.gen.generate_companion("string", 3, 77)
        assert "## Examples" in md

    # 10. Difficulty 1-2 does not include examples
    def test_low_difficulty_no_examples(self) -> None:
        _, md = self.gen.generate_companion("string", 1, 77)
        assert "## Examples" not in md

    # 11. All categories produce valid output
    @pytest.mark.parametrize("category", CATEGORIES)
    def test_all_categories(self, category: str) -> None:
        name, md = self.gen.generate_companion(category, 3, 42)
        assert name
        assert "## Functions" in md
        assert re.search(r"f=\w+\(", md)

    # 12. Invalid category raises ValueError
    def test_invalid_category_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown category"):
            self.gen.generate_companion("nosuchcat", 1, 1)

    # 13. Invalid difficulty raises ValueError
    def test_invalid_difficulty_raises(self) -> None:
        with pytest.raises(ValueError, match="Difficulty must be 1-5"):
            self.gen.generate_companion("math", 0, 1)
        with pytest.raises(ValueError, match="Difficulty must be 1-5"):
            self.gen.generate_companion("math", 6, 1)


# ---------------------------------------------------------------------------
# CompanionToCodePipeline tests
# ---------------------------------------------------------------------------


class TestCompanionToCodePipeline:
    """Tests for CompanionToCodePipeline."""

    def setup_method(self) -> None:
        self.pipeline = CompanionToCodePipeline()

    # 14. Code prompt includes companion description
    def test_prompt_includes_companion(self) -> None:
        companion = "# mymod\n\n## Functions\n\n### f=add(a:i64, b:i64):i64\n"
        prompt = self.pipeline.generate_code_prompt(companion)
        assert "f=add(a:i64, b:i64):i64" in prompt
        assert "mymod" in prompt

    # 15. Code prompt includes toke syntax rules
    def test_prompt_includes_syntax_rules(self) -> None:
        prompt = self.pipeline.generate_code_prompt("# test\n")
        assert "m=modname;" in prompt
        assert "f=name(param:type):rettype" in prompt
        assert "<expr" in prompt

    # 16. Parse response extracts toke code block
    def test_parse_response_extracts_toke_block(self) -> None:
        response = 'Here is the code:\n\n```toke\nm=mymod;\nf=add(a:i64,b:i64):i64{\n<a+b\n}\n```\n'
        code = self.pipeline.parse_response(response)
        assert code.startswith("m=mymod;")
        assert "f=add" in code

    # 17. Parse response extracts generic code block
    def test_parse_response_extracts_generic_block(self) -> None:
        response = 'Code:\n\n```\nm=mod;\n```\n'
        code = self.pipeline.parse_response(response)
        assert "m=mod;" in code

    # 18. Parse response fallback — no code block
    def test_parse_response_fallback(self) -> None:
        response = "m=mod;\nf=foo():$void{}"
        code = self.pipeline.parse_response(response)
        assert code == response.strip()

    # 19. Validate with mock tkc
    def test_validate_pass(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            passed, diag = self.pipeline.validate("m=mod;\n")
            assert passed is True
            assert diag == ""

    # 20. Validate failure returns diagnostic
    def test_validate_fail(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "error: expected ;"
            passed, diag = self.pipeline.validate("m=mod\n")
            assert passed is False
            assert "expected ;" in diag

    # 21. Process builds correct corpus entry
    def test_process_builds_entry(self) -> None:
        companion = "# mod\n\nA module.\n"
        response = "```toke\nm=mod;\nf=foo():$void{}\n```"

        with patch.object(self.pipeline, "validate", return_value=(True, "")):
            entry = self.pipeline.process(companion, response, entry_id="companion-5-001")

        assert entry is not None
        assert entry["id"] == "companion-5-001"
        assert entry["generation_method"] == "companion_to_code"
        assert entry["validation"]["tkc_check"] == "pass"
        assert entry["references"]["companion_md"] == companion
        assert "m=mod;" in entry["tk_source"]
        assert entry["tk_tokens"] > 0

    # 22. Process returns None on validation failure
    def test_process_returns_none_on_fail(self) -> None:
        with patch.object(self.pipeline, "validate", return_value=(False, "error")):
            entry = self.pipeline.process("# mod\n", "bad code")
        assert entry is None

    # 23. Process returns None on empty response
    def test_process_returns_none_on_empty(self) -> None:
        with patch.object(self.pipeline, "validate", return_value=(False, "")):
            entry = self.pipeline.process("# mod\n", "")
        assert entry is None


# ---------------------------------------------------------------------------
# Integration: generator -> pipeline prompt
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests connecting generator to pipeline."""

    # 24. Generated companion produces a valid prompt
    def test_companion_to_prompt_roundtrip(self) -> None:
        gen = CompanionGenerator()
        pipeline = CompanionToCodePipeline()

        _, companion_md = gen.generate_companion("math", 2, 123)
        prompt = pipeline.generate_code_prompt(companion_md)

        # Prompt should contain the function signature from the companion
        assert re.search(r"f=\w+\(", prompt)
        assert "toke syntax rules" in prompt.lower() or "m=modname" in prompt

    # 25. Module name is deterministic across categories
    def test_module_name_deterministic(self) -> None:
        gen = CompanionGenerator()
        for cat in CATEGORIES:
            n1, _ = gen.generate_companion(cat, 1, 42)
            n2, _ = gen.generate_companion(cat, 1, 42)
            assert n1 == n2
