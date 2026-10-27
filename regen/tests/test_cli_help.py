"""131.89 — regen scripts that used to parse sys.argv by position must answer
--help with usage text (exit 0) instead of a traceback, keep their positional
behaviour, and reject a malformed argument with an argparse error (exit 2)."""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
sys.path.insert(0, REGEN)
import build_prompt          # noqa: E402
import recheck_131_44        # noqa: E402
import u64_recheck_131       # noqa: E402

SCRIPTS = ["recheck_131_44.py", "u64_recheck_131.py", "build_prompt.py"]


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, os.path.join(REGEN, script), *args],
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("script", SCRIPTS)
def test_help_prints_usage_not_traceback(script: str) -> None:
    r = _run(script, "--help")
    assert r.returncode == 0, r.stderr
    assert "usage:" in r.stdout
    assert "Traceback" not in r.stderr


@pytest.mark.parametrize("script", ["recheck_131_44.py", "u64_recheck_131.py"])
def test_non_integer_workers_is_an_argparse_error(script: str) -> None:
    # Negative control: the old int(sys.argv[1]) raised ValueError here.
    r = _run(script, "lots")
    assert r.returncode == 2
    assert "Traceback" not in r.stderr
    assert "invalid int value" in r.stderr


@pytest.mark.parametrize("mod", [recheck_131_44, u64_recheck_131])
def test_workers_positional_behaviour_unchanged(mod) -> None:
    assert mod.parse_args([]).workers == 8
    assert mod.parse_args(["3"]).workers == 3


def test_build_prompt_positional_spec_and_card_default() -> None:
    a = build_prompt.parse_args(['{"x": 1}'])
    assert a.spec == '{"x": 1}'
    assert a.card == os.path.join(REGEN, "syntax_card.md")
    assert build_prompt.parse_args(["s.json", "--card", "c.md"]).card == "c.md"


def test_build_prompt_requires_a_spec() -> None:
    r = _run("build_prompt.py")
    assert r.returncode == 2
    assert "Traceback" not in r.stderr


def test_build_prompt_end_to_end(tmp_path) -> None:
    card = tmp_path / "card.md"
    card.write_text("CARD-SENTINEL-131-89\n")
    spec = {"task_type": "full_program", "task_id": "A-MTH-0001",
            "category": "A-MTH", "description": "print 1",
            "function_name": "f", "signature": "f():i64", "test_cases": []}
    r = _run("build_prompt.py", json.dumps(spec), "--card", str(card))
    assert r.returncode == 0, r.stderr
    assert "CARD-SENTINEL-131-89" in r.stdout
