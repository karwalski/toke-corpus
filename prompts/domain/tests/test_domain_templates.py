"""Tests for domain-stratified generation templates and generator."""

from __future__ import annotations

import re

import pytest

from prompts.domain.domain_generator import DomainStratifiedGenerator
from prompts.domain.domain_templates import (
    DOMAINS,
    PARAM_POOLS,
    STDLIB_CONTEXT,
    VALID_TOKE_TYPES,
    DomainTemplate,
    get_all_templates,
    get_param_pool,
    get_stdlib_context,
    get_templates,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gen() -> DomainStratifiedGenerator:
    return DomainStratifiedGenerator()


@pytest.fixture
def all_templates() -> dict[str, list[DomainTemplate]]:
    return get_all_templates()


# ---------------------------------------------------------------------------
# Template coverage: each domain has >= 10 templates
# ---------------------------------------------------------------------------

class TestTemplateCoverage:

    def test_all_8_domains_present(self, all_templates: dict) -> None:
        assert set(all_templates.keys()) == set(DOMAINS)
        assert len(DOMAINS) == 8

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_domain_has_at_least_10_templates(self, domain: str) -> None:
        templates = get_templates(domain)
        assert len(templates) >= 10, (
            f"{domain} has only {len(templates)} templates (need >= 10)"
        )

    def test_template_ids_unique_globally(self, all_templates: dict) -> None:
        all_ids: list[str] = []
        for templates in all_templates.values():
            for t in templates:
                all_ids.append(t.template_id)
        assert len(all_ids) == len(set(all_ids)), "Duplicate template IDs found"

    def test_all_templates_have_nonempty_fields(self, all_templates: dict) -> None:
        for domain, templates in all_templates.items():
            for t in templates:
                assert t.template_id, f"Empty template_id in {domain}"
                assert t.description_template, f"Empty description in {t.template_id}"
                assert t.signature_template, f"Empty signature in {t.template_id}"
                assert 1 <= t.difficulty <= 5, (
                    f"Bad difficulty {t.difficulty} in {t.template_id}"
                )
                assert t.domain == domain, (
                    f"Template {t.template_id} domain mismatch: "
                    f"{t.domain} != {domain}"
                )


# ---------------------------------------------------------------------------
# Template expansion produces valid descriptions
# ---------------------------------------------------------------------------

class TestTemplateExpansion:

    def test_expansion_produces_description(self, gen: DomainStratifiedGenerator) -> None:
        tasks = gen.generate("WEB", 5, seed=42)
        for task in tasks:
            assert task["description"], "Empty description"
            assert "{" not in task["description"], (
                f"Unexpanded placeholder in: {task['description']}"
            )

    def test_expansion_produces_signature(self, gen: DomainStratifiedGenerator) -> None:
        tasks = gen.generate("DATA", 5, seed=42)
        for task in tasks:
            sig = task["expected_signature"]
            assert sig.startswith("f="), f"Signature must start with f=: {sig}"

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_expansion_all_domains(
        self, gen: DomainStratifiedGenerator, domain: str
    ) -> None:
        tasks = gen.generate(domain, 3, seed=99)
        assert len(tasks) == 3
        for task in tasks:
            assert task["domain"] == domain
            assert task["description"]
            assert task["expected_signature"]


# ---------------------------------------------------------------------------
# Signature uses valid toke types
# ---------------------------------------------------------------------------

class TestSignatureTypes:

    _TYPE_PATTERN = re.compile(
        r":(\$?[a-z0-9]+(?:\([^)]*\))?)"
    )

    def _extract_types_from_sig(self, sig: str) -> list[str]:
        """Extract all type annotations from a toke function signature."""
        types: list[str] = []
        # Match parameter types and return type
        # Pattern: after ':' we expect a type like i64, $str, @(T), @(K:V)
        matches = re.findall(
            r":(\$?\w+|\@\([^)]+\))", sig
        )
        return matches

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_signatures_use_valid_types(self, domain: str) -> None:
        templates = get_templates(domain)
        for t in templates:
            types_found = self._extract_types_from_sig(t.signature_template)
            for typ in types_found:
                assert typ in VALID_TOKE_TYPES, (
                    f"Invalid toke type '{typ}' in template "
                    f"{t.template_id}: {t.signature_template}"
                )


# ---------------------------------------------------------------------------
# Determinism: same seed = same output
# ---------------------------------------------------------------------------

class TestDeterminism:

    def test_same_seed_same_output(self, gen: DomainStratifiedGenerator) -> None:
        a = gen.generate("CRYPTO", 20, seed=12345)
        b = gen.generate("CRYPTO", 20, seed=12345)
        assert len(a) == len(b)
        for ta, tb in zip(a, b):
            assert ta["task_id"] == tb["task_id"]
            assert ta["description"] == tb["description"]
            assert ta["expected_signature"] == tb["expected_signature"]

    def test_different_seed_different_output(
        self, gen: DomainStratifiedGenerator
    ) -> None:
        a = gen.generate("FILE_IO", 20, seed=1)
        b = gen.generate("FILE_IO", 20, seed=9999)
        descs_a = {t["description"] for t in a}
        descs_b = {t["description"] for t in b}
        # With 20 tasks and different seeds, descriptions should differ
        # (at least some will have different parameter fills)
        assert descs_a != descs_b

    def test_generate_all_deterministic(
        self, gen: DomainStratifiedGenerator
    ) -> None:
        a = gen.generate_all(per_domain=5, seed=42)
        b = gen.generate_all(per_domain=5, seed=42)
        assert len(a) == len(b)
        for ta, tb in zip(a, b):
            assert ta["task_id"] == tb["task_id"]


# ---------------------------------------------------------------------------
# No duplicate task IDs
# ---------------------------------------------------------------------------

class TestTaskIds:

    def test_no_duplicate_task_ids_within_domain(
        self, gen: DomainStratifiedGenerator
    ) -> None:
        for domain in DOMAINS:
            tasks = gen.generate(domain, 50, seed=42)
            ids = [t["task_id"] for t in tasks]
            assert len(ids) == len(set(ids)), (
                f"Duplicate task IDs in {domain}"
            )

    def test_no_duplicate_task_ids_across_domains(
        self, gen: DomainStratifiedGenerator
    ) -> None:
        all_tasks = gen.generate_all(per_domain=20, seed=42)
        ids = [t["task_id"] for t in all_tasks]
        assert len(ids) == len(set(ids)), "Duplicate task IDs across domains"


# ---------------------------------------------------------------------------
# Stdlib context is relevant to domain
# ---------------------------------------------------------------------------

class TestStdlibContext:

    def test_web_has_http_context(self) -> None:
        ctx = get_stdlib_context("WEB")
        assert "std.http" in ctx
        assert "std.json" in ctx

    def test_data_has_db_context(self) -> None:
        ctx = get_stdlib_context("DATA")
        assert "std.db" in ctx
        assert "std.str" in ctx

    def test_file_io_has_file_context(self) -> None:
        ctx = get_stdlib_context("FILE_IO")
        assert "std.file" in ctx

    def test_crypto_has_str_context(self) -> None:
        ctx = get_stdlib_context("CRYPTO")
        assert "std.str" in ctx

    def test_network_has_http_context(self) -> None:
        ctx = get_stdlib_context("NETWORK")
        assert "std.http" in ctx

    def test_cli_has_file_and_str_context(self) -> None:
        ctx = get_stdlib_context("CLI")
        assert "std.file" in ctx
        assert "std.str" in ctx

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_stdlib_context_nonempty(self, domain: str) -> None:
        ctx = get_stdlib_context(domain)
        assert len(ctx) > 50, f"Stdlib context too short for {domain}"

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_generated_tasks_have_stdlib_imports(
        self, gen: DomainStratifiedGenerator, domain: str
    ) -> None:
        tasks = gen.generate(domain, 3, seed=42)
        for task in tasks:
            assert task["stdlib_imports"], (
                f"No stdlib imports for task {task['task_id']}"
            )


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

class TestPromptGeneration:

    def test_to_prompt_includes_spec_reference(
        self, gen: DomainStratifiedGenerator
    ) -> None:
        tasks = gen.generate("WEB", 1, seed=42)
        prompt = gen.to_prompt(tasks[0])
        assert "toke Phase 2 Specification Reference" in prompt

    def test_to_prompt_includes_description(
        self, gen: DomainStratifiedGenerator
    ) -> None:
        tasks = gen.generate("CLI", 1, seed=42)
        prompt = gen.to_prompt(tasks[0])
        assert tasks[0]["description"] in prompt

    def test_to_prompt_includes_signature(
        self, gen: DomainStratifiedGenerator
    ) -> None:
        tasks = gen.generate("DATA", 1, seed=42)
        prompt = gen.to_prompt(tasks[0])
        assert tasks[0]["expected_signature"] in prompt

    def test_to_prompt_includes_rules(
        self, gen: DomainStratifiedGenerator
    ) -> None:
        tasks = gen.generate("CRYPTO", 1, seed=42)
        prompt = gen.to_prompt(tasks[0])
        assert "m=" in prompt
        assert "Output ONLY the toke source code" in prompt


# ---------------------------------------------------------------------------
# Param pools
# ---------------------------------------------------------------------------

class TestParamPools:

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_param_pool_exists(self, domain: str) -> None:
        pool = get_param_pool(domain)
        assert len(pool) >= 3, (
            f"{domain} param pool has only {len(pool)} keys"
        )

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_param_pool_values_nonempty(self, domain: str) -> None:
        pool = get_param_pool(domain)
        for key, values in pool.items():
            assert len(values) >= 2, (
                f"{domain}.{key} has only {len(values)} values"
            )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_unknown_domain_raises(self, gen: DomainStratifiedGenerator) -> None:
        with pytest.raises(ValueError, match="Unknown domain"):
            gen.generate("INVALID", 1)

    def test_unknown_domain_templates_raises(self) -> None:
        with pytest.raises(KeyError):
            get_templates("NONEXISTENT")

    def test_unknown_domain_stdlib_raises(self) -> None:
        with pytest.raises(KeyError):
            get_stdlib_context("NONEXISTENT")
