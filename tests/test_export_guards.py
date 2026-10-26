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


# ---------------------------------------------------------------- 131.93 ---
from export_guards import count_marker_violations, scan_jsonl, scan_rows  # noqa: E402

COMPARISON_USER = "Show equivalent implementations in Python and toke.\n[PYTHON 7t]\ndef f(): pass"
COMPARISON_TK = "[TK 10t]\nm=m;f=f():i64{<1};"


def _comparison(user: str = COMPARISON_USER, assistant: str = COMPARISON_TK, **extra) -> dict:
    return {"messages": [{"role": "user", "content": user},
                         {"role": "assistant", "content": assistant}], **extra}


def test_per_side_counts_without_tokenizer_are_blocked() -> None:
    """Negative control: the toke-model prepare_data.py comparison shape."""
    row = _comparison()
    assert count_marker_violations(row) == ["unsourced_token_counts"]
    with pytest.raises(ExportGuardError, match="131.93"):
        assert_no_claim_annotations([row])


def test_one_marker_alone_is_still_unsourced() -> None:
    assert count_marker_violations(_comparison(assistant="m=m;")) == ["unsourced_token_counts"]


def test_row_level_tokenizer_names_both_sides() -> None:
    row = _comparison(token_count_tokenizer="cl100k_base")
    assert count_marker_violations(row) == []
    assert assert_no_claim_annotations([row]) == 1


@pytest.mark.parametrize("bad", ["", "   ", 7, None])
def test_row_level_tokenizer_must_be_a_name(bad) -> None:
    assert count_marker_violations(_comparison(token_count_tokenizer=bad)) == ["unsourced_token_counts"]


def test_inline_same_tokenizer_passes() -> None:
    row = _comparison(user="[PYTHON 7t cl100k_base]\ndef f(): pass",
                      assistant="[TK 10t cl100k_base]\nm=m;")
    assert count_marker_violations(row) == []


def test_inline_mixed_tokenizers_are_blocked() -> None:
    row = _comparison(user="[PYTHON 7t cl100k_base]\ndef f(): pass",
                      assistant="[TK 10t tokenizer_v03]\nm=m;")
    assert count_marker_violations(row) == ["mixed_tokenizer_counts"]


def test_inline_partly_named_is_blocked() -> None:
    row = _comparison(user="[PYTHON 7t cl100k_base]\ndef f(): pass", assistant="[TK 10t]\nm=m;")
    assert count_marker_violations(row) == ["unsourced_token_counts"]


def test_field_disagreeing_with_inline_is_mixed() -> None:
    row = _comparison(user="[PYTHON 7t cl100k_base]\nx", assistant="[TK 10t cl100k_base]\ny",
                      token_count_tokenizer="tokenizer_v03")
    assert count_marker_violations(row) == ["mixed_tokenizer_counts"]


def test_string_rows_can_only_use_inline_names() -> None:
    assert count_marker_violations("[PYTHON 7t]\nx [TK 3t]") == ["unsourced_token_counts"]
    assert count_marker_violations("[PYTHON 7t o200k]\nx [TK 3t o200k]") == []


def test_code_brackets_are_not_markers() -> None:
    for text in ("x = a[PYTHON]", "arr[10]", "[TK]", "[C 5]", "[TK 10 tokens]"):
        assert count_marker_violations(text) == [], text


def test_scan_rows_reports_both_guards() -> None:
    rows = [_comparison(), _comparison(token_count_tokenizer="cl100k_base"), _row("m=a;")]
    n, bad = scan_rows(rows)
    assert n == 3 and bad == [(0, ["unsourced_token_counts"])]


def test_cli_parses_rows_so_the_field_counts(tmp_path: Path) -> None:
    f = tmp_path / "train.jsonl"
    f.write_text(json.dumps(_comparison(token_count_tokenizer="cl100k_base")) + "\n")
    assert main([str(f)]) == 0
    f.write_text(json.dumps(_comparison()) + "\n")
    assert main([str(f)]) == 1
    assert scan_jsonl(f)[1] == [(0, ["unsourced_token_counts"])]
