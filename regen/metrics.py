"""Structural metrics for toke source via tkc: --dump-ast (exact nesting depth,
function count/extent), --min (canonical minified size), --lint (style rules).

Epic 129.1. AST spans are BYTE offsets (see toke/scripts/migrate_eq.py note).
el-if chains do not deepen nesting: an IF_STMT that is a direct child of an
IF_STMT (not under a STMT_LIST) is the `el if` continuation, same depth.
"""
import json, subprocess

TKC = "/Users/matthew.watt/tk/toke/tkc"
CONTROL = {"IF_STMT", "LOOP_STMT", "MATCH_STMT"}


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


def lint(path):
    """List of {rule, severity, message} from --lint --diag-json (empty = clean)."""
    r = _run([path, "--lint", "--diag-json"])
    out = []
    if not r:
        return out
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append({"rule": d.get("error_code") or d.get("rule"),
                    "severity": d.get("severity"), "message": d.get("message")})
    return out


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


def analyse(path):
    """Per-file structural metrics. Returns None when the AST is unavailable
    (caller should have compile-checked first)."""
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
    li = lint(path)
    return {
        "func_count": len(funcs),
        "functions": funcs,
        "max_depth": max((f["depth"] for f in funcs), default=0),
        "max_func_bytes": max((f["bytes"] for f in funcs if f["bytes"] is not None), default=0),
        "min_bytes": len(mn.encode()) if mn is not None else None,
        "lint": li,
        "lint_warnings": sum(1 for d in li if d.get("severity") == "warning"),
    }


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(p, json.dumps(analyse(p), indent=1))
