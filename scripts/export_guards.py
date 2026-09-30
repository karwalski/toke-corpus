#!/usr/bin/env python3
"""export_guards.py -- checks a training-data export must pass before it is written (131.63).

The v0.3 training data was built by toke-model/finetune/prepare_data.py, which
appended ``[RATIO] Nx fewer tokens than Python`` to every comparison example --
so the model was trained to state a withdrawn, lane-crossed token-efficiency
claim as fact.  prepare_data.py no longer does (132.15).  This guard makes sure
no export can carry such a line again: every exporter (scripts/
prepare_training_data.py today, the 131.29 v04 exporter when it lands) calls
``assert_no_claim_annotations`` on the rows it is about to write, and the CLI
checks any existing JSONL file.

A *claim annotation* is either
  * the ``[RATIO]`` marker itself, or
  * prose asserting a token-count comparison as fact: ``Nx fewer tokens``,
    ``N% fewer tokens``, ``N% token reduction``, ``fewer tokens than
    <Python|Java|C|...>``.
The patterns are deliberately narrow -- they match claims, not code -- and a
record is scanned as its full JSON text so a claim in any field counts.

CLI
  python3 scripts/export_guards.py FILE.jsonl [FILE.jsonl ...]
  Prints per-file row and offending-row counts; exit 1 if any row carries one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ratio_marker", re.compile(r"\[RATIO\]")),
    ("x_fewer_tokens", re.compile(r"\b\d+(?:\.\d+)?\s*[x×]\s+fewer\s+tokens\b", re.IGNORECASE)),
    ("pct_fewer_tokens", re.compile(r"\b\d+(?:\.\d+)?\s*%\s+fewer\s+tokens\b", re.IGNORECASE)),
    ("pct_token_reduction", re.compile(r"\b\d+(?:\.\d+)?\s*%\s+(?:token\s+)?reduction\s+in\s+tokens"
                                       r"|\b\d+(?:\.\d+)?\s*%\s+token\s+reduction\b", re.IGNORECASE)),
    ("fewer_tokens_than_language", re.compile(
        r"\bfewer\s+tokens\s+than\s+(?:python|java|javascript|typescript|c\+\+|c|rust|go)\b", re.IGNORECASE)),
)


class ExportGuardError(ValueError):
    """Raised when an export would carry a claim annotation."""


def claim_annotations(text: str) -> list[str]:
    """Names of the claim patterns that occur in ``text`` (empty list = clean)."""
    return [name for name, rx in CLAIM_PATTERNS if rx.search(text)]


def _row_text(row: Any) -> str:
    return row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)


def scan_rows(rows: Iterable[Any]) -> tuple[int, list[tuple[int, list[str]]]]:
    """Return (rows scanned, [(row index, pattern names), ...] for offending rows)."""
    n = 0
    bad: list[tuple[int, list[str]]] = []
    for i, row in enumerate(rows):
        n += 1
        hits = claim_annotations(_row_text(row))
        if hits:
            bad.append((i, hits))
    return n, bad


def assert_no_claim_annotations(rows: Iterable[Any], label: str = "export") -> int:
    """Raise ExportGuardError if any row carries a claim annotation; return rows scanned."""
    n, bad = scan_rows(rows)
    if bad:
        first = ", ".join(f"row {i}: {'/'.join(h)}" for i, h in bad[:5])
        raise ExportGuardError(
            f"{label}: {len(bad)} of {n} rows carry a token-ratio/marketing annotation (131.63) -- {first}")
    return n


def scan_jsonl(path: Path) -> tuple[int, list[tuple[int, list[str]]]]:
    with open(path, encoding="utf-8") as f:
        return scan_rows(line for line in f if line.strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="fail if any training JSONL row carries a claim annotation (131.63)")
    ap.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args(argv)
    rc = 0
    for p in a.files:
        n, bad = scan_jsonl(p)
        kinds: dict[str, int] = {}
        for _, hits in bad:
            for h in hits:
                kinds[h] = kinds.get(h, 0) + 1
        status = "FAIL" if bad else "ok"
        print(f"{status:<4} {p}: {len(bad)} of {n} rows carry a claim annotation {kinds or ''}".rstrip())
        if bad:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
