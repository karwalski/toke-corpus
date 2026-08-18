"""Tests for registry.task_extractor — benchmark task extraction.

Story 9.2.6 (Part 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from registry.task_extractor import (
    ExtractedTask,
    TaskExtractor,
    _parse_signature,
    _parse_test_asserts,
)


# ---------------------------------------------------------------------------
# Fixtures — sample data matching real benchmark formats
# ---------------------------------------------------------------------------

HUMANEVAL_SAMPLE = {
    "task_id": "HumanEval/0",
    "prompt": (
        'from typing import List\n\n'
        'def has_close_elements(numbers: List[float], threshold: float) -> bool:\n'
        '    """Check if in given list of numbers, are any two numbers closer '
        'to each other than given threshold."""\n'
    ),
    "entry_point": "has_close_elements",
    "canonical_solution": (
        "    for idx, elem in enumerate(numbers):\n"
        "        for idx2, elem2 in enumerate(numbers):\n"
        "            if idx != idx2:\n"
        "                distance = abs(elem - elem2)\n"
        "                if distance < threshold:\n"
        "                    return True\n"
        "    return False\n"
    ),
    "test": (
        "def check(candidate):\n"
        "    assert candidate([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.3) == True\n"
    ),
}

MBPP_SAMPLE = {
    "task_id": 1,
    "text": "Write a function to find the minimum cost path to reach (m, n) from (0, 0)",
    "code": "def min_cost(cost, m, n):\n    ...",
    "test_list": [
        "assert min_cost([[1,2,3],[4,8,2],[1,5,3]], 2, 2) == 8",
    ],
}


@pytest.fixture()
def humaneval_jsonl(tmp_path: Path) -> Path:
    p = tmp_path / "HumanEval.jsonl"
    p.write_text(json.dumps(HUMANEVAL_SAMPLE) + "\n")
    return p


@pytest.fixture()
def mbpp_jsonl(tmp_path: Path) -> Path:
    p = tmp_path / "mbpp.jsonl"
    p.write_text(json.dumps(MBPP_SAMPLE) + "\n")
    return p


@pytest.fixture()
def apps_dir(tmp_path: Path) -> Path:
    """Create a minimal APPS-style directory with one problem."""
    d = tmp_path / "apps" / "0000"
    d.mkdir(parents=True)
    (d / "question.txt").write_text("Given an integer n, return its factorial.")
    (d / "solutions.json").write_text(json.dumps(["def f(n): return 1 if n<2 else n*f(n-1)"]))
    (d / "input_output.json").write_text(json.dumps({
        "inputs": ["5", "0"],
        "outputs": ["120", "1"],
    }))
    (d / "metadata.json").write_text(json.dumps({"difficulty": "introductory"}))
    return tmp_path / "apps"


@pytest.fixture()
def extractor(tmp_path: Path) -> TaskExtractor:
    return TaskExtractor(cache_dir=tmp_path)


# ---------------------------------------------------------------------------
# HumanEval extraction
# ---------------------------------------------------------------------------


class TestHumanEvalExtraction:
    def test_extracts_one_task(self, extractor: TaskExtractor, humaneval_jsonl: Path) -> None:
        tasks = extractor.extract_humaneval(humaneval_jsonl)
        assert len(tasks) == 1

    def test_task_id(self, extractor: TaskExtractor, humaneval_jsonl: Path) -> None:
        task = extractor.extract_humaneval(humaneval_jsonl)[0]
        assert task.task_id == "HumanEval/0"

    def test_source_name(self, extractor: TaskExtractor, humaneval_jsonl: Path) -> None:
        task = extractor.extract_humaneval(humaneval_jsonl)[0]
        assert task.source_name == "humaneval"

    def test_description_from_prompt(self, extractor: TaskExtractor, humaneval_jsonl: Path) -> None:
        task = extractor.extract_humaneval(humaneval_jsonl)[0]
        assert "has_close_elements" in task.description

    def test_function_signature(self, extractor: TaskExtractor, humaneval_jsonl: Path) -> None:
        task = extractor.extract_humaneval(humaneval_jsonl)[0]
        assert task.function_signature is not None
        assert "has_close_elements" in task.function_signature

    def test_solution_present(self, extractor: TaskExtractor, humaneval_jsonl: Path) -> None:
        task = extractor.extract_humaneval(humaneval_jsonl)[0]
        assert task.solution is not None
        assert "return True" in task.solution

    def test_test_cases_extracted(self, extractor: TaskExtractor, humaneval_jsonl: Path) -> None:
        task = extractor.extract_humaneval(humaneval_jsonl)[0]
        assert len(task.test_cases) >= 1
        assert "assert" in task.test_cases[0]["input"]


# ---------------------------------------------------------------------------
# MBPP extraction
# ---------------------------------------------------------------------------


class TestMBPPExtraction:
    def test_extracts_one_task(self, extractor: TaskExtractor, mbpp_jsonl: Path) -> None:
        tasks = extractor.extract_mbpp(mbpp_jsonl)
        assert len(tasks) == 1

    def test_task_id_is_string(self, extractor: TaskExtractor, mbpp_jsonl: Path) -> None:
        task = extractor.extract_mbpp(mbpp_jsonl)[0]
        assert task.task_id == "1"

    def test_description(self, extractor: TaskExtractor, mbpp_jsonl: Path) -> None:
        task = extractor.extract_mbpp(mbpp_jsonl)[0]
        assert "minimum cost path" in task.description

    def test_test_cases(self, extractor: TaskExtractor, mbpp_jsonl: Path) -> None:
        task = extractor.extract_mbpp(mbpp_jsonl)[0]
        assert len(task.test_cases) == 1
        assert "assert" in task.test_cases[0]["input"]

    def test_function_signature(self, extractor: TaskExtractor, mbpp_jsonl: Path) -> None:
        task = extractor.extract_mbpp(mbpp_jsonl)[0]
        assert task.function_signature is not None
        assert "min_cost" in task.function_signature

    def test_solution_present(self, extractor: TaskExtractor, mbpp_jsonl: Path) -> None:
        task = extractor.extract_mbpp(mbpp_jsonl)[0]
        assert task.solution is not None


# ---------------------------------------------------------------------------
# APPS extraction
# ---------------------------------------------------------------------------


class TestAPPSExtraction:
    def test_extracts_one_task(self, extractor: TaskExtractor, apps_dir: Path) -> None:
        tasks = extractor.extract_apps(apps_dir)
        assert len(tasks) == 1

    def test_task_id_prefixed(self, extractor: TaskExtractor, apps_dir: Path) -> None:
        task = extractor.extract_apps(apps_dir)[0]
        assert task.task_id == "APPS/0000"

    def test_difficulty_mapped(self, extractor: TaskExtractor, apps_dir: Path) -> None:
        task = extractor.extract_apps(apps_dir)[0]
        assert task.difficulty == "easy"

    def test_test_cases(self, extractor: TaskExtractor, apps_dir: Path) -> None:
        task = extractor.extract_apps(apps_dir)[0]
        assert len(task.test_cases) == 2
        assert task.test_cases[0]["expected_output"] == "120"

    def test_solution_present(self, extractor: TaskExtractor, apps_dir: Path) -> None:
        task = extractor.extract_apps(apps_dir)[0]
        assert task.solution is not None

    def test_empty_dir_returns_empty(self, extractor: TaskExtractor, tmp_path: Path) -> None:
        empty = tmp_path / "empty_apps"
        empty.mkdir()
        assert extractor.extract_apps(empty) == []

    def test_nonexistent_dir_returns_empty(self, extractor: TaskExtractor, tmp_path: Path) -> None:
        assert extractor.extract_apps(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# ExtractedTask data model
# ---------------------------------------------------------------------------


class TestExtractedTask:
    def test_required_fields(self) -> None:
        task = ExtractedTask(
            source_name="test",
            task_id="T/0",
            description="Do something",
        )
        assert task.test_cases == []
        assert task.difficulty is None
        assert task.function_signature is None
        assert task.solution is None

    def test_all_fields(self) -> None:
        task = ExtractedTask(
            source_name="test",
            task_id="T/0",
            description="Do something",
            test_cases=[{"input": "1", "expected_output": "2"}],
            difficulty="easy",
            function_signature="def f(x)",
            solution="return x + 1",
        )
        assert task.difficulty == "easy"
        assert len(task.test_cases) == 1


# ---------------------------------------------------------------------------
# Writer methods
# ---------------------------------------------------------------------------


class TestWriters:
    def test_write_descriptions_one_per_line(self, tmp_path: Path) -> None:
        tasks = [
            ExtractedTask(source_name="a", task_id="1", description="Hello world"),
            ExtractedTask(source_name="a", task_id="2", description="Fizzbuzz"),
        ]
        out = tmp_path / "descs.txt"
        TaskExtractor.write_descriptions(tasks, out)
        lines = out.read_text().splitlines()
        assert len(lines) == 2
        assert lines[0] == "Hello world"

    def test_write_descriptions_collapses_newlines(self, tmp_path: Path) -> None:
        tasks = [
            ExtractedTask(source_name="a", task_id="1", description="Line1\nLine2\nLine3"),
        ]
        out = tmp_path / "descs.txt"
        TaskExtractor.write_descriptions(tasks, out)
        lines = out.read_text().splitlines()
        assert len(lines) == 1
        assert "\n" not in lines[0]

    def test_write_descriptions_skips_empty(self, tmp_path: Path) -> None:
        tasks = [
            ExtractedTask(source_name="a", task_id="1", description=""),
            ExtractedTask(source_name="a", task_id="2", description="ok"),
        ]
        out = tmp_path / "descs.txt"
        TaskExtractor.write_descriptions(tasks, out)
        lines = out.read_text().splitlines()
        assert len(lines) == 1

    def test_write_tasks_jsonl_valid_json(self, tmp_path: Path) -> None:
        tasks = [
            ExtractedTask(source_name="a", task_id="1", description="Hello"),
            ExtractedTask(source_name="b", task_id="2", description="World"),
        ]
        out = tmp_path / "tasks.jsonl"
        TaskExtractor.write_tasks_jsonl(tasks, out)
        lines = out.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            rec = json.loads(line)
            assert "source_name" in rec
            assert "task_id" in rec
            assert "description" in rec

    def test_write_tasks_jsonl_roundtrip(self, tmp_path: Path) -> None:
        tasks = [
            ExtractedTask(
                source_name="x",
                task_id="X/0",
                description="Desc",
                test_cases=[{"input": "1", "expected_output": "2"}],
                difficulty="hard",
                function_signature="def f(x)",
                solution="return x+1",
            ),
        ]
        out = tmp_path / "tasks.jsonl"
        TaskExtractor.write_tasks_jsonl(tasks, out)
        rec = json.loads(out.read_text().strip())
        assert rec["source_name"] == "x"
        assert rec["difficulty"] == "hard"
        assert len(rec["test_cases"]) == 1


# ---------------------------------------------------------------------------
# Difficulty mapping
# ---------------------------------------------------------------------------


class TestDifficultyMapping:
    def test_apps_introductory_maps_to_easy(self, extractor: TaskExtractor, apps_dir: Path) -> None:
        task = extractor.extract_apps(apps_dir)[0]
        assert task.difficulty == "easy"

    def test_apps_interview_maps_to_medium(self, extractor: TaskExtractor, tmp_path: Path) -> None:
        d = tmp_path / "apps2" / "0001"
        d.mkdir(parents=True)
        (d / "question.txt").write_text("Solve this.")
        (d / "metadata.json").write_text(json.dumps({"difficulty": "interview"}))
        task = extractor.extract_apps(tmp_path / "apps2")[0]
        assert task.difficulty == "medium"

    def test_apps_competition_maps_to_hard(self, extractor: TaskExtractor, tmp_path: Path) -> None:
        d = tmp_path / "apps3" / "0002"
        d.mkdir(parents=True)
        (d / "question.txt").write_text("Hard problem.")
        (d / "metadata.json").write_text(json.dumps({"difficulty": "competition"}))
        task = extractor.extract_apps(tmp_path / "apps3")[0]
        assert task.difficulty == "hard"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_signature_simple(self) -> None:
        sig = _parse_signature("def foo(x, y):\n    return x + y")
        assert sig == "def foo(x, y)"

    def test_parse_signature_none(self) -> None:
        assert _parse_signature("no function here") is None

    def test_parse_test_asserts(self) -> None:
        code = "def check(c):\n    assert c(1) == 2\n    assert c(3) == 4\n"
        results = _parse_test_asserts(code)
        assert len(results) == 2
        assert results[0]["input"].startswith("assert")
