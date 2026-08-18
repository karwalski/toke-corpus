"""Companion-file generator for toke corpus (Story 9.2.4).

Generates .tkc.md companion-file descriptions that serve as LLM prompts
for producing toke source code.  Each companion describes a toke module
with function signatures, parameter types, return types, and usage examples.

Deterministic via seed — same (category, difficulty, seed) always produces
the same companion markdown.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

CATEGORIES: list[str] = [
    "math",
    "string",
    "array",
    "sorting",
    "error_handling",
    "web",
    "data",
    "crypto",
    "file_io",
]

# ---------------------------------------------------------------------------
# Type vocabulary (Phase 2 toke syntax)
# ---------------------------------------------------------------------------

PARAM_TYPES: list[str] = ["i64", "u64", "f64", "$str", "bool"]
RETURN_TYPES: list[str] = ["i64", "u64", "f64", "$str", "bool", "$void"]
ARRAY_TYPES: list[str] = ["@(i64)", "@(u64)", "@(f64)", "@($str)"]
MAP_TYPES: list[str] = ["@($str:i64)", "@($str:$str)", "@($str:bool)"]

# ---------------------------------------------------------------------------
# Per-category function templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FuncTemplate:
    """Template for generating a function description."""

    name: str
    params: list[tuple[str, str]]  # (param_name, type)
    ret: str
    desc: str


_TEMPLATES: dict[str, list[_FuncTemplate]] = {
    "math": [
        _FuncTemplate("add", [("a", "i64"), ("b", "i64")], "i64", "Return the sum of two integers."),
        _FuncTemplate("factorial", [("n", "u64")], "u64", "Compute the factorial of n."),
        _FuncTemplate("gcd", [("a", "i64"), ("b", "i64")], "i64", "Compute the greatest common divisor."),
        _FuncTemplate("fibonacci", [("n", "u64")], "u64", "Return the nth Fibonacci number."),
        _FuncTemplate("isprime", [("n", "u64")], "bool", "Return true if n is prime."),
        _FuncTemplate("abs", [("x", "i64")], "i64", "Return the absolute value of x."),
        _FuncTemplate("pow", [("base", "i64"), ("exp", "u64")], "i64", "Compute base raised to exp."),
        _FuncTemplate("clamp", [("x", "i64"), ("lo", "i64"), ("hi", "i64")], "i64", "Clamp x between lo and hi."),
        _FuncTemplate("max", [("a", "i64"), ("b", "i64")], "i64", "Return the larger of two integers."),
        _FuncTemplate("min", [("a", "i64"), ("b", "i64")], "i64", "Return the smaller of two integers."),
    ],
    "string": [
        _FuncTemplate("strlen", [("s", "$str")], "u64", "Return the length of a string."),
        _FuncTemplate("concat", [("a", "$str"), ("b", "$str")], "$str", "Concatenate two strings."),
        _FuncTemplate("toupper", [("s", "$str")], "$str", "Convert a string to uppercase."),
        _FuncTemplate("tolower", [("s", "$str")], "$str", "Convert a string to lowercase."),
        _FuncTemplate("contains", [("haystack", "$str"), ("needle", "$str")], "bool", "Check if haystack contains needle."),
        _FuncTemplate("reverse", [("s", "$str")], "$str", "Reverse a string."),
        _FuncTemplate("repeat", [("s", "$str"), ("n", "u64")], "$str", "Repeat a string n times."),
        _FuncTemplate("startswith", [("s", "$str"), ("prefix", "$str")], "bool", "Check if s starts with prefix."),
    ],
    "array": [
        _FuncTemplate("sumarray", [("arr", "@(i64)")], "i64", "Return the sum of all elements."),
        _FuncTemplate("maxarray", [("arr", "@(i64)")], "i64", "Return the largest element."),
        _FuncTemplate("minarray", [("arr", "@(i64)")], "i64", "Return the smallest element."),
        _FuncTemplate("countarray", [("arr", "@(i64)")], "u64", "Return the number of elements."),
        _FuncTemplate("avgarray", [("arr", "@(f64)")], "f64", "Return the average of all elements."),
        _FuncTemplate("includes", [("arr", "@(i64)"), ("val", "i64")], "bool", "Check if arr contains val."),
        _FuncTemplate("flatten", [("arr", "@(i64)")], "@(i64)", "Return a new array with duplicates removed."),
    ],
    "sorting": [
        _FuncTemplate("bubblesort", [("arr", "@(i64)")], "@(i64)", "Sort an array using bubble sort."),
        _FuncTemplate("insertionsort", [("arr", "@(i64)")], "@(i64)", "Sort an array using insertion sort."),
        _FuncTemplate("selectionsort", [("arr", "@(i64)")], "@(i64)", "Sort an array using selection sort."),
        _FuncTemplate("issorted", [("arr", "@(i64)")], "bool", "Check if an array is sorted ascending."),
        _FuncTemplate("mergesort", [("arr", "@(i64)")], "@(i64)", "Sort an array using merge sort."),
    ],
    "error_handling": [
        _FuncTemplate("safediv", [("a", "i64"), ("b", "i64")], "i64", "Divide a by b, return 0 on division by zero."),
        _FuncTemplate("parseint", [("s", "$str")], "i64", "Parse a string to integer, return -1 on failure."),
        _FuncTemplate("safeget", [("arr", "@(i64)"), ("idx", "u64")], "i64", "Get element at index, return 0 if out of bounds."),
        _FuncTemplate("coalesce", [("a", "i64"), ("b", "i64")], "i64", "Return a if nonzero, else b."),
        _FuncTemplate("clamprange", [("val", "i64"), ("lo", "i64"), ("hi", "i64")], "i64", "Clamp val to [lo, hi], swap lo/hi if inverted."),
    ],
    "web": [
        _FuncTemplate("urlencode", [("s", "$str")], "$str", "Percent-encode a string for use in URLs."),
        _FuncTemplate("parsequery", [("qs", "$str")], "@($str:$str)", "Parse a URL query string into key-value pairs."),
        _FuncTemplate("buildquery", [("params", "@($str:$str)")], "$str", "Build a query string from key-value pairs."),
        _FuncTemplate("statusmsg", [("code", "u64")], "$str", "Return the HTTP status message for a code."),
        _FuncTemplate("isvalidurl", [("url", "$str")], "bool", "Check if a string looks like a valid URL."),
    ],
    "data": [
        _FuncTemplate("csvrow", [("fields", "@($str)")], "$str", "Join fields into a CSV row string."),
        _FuncTemplate("parsecsv", [("line", "$str")], "@($str)", "Split a CSV line into fields."),
        _FuncTemplate("jsonval", [("key", "$str"), ("val", "$str")], "$str", "Format a JSON key-value pair."),
        _FuncTemplate("wordcount", [("text", "$str")], "u64", "Count the number of words in text."),
        _FuncTemplate("frequencies", [("words", "@($str)")], "@($str:u64)", "Count word frequencies."),
    ],
    "crypto": [
        _FuncTemplate("rot13", [("s", "$str")], "$str", "Apply ROT13 cipher to a string."),
        _FuncTemplate("xorbytes", [("data", "@(u64)"), ("key", "u64")], "@(u64)", "XOR each byte with a key."),
        _FuncTemplate("caesarenc", [("s", "$str"), ("shift", "u64")], "$str", "Encrypt with Caesar cipher."),
        _FuncTemplate("caesardec", [("s", "$str"), ("shift", "u64")], "$str", "Decrypt a Caesar cipher."),
        _FuncTemplate("hashsimple", [("s", "$str")], "u64", "Compute a simple hash of a string."),
    ],
    "file_io": [
        _FuncTemplate("linecount", [("content", "$str")], "u64", "Count the number of lines in content."),
        _FuncTemplate("firstline", [("content", "$str")], "$str", "Return the first line of content."),
        _FuncTemplate("lastline", [("content", "$str")], "$str", "Return the last line of content."),
        _FuncTemplate("appendline", [("content", "$str"), ("line", "$str")], "$str", "Append a line to content."),
        _FuncTemplate("findline", [("content", "$str"), ("needle", "$str")], "i64", "Return line number containing needle, or -1."),
    ],
}

# ---------------------------------------------------------------------------
# Example-value generation helpers
# ---------------------------------------------------------------------------


def _example_value(typ: str, rng: random.Random) -> str:
    """Return a plausible example literal for a toke type."""
    if typ == "i64":
        return str(rng.randint(-100, 100))
    if typ == "u64":
        return str(rng.randint(0, 100))
    if typ == "f64":
        return f"{rng.uniform(-50.0, 50.0):.1f}"
    if typ == "$str":
        words = ["hello", "world", "toke", "test", "foo", "bar", "data", "key"]
        return f'"{rng.choice(words)}"'
    if typ == "bool":
        return rng.choice(["true", "false"])
    # Array / map types — show a small literal
    if typ.startswith("@(") and ":" not in typ:
        inner = typ[2:-1]
        elems = [_example_value(inner, rng) for _ in range(rng.randint(2, 4))]
        return f"@({', '.join(elems)})"
    return '"..."'


# ---------------------------------------------------------------------------
# CompanionGenerator
# ---------------------------------------------------------------------------


class CompanionGenerator:
    """Generate .tkc.md companion file descriptions for toke modules."""

    def generate_companion(
        self,
        category: str,
        difficulty: int,
        seed: int,
    ) -> tuple[str, str]:
        """Generate a companion markdown description.

        Args:
            category: One of CATEGORIES (e.g. "math", "string").
            difficulty: 1-5 (1-2 = single function, 3-5 = multi-function).
            seed: RNG seed for determinism.

        Returns:
            Tuple of (module_name, companion_markdown).

        Raises:
            ValueError: If category is unknown or difficulty out of range.
        """
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category: {category!r}")
        if not 1 <= difficulty <= 5:
            raise ValueError(f"Difficulty must be 1-5, got {difficulty}")

        rng = random.Random(seed)
        templates = _TEMPLATES[category]

        # Pick functions based on difficulty
        if difficulty <= 2:
            num_funcs = 1
        else:
            num_funcs = min(rng.randint(2, difficulty), len(templates))

        chosen = rng.sample(templates, k=min(num_funcs, len(templates)))

        # Build module name
        module_name = f"{category}{seed % 1000:03d}"

        # Build markdown
        lines: list[str] = []
        lines.append(f"# {module_name}")
        lines.append("")
        lines.append(f"A toke module for {_category_desc(category)} operations.")
        lines.append("")
        lines.append("## Functions")
        lines.append("")

        for tmpl in chosen:
            sig_params = ", ".join(f"{p}:{t}" for p, t in tmpl.params)
            lines.append(f"### f={tmpl.name}({sig_params}):{tmpl.ret}")
            lines.append("")
            lines.append(tmpl.desc)
            lines.append("")

        # Add examples section for difficulty >= 3
        if difficulty >= 3:
            lines.append("## Examples")
            lines.append("")
            for tmpl in chosen:
                args = ", ".join(
                    _example_value(t, rng) for _, t in tmpl.params
                )
                lines.append(f"```toke")
                lines.append(f"let result = {tmpl.name}({args});")
                lines.append("```")
                lines.append("")

        md = "\n".join(lines)
        return module_name, md


def _category_desc(category: str) -> str:
    """Human-readable description for a category."""
    descs = {
        "math": "mathematical",
        "string": "string manipulation",
        "array": "array processing",
        "sorting": "sorting algorithm",
        "error_handling": "error handling",
        "web": "web utility",
        "data": "data processing",
        "crypto": "cryptographic",
        "file_io": "file I/O",
    }
    return descs.get(category, category)
