"""131.115: scripts/regenerate.py renders generate_toke.md before sending it.

The template is a ``str.format`` template. Sent raw, the request carried
literal ``{category}``/``{task_description}``/``{expected_signature}``
placeholders and every code example with doubled braces
(``t=$point{{x:i64;y:i64}};``), which is not valid toke.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import main as corpus_main  # noqa: E402
import regenerate  # noqa: E402
from generator.curriculum import TaskSpec  # noqa: E402
from generator.prompt_render import render_task_template, unrendered_markers  # noqa: E402
from trial import runner  # noqa: E402

PROMPTS = REPO / "prompts"


@pytest.fixture(scope="module")
def templates() -> dict[str, str]:
    return regenerate.load_prompts({"prompts_dir": str(PROMPTS)})


def _tasks() -> list[dict]:
    cfg = {"seed": 7, "providers": {}, "prompts_dir": str(PROMPTS), "total_tasks": 12}
    return regenerate.build_task_list(cfg, None)


def test_raw_template_does_carry_markers() -> None:
    # guards the test itself: if the template stops being a format template,
    # the regression assertions below would pass vacuously
    raw = (PROMPTS / "generate_toke.md").read_text()
    markers = unrendered_markers(raw)
    assert "{category}" in markers and "{{" in markers


def test_regenerate_request_has_no_placeholder_or_doubled_brace(templates) -> None:
    tasks = _tasks()
    assert tasks
    for task in tasks:
        prompt = regenerate.build_prompt(task, templates)
        assert unrendered_markers(prompt) == [], task["task_id"]
        assert "{{" not in prompt
        assert f"**Category:** {task['category']}" in prompt
        # code examples arrive as real toke, single-braced
        assert "t=$point{x:i64;y:i64};" in prompt
        assert f"Task: {task['task_id']}" in prompt


def test_regenerate_uses_task_description_and_signature_when_present(templates) -> None:
    task = {"task_id": "A-MTH-0001", "phase": "A", "category": "A-MTH", "seed": 1,
            "description": "Add two integers.", "expected_signature": "f=add(a:i64;b:i64):i64"}
    prompt = regenerate.build_prompt(task, templates)
    assert "**Task:** Add two integers." in prompt
    assert "**Expected function signature:** f=add(a:i64;b:i64):i64" in prompt


def test_system_prompt_is_not_format_rendered(templates) -> None:
    # system.md is plain markdown with literal single braces; rendering it
    # would raise or mangle it
    prompt = regenerate.build_prompt(_tasks()[0], templates)
    assert templates["system"] in prompt


def test_one_renderer_for_all_three_callers() -> None:
    template = (PROMPTS / "generate_toke.md").read_text()
    spec = TaskSpec(task_id="A-MTH-0001", category="A-MTH", description="Add two integers.",
                    expected_signature="f=add(a:i64;b:i64):i64", difficulty=1)
    expected = render_task_template(template, category=spec.category,
                                    task_description=spec.description,
                                    expected_signature=spec.expected_signature)
    assert corpus_main.format_toke_prompt(template, spec) == expected
    assert runner._format_prompt(template, spec) == expected
    assert unrendered_markers(expected) == []
