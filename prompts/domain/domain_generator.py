"""Domain-stratified generator for toke corpus prompt generation.

Expands parameterized domain templates into concrete LLM-ready prompts
with deterministic seeding for reproducibility.
"""

from __future__ import annotations

import re
import random
from pathlib import Path

from prompts.domain.domain_templates import (
    DOMAINS,
    SPEC_REFERENCE,
    DomainTemplate,
    get_all_templates,
    get_param_pool,
    get_stdlib_context,
    get_templates,
)


# ---------------------------------------------------------------------------
# Prompt template used when converting a task dict to an LLM prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a toke programming language expert. Generate a toke program for the
following task.

## toke syntax reference
{spec_reference}

## Domain context
The program should use the following stdlib modules:
```
{stdlib_context}
```

## Task
{description}

## Expected function signature
```
{expected_signature}
```

## Rules
- Output ONLY the toke source code. No explanations, no markdown fences.
- The program must start with `m=` module declaration.
- Parameters and arguments use `;` as separator, NOT `,`.
- Return with `<`, not `return`.
- No comments of any kind.
- The program must be complete and compilable.
- All identifiers must be lowercase with no underscores.
- Use `let x=val;` for immutable bindings, `let x=mut.val;` for mutable.
- Arrays use `@(T)` syntax, maps use `@(K:V)` syntax.
- Equality uses `=` not `==`.
"""


class DomainStratifiedGenerator:
    """Generates domain-stratified task prompts from parameterized templates."""

    def __init__(self) -> None:
        self._all_templates = get_all_templates()

    def generate(self, domain: str, count: int, seed: int = 42) -> list[dict]:
        """Generate *count* tasks for a single domain.

        Templates are expanded with random parameters drawn from the
        domain's parameter pool.  Deterministic for a given seed.
        """
        if domain not in DOMAINS:
            raise ValueError(f"Unknown domain: {domain!r}")

        rng = random.Random(seed)
        templates = get_templates(domain)
        pool = get_param_pool(domain)
        stdlib_ctx = get_stdlib_context(domain)

        tasks: list[dict] = []
        for i in range(count):
            template = templates[i % len(templates)]
            params = self._pick_params(template, pool, rng)
            task = self.expand_template(template, params, stdlib_ctx,
                                        task_index=i, domain=domain)
            tasks.append(task)

        return tasks

    def generate_all(self, per_domain: int = 500, seed: int = 42) -> list[dict]:
        """Generate *per_domain* tasks for every domain.

        Returns a flat list of all tasks across all domains.
        """
        all_tasks: list[dict] = []
        for idx, domain in enumerate(DOMAINS):
            domain_seed = seed + idx * 100_000
            tasks = self.generate(domain, per_domain, seed=domain_seed)
            all_tasks.extend(tasks)
        return all_tasks

    def expand_template(
        self,
        template: DomainTemplate,
        params: dict[str, str],
        stdlib_context: str,
        task_index: int = 0,
        domain: str = "",
    ) -> dict:
        """Expand a single template with concrete parameter values.

        Returns a task dict with all fields needed for prompt generation.
        """
        description = self._fill_placeholders(
            template.description_template, params
        )
        signature = self._fill_placeholders(
            template.signature_template, params
        )

        task_domain = domain or template.domain
        task_id = f"{task_domain}-{template.template_id}-{task_index:05d}"

        # Determine stdlib imports from the context
        stdlib_imports = self._extract_imports(stdlib_context)

        return {
            "task_id": task_id,
            "description": description,
            "expected_signature": signature,
            "stdlib_imports": stdlib_imports,
            "stdlib_context": stdlib_context,
            "domain": task_domain,
            "difficulty": template.difficulty,
            "template_id": template.template_id,
        }

    def to_prompt(self, task: dict) -> str:
        """Convert a task dict to an LLM-ready prompt string."""
        return _PROMPT_TEMPLATE.format(
            spec_reference=SPEC_REFERENCE,
            stdlib_context=task["stdlib_context"],
            description=task["description"],
            expected_signature=task["expected_signature"],
        )

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _pick_params(
        template: DomainTemplate,
        pool: dict[str, list[str]],
        rng: random.Random,
    ) -> dict[str, str]:
        """Pick random values for each placeholder in the template."""
        placeholders = set(
            re.findall(r"\{(\w+)\}", template.description_template)
        )
        params: dict[str, str] = {}
        for ph in placeholders:
            if ph in pool:
                params[ph] = rng.choice(pool[ph])
            else:
                params[ph] = ph  # fallback: use placeholder name itself
        return params

    @staticmethod
    def _fill_placeholders(template_str: str, params: dict[str, str]) -> str:
        """Replace {placeholder} tokens with concrete values."""
        result = template_str
        for key, val in params.items():
            result = result.replace(f"{{{key}}}", val)
        return result

    @staticmethod
    def _extract_imports(stdlib_context: str) -> list[str]:
        """Extract import module names from stdlib context string."""
        imports: list[str] = []
        for line in stdlib_context.splitlines():
            line = line.strip()
            if line.startswith("i=") and line.endswith(";"):
                mod = line[2:-1]
                imports.append(mod)
        return imports
