"""131.11 — the syntax card's own compile gate (check_card.py).

The card is the one document every generation wave reads, and until story
131.11 it had no compile gate: it drifted into declaring two legal constructs
illegal (`let x:i64=42`, `let x=mut.arr.0;`) and into carrying workarounds for
eight Epic 127 bugs that had been fixed. This test is that gate — it keeps the
committed card honest against the compiler on disk.

Needs tkc (~/tk/toke/tkc or $TKC); skipped when it is missing.
"""
import os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import check_card                 # noqa: E402


pytestmark = pytest.mark.skipif(not os.path.exists(check_card.TKC),
                                reason="no tkc on disk")


def _run(card, argv_extra=()):
    argv = sys.argv
    sys.argv = ["check_card.py", "--card", card, *argv_extra]
    try:
        return check_card.main()
    finally:
        sys.argv = argv


def test_committed_card_has_no_mismatch():
    """Every gated fragment of the committed card behaves as the card says."""
    assert _run(check_card.CARD) == 0


def test_gate_catches_a_card_that_teaches_broken_code(tmp_path):
    """A card claiming a construct compiles when it does not must fail."""
    bad = tmp_path / "bad_card.md"
    bad.write_text("## Bindings and assignment\n"
                   "Always use `let q:str=42;` for this.\n",
                   encoding="utf-8")
    assert _run(str(bad)) == 1


def test_gate_catches_a_stale_broken_claim(tmp_path):
    """A card calling a legal construct a compile error must fail — this is the
    exact drift the pre-131.11 card had accumulated."""
    bad = tmp_path / "stale_card.md"
    bad.write_text("## Bindings and assignment\n"
                   "`let x:i64=42;` is a compile error.\n",
                   encoding="utf-8")
    assert _run(str(bad)) == 1
