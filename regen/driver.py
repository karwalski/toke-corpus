"""Audit-only driver synthesis for single_function specs (Epic 129.1).

Appends a generated main() to an assembled single_function module so its spec
test_cases can finally execute: main() calls the target function on each test
input and prints one line per result value.

Printing uses interpolation, so the comparator must use THESE renderings
(probe-verified on tkc 2.8.0): bool -> 1/0, integral float -> int, array
returns -> one printed line per element.
"""
import re
from assemble import assemble

_FN = re.compile(r"f=([a-z0-9]+)\(([^)]*)\):(\S+?)\{")
_SIG = re.compile(r"f=([a-z0-9]+)\(([^)]*)\):(\S+)")

# Real bodies for the domain_context helper set (closed: 29 distinct signatures
# across all single_function specs — surveyed 2026-08-12). The regen harness
# stubs these with dummy constants, which makes execution meaningless whenever
# the worker's function calls one; delegating to the real stdlib makes the
# spec test_cases genuinely verifiable. Bodies are written against renamed
# params v0..vn so the module's `s` (std.str) alias is never shadowed.
# Every body probe-verified on tkc 2.8.0.
REAL_STUBS = {
    "split(str;str):@str": "<s.split(v0;v1)",
    "concat(str;str):str": "<s.concat(v0;v1)",
    "startswith(str;str):bool": "<s.startswith(v0;v1)",
    "endswith(str;str):bool": "<s.endswith(v0;v1)",
    "tostring(i64):str": '<"\\(v0)"',
    "parseint(str):i64": "<mt s.toint(v0) {$ok:n n;$err:e 0}",
    "trim(str):str": "<s.trim(v0)",
    "len(str):i64": "<s.len(v0) as i64",
    "len(@str):i64": "<v0.len as i64",
    "len(@i64):i64": "<v0.len as i64",
    "len(@bool):i64": "<v0.len as i64",
    "charcode(str):i64": "<s.charcode(v0;0)",
    "fromcharcode(i64):str": '<mt s.frombytes(@(v0 as byte)) {$ok:r r;$err:e ""}',
    "mod(i64;i64):i64": "<v0%v1",
    "xor(i64;i64):i64": "<v0^v1",
    "shl(i64;i64):i64": "<v0<<v1",
    "shr(i64;i64):i64": "<v0>>v1",
    "and(i64;i64):i64": "<v0&v1",
    "eq(str;str):bool": "<v0==v1",
    "eq(i64;i64):bool": "<v0==v1",
    "replace(str;str;str):str": "<s.replace(v0;v1;v2)",
    "upper(str):str": "<s.upper(v0)",
    "lower(str):str": "<s.lower(v0)",
    "contains(str;str):bool": "<s.contains(v0;v1)",
    "contains(@str;str):bool":
        "lp(let i=0;i<v0.len;i=i+1){if(v0.get(i)==v1){<true}};<false",
    "repeat(str;i64):str": "<s.repeat(v0;v1)",
    "push(@i64;i64):@i64": "<v0.append(v1)",
    "push(@str;str):@str": "<v0.append(v1)",
    "get(@i64;i64):i64": "<v0.get(v1)",
}


def real_stub(sig):
    """Real-bodied stub declaration for a domain_context signature, or None."""
    m = _SIG.match(sig.strip())
    if not m:
        return None
    name, params, ret = m.groups()
    types = [p.split(":")[-1].strip() for p in params.split(";") if p.strip()]
    body = REAL_STUBS.get(f"{name}({';'.join(types)}):{ret}")
    if body is None:
        return None
    plist = ";".join(f"v{i}:{t}" for i, t in enumerate(types))
    return f"f={name}({plist}):{ret}{{{body}}};"



_FSTART = re.compile(r"f=([a-z][a-z0-9]*)\(")


def _scan_params(src, start):
    """Parameter list starting just after the `(` at `start`, paren-aware
    (`a:@(@u64);b:i64` -> ['a:@(@u64)', 'b:i64']). Returns (params, index of
    the closing `)`) or (None, -1) when unterminated."""
    depth, cur, params = 0, "", []
    for i in range(start, len(src)):
        ch = src[i]
        if ch == ")" and depth == 0:
            params.append(cur)
            return [p for p in params if p.strip()], i
        if ch == ";" and depth == 0:
            params.append(cur); cur = ""
            continue
        depth += (ch == "(") - (ch == ")")
        cur += ch
    return None, -1




def _norm_type(t):
    """`@(T)` -> `@T` at every level (`@(@(i64))` -> `@@i64`); other spellings
    are returned stripped. 131.44: the sampler-mangled UNTERMINATED spelling
    the 129.7 a_tests bank carries (`@(u64`, `@(@(u64`, `@(str:i64`) is
    normalised the same way (`@u64`, `@@u64`, `@str:i64`) so a_tests
    input_types compare equal to effective_input_types(spec)."""
    t = t.strip()
    if t.startswith("@("):
        inner = t[2:]
        if inner.endswith(")") and _balanced(inner[:-1]):
            return "@" + _norm_type(inner[:-1])
        if inner.count("(") >= inner.count(")"):
            return "@" + _norm_type(inner)
    return t


def _balanced(t):
    depth = 0
    for ch in t:
        depth += (ch == "(") - (ch == ")")
        if depth < 0:
            return False
    return depth == 0


def norm_types(types):
    """_norm_type over a list (a_tests `input_types`, spec input types)."""
    return [_norm_type(t) for t in (types or [])]


def sig_input_types(spec):
    """Input types parsed from the description's f=sig — authoritative when the
    spec's input_types field is corrupted (the @(T);U sampler split bug that
    mangled 3,976 A-side specs, found 129.7)."""
    desc = spec.get("description", "") or ""
    m = _FSTART.search(desc)
    if not m:
        return None
    # 131.44: paren-aware at any depth (the old regex handled one level, so
    # `f=flatten(arrs:@(@(u64))):@(u64)` specs kept their mangled types)
    types, close = _scan_params(desc, m.end())
    if types is None or not desc[close + 1:].startswith(":"):
        return None
    out = []
    for p in types:
        if ":" not in p:
            return None
        out.append(_norm_type(p.split(":", 1)[1]))
    return out


def effective_input_types(spec):
    spec_types = spec.get("input_types_v03") or spec.get("input_types") or []
    if any(("(" in t) != (")" in t) for t in spec_types):
        return sig_input_types(spec) or spec_types
    return spec_types


def _esc(s):
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\t", "\\t"))


# Numeric scalar types whose bare literal does NOT type-check as the parameter
# type: an integer literal is i64 and a float literal is f64 (probe-verified on
# tkc 2.8.0: `g(6)` for `g(a:u64)` is E4031, `g((6 as u64))` passes; the cast
# raises only W1001 "lossy cast", a warning). 131.40 — ported from
# diff_check.lit (131.14), which fixed it locally.
_CAST_INT = ("u64", "u32", "u16", "u8", "i32", "i16", "i8", "byte")
_CAST_FLT = ("f32",)
_UNSIGNED = ("u64", "u32", "u16", "u8", "byte")


def lit(v, t):
    """Render a spec test-case input as a toke literal of (v03) type t.
    Non-default numeric scalars are wrapped as `(N as T)` (see _CAST_INT);
    array element types recurse, so `@u64` renders `@((1 as u64);(2 as u64))`
    and `@@u64` nests. Empty arrays render `@()` for every element type."""
    t = _norm_type(t)
    if t.startswith("@"):
        if not isinstance(v, list):
            raise ValueError(f"array type {t} but non-list input {v!r}")
        return "@(" + ";".join(lit(x, t[1:]) for x in v) + ")"
    if t in ("str", "$str"):
        return '"' + _esc(str(v)) + '"'
    if t == "bool":
        return "true" if v else "false"
    if t in ("f64",) + _CAST_FLT:
        s = repr(float(v))
        s = s if "." in s or "e" in s else s + ".0"
        return f"({s} as {t})" if t in _CAST_FLT else s
    if isinstance(v, bool):
        raise ValueError(f"bool input {v!r} for integer type {t}")
    n = int(v)
    if t in _UNSIGNED and n < 0:
        raise ValueError(f"negative input {n} for unsigned type {t}")
    if t in _CAST_INT:
        return f"({n} as {t})"
    return str(n)


def render_out(v):
    """One printed line as the driver's interpolation will render the value."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def expected_lines(spec):
    """Flat list of expected stdout lines for the driver module (array-return
    expectations contribute one line per element; a str expected containing
    embedded newlines prints as — and must match — that many stdout lines)."""
    lines = []
    for tc in spec.get("test_cases") or []:
        exp = tc.get("expected")
        if err_name(exp) is not None:
            lines.append(render_err(err_name(exp)))   # 131.44 (d)
        elif isinstance(exp, list):
            lines.extend(render_out(x) for x in exp)
        elif isinstance(exp, str) and "\n" in exp:
            lines.extend(exp.split("\n"))
        else:
            lines.append(render_out(exp))
    return lines


def _stub_names(spec):
    ctx = spec.get("domain_context_v03", "") or spec.get("domain_context", "")
    return {m.group(1) for m in _FN.finditer(ctx.replace(" ", "\n") + "{")} | \
           {m.group(1) for m in re.finditer(r"f=([a-z0-9]+)\(", ctx)}


def function_decls(src):
    """Every `f=name(params):ret{` declaration in src, paren-aware in the
    parameter list (131.44: the old `[^)]*` regex could not see
    `f=flatten(p:@(@u64)):@u64` and mis-reported 6 A-ARR-0077 records as
    function-less). Returns [{name, params, ret, pos}] in source order; `ret`
    is the raw return type text (None when no `:ret{` follows)."""
    out = []
    for m in _FSTART.finditer(src):
        params, close = _scan_params(src, m.end())
        if params is None:
            continue
        ret = None
        rest = src[close + 1:]
        r = re.match(r":([^{\s]+)\s*\{", rest)
        if r:
            ret = r.group(1)
        out.append({"name": m.group(1), "params": params, "ret": ret, "pos": m.start()})
    return out


def find_target(spec, assembled_src):
    """The worker's target function in an assembled module: last declared
    function that is not a context stub and whose arity matches the spec input
    count (falls back to the last non-stub declaration)."""
    n_inputs = len(effective_input_types(spec))
    stubs = _stub_names(spec)
    decls = [(d["name"], d["params"]) for d in function_decls(assembled_src)
             if d["name"] not in stubs and d["name"] != "main"]
    if not decls:
        return None
    matches = [name for name, params in decls if len(params) == n_inputs]
    return matches[-1] if matches else decls[-1][0]


def _last_decl(assembled_src, target):
    ds = [d for d in function_decls(assembled_src) if d["name"] == target]
    return ds[-1] if ds else None


def declared_input_types(assembled_src, target):
    """Parameter types as the target function DECLARES them in the assembled
    module (normalised, `@(T)` -> `@T`), or None when the declaration cannot
    be parsed. 131.40: literals must type-check against the callee, not the
    spec — 250 frozen records declare `i64` where the spec says `u64`
    (signature drift the arity-only signature gate accepts), and a `u64`
    cast on those is E4031 exactly like a bare int on a real `u64` param."""
    d = _last_decl(assembled_src, target)
    if d is None or any(":" not in t for t in d["params"]):
        return None
    return [_norm_type(t.split(":", 1)[1]) for t in d["params"]]


def declared_return_type(assembled_src, target):
    """Raw return type text of the target's declaration (e.g. `u64!$lookuperr`,
    `@(u64)`), or None."""
    d = _last_decl(assembled_src, target)
    return d["ret"] if d else None


# ---------------------------------------------------------------------------
# 131.44 (d): `T!Err` err-case expectations.
#
# Printed form for an err result: `err:<variant>` where <variant> is the tag
# of the error-type variant the record returns, without its `$` (Profile-1
# lowercase, no underscore — e.g. `err:notfound`, `err:emptycollection`).
# The a_tests side spells the name in the reference's Python (`{'err':
# 'NotFound'}`, `{'error': 'DivByZero'}`, `{'err': 'Overflow', 'msg': …}`):
# render_err lowercases and strips non-alphanumerics, so the spec-mandated
# `t=$lookuperr{NotFound:str;...}` and the record's `$notfound` agree.
#
# Probe-verified on tkc 2.8.0 (131.44): interpolating a `T!Err` value prints
# the ok payload (an err prints `0`), `"\(e)"` on the err binding prints a
# pointer, but a nested match on the err binding —
#   mt g(0) {$ok:v "\(v)";$err:q mt q {$notfound:w "err:notfound";$emptycollection:w "err:emptycollection"}}
# — yields the variant name; arms are single expressions, so array results
# are printed by a second call guarded by the err string (see append_main).
# ---------------------------------------------------------------------------
_ERR_KEYS = ("err", "error")


def err_name(expected):
    """The err marker name of an a_tests expectation (`{'err': 'NotFound'}` ->
    'NotFound'), or None when the expectation is not an err marker."""
    if not isinstance(expected, dict):
        return None
    for k in _ERR_KEYS:
        v = expected.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def render_err(name):
    """Expected stdout line for an err marker: `err:` + name lowercased with
    non-alphanumerics dropped (`NotFound` -> `err:notfound`)."""
    return "err:" + re.sub(r"[^a-z0-9]", "", name.lower())


def declared_err_variants(src, errtype):
    """Variant tags (without `$`) of `t=$name{$a:T;$b:T}` declared in src for
    the error type `$name` (as spelt in a `T!$name` return type), or None."""
    name = errtype.lstrip("$")
    m = re.search(rf"t=\${re.escape(name)}\{{([^}}]*)\}}", src)
    if not m:
        return None
    tags = re.findall(r"\$([a-z0-9]+)\s*:", m.group(1))
    return tags or None


def err_arm(src, ret, k):
    """The `$err` arm expression that prints `err:<variant>` for the declared
    return type `T!$name` (binding `q{k}`), or (None, error)."""
    errtype = ret.split("!", 1)[1]
    if not errtype.startswith("$"):
        return None, f"return type {ret}: error type {errtype} is not a declared $type"
    tags = declared_err_variants(src, errtype)
    if not tags:
        return None, f"return type {ret}: no t={errtype}{{...}} declaration in the module"
    arms = ";".join(f'${t}:w{k} "{render_err(t)}"' for t in tags)
    return f"mt q{k} {{{arms}}}", None


def literal_types(spec, assembled_src, target):
    """Types the driver renders each test input with: the target's declared
    parameter types when they parse at the spec's arity, else the spec's."""
    in_types = effective_input_types(spec)
    decl = declared_input_types(assembled_src, target)
    return decl if decl is not None and len(decl) == len(in_types) else in_types


def _swap_real_stubs(spec, assembled_src):
    """Replace the harness's dummy stubs with real-bodied ones so execution is
    meaningful. Returns (src, error)."""
    from assemble import stub_from_sig
    ctx = spec.get("domain_context_v03", "")
    src = assembled_src
    for sig in re.findall(r"f=[a-z0-9]+\([^)]*\):\S+", ctx):
        dummy = stub_from_sig(sig)
        if not dummy or dummy not in src:
            continue
        real = real_stub(sig)
        if real:
            src = src.replace(dummy, real)
        else:
            # no real body known: only fatal if the worker actually calls it
            name = _SIG.match(sig).group(1)
            tail = src.split(dummy, 1)[1]
            if re.search(rf"\b{re.escape(name)}\s*\(", tail):
                return None, f"stub-dependent: no real body for {sig}"
    return src, None


def stdin_cases(spec):
    """131.18 stdin_program specs: normalised list of {input, expected_output,
    fixtures} — the per-execution unit (one binary run per case, input on
    stdin, whole stdout compared). Mirrors expected_lines() for the
    single_function/full_program one-line-per-case convention."""
    out = []
    for tc in spec.get("test_cases") or []:
        out.append({"input": tc.get("input", "") or "",
                    "expected_output": tc.get("expected_output", "") or "",
                    "fixtures": tc.get("fixtures") or None})
    return out


def append_main(spec, assembled_src):
    """Assembled single_function module + real-bodied stubs + generated main().
    Returns (source, error) — error is set when synthesis isn't possible."""
    if spec.get("task_type") == "stdin_program":
        return None, "stdin_program: has its own main; run via validate.run_stdin_cases"
    in_types = effective_input_types(spec)
    ret = spec.get("output_type_v03") or spec.get("output_type") or ""
    tcs = spec.get("test_cases") or []
    if not tcs:
        return None, "no test cases"
    if "f=main(" in assembled_src:
        return None, "module already has main"
    assembled_src, err = _swap_real_stubs(spec, assembled_src)
    if err:
        return None, err
    target = find_target(spec, assembled_src)
    if not target:
        return None, "no target function found"
    lit_types = literal_types(spec, assembled_src, target)
    # 131.44 (d): the PRINTING form follows the target's DECLARED return type
    # (an err union is matched with mt; a record that drifted to `str` prints
    # its string and fails the `err:<variant>` expectation — 131.42)
    decl_ret = declared_return_type(assembled_src, target)
    is_err = bool(decl_ret and "!" in decl_ret)
    calls = []
    for k, tc in enumerate(tcs):
        ins = tc.get("inputs") or []
        if len(ins) != len(in_types):
            return None, f"test case {k}: {len(ins)} inputs vs {len(in_types)} types"
        try:
            args = ";".join(lit(v, t) for v, t in zip(ins, lit_types))
        except (ValueError, TypeError) as e:
            return None, f"test case {k}: {e}"
        call = f"{target}({args})"
        if is_err:
            c, err = _err_call(assembled_src, decl_ret, call, k)
            if err:
                return None, err
            calls.append(c)
            continue
        # str results are printed DIRECTLY (io.println(v)) — interpolating a
        # method-call-derived str prints a pointer (compiler bug 127.7), and the
        # driver must not amplify that into false failures
        if ret == "@str":
            calls.append(f"let r{k}={call};"
                         f"lp(let x{k}=0;x{k}<r{k}.len;x{k}=x{k}+1){{io.println(r{k}.get(x{k}))}}")
        elif ret.startswith("@"):
            calls.append(f"let r{k}={call};"
                         f'lp(let x{k}=0;x{k}<r{k}.len;x{k}=x{k}+1){{io.println("\\(r{k}.get(x{k}))")}}')
        elif ret == "str":
            calls.append(f"let r{k}={call};io.println(r{k})")
        else:
            calls.append(f'io.println("\\({call})")')
    return assembled_src.rstrip() + "\nf=main():i64{" + ";".join(calls) + ";<0};\n", None


def _err_call(src, decl_ret, call, k):
    """One test case against a `T!$err` target: prints the ok value in the
    same form as the non-union path (str direct, arrays one line per element,
    else interpolated) or `err:<variant>`. Returns (statement, error)."""
    arm, err = err_arm(src, decl_ret, k)
    if err:
        return None, err
    okt = _norm_type(decl_ret.split("!", 1)[0])
    if okt.startswith("@"):
        elem = f"r{k}.get(x{k})" if okt == "@str" else f'"\\(r{k}.get(x{k}))"'
        return (f'let e{k}=mt {call} {{$ok:v{k} "";$err:q{k} {arm}}};'
                f"if(e{k}==\"\"){{let r{k}=mt {call} {{$ok:v{k} v{k};$err:q{k} @()}};"
                f"lp(let x{k}=0;x{k}<r{k}.len;x{k}=x{k}+1){{io.println({elem})}}}}"
                f"el{{io.println(e{k})}}"), None
    okv = f"v{k}" if okt in ("str", "$str") else f'"\\(v{k})"'
    return f"let r{k}=mt {call} {{$ok:v{k} {okv};$err:q{k} {arm}}};io.println(r{k})", None


def build_module(spec, raw):
    """From raw worker output: assemble (stubs + sanitize) then append main()."""
    return append_main(spec, assemble(spec, raw))


if __name__ == "__main__":
    import json, sys
    spec = json.load(open(sys.argv[1]))
    src, err = append_main(spec, open(sys.argv[2]).read())
    if err:
        sys.exit(f"driver: {err}")
    sys.stdout.write(src)
