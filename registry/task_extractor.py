"""Task description extractor — uniform format from heterogeneous benchmarks.

Story 9.2.6 (Part 3).

Reads downloaded benchmark data and produces :class:`ExtractedTask` records
regardless of the upstream format.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ExtractedTask:
    source_name: str
    task_id: str
    description: str
    test_cases: list[dict[str, Any]] = field(default_factory=list)
    difficulty: str | None = None
    function_signature: str | None = None
    solution: str | None = None


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

# Maps APPS numeric difficulty to a human-readable label.
_APPS_DIFFICULTY = {
    "introductory": "easy",
    "interview": "medium",
    "competition": "hard",
}

# Maps LeetCode labels to canonical difficulty.
_LEETCODE_DIFFICULTY = {
    "Easy": "easy",
    "Medium": "medium",
    "Hard": "hard",
}


def _parse_signature(prompt: str) -> str | None:
    """Extract the first ``def …(…)`` line from a prompt string.

    Handles optional return-type annotations such as ``-> bool``.
    """
    match = re.search(r"(def \w+\(.*?\)(?:\s*->.*?)?)\s*:", prompt, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _parse_test_asserts(test_code: str) -> list[dict[str, str]]:
    """Pull ``assert candidate(…) == …`` lines into input/expected pairs."""
    results: list[dict[str, str]] = []
    for line in test_code.splitlines():
        line = line.strip()
        if line.startswith("assert"):
            results.append({"input": line, "expected_output": ""})
    return results


class TaskExtractor:
    """Extracts task descriptions and test cases from downloaded benchmarks.

    Produces a uniform :class:`ExtractedTask` regardless of the source
    dataset format.
    """

    def __init__(self, cache_dir: Path = Path("data/benchmarks")) -> None:
        self.cache_dir = cache_dir

    # -- per-source extractors ----------------------------------------------

    def extract_humaneval(self, data_path: Path) -> list[ExtractedTask]:
        """Extract from HumanEval JSONL format.

        HumanEval format::

            {"task_id": "HumanEval/0", "prompt": "...",
             "entry_point": "has_close_elements", "test": "...",
             "canonical_solution": "..."}
        """
        tasks: list[ExtractedTask] = []
        import gzip
        opener = gzip.open if str(data_path).endswith(".gz") else open
        with opener(data_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                prompt = rec.get("prompt", "")
                test_code = rec.get("test", "")
                tasks.append(
                    ExtractedTask(
                        source_name="humaneval",
                        task_id=rec["task_id"],
                        description=prompt,
                        test_cases=_parse_test_asserts(test_code),
                        difficulty=None,
                        function_signature=_parse_signature(prompt),
                        solution=rec.get("canonical_solution"),
                    )
                )
        return tasks

    def extract_mbpp(self, data_path: Path) -> list[ExtractedTask]:
        """Extract from MBPP JSON/JSONL format.

        MBPP format::

            {"task_id": 1, "text": "...", "code": "...",
             "test_list": ["assert ...", ...]}
        """
        tasks: list[ExtractedTask] = []
        text = data_path.read_text()
        # Support both single JSON array and JSONL
        try:
            records = json.loads(text)
            if isinstance(records, dict):
                records = [records]
        except json.JSONDecodeError:
            records = [json.loads(l) for l in text.splitlines() if l.strip()]

        for rec in records:
            test_list = rec.get("test_list", [])
            test_cases = [{"input": t, "expected_output": ""} for t in test_list]
            code = rec.get("code", "")
            tasks.append(
                ExtractedTask(
                    source_name="mbpp",
                    task_id=str(rec["task_id"]),
                    description=rec.get("text", ""),
                    test_cases=test_cases,
                    difficulty=None,
                    function_signature=_parse_signature(code),
                    solution=code or None,
                )
            )
        return tasks

    def extract_apps(self, data_path: Path) -> list[ExtractedTask]:
        """Extract from APPS directory format.

        Each problem lives in a numbered directory containing:
        ``question.txt``, ``solutions.json``, ``input_output.json``,
        and optionally ``metadata.json`` with a ``difficulty`` key.
        """
        tasks: list[ExtractedTask] = []
        if not data_path.is_dir():
            return tasks

        for problem_dir in sorted(data_path.iterdir()):
            if not problem_dir.is_dir():
                continue
            question_path = problem_dir / "question.txt"
            if not question_path.exists():
                continue

            description = question_path.read_text().strip()
            task_id = f"APPS/{problem_dir.name}"

            # Difficulty
            difficulty: str | None = None
            meta_path = problem_dir / "metadata.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                raw_diff = meta.get("difficulty", "")
                difficulty = _APPS_DIFFICULTY.get(raw_diff, raw_diff or None)

            # Test cases
            test_cases: list[dict[str, str]] = []
            io_path = problem_dir / "input_output.json"
            if io_path.exists():
                io_data = json.loads(io_path.read_text())
                inputs = io_data.get("inputs", [])
                outputs = io_data.get("outputs", [])
                for inp, out in zip(inputs, outputs):
                    test_cases.append({"input": str(inp), "expected_output": str(out)})

            # Solution
            solution: str | None = None
            sol_path = problem_dir / "solutions.json"
            if sol_path.exists():
                sols = json.loads(sol_path.read_text())
                if isinstance(sols, list) and sols:
                    solution = sols[0]

            tasks.append(
                ExtractedTask(
                    source_name="apps",
                    task_id=task_id,
                    description=description,
                    test_cases=test_cases,
                    difficulty=difficulty,
                    function_signature=None,
                    solution=solution,
                )
            )
        return tasks

    def extract_codecontests(self, data_path: Path) -> list[ExtractedTask]:
        """Extract from CodeContests JSONL format."""
        tasks: list[ExtractedTask] = []
        if not data_path.exists():
            return tasks

        with open(data_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                test_cases: list[dict[str, str]] = []
                for inp, out in zip(
                    rec.get("public_tests", {}).get("input", []),
                    rec.get("public_tests", {}).get("output", []),
                ):
                    test_cases.append({"input": inp, "expected_output": out})

                diff_raw = rec.get("difficulty", {})
                difficulty: str | None = None
                if isinstance(diff_raw, dict):
                    difficulty = diff_raw.get("label")
                elif isinstance(diff_raw, str):
                    difficulty = diff_raw or None

                tasks.append(
                    ExtractedTask(
                        source_name="codecontests",
                        task_id=str(rec.get("name", rec.get("task_id", ""))),
                        description=rec.get("description", ""),
                        test_cases=test_cases,
                        difficulty=difficulty,
                        function_signature=None,
                        solution=None,
                    )
                )
        return tasks

    def extract_taco(self, data_path: Path) -> list[ExtractedTask]:
        """Extract from TACO JSONL format."""
        tasks: list[ExtractedTask] = []
        if not data_path.exists():
            return tasks

        with open(data_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                test_cases: list[dict[str, str]] = []
                for tc in rec.get("test_cases", []):
                    if isinstance(tc, dict):
                        test_cases.append({
                            "input": str(tc.get("input", "")),
                            "expected_output": str(tc.get("output", "")),
                        })

                tasks.append(
                    ExtractedTask(
                        source_name="taco",
                        task_id=str(rec.get("task_id", rec.get("id", ""))),
                        description=rec.get("question", rec.get("description", "")),
                        test_cases=test_cases,
                        difficulty=rec.get("difficulty"),
                        function_signature=None,
                        solution=rec.get("solution"),
                    )
                )
        return tasks

    def extract_leetcode(self, data_path: Path) -> list[ExtractedTask]:
        """Extract from LeetCode dataset JSONL format.

        Difficulties: Easy, Medium, Hard.
        """
        tasks: list[ExtractedTask] = []
        if not data_path.exists():
            return tasks

        with open(data_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                raw_diff = rec.get("difficulty", "")
                difficulty = _LEETCODE_DIFFICULTY.get(raw_diff, raw_diff or None)

                test_cases: list[dict[str, str]] = []
                for tc in rec.get("test_cases", []):
                    if isinstance(tc, dict):
                        test_cases.append({
                            "input": str(tc.get("input", "")),
                            "expected_output": str(tc.get("output", "")),
                        })

                tasks.append(
                    ExtractedTask(
                        source_name="leetcodedataset",
                        task_id=str(rec.get("id", rec.get("task_id", ""))),
                        description=rec.get("description", rec.get("content", "")),
                        test_cases=test_cases,
                        difficulty=difficulty,
                        function_signature=rec.get("function_signature"),
                        solution=rec.get("solution"),
                    )
                )
        return tasks

    # -- aggregate ----------------------------------------------------------

    def extract_all(self) -> dict[str, list[ExtractedTask]]:
        """Extract from all cached datasets.

        Returns ``{source_name: [tasks]}``.  Sources that are not cached are
        silently skipped.
        """
        results: dict[str, list[ExtractedTask]] = {}

        _dispatch: dict[str, tuple[str, Any]] = {
            "humaneval": ("human-eval/data/HumanEval.jsonl.gz", self.extract_humaneval),
            "mbpp": ("mbpp/mbpp.jsonl", self.extract_mbpp),
            "apps": ("apps/train", self.extract_apps),
            "codecontests": ("code_contests/data.jsonl", self.extract_codecontests),
            "taco": ("TACO/data.jsonl", self.extract_taco),
            "leetcodedataset": ("leetcode/data.jsonl", self.extract_leetcode),
        }

        for name, (rel_path, extractor) in _dispatch.items():
            path = self.cache_dir / rel_path
            if path.exists():
                results[name] = extractor(path)

        return results

    # -- writers ------------------------------------------------------------

    @staticmethod
    def write_descriptions(tasks: list[ExtractedTask], output_path: Path) -> None:
        """Write task descriptions to a file (one per line) for firewall loading."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as fh:
            for task in tasks:
                # Collapse newlines so each description is a single line.
                desc = task.description.replace("\n", " ").replace("\r", "").strip()
                if desc:
                    fh.write(desc + "\n")

    @staticmethod
    def write_tasks_jsonl(tasks: list[ExtractedTask], output_path: Path) -> None:
        """Write full extracted tasks as JSONL for pipeline consumption."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as fh:
            for task in tasks:
                fh.write(json.dumps(asdict(task), ensure_ascii=False) + "\n")
