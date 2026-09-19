"""131.72 — the generation prompt must ask for exactly what the gate accepts.

The defect this locks out: `build_prompt.format_test_cases` used to dump a test
case's expectation as raw JSON (`expected output: {"err": "NotFound"}`) while
the acceptance gate compared stdout against `validate.render_expected`
(`err:notfound`).  A worker was told to print the harness's error marker and
was then failed for printing it; `str`, `float`, `list` and `None`
expectations diverged the same way (`"alice"` vs `alice`, `2.0` vs `2`,
`null` vs `None`).

The invariant, per task type:

  full_program     the stdout lines the prompt shows == the `want` list
                   `run_shard.run_test_cases` (and `audit.audit_one`) builds
                   from `validate.render_expected`
  single_function  == `driver.expected_lines`, the list `audit.audit_one`
                   compares the driver-main run against

Two layers of guard, because either alone can be defeated:

  1. VALUE agreement over a table of expectation shapes — catches a renderer
     that changes behaviour.
  2. DELEGATION identity — `build_prompt` must be calling the gate's own
     functions, not a private copy.  Catches a second renderer being
     hand-written here, which is how the two drifted apart in the first place.
"""
import json
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import build_prompt          # noqa: E402
import driver as drv         # noqa: E402
import run_shard             # noqa: E402
import validate              # noqa: E402

# Every expectation shape the corpus actually carries, plus the ones whose
# JSON spelling differed from the gate's (the 131.72 audit counted, over the
# 12 shards' full_program specs: 10,224 str cases, 1,038 list, 90 float).
EXPECTATIONS = [
    {"err": "NotFound"},            # a_tests err marker  -> err:notfound
    {"error": "DivByZero"},         # the `error` spelling -> err:divbyzero
    {"err": "ParseFailed", "msg": "x"},
    -10,
    0,
    2.0,                            # gate: 2       json: 2.0
    2.5,
    True,                           # gate: true    driver: 1
    False,
    "alice",                        # gate: alice   json: "alice"
    "two\nlines",                   # two stdout lines
    "",
    [1, 2, 3],                      # driver: one line per element
    ["abc", "abcd"],
    [],
    None,                           # gate: None    json: null
]

_LINE = re.compile(r"\s*inputs=.* -> stdout (\[.*\])$")


def _spec(ttype, cases):
    return {
        "task_id": "A-ERR-0083v1",
        "category": "A-ERR",
        "task_type": ttype,
        "description_v03": "Write a function f=parseordefault(p:str):i64!$parseerr "
                           "that parses p. Return ParseFailed error when p is not "
                           "numeric. Define t=$parseerr{ParseFailed:str;DivByZero:bool}.",
        "input_types_v03": ["str"],
        "output_type_v03": "i64!ParseErr",
        "domain_context_v03": "",
        "test_cases": cases,
    }


def _prompt_stdout_lines(text):
    """The stdout lines the rendered prompt tells the worker to produce."""
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out.extend(json.loads(m.group(1)))
    return out


def _gate_full_program(spec):
    """Verbatim `want` construction of run_shard.run_test_cases."""
    want = []
    for tc in spec["test_cases"]:
        want.extend(run_shard.render_expected(tc.get("expected")).split("\n"))
    return want


def _gate_single_function(spec):
    return drv.expected_lines(spec)


GATES = {"full_program": _gate_full_program, "single_function": _gate_single_function}


@pytest.mark.parametrize("ttype", sorted(GATES))
@pytest.mark.parametrize("expected", EXPECTATIONS,
                         ids=[json.dumps(e) for e in EXPECTATIONS])
def test_one_case_agrees(ttype, expected):
    """Per case: the prompt's stdout lines are the gate's expected lines."""
    spec = _spec(ttype, [{"inputs": ["x"], "expected": expected}])
    assert _prompt_stdout_lines(build_prompt.build(spec, "")) == GATES[ttype](spec)


@pytest.mark.parametrize("ttype", sorted(GATES))
def test_whole_spec_agrees(ttype):
    """End to end over every shape at once, order included."""
    spec = _spec(ttype, [{"inputs": [i], "expected": e}
                         for i, e in enumerate(EXPECTATIONS)])
    assert _prompt_stdout_lines(build_prompt.build(spec, "")) == GATES[ttype](spec)


def test_bool_follows_its_own_gate():
    """The two gates DISAGREE on bool (full_program `true`, driver `1`), so an
    agreeing prompt must render bools differently per task type — proof the
    prompt is delegating per task type rather than picking one renderer."""
    cases = [{"inputs": [1], "expected": True}]
    assert _prompt_stdout_lines(build_prompt.build(_spec("full_program", cases), "")) == ["true"]
    assert _prompt_stdout_lines(build_prompt.build(_spec("single_function", cases), "")) == ["1"]


@pytest.mark.parametrize("ttype", sorted(GATES))
def test_err_marker_never_reaches_the_prompt(ttype):
    """The 131.72 regression itself: no spelling of the raw marker may appear
    in a rendered prompt's test-case block, only `err:<variant>`."""
    spec = _spec(ttype, [{"inputs": ["xyz"], "expected": {"err": "ParseFailed"}},
                         {"inputs": ["0"], "expected": {"error": "DivByZero"}}])
    body = "\n".join(l for l in build_prompt.build(spec, "").splitlines()
                     if _LINE.match(l))
    assert "err:parsefailed" in body and "err:divbyzero" in body
    for banned in ('{"err": "ParseFailed"}', "{'err': 'ParseFailed'}",
                   '{"error": "DivByZero"}', "{'error': 'DivByZero'}"):
        assert banned not in body, f"raw marker {banned} rendered into the prompt"


@pytest.mark.parametrize("ttype", sorted(GATES))
def test_err_convention_is_stated(ttype):
    """A prompt carrying an err case must spell the convention out — the
    requirement has to be legible, not merely implied by the line."""
    spec = _spec(ttype, [{"inputs": ["xyz"], "expected": {"err": "ParseFailed"}}])
    text = build_prompt.build(spec, "")
    assert "err:<variant>" in text
    assert "hard rejection" in text


def test_no_err_convention_without_an_err_case():
    """...and only then: a spec with no err expectation gets no err prose."""
    spec = _spec("full_program", [{"inputs": ["5"], "expected": -10}])
    assert "err:<variant>" not in build_prompt.build(spec, "")


# --------------------------------------------------------------- delegation --

def test_prompt_uses_the_gates_own_renderers():
    """Anti-drift: build_prompt must hold the SAME function objects the gate
    runs, not a copy of them.  If someone re-implements the rendering here,
    this fails even when the two happen to agree today."""
    assert build_prompt.render_expected is validate.render_expected
    assert run_shard.render_expected is validate.render_expected
    assert build_prompt.drv.expected_lines is drv.expected_lines


def test_expected_stdout_lines_delegates(monkeypatch):
    """Behavioural proof of the same thing: poison each gate renderer and the
    prompt must change with it."""
    monkeypatch.setattr(validate, "render_expected", lambda v: "POISON-FP")
    monkeypatch.setattr(build_prompt, "render_expected", validate.render_expected)
    monkeypatch.setattr(drv, "expected_lines", lambda s: ["POISON-SF"])
    fp = _spec("full_program", [{"inputs": ["x"], "expected": 1}])
    sf = _spec("single_function", [{"inputs": ["x"], "expected": 1}])
    assert _prompt_stdout_lines(build_prompt.build(fp, "")) == ["POISON-FP"]
    assert _prompt_stdout_lines(build_prompt.build(sf, "")) == ["POISON-SF"]


def test_no_json_dumps_of_an_expectation():
    """Source-level guard: `json.dumps(tc[...'expected'...])` is the exact
    construct that caused 131.72 — it must not come back into this file."""
    src = open(os.path.join(os.path.dirname(HERE), "build_prompt.py")).read()
    body = src.split("def format_test_cases", 1)[1].split("\ndef ", 1)[0]
    assert not re.search(r"json\.dumps\(\s*tc\.get\(['\"]expected['\"]", body)
    assert not re.search(r"json\.dumps\(\s*tc\[['\"]expected['\"]", body)
