"""131.63: an export fails if any record carries a [RATIO] line or another token-ratio claim."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from export_guards import (
    ExportGuardError,
    assert_no_claim_annotations,
    claim_annotations,
    main,
)


def _row(assistant: str) -> dict[str, list[dict[str, str]]]:
    return {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                         {"role": "assistant", "content": assistant}]}


@pytest.mark.parametrize("text", [
    "[TK 12t]\nm=x;\n[RATIO] 2.6x fewer tokens than Python.",
    "toke uses 3x fewer tokens",
    "a 12.5% token reduction vs Python",
    "52% fewer tokens",
    "it needs fewer tokens than Python",
])
def test_claims_are_caught(text: str) -> None:
    assert claim_annotations(text)


@pytest.mark.parametrize("text", [
    'm=m;f=f(x:i64):i64{<x*2};',
    "apply a 20% reduction to the price",       # not a token claim
    "count the tokens in the input string",
    "[PYTHON 40t]\\ndef f(): pass",
])
def test_code_and_plain_prose_pass(text: str) -> None:
    assert claim_annotations(text) == []


def test_assert_raises_on_any_row() -> None:
    rows = [_row("m=a;"), _row("m=b;\n[RATIO] 1.1x fewer tokens than Python.")]
    with pytest.raises(ExportGuardError, match="1 of 2 rows"):
        assert_no_claim_annotations(rows, "train.jsonl")
    assert assert_no_claim_annotations(rows[:1]) == 1


def test_cli_red_then_green(tmp_path: Path) -> None:
    f = tmp_path / "train.jsonl"
    f.write_text(json.dumps(_row("m=a;\n[RATIO] 2x fewer tokens than Python.")) + "\n")
    assert main([str(f)]) == 1
    f.write_text(json.dumps(_row("m=a;")) + "\n")
    assert main([str(f)]) == 0
