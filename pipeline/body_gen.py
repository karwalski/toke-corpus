"""Body generation for interface-first pipeline — Story 9.2.3.

Creates LLM prompts that ask for function bodies matching a .tki interface,
parses responses to merge bodies with interfaces, and validates the result
with tkc --check.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

TKC_BINARY = Path("/Users/matthew.watt/tk/toke/tkc")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_BODY_PROMPT_TEMPLATE = """\
You are a toke language expert. Given the following toke interface file (.tki),
write the function bodies that implement each signature.

## toke syntax rules
- Module: m=modname;
- Functions: f=name(param:type):rettype{{...}}
- Types: i64, u64, f64, $str, bool, $void
- Arrays: @(T) — accessed via .get(i), .len, .push(val)
- Maps: @(K:V) — accessed via .get(key)
- Let bindings: let x=val; (immutable), let x=mut.val; (mutable)
- Return: <expr (bare < is return)
- Equality: = not ==
- No underscores in identifiers
- Semicolons terminate statements
- For loops: for i=0;i<n;i=i+1{{...}}
- Error types: e=Name{{variant1;variant2;}}

## Interface
```toke
{interface}
```

## Instructions
- Output a single fenced code block containing the COMPLETE .tk file
- Include the module declaration and any error type declarations from the interface
- Replace each function signature (ending with ;) with a full function body (ending with }})
- Every function must have a return expression using < syntax
- Use only valid toke Phase 2 syntax
"""


class BodyGenerator:
    """Generates LLM prompts for body generation and parses responses."""

    def __init__(self, tkc_path: Path | None = None) -> None:
        self.tkc_path = tkc_path or TKC_BINARY

    def generate_body_prompt(self, interface: str) -> str:
        """Create an LLM prompt asking for function bodies matching the
        interface signatures.

        Args:
            interface: The .tki interface source string.

        Returns:
            A prompt string ready to send to an LLM.
        """
        return _BODY_PROMPT_TEMPLATE.format(interface=interface.strip())

    def parse_response(self, response: str, interface: str) -> str:
        """Extract toke code from an LLM response and merge with the interface.

        Extracts the first fenced code block from the response.  If extraction
        fails, falls back to treating the entire response as toke source.

        Then validates that every function declared in the interface is present
        in the extracted code.

        Args:
            response: Raw LLM response text.
            interface: The original .tki interface source.

        Returns:
            Complete .tk source string.
        """
        code = self._extract_code_block(response)
        if code is None:
            # Fallback: treat whole response as code
            code = response.strip()

        # Validate that the module line is present
        if not re.search(r"^m=\w+;", code, re.MULTILINE):
            # Prepend module from interface
            mod_match = re.search(r"^m=\w+;", interface, re.MULTILINE)
            if mod_match:
                code = mod_match.group(0) + "\n" + code

        # Check all interface functions are present (with bodies)
        iface_funcs = self._extract_func_names(interface)
        code_funcs = self._extract_func_names(code)
        missing = set(iface_funcs) - set(code_funcs)
        if missing:
            # Append stubs for missing functions
            for fname in sorted(missing):
                sig = self._find_signature(fname, interface)
                if sig:
                    # Convert interface sig to stub with return
                    stub = self._sig_to_stub(sig)
                    code = code.rstrip() + "\n" + stub

        return code

    def validate(self, complete_source: str) -> tuple[bool, str]:
        """Run tkc --check on the complete source.

        Args:
            complete_source: Full .tk source to validate.

        Returns:
            (passed, diagnostic_output) tuple.
        """
        with tempfile.NamedTemporaryFile(
            suffix=".tk", mode="w", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(complete_source)
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

    # -- private helpers ----------------------------------------------------

    def _extract_code_block(self, text: str) -> str | None:
        """Extract the first fenced code block from text."""
        pattern = r"```(?:toke|tk)?\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    def _extract_func_names(self, source: str) -> list[str]:
        """Extract function names from toke source (interface or full)."""
        return re.findall(r"f=(\w+)\(", source)

    def _find_signature(self, fname: str, interface: str) -> str | None:
        """Find the full signature line for a function in the interface."""
        pattern = rf"^f={re.escape(fname)}\(.*$"
        match = re.search(pattern, interface, re.MULTILINE)
        if match:
            return match.group(0)
        return None

    def _sig_to_stub(self, sig: str) -> str:
        """Convert an interface signature to a stub function body.

        Takes 'f=name(params):rettype;' and produces a function with a
        default return value.
        """
        # Remove trailing semicolon, add body
        base = sig.rstrip().rstrip(";")

        # Determine return type for default value
        ret_match = re.search(r":([^{]+)$", base)
        ret_type = ret_match.group(1) if ret_match else "i64"
        default = self._default_value(ret_type)

        return f"{base}{{\n<{default}\n}}"

    def _default_value(self, typ: str) -> str:
        """Return a default literal for a toke type."""
        typ = typ.strip()
        if typ in ("i64", "u64"):
            return "0"
        if typ == "f64":
            return "0.0"
        if typ == "bool":
            return "false"
        if typ == "$str":
            return '""'
        if typ == "$void":
            return "0"
        # Arrays/maps — empty isn't valid, return a literal
        return "0"
