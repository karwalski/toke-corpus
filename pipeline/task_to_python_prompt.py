"""Generate LLM prompts that request Python solutions for toke curriculum tasks.

Takes a TaskSpecV2 from the curriculum generator and produces a prompt string
suitable for sending to Claude or GPT. The prompt asks for a single pure
Python function with type annotations and no external dependencies.
"""
from __future__ import annotations

from generator.curriculum_v2 import TaskSpecV2

# ---------------------------------------------------------------------------
# Toke-to-Python type mapping
# ---------------------------------------------------------------------------

_TOKE_TO_PYTHON: dict[str, str] = {
    "i64": "int",
    "u64": "int",
    "f64": "float",
    "Str": "str",
    "bool": "bool",
    "void": "None",
}


def _toke_type_to_python(toke_type: str) -> str:
    """Convert a toke type string to a Python type annotation.

    Handles scalar types, ``Arr<T>``, ``Map<K;V>``, ``Opt<T>``, ``Res<T>``.
    """
    if toke_type in _TOKE_TO_PYTHON:
        return _TOKE_TO_PYTHON[toke_type]

    if toke_type.startswith("Arr<") and toke_type.endswith(">"):
        inner = toke_type[4:-1]
        return f"list[{_toke_type_to_python(inner)}]"

    if toke_type.startswith("Map<") and toke_type.endswith(">"):
        inner = toke_type[4:-1]
        parts = inner.split(";", 1)
        if len(parts) == 2:
            return f"dict[{_toke_type_to_python(parts[0])}, {_toke_type_to_python(parts[1])}]"
        return f"dict[str, {_toke_type_to_python(parts[0])}]"

    if toke_type.startswith("Opt<") and toke_type.endswith(">"):
        inner = toke_type[4:-1]
        return f"{_toke_type_to_python(inner)} | None"

    if toke_type.startswith("Res<") and toke_type.endswith(">"):
        inner = toke_type[4:-1]
        return _toke_type_to_python(inner)

    # Fallback — return as-is
    return toke_type


def _build_function_name(task_id: str) -> str:
    """Derive a Python function name from the task ID.

    ``D-WEB-0001`` -> ``dweb0001``, ``A-ARR-0042`` -> ``aarr0042``.
    """
    return task_id.lower().replace("-", "").replace("_", "")


def _build_signature(task: TaskSpecV2) -> str:
    """Build a Python function signature string with type annotations."""
    func_name = _build_function_name(task.task_id)

    params: list[str] = []
    for i, toke_type in enumerate(task.input_types):
        py_type = _toke_type_to_python(toke_type)
        param_name = f"p{i}" if len(task.input_types) > 1 else "x"
        params.append(f"{param_name}: {py_type}")

    ret_type = _toke_type_to_python(task.output_type)
    param_str = ", ".join(params)
    return f"def {func_name}({param_str}) -> {ret_type}:"


def _format_test_cases(task: TaskSpecV2, func_name: str) -> str:
    """Format test cases as assert statements."""
    lines: list[str] = []
    for i, tc in enumerate(task.test_cases):
        args = ", ".join(repr(v) for v in tc.inputs)
        lines.append(f"assert {func_name}({args}) == {tc.expected!r}")
    return "\n".join(lines)


def generate_python_prompt(task: TaskSpecV2) -> str:
    """Produce an LLM prompt requesting a Python solution for *task*.

    The prompt includes:
    - Natural-language task description
    - Required function signature with type annotations
    - Test cases for validation
    - Constraints (pure function, no classes, builtins only)

    Returns:
        A string suitable for sending to an LLM API.
    """
    func_name = _build_function_name(task.task_id)
    signature = _build_signature(task)
    test_block = _format_test_cases(task, func_name)

    domain_section = ""
    if task.domain_context:
        domain_section = f"""
Domain context (relevant standard library signatures):
{task.domain_context}
"""

    prompt = f"""Write a single pure Python function that solves the following task.

Task: {task.description}

Required function signature:
```python
{signature}
    ...
```
{domain_section}
Constraints:
- Write exactly ONE function with the signature above.
- Use only Python builtins (no imports beyond builtins).
- No classes, no global state, no side effects.
- The function must be pure: same inputs always produce the same output.
- Include type annotations on all parameters and the return type.

Test cases your solution must pass:
```python
{test_block}
```

Return ONLY the Python function definition. No explanation, no tests, no imports.
"""
    return prompt.strip() + "\n"
