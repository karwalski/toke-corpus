#!/usr/bin/env python3
"""program_shape.py -- a "program" must define at least one function body (128.28).

Seven records in toke-model/training-data/train.jsonl are a module declaration,
a type declaration and an ``F=`` signature with no ``{...}`` body, e.g.

    M=safetrim;T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str};F=safeTrim(s:Str):Str

They were accepted as assistant targets because the phase-era pipeline's only
check on the toke source was ``tkc --check`` (validate/compiler.py
``validate_toke``), and a body-less ``f=`` is a legal extern declaration, so a
declaration-only module type-checks clean.  The differential test never runs
toke at all (validate/diff_test.py records toke as ``(compile-only)`` and sets
``toke_agrees=True``), so nothing downstream noticed either.

This module is the check that closes that gap.  ``bodyless_reason(src)``
returns a reason string when the source declares no function WITH a body
(declaration-only, or no function at all), else ``None``.  A body-less
declaration next to a defined function (an extern) is still fine -- only a
source in which nothing is defined is rejected.

Interface tasks (a ``.tki`` / interface record) are declaration-only by design;
``is_interface_record`` recognises them and callers exempt them.

Wired into
  * validate/quality.py      QualityScorer.score -- required gate
  * regen/validate.py        has_target_function -- the target must be defined
  * scripts/prepare_training_data.py -- exporter quality gate (skips + counts)

CLI
  python3 scripts/program_shape.py FILE.jsonl [FILE.jsonl ...]
  Scans corpus records (``tk_source``), chat rows (``messages`` assistant
  turn) or ChatML ``text`` rows (assistant segment).  Prints every offending
  row (line number, id if present, head of source); exit 1 if any.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Start of a function declaration: `f=` (v0.4) or `F=` (legacy) at the start
# of the source or after a top-level `;`.
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
INTERFACE_TASK_TYPES = frozenset({"interface", "tki", "interface_file", "tki_task"})


def _skip_string(src: str, i: int) -> int:
    """Index just past the string literal whose opening quote is at ``i``."""
    j = i + 1
    while j < len(src):
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == '"':
            return j + 1
        j += 1
    return j


def _read_decl(src: str, start: int) -> tuple[str, bool, int] | None:
    """Read ``name(params):R`` from ``start`` (just past ``f=``).

    Returns ``(name, has_body, index after the header)`` or ``None`` when the
    text is not a function declaration.  The header ends at the first ``{``
    (a body) or ``;`` / ``}`` / end of text (no body) outside any bracket.
    """
    m = _NAME.match(src, start)
    if not m:
        return None
    i = m.end()
    while i < len(src) and src[i].isspace():
        i += 1
    if i >= len(src) or src[i] != "(":
        return None
    depth = 0
    while i < len(src):
        c = src[i]
        if c == '"':
            i = _skip_string(src, i)
            continue
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0:
            if c == "{":
                return m.group(0), True, i
            if c in ";}":
                return m.group(0), False, i
        i += 1
    return m.group(0), False, i


def function_decls(src: str) -> list[tuple[str, bool]]:
    """Every top-level function declaration in ``src`` as ``(name, has_body)``."""
    out: list[tuple[str, bool]] = []
    n = len(src)
    i = 0
    depth = 0
    at_decl_start = True
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c == '"':
            i = _skip_string(src, i)
            at_decl_start = False
            continue
        if depth == 0 and at_decl_start and src.startswith(("f=", "F="), i):
            decl = _read_decl(src, i + 2)
            if decl is not None:
                name, has_body, end = decl
                out.append((name, has_body))
                if not has_body:
                    i = end + 1 if end < n and src[end] == ";" else end
                    at_decl_start = True
                    continue
                i = end           # at the body's `{`; the loop tracks its depth
                at_decl_start = False
                continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(depth - 1, 0)
        at_decl_start = depth == 0 and c == ";"
        i += 1
    return out


def defined_functions(src: str) -> list[str]:
    """Names of the functions ``src`` defines (declarations with a body)."""
    return [name for name, has_body in function_decls(src) if has_body]


def bodyless_reason(src: str) -> str | None:
    """Why ``src`` is not a program, or ``None`` if it defines a function."""
    decls = function_decls(src)
    if any(has_body for _, has_body in decls):
        return None
    if not decls:
        return "no function declared"
    names = ",".join(name for name, _ in decls)
    return f"declaration-only: no function has a body ({names})"


def is_interface_record(record: dict[str, Any] | None) -> bool:
    """True for a record that is an interface (.tki) task by declaration."""
    if not record:
        return False
    for key in ("task_type", "record_type", "kind"):
        v = record.get(key)
        if isinstance(v, str) and v.lower() in INTERFACE_TASK_TYPES:
            return True
    return False


def check_record(record: dict[str, Any], src: str) -> str | None:
    """``bodyless_reason`` unless the record is an interface task."""
    if is_interface_record(record):
        return None
    return bodyless_reason(src)


# --------------------------------------------------------------------------
# CLI: scan JSONL files of corpus records or training rows
# --------------------------------------------------------------------------

_CHATML_ASSISTANT = re.compile(r"<\|im_start\|>assistant\n(.*?)(?:<\|im_end\|>|$)", re.DOTALL)


def row_source(row: dict[str, Any]) -> str | None:
    """The toke source a row carries: ``tk_source``, the assistant turn of
    ``messages``, or the assistant segment of a ChatML ``text``."""
    if isinstance(row.get("tk_source"), str):
        return row["tk_source"]
    msgs = row.get("messages")
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "assistant":
                return str(m.get("content", ""))
    text = row.get("text")
    if isinstance(text, str):
        m = _CHATML_ASSISTANT.search(text)
        if m:
            return m.group(1)
    return None


def _looks_like_toke(src: str) -> bool:
    """A source opening with a module declaration (prose answers are skipped)."""
    return bool(re.match(r"\s*[mM]=", src))


def scan_rows(rows: Iterable[dict[str, Any]]) -> tuple[int, list[tuple[int, str, str]]]:
    """Return (toke rows scanned, [(1-based row number, reason, source), ...])."""
    scanned = 0
    bad: list[tuple[int, str, str]] = []
    for n, row in enumerate(rows, 1):
        src = row_source(row)
        if src is None or not _looks_like_toke(src):
            continue
        scanned += 1
        reason = check_record(row, src)
        if reason:
            bad.append((n, reason, src))
    return scanned, bad


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            yield json.loads(line) if line else {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)
    total_bad = 0
    for path in args.files:
        rows = list(_iter_jsonl(path))
        scanned, bad = scan_rows(rows)
        total_bad += len(bad)
        print(f"{path}: {scanned} toke rows, {len(bad)} without a function body")
        for n, reason, src in bad:
            rid = rows[n - 1].get("id", "")
            head = src.strip().replace("\n", " ")[:110]
            print(f"  line {n}{' ' + rid if rid else ''}: {reason}\n    {head}")
    return 1 if total_bad else 0


if __name__ == "__main__":
    sys.exit(main())
