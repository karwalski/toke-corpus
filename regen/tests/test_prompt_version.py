"""131.113 (gate half): prompt builders take the toke version from the syntax
card's header, never a hard-coded number (they said 2.8.0 after the card had
moved to 3.1.0)."""
import os
import re
import sys

import pytest

REGEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REGEN)
import build_prompt
import check_card
import corpus_pin
import library_prep
import pattern_prep

FAKE = "# toke syntax card - tkc 9.8.7 / v9.6 idiom (every rule verified)\nbody\n"
BUILDERS = ("pattern_prep.py", "library_prep.py", "build_prompt.py")


def _ctx(card):
    return {"must": [], "hints": [], "sweep_only": [], "exempt": [], "exempt_hit": [], "entries": [],
            "before": {"min_bytes": 1, "proxy_tokens": 1}, "attempts": 1, "prev_reason": None,
            "card": card, "card_sha": "x", "rule_texts": {}, "row": {}, "candidate_path": "c.tk"}


SPEC = {"task_id": "A-SRT-0001v1", "category": "A-SRT", "task_type": "full_program", "description": "d",
        "test_cases": [{"input": "", "expected_output": "1"}], "library": {"id": "x", "category": "y"}}


def test_card_header_names_the_corpus_pin_version():
    tkc, idiom = check_card.card_versions()
    assert re.fullmatch(r"\d+\.\d+\.\d+", tkc) and idiom.startswith("v")
    # the card is gated against the pin (131.103), so it must name the pin's version
    assert corpus_pin.VERSION.startswith(f"toke {tkc} ")


def test_card_without_a_version_header_is_refused():
    with pytest.raises(ValueError):
        check_card.card_versions("# toke syntax card\n")


@pytest.mark.parametrize("name", BUILDERS)
def test_no_hard_coded_toke_version_in_prompt_builders(name):
    with open(os.path.join(REGEN, name), encoding="utf-8") as f:
        src = f.read()
    assert not re.findall(r"(?:toke|tkc) \d+\.\d+\.\d+", src), name


@pytest.mark.parametrize("mod", (pattern_prep, library_prep), ids=("pattern_prep", "library_prep"))
def test_rewrite_prompts_take_the_version_from_the_card(mod):
    out = mod.build_prompt(SPEC, "f=main():i64{<0};\n", _ctx(FAKE))
    assert "the toke 9.8.7 (v9.6 idiom) program below" in out
    assert "the only authoritative toke 9.8.7 (v9.6 idiom) spec" in out
    assert "2.8.0" not in out
    plain = mod.build_prompt(SPEC, "f=main():i64{<0};\n", _ctx(None))   # card not embedded: the default card
    assert f"the {check_card.card_toolchain()} program below" in plain


def test_migrate_prompt_takes_the_version_from_the_card():
    spec = dict(SPEC, task_type="migrate_fix", legacy_source="m=x;", legacy_diagnostics="E2002")
    out = build_prompt.build(spec, FAKE)
    assert "current v9.6 syntax (tkc 9.8.7)" in out and "2.8.0" not in out and "v0.3" not in out
