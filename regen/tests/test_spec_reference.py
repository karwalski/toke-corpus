"""131.62 — gate for prompts/spec-reference.md, the spec page the phase-era
generators (prompts/constraints, prompts/domain, scripts/ec2_generate_all.py)
inject as system context.

Until this story the page had no gate and taught a spec that no compiler had
accepted since v0.4: `=` as equality, no `!=`/`&&`/`%`, `if` not an expression,
no `el if`, `expr|{...}` match, `$str`/`@(T)` types. 132.15 corrected only its
character-set line. Three checks keep it honest now:

  1. every code block and statement span compiles as the page says
     (regen/check_card.py, the same gate the syntax card has) and every
     whole-program block builds and exits 0;
  2. its facts — the 23 symbols, the 14 keywords — equal the syntax card's and,
     when the toke checkout is present, toke/docs/about/canonical.json;
  3. none of the retired teachings comes back.

Compiler-backed checks need tkc (~/tk/toke/tkc or $TKC); skipped without it.
"""
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
ROOT = os.path.dirname(REGEN)
sys.path.insert(0, REGEN)
import check_card  # noqa: E402

SPEC_REF = os.path.join(ROOT, "prompts", "spec-reference.md")
CARD = os.path.join(REGEN, "syntax_card.md")
CANON = [os.path.join(os.path.dirname(ROOT), "toke", "docs", "about", "canonical.json"),
         os.path.expanduser("~/tk/toke/docs/about/canonical.json")]

needs_tkc = pytest.mark.skipif(not os.path.exists(check_card.TKC), reason="no tkc on disk")


def _text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _symbols(text, heading_rx):
    """The backticked symbol list on the character-set line: `( ) { } ...`."""
    sec = re.search(heading_rx + r".*?(?=\n## )", text, re.DOTALL | re.IGNORECASE)
    assert sec, f"no character-set section matching {heading_rx}"
    for span in re.findall(r"`([^`]+)`", sec.group(0)):
        toks = span.split()
        if len(toks) >= 20 and all(len(t) == 1 for t in toks):
            return toks
    raise AssertionError("no symbol list found in the character-set section")


def _canon():
    for p in CANON:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return None


@needs_tkc
def test_every_fragment_compiles_as_the_page_says():
    argv = sys.argv
    sys.argv = ["check_card.py", "--card", SPEC_REF]
    try:
        assert check_card.main() == 0
    finally:
        sys.argv = argv


@needs_tkc
def test_gate_is_not_vacuous():
    frags = check_card.extract(_text(SPEC_REF))
    gated = [f for f in frags if not check_card.TEMPLATE.search(f["code"])
             and check_card.harness(f) is not None]
    programs = [f for f in gated if f["code"].startswith("m=")]
    assert len(gated) >= 15, len(gated)
    assert len(programs) >= 9, len(programs)


@needs_tkc
def test_every_program_block_builds_and_exits_zero(tmp_path):
    blocks = [b for b in re.findall(r"```\n(.*?)```", _text(SPEC_REF), re.DOTALL)
              if b.startswith("m=")]
    assert blocks
    for i, src in enumerate(blocks):
        p, exe = tmp_path / f"p{i}.tk", tmp_path / f"p{i}"
        p.write_text(src, encoding="utf-8")
        b = subprocess.run([check_card.TKC, str(p), "-o", str(exe)],
                           capture_output=True, text=True, timeout=300)
        assert b.returncode == 0, f"block {i} ({src.splitlines()[0]}) does not build:\n{b.stderr[:600]}"
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"block {i} ({src.splitlines()[0]}) exits {r.returncode}"


def test_character_set_matches_the_card():
    ref = _symbols(_text(SPEC_REF), r"## Character set \(59 chars\)")
    card = _symbols(_text(CARD), r"## Character set")
    assert len(ref) == 23 and len(set(ref)) == 23
    assert set(ref) == set(card)
    canon = _canon()
    if canon:
        assert canon["language_facts"]["character_set"]["count"] == 26 + 10 + len(ref)


def test_keywords_match_the_card_and_canonical():
    sec = re.search(r"## Keywords \((\d+)\)(.*?)\n## ", _text(SPEC_REF), re.DOTALL)
    assert sec
    table = re.findall(r"^\| `([a-z]+)` \|", sec.group(2), re.MULTILINE)
    assert int(sec.group(1)) == len(table) == len(set(table))
    card = re.search(r"The 14 keywords are: ([a-z ]+)\.", _text(CARD))
    assert card
    assert set(table) == set(card.group(1).split())
    canon = _canon()
    if canon:
        assert set(table) == set(canon["language_facts"]["keywords"]["list"])


# What the pre-131.62 page taught, and why each is wrong (spec v0.4 section).
RETIRED = [
    (r"Phase 2", "the Phase 1/Phase 2 type-sigil table is retired"),
    (r"^(?!.*(legacy|not `\$str`)).*`\$str`", "`str` is the string type; `$str` is legacy notation"),
    (r"`\$void`", "`void` is the unit type"),
    (r"\|\{", "match is `mt expr {...}`, not `expr|{...}`"),
    (r"Equality is `=`|\(equality, NOT `==`\)", "§B: `==` is equality"),
    (r"No `!=`", "§A/§D: `!=` `<=` `>=` `&&` `||` `%` are operators"),
    (r"`if` is NOT an expression|cannot assign from it", "§C: `if` is an expression"),
    (r"No `elif`\. Nest", "`el if` chains; never nest `el{if}`"),
    (r"nested `if` for logical", "§D: use `&&` / `||`"),
    (r"a-a/b\*b", "`%` is modulo"),
    (r"uppercase-initial variant", "variants are lowercase `$name`"),
    (r"No comments \(`//`", "§H: `(* *)` is the comment form"),
]


@pytest.mark.parametrize("rx,why", RETIRED, ids=[w for _, w in RETIRED])
def test_retired_teaching_is_absent(rx, why):
    hits = [ln for ln in _text(SPEC_REF).splitlines() if re.search(rx, ln)]
    assert not hits, f"{why}: {hits}"


def test_retired_list_catches_the_old_page(tmp_path):
    """The pre-131.62 wording trips the retired-teaching rules."""
    old = ("1. Match: `expr\\|{arms}`\n"
           "2. Comparison: `<` `>` `=` (equality, NOT `==`)\n"
           "No `!=`, `<=`, `>=`, `&&`, `||`, `%`.\n"
           "10. `if` is NOT an expression; cannot assign from it\n"
           "| `Str` | `$str` | UTF-8 string |\n")
    caught = {why for rx, why in RETIRED if re.search(rx, old)}
    assert len(caught) >= 4, caught
