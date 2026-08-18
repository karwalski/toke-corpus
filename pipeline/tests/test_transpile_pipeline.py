"""Tests for the transpile pipeline (Story 9.2.2).

Covers:
- Prompt generation
- Python response parsing (code blocks, raw code, edge cases)
- Transpile + validate integration (tkc mocked)
- Corpus entry format
- At least 15 tests
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from generator.curriculum_v2 import TaskSpecV2, TestCase
from pipeline.task_to_python_prompt import (
    _build_function_name,
    _build_signature,
    _toke_type_to_python,
    generate_python_prompt,
)
from pipeline.transpile_pipeline import TranspilePipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_task() -> TaskSpecV2:
    return TaskSpecV2(
        task_id="A-ARR-0001",
        category="A-ARR",
        description="Return the sum of all elements in an integer array.",
        input_types=["Arr<i64>"],
        output_type="i64",
        test_cases=[
            TestCase(inputs=[[1, 2, 3]], expected=6),
            TestCase(inputs=[[0]], expected=0),
            TestCase(inputs=[[-1, 1]], expected=0),
        ],
        difficulty=1,
    )


@pytest.fixture
def domain_task() -> TaskSpecV2:
    return TaskSpecV2(
        task_id="D-WEB-0042",
        category="D-WEB",
        description="Parse a URL query string into key-value pairs.",
        input_types=["Str"],
        output_type="Map<Str;Str>",
        test_cases=[
            TestCase(inputs=["a=1&b=2"], expected={"a": "1", "b": "2"}),
        ],
        difficulty=2,
        domain_context="urllib.parse.parse_qs(qs: str) -> dict[str, list[str]]",
    )


@pytest.fixture
def multi_param_task() -> TaskSpecV2:
    return TaskSpecV2(
        task_id="A-NUM-0010",
        category="A-NUM",
        description="Return the larger of two integers.",
        input_types=["i64", "i64"],
        output_type="i64",
        test_cases=[
            TestCase(inputs=[3, 5], expected=5),
            TestCase(inputs=[7, 2], expected=7),
            TestCase(inputs=[4, 4], expected=4),
        ],
        difficulty=1,
    )


@pytest.fixture
def pipeline() -> TranspilePipeline:
    return TranspilePipeline()


# ---------------------------------------------------------------------------
# Type mapping tests
# ---------------------------------------------------------------------------


class TestTypeMapping:
    def test_scalar_types(self):
        assert _toke_type_to_python("i64") == "int"
        assert _toke_type_to_python("f64") == "float"
        assert _toke_type_to_python("Str") == "str"
        assert _toke_type_to_python("bool") == "bool"
        assert _toke_type_to_python("void") == "None"

    def test_array_type(self):
        assert _toke_type_to_python("Arr<i64>") == "list[int]"
        assert _toke_type_to_python("Arr<Str>") == "list[str]"

    def test_map_type(self):
        assert _toke_type_to_python("Map<Str;i64>") == "dict[str, int]"
        assert _toke_type_to_python("Map<Str;Str>") == "dict[str, str]"

    def test_optional_type(self):
        assert _toke_type_to_python("Opt<i64>") == "int | None"

    def test_result_type(self):
        assert _toke_type_to_python("Res<Str>") == "str"


# ---------------------------------------------------------------------------
# Function name tests
# ---------------------------------------------------------------------------


class TestFunctionName:
    def test_simple_id(self):
        assert _build_function_name("A-ARR-0001") == "aarr0001"

    def test_domain_id(self):
        assert _build_function_name("D-WEB-0042") == "dweb0042"

    def test_underscores_removed(self):
        assert _build_function_name("A_NUM_0010") == "anum0010"


# ---------------------------------------------------------------------------
# Prompt generation tests
# ---------------------------------------------------------------------------


class TestPromptGeneration:
    def test_contains_description(self, simple_task):
        prompt = generate_python_prompt(simple_task)
        assert simple_task.description in prompt

    def test_contains_function_signature(self, simple_task):
        prompt = generate_python_prompt(simple_task)
        assert "def aarr0001(x: list[int]) -> int:" in prompt

    def test_contains_test_cases(self, simple_task):
        prompt = generate_python_prompt(simple_task)
        assert "assert aarr0001([1, 2, 3]) == 6" in prompt

    def test_contains_constraints(self, simple_task):
        prompt = generate_python_prompt(simple_task)
        assert "pure" in prompt.lower()
        assert "no classes" in prompt.lower() or "No classes" in prompt

    def test_multi_param_signature(self, multi_param_task):
        prompt = generate_python_prompt(multi_param_task)
        assert "def anum0010(p0: int, p1: int) -> int:" in prompt

    def test_domain_context_included(self, domain_task):
        prompt = generate_python_prompt(domain_task)
        assert "urllib.parse.parse_qs" in prompt

    def test_domain_context_absent_when_empty(self, simple_task):
        prompt = generate_python_prompt(simple_task)
        assert "Domain context" not in prompt

    def test_prompt_requests_python_only(self, simple_task):
        prompt = generate_python_prompt(simple_task)
        assert "ONLY the Python function" in prompt


# ---------------------------------------------------------------------------
# Response parsing tests
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def test_fenced_python_block(self, pipeline):
        response = '```python\ndef foo(x: int) -> int:\n    return x + 1\n```'
        result = pipeline.parse_python_response(response)
        assert "def foo(x: int) -> int:" in result
        assert "return x + 1" in result

    def test_fenced_block_no_language(self, pipeline):
        response = '```\ndef foo(x: int) -> int:\n    return x + 1\n```'
        result = pipeline.parse_python_response(response)
        assert "def foo" in result

    def test_multiple_blocks_prefers_def(self, pipeline):
        response = (
            "Here is the solution:\n"
            "```python\nx = 42\n```\n"
            "```python\ndef solve(x: int) -> int:\n    return x * 2\n```"
        )
        result = pipeline.parse_python_response(response)
        assert "def solve" in result

    def test_raw_function_no_fences(self, pipeline):
        response = "def bar(x: int) -> int:\n    return x - 1\n"
        result = pipeline.parse_python_response(response)
        assert "def bar" in result

    def test_raw_function_with_preamble(self, pipeline):
        response = (
            "Here is my solution:\n\n"
            "def bar(x: int) -> int:\n"
            "    return x - 1\n"
        )
        result = pipeline.parse_python_response(response)
        assert "def bar" in result
        # Should not include the preamble text
        assert "Here is my solution" not in result

    def test_no_code_raises(self, pipeline):
        with pytest.raises(ValueError, match="No Python code"):
            pipeline.parse_python_response("I don't know how to solve this.")


# ---------------------------------------------------------------------------
# Transpile tests
# ---------------------------------------------------------------------------


class TestTranspile:
    def test_transpile_simple_function(self, pipeline):
        python_src = "def add(a: int, b: int) -> int:\n    return a + b\n"
        result = pipeline.transpile_to_toke(python_src, "A-NUM-0001")
        assert "m=anum0001;" in result
        assert "f=add(" in result
        assert "<a+b" in result

    def test_transpile_module_name_sanitised(self, pipeline):
        python_src = "def foo(x: int) -> int:\n    return x\n"
        result = pipeline.transpile_to_toke(python_src, "D-WEB-0042")
        assert "m=dweb0042;" in result


# ---------------------------------------------------------------------------
# Validate tests (tkc mocked)
# ---------------------------------------------------------------------------


class TestValidate:
    @patch("pipeline.transpile_pipeline.subprocess.run")
    def test_validate_pass(self, mock_run, pipeline):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        passed, diag = pipeline.validate_toke("m=test;\n")
        assert passed is True
        assert diag == ""

    @patch("pipeline.transpile_pipeline.subprocess.run")
    def test_validate_fail(self, mock_run, pipeline):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="error: unexpected token"
        )
        passed, diag = pipeline.validate_toke("m=test;\n")
        assert passed is False
        assert "unexpected token" in diag


# ---------------------------------------------------------------------------
# Full pipeline tests (process_task with mocked tkc)
# ---------------------------------------------------------------------------


class TestProcessTask:
    @patch.object(TranspilePipeline, "validate_toke")
    def test_full_pipeline_success(self, mock_validate, simple_task):
        mock_validate.return_value = (True, "")
        pipeline = TranspilePipeline()

        response = (
            "```python\n"
            "def aarr0001(x: list[int]) -> int:\n"
            "    result: int = 0\n"
            "    for i in range(len(x)):\n"
            "        result += x[i]\n"
            "    return result\n"
            "```"
        )
        entry = pipeline.process_task(simple_task, response)

        assert entry is not None
        assert entry["id"] == "task-A-ARR-0001-001"
        assert entry["generation_method"] == "transpilation_pipeline"
        assert entry["source"]["origin"] == "curriculum_v2"
        assert entry["source"]["task_id"] == "A-ARR-0001"
        assert entry["source"]["category"] == "A-ARR"
        assert entry["validation"]["tkc_check"] == "pass"
        assert "python_source" in entry["references"]
        assert isinstance(entry["tk_tokens"], int)
        assert entry["tk_tokens"] > 0

    @patch.object(TranspilePipeline, "validate_toke")
    def test_pipeline_returns_none_on_bad_response(
        self, mock_validate, simple_task
    ):
        pipeline = TranspilePipeline()
        entry = pipeline.process_task(simple_task, "No code here at all.")
        assert entry is None

    @patch.object(TranspilePipeline, "validate_toke")
    def test_pipeline_returns_none_on_tkc_fail(
        self, mock_validate, simple_task
    ):
        mock_validate.return_value = (False, "error")
        pipeline = TranspilePipeline()
        response = (
            "```python\n"
            "def aarr0001(x: list[int]) -> int:\n"
            "    return sum(x)\n"
            "```"
        )
        # sum() is not a supported builtin in transpiler — but even if
        # transpile succeeds, tkc validation failing means None.
        # We need a response that transpiles but fails tkc.
        response2 = (
            "```python\n"
            "def aarr0001(x: list[int]) -> int:\n"
            "    result: int = 0\n"
            "    for i in range(len(x)):\n"
            "        result += x[i]\n"
            "    return result\n"
            "```"
        )
        entry = pipeline.process_task(simple_task, response2)
        assert entry is None

    @patch.object(TranspilePipeline, "validate_toke")
    def test_corpus_entry_is_json_serialisable(
        self, mock_validate, simple_task
    ):
        mock_validate.return_value = (True, "")
        pipeline = TranspilePipeline()
        response = (
            "```python\n"
            "def aarr0001(x: list[int]) -> int:\n"
            "    result: int = 0\n"
            "    for i in range(len(x)):\n"
            "        result += x[i]\n"
            "    return result\n"
            "```"
        )
        entry = pipeline.process_task(simple_task, response)
        assert entry is not None
        # Must serialise without error
        serialised = json.dumps(entry, ensure_ascii=False)
        roundtrip = json.loads(serialised)
        assert roundtrip["id"] == entry["id"]


# ---------------------------------------------------------------------------
# Signature building tests
# ---------------------------------------------------------------------------


class TestSignatureBuilding:
    def test_single_param(self, simple_task):
        sig = _build_signature(simple_task)
        assert sig == "def aarr0001(x: list[int]) -> int:"

    def test_multi_param(self, multi_param_task):
        sig = _build_signature(multi_param_task)
        assert sig == "def anum0010(p0: int, p1: int) -> int:"

    def test_no_params(self):
        task = TaskSpecV2(
            task_id="A-NUM-0099",
            category="A-NUM",
            description="Return zero.",
            input_types=[],
            output_type="i64",
        )
        sig = _build_signature(task)
        assert sig == "def anum0099() -> int:"
