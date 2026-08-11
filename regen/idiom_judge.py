"""Idiom judge — deterministic rule-based scorer for idiomatic toke.

Enforces the normative anti-patterns in `docs/spec/idiom-v0.4.md` (Epic 116 / B1).
Named `qwen_judge` for pipeline compatibility, but implemented as a rule-based
checker rather than an LLM call: the idiom rules are structural and detectable
directly, which is cheaper, reproducible, and free of LLM variance. Returns a
0.0–1.0 idiom score plus human-readable notes. The corpus pipeline
(`validate/quality.py`) rejects entries scoring below `IDIOM_FLOOR`.
"""
from __future__ import annotations

import re

IDIOM_FLOOR: float = 0.6

_WS = r"[ \t\r\n]*"


def _mut_flag_if(src: str) -> int:
    """`let X=mut.<lit>` whose only purpose is an `if` that assigns X — should be
    an expression-`if` (`let X=if(...){a}el{b}`)."""
    n = 0
    for m in re.finditer(r"let\s+(\w+)\s*=\s*mut\.\s*[-\d\"']", src):
        var = re.escape(m.group(1))
        tail = src[m.end():m.end() + 400]
        if re.search(rf"if\s*\([^)]*\)\s*\{{[^}}]*\b{var}\s*=[^=]", tail):
            n += 1
    return n


def _flag_soup(src: str) -> int:
    """Two or more separate `if(...){FLAG=<lit>}` assigning the same variable a
    constant — should be `||`/`&&`."""
    counts: dict[str, int] = {}
    for m in re.finditer(r"if\s*\([^)]*\)\s*\{" + _WS + r"(\w+)\s*=\s*[-\d\"']", src):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return sum(1 for c in counts.values() if c >= 2)


def _hand_parser(src: str) -> int:
    """A `lp` loop that scans text one character at a time (`charat`/`charcode`
    + `slice`) — should use `json.dec`/`csv`/`str.*`."""
    n = 0
    for m in re.finditer(r"lp\s*\(", src):
        body = src[m.end():m.end() + 500]
        if re.search(r"\.(charat|charcode)\s*\(", body) and re.search(r"\.slice\s*\(", body):
            n += 1
    return n


def _nested_concat(src: str) -> int:
    """`str.concat(... str.concat(...` nested ≥ 2 deep — should interpolate or
    `str.join`."""
    return len(re.findall(r"\.concat\s*\([^;()]*\.concat\s*\(", src))


_RULES = [
    ("mut-flag-if", _mut_flag_if, 0.15),
    ("flag-soup", _flag_soup, 0.15),
    ("hand-rolled-parser", _hand_parser, 0.20),
    ("nested-concat", _nested_concat, 0.15),
]


def score(toke_src: str) -> tuple[float, list[str]]:
    """Return (idiom_score in [0,1], notes). 1.0 = fully idiomatic. Penalty is
    per-occurrence, capped at 3 per rule so one file can't score far below 0."""
    penalty = 0.0
    notes: list[str] = []
    for name, fn, per in _RULES:
        n = fn(toke_src)
        if n > 0:
            p = per * min(n, 3)
            penalty += p
            notes.append(f"{name}×{n} (-{p:.2f})")
    return max(0.0, 1.0 - penalty), notes


class IdiomJudge:
    """Thin OO wrapper for pipeline call-sites."""

    floor = IDIOM_FLOOR

    def score(self, toke_src: str) -> tuple[float, list[str]]:
        return score(toke_src)

    def passes(self, toke_src: str) -> bool:
        return self.score(toke_src)[0] >= self.floor


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        s, notes = score(open(path).read())
        print(f"{path}: idiom={s:.2f} {'PASS' if s >= IDIOM_FLOOR else 'FAIL'} {notes}")
