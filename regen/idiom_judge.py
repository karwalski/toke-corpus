"""Idiom judge — scores idiomatic toke from the compiler's own pattern rules.

Epic 131.10: the judge and the linter are one implementation. `tkc --lint
--diag-json` (already run by `metrics.lint()`) emits the six 131.9 pattern
rules (`docs/lint-rules-v1.md`, "Pattern rules"); this module maps those rule
ids to the penalties/weights the 129-era regex judge used and keeps a regex
only for `hand-rolled-parser`, which needs stdlib knowledge the linter lacks.

    score(src)                 -> (score, notes)   runs tkc itself
    score(src, diags=...)      -> same, from a pre-computed diag list (no tkc)
    legacy_score(src)          -> the 129 regex judge, kept for delta reporting
    hard_gate(diags, exempt)   -> pattern-rule error/warning hits (131.10 gate)

History: before 131.9 `--lint --diag-json` never emitted JSON, so
`metrics.lint()` always returned `[]` and the 129.6 "lint 0 warnings" gate was
vacuous. The regex judge was the only idiom signal on the frozen corpus; its
scores are reproduced by `legacy_score` (`RESCORE_131.md` reports the delta).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

IDIOM_FLOOR: float = 0.6
TKC = os.environ.get("TKC", "/Users/matthew.watt/tk/toke/tkc")

# rule id -> (penalty per occurrence, severity as emitted by tkc). Penalties are
# the 129 weights (nested-concat -> string-concat-chain); the three rules the
# regex judge never had take the weights the 131.10 story fixes.
PATTERN_RULES: dict[str, float] = {
    "mut-flag-if": 0.15,
    "flag-soup": 0.15,
    "string-concat-chain": 0.15,
    "discarded-value-result": 0.20,
    "loop-rebuilds-array": 0.05,
    "single-use-let": 0.02,
}
HAND_PARSER_PENALTY = 0.20
PER_RULE_CAP = 3
GATE_SEVERITIES = ("error", "warning")   # hints pass the hard gate

# Spec-mandated styles (quality_rubric.md "Exemptions"): a task description
# that explicitly requires a verbose form exempts the rule it contradicts.
# Keyed on a lowercase substring of the `Variant N: ...` clause (audit.style_mandate).
# `discarded-value-result` is never exempt — the discarded value is a bug.
MANDATE_EXEMPTIONS: dict[str, tuple[str, ...]] = {
    "mutable binding": ("mut-flag-if", "flag-soup", "loop-rebuilds-array"),
    "helper variable": ("single-use-let",),
    "loop to compute the result": ("loop-rebuilds-array",),
}

_WS = r"[ \t\r\n]*"
_MANDATE = re.compile(r"Variant \d+:\s*(.+)$")
_STUB_HEAD = "m=harness;"
_STUB_LINE = re.compile(r"^(i=\w+:[\w.]+;|f=[a-z0-9]+\([^)]*\):\S+\{<[^{}]*\};)$")


# ------------------------------------------------------------------ diags ---
def lint_diags(src: str | None = None, path: str | None = None, timeout: int = 30) -> list[dict]:
    """Raw diagnostics from `tkc --lint --diag-json` (one JSON object per
    line, shape {code, rule, severity, stage, message, file, pos, span, fix}).
    Non-JSON lines are ignored. Returns [] when tkc cannot run."""
    tmp = None
    if path is None:
        fd, tmp = tempfile.mkstemp(suffix=".tk", prefix="ij131_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(src or "")
        path = tmp
    try:
        try:
            r = subprocess.run([TKC, path, "--lint", "--diag-json"], capture_output=True,
                               text=True, errors="replace", timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            return []
        return parse_diag_lines(r.stdout)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def parse_diag_lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not d.get("rule") and d.get("code"):
            d["rule"] = d["code"]
        out.append(d)
    return out


def stub_prefix_len(src: str) -> int:
    """Byte length of the single_function harness stub prefix (`m=harness;`
    plus the import / context-stub one-liners `assemble.py` prepends), or 0
    when the source is not a harness module. Diagnostics inside this prefix
    are the harness's, not the record's."""
    if not src.startswith(_STUB_HEAD):
        return 0
    end = 0
    for line in src.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if bare == _STUB_HEAD or _STUB_LINE.match(bare):
            end += len(line.encode("utf-8"))
            continue
        break
    return end


def is_stub_import(diag: dict, src: str, prefix_len: int | None = None) -> bool:
    """True for an `unused-import` (or any diagnostic) whose span starts inside
    the harness stub prefix. `m=harness;i=io:std.io;i=s:std.str;` are emitted
    unconditionally by assemble(), so their unused-import warnings say nothing
    about the record (131.9: 74/200 sampled records)."""
    if prefix_len is None:
        prefix_len = stub_prefix_len(src)
    if prefix_len <= 0:
        return False
    start = (diag.get("span") or {}).get("start")
    if start is None:
        start = (diag.get("pos") or {}).get("offset")
    return start is not None and start < prefix_len


def strip_stub_diags(diags: list[dict], src: str) -> list[dict]:
    """Drop diagnostics located in the harness stub prefix (all rules — an
    unused stub parameter or import is the harness's business)."""
    n = stub_prefix_len(src)
    if not n:
        return list(diags)
    return [d for d in diags if not is_stub_import(d, src, n)]


def pattern_hits(diags: list[dict]) -> list[dict]:
    """The subset of diags that are 131.9 pattern rules (suppressed linter
    false positives excluded — see suppress_linter_fps)."""
    return [d for d in diags if d.get("rule") in PATTERN_RULES and not d.get("suppressed")]


# ---------------------------------------------- linter false positives ---
# tkc 2.8.0 @ 131.9 reports `discarded-value-result` on a value-returning call
# that is the sole expression of an expression-`if` branch:
#     out=if(c){out.append(v)}el{out};   let y=if(c){x.push(9)}el{x};   <if(c){r.append(v)}el{r}
# The value is the branch's value, not discarded (the program runs correctly).
# Until src/lint.c is fixed the judge tags those hits `suppressed` (kept in
# the diag list, excluded from the score and the hard gate, counted by
# RESCORE_131). Remove this block when the lint rule is fixed.
EXPR_IF_FP_TAG = "expr-if-branch-value (131.9 lint false positive)"


def _string_mask(src: str) -> list[bool]:
    """mask[i] True when src[i] is inside a string literal (incl. quotes)."""
    mask = [False] * len(src)
    i, n = 0, len(src)
    while i < n:
        if src[i] == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            for k in range(i, min(j + 1, n)):
                mask[k] = True
            i = j + 1
        else:
            i += 1
    return mask


def _prev_code(src: str, mask: list[bool], i: int) -> int:
    """Index of the last non-whitespace, non-string char before i, or -1."""
    i -= 1
    while i >= 0 and (src[i].isspace() or mask[i]):
        i -= 1
    return i


def _match_back(src: str, mask: list[bool], close_i: int) -> int:
    """Index of the bracket matching the closer at close_i (string-aware)."""
    close = src[close_i]
    opener = {")": "(", "}": "{", "]": "["}[close]
    depth = 0
    for i in range(close_i, -1, -1):
        if mask[i]:
            continue
        if src[i] == close:
            depth += 1
        elif src[i] == opener:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _enclosing_block(src: str, mask: list[bool], offset: int) -> int:
    """Index of the `{` opening the block that directly contains offset (the
    statement at offset must start the block or follow a `;`), or -1."""
    p = _prev_code(src, mask, offset)
    if p < 0 or src[p] not in "{;":
        return -1
    depth = 0
    for i in range(offset - 1, -1, -1):
        if mask[i]:
            continue
        c = src[i]
        if c in ")}]":
            depth += 1
        elif c in "({[":
            if depth == 0:
                return i if c == "{" else -1
            depth -= 1
    return -1


def _is_last_expr_in_block(src: str, mask: list[bool], open_i: int, offset: int) -> bool:
    """The expression starting at offset runs to the block's closing `}` with
    no further top-level `;` — i.e. it is the block's value."""
    depth = 0
    for i in range(offset, len(src)):
        if mask[i]:
            continue
        c = src[i]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            if depth == 0:
                return c == "}" and _match_back(src, mask, i) == open_i
            depth -= 1
        elif c == ";" and depth == 0:
            return False
    return False


def _if_chain_head(src: str, mask: list[bool], open_i: int) -> int | None:
    """open_i is the `{` of an if/el-if/el branch: return the index of the
    head `if` keyword of the chain, or None if this is not an if branch."""
    i = open_i
    for _ in range(64):
        p = _prev_code(src, mask, i)
        if p < 0:
            return None
        if src[p] == ")":                       # `if(cond){` or `el if(cond){`
            lp = _match_back(src, mask, p)
            if lp < 0:
                return None
            k = _prev_code(src, mask, lp)
            if k < 1 or src[k - 1:k + 1] != "if" or (k >= 2 and (src[k - 2].isalnum() or src[k - 2] == "_")):
                return None
            q = _prev_code(src, mask, k - 1)
            if q >= 1 and src[q - 1:q + 1] == "el" and not (q >= 2 and (src[q - 2].isalnum() or src[q - 2] == "_")):
                # `el if(...)`: the preceding `}` closes the previous branch
                r = _prev_code(src, mask, q - 1)
                if r < 0 or src[r] != "}":
                    return None
                i = _match_back(src, mask, r)
                if i < 0:
                    return None
                continue
            return k - 1
        if p >= 1 and src[p - 1:p + 1] == "el" and not (p >= 2 and (src[p - 2].isalnum() or src[p - 2] == "_")):
            r = _prev_code(src, mask, p - 1)     # `el{`: previous branch's `}`
            if r < 0 or src[r] != "}":
                return None
            i = _match_back(src, mask, r)
            if i < 0:
                return None
            continue
        return None
    return None


def is_expr_if_branch_value(src: str, offset: int) -> bool:
    """True when the statement starting at byte `offset` is the LAST
    expression (the value) of a branch of an `if` that sits in expression
    position — after `=`, `<`, `(` or `,`: `x=if`, `let y=if`, `<if`, `f(if`.
    Earlier statements in the branch (`{let v=…;i=i+1;merged.append(v)}`) are
    allowed; a `;` after the call means it really is discarded."""
    b = src.encode("utf-8")
    try:
        offset = len(b[:offset].decode("utf-8"))   # byte -> char offset
    except UnicodeDecodeError:
        return False
    mask = _string_mask(src)
    open_i = _enclosing_block(src, mask, offset)
    if open_i < 0 or not _is_last_expr_in_block(src, mask, open_i, offset):
        return False
    head = _if_chain_head(src, mask, open_i)
    if head is None:
        return False
    q = _prev_code(src, mask, head)
    return q >= 0 and src[q] in "=<(,"


def suppress_linter_fps(diags: list[dict], src: str) -> list[dict]:
    """Tag known linter false positives with `suppressed` (kept in the list).
    Currently: discarded-value-result on an expression-if branch value."""
    out = []
    for d in diags:
        if d.get("rule") == "discarded-value-result" and not d.get("suppressed"):
            start = (d.get("span") or {}).get("start")
            if start is None:
                start = (d.get("pos") or {}).get("offset")
            if start is not None and is_expr_if_branch_value(src, start):
                d = dict(d, suppressed=EXPR_IF_FP_TAG)
        out.append(d)
    return out


def style_mandate(spec: dict) -> str | None:
    """The `Variant N: ...` style clause of a task description (same regex as
    audit.style_mandate; duplicated here because audit imports run_shard)."""
    m = _MANDATE.search((spec or {}).get("description", "") or "")
    return m.group(1).strip() if m else None


def mandate_exempt_rules(style_mandate: str | None) -> tuple[str, ...]:
    """Rules exempted by a spec's style mandate (see MANDATE_EXEMPTIONS)."""
    if not style_mandate:
        return ()
    m = style_mandate.lower()
    out: list[str] = []
    for key, rules in MANDATE_EXEMPTIONS.items():
        if key in m:
            out.extend(r for r in rules if r not in out)
    return tuple(out)


def hard_gate(diags: list[dict], exempt: tuple[str, ...] | list[str] = ()) -> list[dict]:
    """Pattern-rule diagnostics that fail the 131.10 hard gate: severity
    error/warning, not in `exempt`. Hints always pass. `diags` should already
    be stub-stripped (metrics.lint does that)."""
    return [d for d in pattern_hits(diags)
            if d.get("severity") in GATE_SEVERITIES and d.get("rule") not in exempt]


def violation_summary(diags: list[dict]) -> str:
    """`mut-flag-if×2,flag-soup×1` — stable order (PATTERN_RULES order)."""
    counts: dict[str, int] = {}
    for d in diags:
        counts[d["rule"]] = counts.get(d["rule"], 0) + 1
    return ",".join(f"{r}×{counts[r]}" for r in PATTERN_RULES if r in counts)


# --------------------------------------------------------------- scoring ---
def _hand_parser(src: str) -> int:
    """A `lp` loop that scans text one character at a time (`charat`/`charcode`
    + `slice`) — should use `json.dec`/`csv`/`str.*`. Regex: needs stdlib
    knowledge the linter does not have (idiom rule 4)."""
    n = 0
    for m in re.finditer(r"lp\s*\(", src):
        body = src[m.end():m.end() + 500]
        if re.search(r"\.(charat|charcode)\s*\(", body) and re.search(r"\.slice\s*\(", body):
            n += 1
    return n


def score(toke_src: str, diags: list[dict] | None = None) -> tuple[float, list[str]]:
    """Return (idiom_score in [0,1], notes). 1.0 = fully idiomatic. Penalty is
    per-occurrence, capped at PER_RULE_CAP per rule so one file can't score far
    below 0. `diags`: a pre-computed `tkc --lint --diag-json` list (the caller's
    `metrics.lint()` output) — when None, tkc is run on `toke_src`. Diagnostics
    in a harness stub prefix are ignored either way."""
    if diags is None:
        diags = lint_diags(src=toke_src)
    diags = suppress_linter_fps(strip_stub_diags(diags, toke_src), toke_src)
    counts: dict[str, int] = {}
    for d in pattern_hits(diags):
        counts[d["rule"]] = counts.get(d["rule"], 0) + 1
    penalty = 0.0
    notes: list[str] = []
    for rule, per in PATTERN_RULES.items():
        n = counts.get(rule, 0)
        if n > 0:
            p = per * min(n, PER_RULE_CAP)
            penalty += p
            notes.append(f"{rule}×{n} (-{p:.2f})")
    n = _hand_parser(toke_src)
    if n > 0:
        p = HAND_PARSER_PENALTY * min(n, PER_RULE_CAP)
        penalty += p
        notes.append(f"hand-rolled-parser×{n} (-{p:.2f})")
    return max(0.0, round(1.0 - penalty, 10)), notes


# ---------------------------------------------------- legacy regex judge ---
def _legacy_mut_flag_if(src: str) -> int:
    n = 0
    for m in re.finditer(r"let\s+(\w+)\s*=\s*mut\.\s*[-\d\"']", src):
        var = re.escape(m.group(1))
        tail = src[m.end():m.end() + 400]
        if re.search(rf"if\s*\([^)]*\)\s*\{{[^}}]*\b{var}\s*=[^=]", tail):
            n += 1
    return n


def _legacy_flag_soup(src: str) -> int:
    counts: dict[str, int] = {}
    for m in re.finditer(r"if\s*\([^)]*\)\s*\{" + _WS + r"(\w+)\s*=\s*[-\d\"']", src):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return sum(1 for c in counts.values() if c >= 2)


def _legacy_nested_concat(src: str) -> int:
    return len(re.findall(r"\.concat\s*\([^;()]*\.concat\s*\(", src))


_LEGACY_RULES = [
    ("mut-flag-if", _legacy_mut_flag_if, 0.15),
    ("flag-soup", _legacy_flag_soup, 0.15),
    ("hand-rolled-parser", _hand_parser, 0.20),
    ("nested-concat", _legacy_nested_concat, 0.15),
]


def legacy_score(toke_src: str) -> tuple[float, list[str]]:
    """The 129-era regex judge, byte-for-byte the scorer that gated the frozen
    corpus. Kept only so RESCORE_131 can report old-vs-new; not a gate."""
    penalty = 0.0
    notes: list[str] = []
    for name, fn, per in _LEGACY_RULES:
        n = fn(toke_src)
        if n > 0:
            p = per * min(n, PER_RULE_CAP)
            penalty += p
            notes.append(f"{name}×{n} (-{p:.2f})")
    return max(0.0, 1.0 - penalty), notes


class IdiomJudge:
    """Thin OO wrapper for pipeline call-sites."""

    floor = IDIOM_FLOOR

    def score(self, toke_src: str, diags: list[dict] | None = None) -> tuple[float, list[str]]:
        return score(toke_src, diags)

    def passes(self, toke_src: str, diags: list[dict] | None = None) -> bool:
        return self.score(toke_src, diags)[0] >= self.floor


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        src = open(path, encoding="utf-8", errors="replace").read()
        s, notes = score(src)
        o, onotes = legacy_score(src)
        gate = hard_gate(suppress_linter_fps(strip_stub_diags(lint_diags(path=path), src), src))
        print(f"{path}: idiom={s:.2f} {'PASS' if s >= IDIOM_FLOOR else 'FAIL'} {notes} "
              f"| legacy={o:.2f} {onotes} | gate={'FAIL ' + violation_summary(gate) if gate else 'pass'}")
