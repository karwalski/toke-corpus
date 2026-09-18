"""Structural metrics for toke source via tkc: --dump-ast (exact nesting depth,
function count/extent), --min (canonical minified size), --lint (style +
pattern rules), plus the proxy8k token count of the masked --min form.

Epic 129.1; 131.10 added `proxy_tokens` and `lint_pattern_violations` and made
`lint()` parse the JSON `--lint --diag-json` emits since 131.9 (before that
commit the flag emitted nothing, so `lint()` always returned `[]`).

AST spans are BYTE offsets (see toke/scripts/migrate_eq.py note).
el-if chains do not deepen nesting: an IF_STMT that is a direct child of an
IF_STMT (not under a STMT_LIST) is the `el if` continuation, same depth.

Proxy tokenizer: `scripts/patterns/count_tokens.py` in the toke repo
(TOKE_PATTERNS_DIR overrides the directory; TOKE_PROXY pins the artefact,
default = latest `patterns/proxy/proxy8k-*.json`). Loaded lazily once per
process; `proxy_tokens` is None when the tokenizer cannot be loaded.
"""
import json, os, subprocess, sys

import idiom_judge

TKC = os.environ.get("TKC", "/Users/matthew.watt/tk/toke/tkc")
CONTROL = {"IF_STMT", "LOOP_STMT", "MATCH_STMT"}
PATTERNS_DIR = os.environ.get("TOKE_PATTERNS_DIR",
                              os.path.expanduser("~/tk/toke/scripts/patterns"))
PROXY_PATH = os.environ.get("TOKE_PROXY") or None
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")   # fork-safe under Pool

_counter = None
_counter_err = None
BUDGET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "freeze", "proxy_budget_v04.json")
_budget = None


def _run(args, timeout=30):
    try:
        return subprocess.run([TKC] + args, capture_output=True, text=True,
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def dump_ast(path):
    r = _run([path, "--dump-ast"])
    if not r or r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def min_form(path):
    r = _run([path, "--min"])
    return r.stdout.strip() if r and r.returncode == 0 else None


def lint(path, src=None):
    """Diagnostics from --lint --diag-json as dicts {rule, severity, message,
    line, span, fix} (empty = clean). Diagnostics inside a single_function
    harness stub prefix (`m=harness;` + stub imports/context stubs) are dropped:
    the stub imports `io`/`s` unconditionally, so their `unused-import` says
    nothing about the record (131.9 found it on 74/200 records)."""
    r = _run([path, "--lint", "--diag-json"])
    if not r:
        return []
    diags = idiom_judge.parse_diag_lines(r.stdout)
    if src is None:
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            src = ""
    out = []
    diags = idiom_judge.suppress_linter_fps(idiom_judge.strip_stub_diags(diags, src), src)
    for d in diags:
        e = {"rule": d.get("rule") or d.get("code"),
             "severity": d.get("severity"), "message": d.get("message"),
             "line": (d.get("pos") or {}).get("line"),
             "span": d.get("span"), "fix": d.get("fix")}
        if d.get("suppressed"):
            e["suppressed"] = d["suppressed"]     # linter FP: not scored, not gated
        out.append(e)
    return out


def pattern_violations(diags):
    """[{rule, severity, line[, suppressed]}] for the 131.9 pattern rules
    (suppressed linter false positives included, tagged)."""
    out = []
    for d in diags:
        if d.get("rule") not in idiom_judge.PATTERN_RULES:
            continue
        v = {"rule": d["rule"], "severity": d.get("severity"), "line": d.get("line")}
        if d.get("suppressed"):
            v["suppressed"] = d["suppressed"]
        out.append(v)
    return out


def proxy_counter():
    """The count_tokens.Counter (proxy8k only, no external tokenizers), or
    None with the load error recorded in `_counter_err`."""
    global _counter, _counter_err
    if _counter is not None or _counter_err is not None:
        return _counter
    try:
        if PATTERNS_DIR not in sys.path:
            sys.path.insert(0, PATTERNS_DIR)
        import count_tokens                       # noqa: E402  (toke repo)
        _counter = count_tokens.Counter(PROXY_PATH, external=False)
    except Exception as e:                        # missing repo / artefact / tokenizers
        _counter_err = f"{type(e).__name__}: {e}"
        _counter = None
    return _counter


def proxy_tokens(min_text):
    """proxy8k token count of the string-masked --min text (whole program),
    the 131.4 decision metric. None when the proxy cannot be loaded."""
    if min_text is None:
        return None
    c = proxy_counter()
    if c is None:
        return None
    return c.measure_snippet(min_text)["tokens"]["proxy8k"]


def load_proxy_budget(path=None):
    """regen/freeze/proxy_budget_v04.json (written by rescore_131.py report):
    per `category/task_type` median proxy_tokens over the frozen corpus and the
    budget = factor × median. {} when the file is absent."""
    global _budget
    if path is None and _budget is not None:
        return _budget
    p = path or BUDGET_PATH
    try:
        b = json.load(open(p))
    except (OSError, json.JSONDecodeError):
        b = {}
    if path is None:
        _budget = b
    return b


def budget_for(category, task_type, budget=None):
    """The proxy-token budget for a record (category/task_type key, falling
    back to the category-wide key), or None when unknown."""
    b = budget if budget is not None else load_proxy_budget()
    if not b:
        return None
    lim = b.get("budget") or {}
    v = lim.get(f"{category}/{task_type}") if task_type else None
    return v if v is not None else lim.get(category)


def over_budget(category, task_type, n, budget=None):
    """True/False against the frozen-corpus budget; None when n or the budget
    is unknown. Soft flag (131.10) — whether it becomes hard is 131.13's call."""
    if n is None:
        return None
    lim = budget_for(category, task_type, budget)
    return None if lim is None else n > lim


def _extent(node):
    lo = node.get("span", {}).get("start")
    hi = node.get("span", {}).get("end")
    for c in node.get("children") or []:
        clo, chi = _extent(c)
        if clo is not None and (lo is None or clo < lo):
            lo = clo
        if chi is not None and (hi is None or chi > hi):
            hi = chi
    return lo, hi


def _max_depth(node, depth=0, parent_kind=None):
    kind = node.get("kind")
    here = depth
    if kind in CONTROL:
        # el-if chain: IF_STMT directly under IF_STMT stays at the same depth
        if not (kind == "IF_STMT" and parent_kind == "IF_STMT"):
            here = depth + 1
    best = here
    for c in node.get("children") or []:
        d = _max_depth(c, here, kind)
        if d > best:
            best = d
    return best


def _func_name(fn_node):
    for c in fn_node.get("children") or []:
        if c.get("kind") == "IDENT":
            return c.get("name")
    return None


def analyse(path, src=None):
    """Per-file structural metrics. Returns None when the AST is unavailable
    (caller should have compile-checked first). `src`: the file's text, if the
    caller already has it (saves a read for the stub-prefix check)."""
    ast = dump_ast(path)
    if ast is None:
        return None
    funcs = []

    def walk(n):
        if n.get("kind") == "FUNC_DECL":
            lo, hi = _extent(n)
            funcs.append({"name": _func_name(n),
                          "bytes": (hi - lo) if lo is not None and hi is not None else None,
                          "depth": _max_depth(n)})
        for c in n.get("children") or []:
            walk(c)

    walk(ast)
    mn = min_form(path)
    li = lint(path, src)
    return {
        "func_count": len(funcs),
        "functions": funcs,
        "max_depth": max((f["depth"] for f in funcs), default=0),
        "max_func_bytes": max((f["bytes"] for f in funcs if f["bytes"] is not None), default=0),
        "min_bytes": len(mn.encode()) if mn is not None else None,
        "proxy_tokens": proxy_tokens(mn),
        "lint": li,
        "lint_warnings": sum(1 for d in li if d.get("severity") == "warning"),
        "lint_pattern_violations": pattern_violations(li),
    }


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p, json.dumps(analyse(p), indent=1))
