"""131.113 (gate half) — the phase-era prompts main.py, trial/runner.py and
scripts/regenerate.py load (prompts/system.md, system_base.md,
generate_toke.md) and the few-shot template (few-shot-template.md) are
compile-gated like prompts/spec-reference.md (131.62) and the syntax card.

Until this story they taught the pre-v0.4 language (`M=`/`F=` heads, `=` as
equality, no `!=`/`&&`/`%`, `expr|{...}` match, `$str`), and nothing checked
them. Now:

  1. every code block and statement span compiles as the page says
     (regen/check_card.py, run with the 131.103 corpus pin);
  2. every whole program builds and exits 0 (the few-shot template's programs
     sit inside its `text` prompt fences and are pulled out for this);
  3. the retired teachings do not come back.

generate_toke.md is a str.format template (`{{`/`}}` are literal braces), so it
is rendered the way main.py / trial/runner.py render it before it is checked.
Compiler-backed checks skip when the pin (or $TKC) is absent.
"""
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
ROOT = os.path.dirname(REGEN)
sys.path.insert(0, REGEN)
import check_card
import corpus_pin

PROMPTS = os.path.join(ROOT, "prompts")
FILES = ("system.md", "system_base.md", "generate_toke.md", "few-shot-template.md")
TKC = corpus_pin.default_tkc()
needs_tkc = pytest.mark.skipif(not os.path.exists(TKC), reason="no corpus pin / tkc on disk")
TEXT_FENCE = re.compile(r"^```text\n.*?^```\n", re.DOTALL | re.MULTILINE)
# fragments the gate must at least check per file, so it cannot pass by checking nothing
MIN_GATED = {"system.md": 25, "system_base.md": 15, "generate_toke.md": 5, "few-shot-template.md": 0}
MIN_PROGRAMS = {"system.md": 19, "system_base.md": 0, "generate_toke.md": 1, "few-shot-template.md": 2}
RETIRED = [
    (re.compile(r"(?m)^\s*[MFTI]=[A-Za-z]"), "uppercase declaration head (M= F= T= I=)"),
    (re.compile(r"\|\{"), "pipe-brace match `expr|{...}`"),
    (re.compile(r"(?i)no `%`"), "'no % operator'"),
    (re.compile(r"(?i)no `!=`|there is no `!=`"), "'no != operator'"),
    (re.compile(r"(?i)no `&&`"), "'no && operator'"),
    (re.compile(r"(?i)`if` is not an expression"), "'if is not an expression'"),
    (re.compile(r"(?i)equality is `=`|`=` \(equality\)|equality: `=` "), "`=` as equality"),
    (re.compile(r"\$str\b(?!`, )"), "`$str` taught as the string type"),
    (re.compile(r"@\((?:i64|u64|f64|str|bool)\)"), "legacy array type `@(i64)` (now `@i64`)"),
    (re.compile(r"\bif\([^()=!<>]*[^=!<>]=[^=]"), "a bare `=` as the comparison in an `if`"),
]


def _text(name):
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as f:
        return f.read()


def rendered(name):
    """The prompt as a model sees it (generate_toke.md: format-rendered)."""
    t = _text(name)
    if name == "generate_toke.md":
        t = t.format(category="CAT", task_description="TASK", expected_signature="SIG")
    return t


def gate_text(name):
    """What check_card reads: `text` fences (prompt scaffolding, not toke) removed."""
    return TEXT_FENCE.sub("", rendered(name))


def programs(name):
    """Every whole toke program: fenced `m=` blocks, plus the `m=` programs inside
    `text` fences (a program runs from an `m=` line to the next blank line)."""
    t = rendered(name)
    out = [b for b in re.findall(r"^```\n(.*?)^```", t, re.DOTALL | re.MULTILINE) if b.startswith("m=")]
    for fence in TEXT_FENCE.findall(t):
        out += [m.group(0) for m in re.finditer(r"(?m)^m=[a-z0-9.]+;\n(?:.+\n)+", fence)]
    return out


def _run_gate(path):
    argv, tkc = sys.argv, check_card.TKC
    sys.argv, check_card.TKC = ["check_card.py", "--card", path], TKC
    try:
        return check_card.main()
    finally:
        sys.argv, check_card.TKC = argv, tkc


@needs_tkc
@pytest.mark.parametrize("name", FILES)
def test_every_fragment_compiles_as_the_prompt_says(name, tmp_path):
    p = tmp_path / name
    p.write_text(gate_text(name), encoding="utf-8")
    assert _run_gate(str(p)) == 0


@pytest.mark.parametrize("name", FILES)
def test_gate_is_not_vacuous(name):
    frags = check_card.extract(gate_text(name))
    gated = [f for f in frags if not check_card.TEMPLATE.search(f["code"]) and check_card.harness(f) is not None]
    assert len(gated) >= MIN_GATED[name], (name, len(gated))
    assert len(programs(name)) >= MIN_PROGRAMS[name], (name, len(programs(name)))


@needs_tkc
@pytest.mark.parametrize("name", [n for n in FILES if MIN_PROGRAMS[n]])
def test_every_program_builds_and_exits_zero(name, tmp_path):
    for i, src in enumerate(programs(name)):
        p, exe = tmp_path / f"p{i}.tk", tmp_path / f"p{i}"
        p.write_text(src, encoding="utf-8")
        b = subprocess.run([TKC, str(p), "-o", str(exe)], capture_output=True, text=True, timeout=300,
                           check=False)
        assert b.returncode == 0, f"{name} program {i} ({src.splitlines()[0]}) does not build:\n{b.stdout[:600]}"
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60, check=False)
        assert r.returncode == 0, f"{name} program {i} ({src.splitlines()[0]}) exits {r.returncode}"


@pytest.mark.parametrize("name", FILES)
def test_retired_teachings_do_not_come_back(name):
    t = _text(name)
    hits = [what for rx, what in RETIRED if rx.search(t)]
    assert not hits, (name, hits)


def test_generate_toke_still_formats_with_the_runner_keys():
    out = rendered("generate_toke.md")
    assert "CAT" in out and "TASK" in out and "SIG" in out and "{{" not in out
