"""Companion-to-code pipeline for toke corpus (Story 9.2.4).

Takes a .tkc.md companion description, creates an LLM prompt for generating
toke source code, parses LLM responses, validates with tkc --check, and
produces corpus entries.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

TKC_BINARY = Path("/Users/matthew.watt/tk/toke/tkc")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a toke programming language expert. Generate valid toke source code
that implements the module described below.

## toke syntax rules
- Module declaration: `m=modname;`
- Functions: `f=name(param:type):rettype{{...}}` (lowercase f=)
- Types: i64, u64, f64, $str, bool, $void
- Arrays: `@(T)` — accessed via .get(i), .len, .push(val)
- Maps: `@(K:V)` — accessed via .get(key)
- Let bindings: `let x=val;` (immutable), `let x=mut.val;` (mutable)
- Return: `<expr` (bare < is return)
- Equality: `=` not `==`
- No underscores in identifiers
- Semicolons terminate statements
- For loops: `for i=0;i<n;i=i+1{{...}}`

## Module description

{companion_md}

## Instructions
- Output ONLY the toke source code inside a single ```toke code block.
- Start with the module declaration `m=...;`
- Implement every function listed above.
- Use correct toke syntax — no Python, no JavaScript.
"""


class CompanionToCodePipeline:
    """Pipeline to convert companion descriptions to validated toke code."""

    def __init__(self, tkc_path: Path | None = None) -> None:
        self.tkc_path = tkc_path or TKC_BINARY

    def generate_code_prompt(self, companion_md: str) -> str:
        """Create an LLM prompt from a companion markdown description.

        Args:
            companion_md: The .tkc.md companion file content.

        Returns:
            A prompt string suitable for sending to an LLM.
        """
        return _PROMPT_TEMPLATE.format(companion_md=companion_md)

    def parse_response(self, response: str) -> str:
        """Extract toke source code from an LLM response.

        Looks for a fenced code block (```toke or ```) and extracts its
        contents.  Falls back to the entire response if no code block is
        found.

        Args:
            response: Raw LLM response text.

        Returns:
            Extracted toke source code.
        """
        # Try ```toke first, then generic ```
        patterns = [
            r"```toke\s*\n(.*?)```",
            r"```\s*\n(.*?)```",
        ]
        for pattern in patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                return match.group(1).strip()

        # Fallback: return the whole response stripped
        return response.strip()

    def validate(self, toke_source: str) -> tuple[bool, str]:
        """Validate toke source using tkc --check.

        Args:
            toke_source: toke source code to validate.

        Returns:
            Tuple of (passed, diagnostic_output).
        """
        with tempfile.NamedTemporaryFile(
            suffix=".tk", mode="w", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(toke_source)
            tmp_path = Path(tmp.name)

        try:
            result = subprocess.run(
                [str(self.tkc_path), "--check", str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return False, str(exc)
        finally:
            tmp_path.unlink(missing_ok=True)

    def process(
        self,
        companion_md: str,
        llm_response: str,
        entry_id: str = "companion-0-001",
    ) -> dict | None:
        """Full pipeline: parse response, validate, build corpus entry.

        Args:
            companion_md: The companion markdown description.
            llm_response: Raw LLM response containing toke code.
            entry_id: Corpus entry ID.

        Returns:
            Corpus entry dict, or None if validation fails.
        """
        toke_source = self.parse_response(llm_response)

        if not toke_source:
            return None

        passed, diag = self.validate(toke_source)

        if not passed:
            return None

        tk_tokens = len(toke_source.split())

        return {
            "id": entry_id,
            "tk_source": toke_source,
            "tk_tokens": tk_tokens,
            "generation_method": "companion_to_code",
            "validation": {"tkc_check": "pass"},
            "references": {
                "companion_md": companion_md,
            },
            "metadata": {
                "retrieval_date": str(date.today()),
            },
        }
