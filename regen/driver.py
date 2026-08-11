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


def _esc(s):
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\t", "\\t"))


def lit(v, t):
    """Render a spec test-case input as a toke literal of (v03) type t."""
    if t.startswith("@"):
        if not isinstance(v, list):
            raise ValueError(f"array type {t} but non-list input {v!r}")
        return "@(" + ";".join(lit(x, t[1:]) for x in v) + ")"
    if t == "str":
        return '"' + _esc(str(v)) + '"'
    if t == "bool":
        return "true" if v else "false"
    if t in ("f64", "f32"):
        s = repr(float(v))
        return s if "." in s or "e" in s else s + ".0"
    return str(int(v))


def render_out(v):
    """One printed line as the driver's interpolation will render the value."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def expected_lines(spec):
    """Flat list of expected stdout lines for the driver module (array-return
    expectations contribute one line per element)."""
    lines = []
    for tc in spec.get("test_cases") or []:
        exp = tc.get("expected")
        if isinstance(exp, list):
            lines.extend(render_out(x) for x in exp)
        else:
            lines.append(render_out(exp))
    return lines


def _stub_names(spec):
    ctx = spec.get("domain_context_v03", "") or spec.get("domain_context", "")
    return {m.group(1) for m in _FN.finditer(ctx.replace(" ", "\n") + "{")} | \
           {m.group(1) for m in re.finditer(r"f=([a-z0-9]+)\(", ctx)}


def find_target(spec, assembled_src):
    """The worker's target function in an assembled module: last declared
    function that is not a context stub and whose arity matches the spec input
    count (falls back to the last non-stub declaration)."""
    n_inputs = len(spec.get("input_types_v03") or spec.get("input_types") or [])
    stubs = _stub_names(spec)
    decls = [(m.group(1), [p for p in m.group(2).split(";") if p.strip()])
             for m in _FN.finditer(assembled_src)]
    decls = [(n, p) for n, p in decls if n not in stubs and n != "main"]
    if not decls:
        return None
    matches = [name for name, params in decls if len(params) == n_inputs]
    return matches[-1] if matches else decls[-1][0]


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


def append_main(spec, assembled_src):
    """Assembled single_function module + real-bodied stubs + generated main().
    Returns (source, error) — error is set when synthesis isn't possible."""
    in_types = spec.get("input_types_v03") or spec.get("input_types") or []
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
    calls = []
    for k, tc in enumerate(tcs):
        ins = tc.get("inputs") or []
        if len(ins) != len(in_types):
            return None, f"test case {k}: {len(ins)} inputs vs {len(in_types)} types"
        try:
            args = ";".join(lit(v, t) for v, t in zip(ins, in_types))
        except (ValueError, TypeError) as e:
            return None, f"test case {k}: {e}"
        call = f"{target}({args})"
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
