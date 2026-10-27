"""131.75 — the generation prompt states the gates a record must pass, with the
numbers read from the code that enforces them.

Before this story a worker was never told about the idiom floor, the depth and
function-size limits, or the pattern-lint hard gate, all of which rejected its
output after the fact. The prompt also showed the sampler's split input types
(`['@(i64']`, 3,976 specs) that the gate silently replaces, and cut stdin
fixtures at 400 characters.
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
sys.path.insert(0, REGEN)
import build_prompt  # noqa: E402
import driver as drv  # noqa: E402
import idiom_judge  # noqa: E402
import metrics  # noqa: E402

# toke story 119.8 moves the page to docs/compiler/ (and deletes the root copy;
# until then the compiler/ copy is stale), so the root copy wins while it exists.
_TOKE = [os.path.join(os.path.dirname(os.path.dirname(REGEN)), "toke"),
         os.path.expanduser("~/tk/toke")]
LINT_RULES = [os.path.join(t, "docs", sub, "lint-rules-v1.md")
              for t in _TOKE for sub in ("", "compiler")]

BASE = {"task_id": "A-SRT-0001", "category": "A-SRT", "description":
        "Write a function f=nth(arr:@(i64);n:i64):i64 that returns the n-th element.",
        "input_types": "['@(i64']", "output_type": "i64", "test_cases": []}


def _prompt(**kw):
    spec = dict(BASE, **kw)
    return build_prompt.build(spec, "CARD")


@pytest.mark.parametrize("ttype,extra", [
    ("full_program", {}),
    ("single_function", {}),
    ("stdin_program", {"library": {}}),
    ("migrate_fix", {"legacy_source": "m=x;", "legacy_diagnostics": "E2002"}),
])
def test_every_task_type_states_the_gates(ttype, extra):
    out = _prompt(task_type=ttype, **extra)
    assert build_prompt.gates_block(dict(BASE, task_type=ttype)) in out


def test_gate_numbers_come_from_the_enforcing_code():
    g = build_prompt.gates_block(BASE)
    assert f"depth <= {metrics.MAX_DEPTH} " in g
    assert f"longer than {metrics.MAX_FUNC_BYTES} bytes" in g
    assert f"idiom score >= {idiom_judge.IDIOM_FLOOR:g}:" in g
    assert f"at most {idiom_judge.PER_RULE_CAP} per rule" in g
    for rule, pen in idiom_judge.PATTERN_RULES.items():
        assert f"{rule} -{pen:g}" in g
    for rule in idiom_judge.HARD_GATE_RULES:
        assert f"`{rule}`" in g


def test_gates_follow_a_changed_constant(monkeypatch):
    monkeypatch.setattr(metrics, "MAX_DEPTH", 7)
    monkeypatch.setattr(metrics, "MAX_FUNC_BYTES", 999)
    monkeypatch.setattr(idiom_judge, "IDIOM_FLOOR", 0.75)
    g = build_prompt.gates_block(BASE)
    assert "depth <= 7 " in g and "longer than 999 bytes" in g and ">= 0.75:" in g


def test_structure_gate_uses_the_same_constants():
    assert metrics.structure_failure({"max_depth": metrics.MAX_DEPTH,
                                      "max_func_bytes": metrics.MAX_FUNC_BYTES}) is None
    assert metrics.structure_failure({"max_depth": metrics.MAX_DEPTH + 1,
                                      "max_func_bytes": 0}) == \
        f"nesting depth {metrics.MAX_DEPTH + 1} > {metrics.MAX_DEPTH}"
    assert metrics.structure_failure({"max_depth": 0,
                                      "max_func_bytes": metrics.MAX_FUNC_BYTES + 1}) == \
        f"function {metrics.MAX_FUNC_BYTES + 1} bytes > {metrics.MAX_FUNC_BYTES}"


ENFORCERS = ["run_shard.py", "check_repair.py", "bank_repairs.py", "pattern_sweep.py",
             "pattern_autofix.py", "build_prompt.py", "library_prep.py"]


@pytest.mark.parametrize("fname", ENFORCERS)
def test_no_hand_typed_limit_in_an_enforcer(fname):
    """A literal next to max_depth / max_func_bytes / the idiom floor is how the
    numbers drifted apart; every site reads the constants now."""
    with open(os.path.join(REGEN, fname), encoding="utf-8") as f:
        src = f.read()
    bad = [ln.strip() for ln in src.splitlines()
           if re.search(r"max_depth[^\n]*[<>]=?\s*\d|max_depth\"?:\s*\d", ln)
           or re.search(r"max_func_bytes[^\n]*[<>]=?\s*\d{3}|max_func_bytes\"?:\s*\d", ln)
           or re.search(r"\(limit \d+\)|over \d{3} bytes|[<>]=?\s*0\.6\b", ln)]
    assert not bad, bad


def test_hard_gate_rules_match_the_lint_rule_table():
    path = next((p for p in LINT_RULES if os.path.exists(p)), None)
    if path is None:
        pytest.skip("no toke checkout beside toke-corpus")
    with open(path, encoding="utf-8") as f:
        table = dict(re.findall(r"^\| `([a-z-]+)` \| (error|warning|hint) \|", f.read(), re.MULTILINE))
    gated = tuple(r for r in idiom_judge.PATTERN_RULES
                  if table.get(r) in idiom_judge.GATE_SEVERITIES)
    assert set(idiom_judge.PATTERN_RULES) <= set(table)
    assert gated == idiom_judge.HARD_GATE_RULES


def test_split_input_types_are_shown_as_the_gate_reads_them():
    out = _prompt(task_type="full_program")
    want = drv.effective_input_types(dict(BASE, input_types=["@(i64"]))
    assert want == ["@i64", "i64"]
    assert f"Target signature: inputs {want} -> i64" in out
    assert "'@(i64'" not in out


def test_balanced_input_types_are_unchanged():
    out = _prompt(task_type="full_program", input_types="['i64', 'str']",
                  description="Write f=g(a:i64;b:str):i64.")
    assert "inputs ['i64', 'str'] -> i64" in out


def test_stdin_fixtures_are_not_truncated():
    big = "x" * 2000
    out = _prompt(task_type="stdin_program", library={},
                  test_cases=[{"input": "", "expected_output": "1",
                               "fixtures": [{"path": "a.txt", "content": big}]}])
    assert big + '"}]' in out


def test_style_mandate_exemption_is_stated():
    desc = BASE["description"] + " Variant 3: Accumulate the result in a mutable binding."
    g = build_prompt.gates_block(dict(BASE, description=desc))
    for rule in idiom_judge.MANDATE_EXEMPTIONS["mutable binding"]:
        assert rule in g.split("exempts:")[1]
    assert "exempts" not in build_prompt.gates_block(BASE)
