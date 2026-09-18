#!/usr/bin/env python3
"""Shared verifier for A-category test files (worker self-check AND
main-thread banking both call verify() — banking re-executes the Python
reference, so expected values are never taken on faith).

File shape (<workdir>/gen/tests_<base>.json):
  {"base": "A-XXX-0000", "function": "name",
   "python_ref": "def f(...):\\n    ...",
   "test_cases": [{"inputs": [...], "expected": <value>}]}

Checks: schema, 3-5 cases, inputs arity/type vs the spec, ref executes, and
ref(inputs) == expected for every case (this is the ground truth).

131.47: the spec's input types are the 131.44 NORMALISED effective types
(`driver.effective_input_types` -> `driver.norm_types`: `@(u64` -> `@u64`,
description signature wins over a mangled field), and the arity rule is
explicit — a case whose single input is a list holding exactly the spec's
arity of values is called out as PACKED-ARITY (the 129.7 authoring fault the
54-base re-author wave corrects) and rejected. `driver_probe` additionally
renders the cases through the audit driver against a stub of the spec's
signature and `tkc --check`s the result under the PINNED compiler
(tkc_pin), so a case the driver cannot render (negative u64, bool for i64,
an err marker whose variant the declared error type lacks) is rejected at
bank time rather than surfacing as a driver_fail in the audit.

Workdir: 129.7 used work/a_tests_129 (default); the 131.47 wave passes
--workdir corpus/regen_v04/work/atest_131.
"""
import argparse, json, os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import driver as drv                                    # noqa: E402
from driver import effective_input_types                # noqa: E402
from assemble import DEFAULTS                           # noqa: E402
import tkc_pin                                          # noqa: E402  (131.39)

CORPUS = "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04"
WD = os.path.join(CORPUS, "work", "a_tests_129")

_INTS = ("i64", "i32", "i16", "i8", "u64", "u32", "u16", "u8", "byte")
_FLTS = ("f64", "f32")
_STRS = ("str", "$str")
_TDECL = re.compile(r"t=\$([a-z0-9]+)\{([^}]*)\}")


def input_types(spec):
    """131.44/131.47: the spec's effective input types, normalised."""
    return drv.norm_types(effective_input_types(spec))


def _py_ok(v, t):
    """Does JSON value v have the Python shape of (normalised) toke type t?
    Unknown types (maps, user types) are not checked."""
    if t.startswith("@"):
        inner = t[1:]
        if ":" in inner and not inner.startswith("@"):
            return isinstance(v, dict)
        return isinstance(v, list) and all(_py_ok(x, inner) for x in v)
    if t in _INTS:
        return isinstance(v, int) and not isinstance(v, bool)
    if t in _FLTS:
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t in _STRS:
        return isinstance(v, str)
    if t == "bool":
        return isinstance(v, bool)
    return True


def _neg_unsigned(v, t):
    """The first negative value inside v where (normalised) type t says
    unsigned, at any array depth; None otherwise."""
    if t.startswith("@") and isinstance(v, list):
        for x in v:
            n = _neg_unsigned(x, t[1:])
            if n is not None:
                return n
        return None
    if t in drv._UNSIGNED and isinstance(v, int) and not isinstance(v, bool) and v < 0:
        return v
    return None


def arity_error(k, ins, in_types):
    """The arity message for case k, or None. Names the packed-arity shape
    explicitly (131.44 (c): one list carrying all the arguments)."""
    if isinstance(ins, list) and len(ins) == len(in_types):
        return None
    n = len(ins) if isinstance(ins, list) else 0
    msg = f"case {k}: {n} inputs vs {len(in_types)} types"
    if (isinstance(ins, list) and n == 1 and isinstance(ins[0], list)
            and len(in_types) > 1 and len(ins[0]) == len(in_types)):
        msg += (" (packed-arity: the arguments are packed into one list — "
                "`inputs` must list them separately, in signature order)")
    return msg


def declared_err_tags(spec):
    """Variant tags (lowercase, no `$`) of the error type the description
    defines for a `T!Err` return (`Define t=$lookuperr{NotFound:$str;...}`),
    or an empty set when the spec declares none."""
    ret = spec.get("output_type_v03") or spec.get("output_type") or ""
    if "!" not in ret:
        return set()
    ename = re.sub(r"[^a-z0-9]", "", ret.split("!", 1)[1].lower())
    tags = set()
    for dm in _TDECL.finditer(spec.get("description", "") or ""):
        if dm.group(1).lower() == ename:
            for part in dm.group(2).split(";"):
                if ":" in part:
                    tags.add(re.sub(r"[^a-z0-9]", "", part.split(":", 1)[0].lower()))
    return tags


def verify(base, doc, spec, probe=False, notes=None):
    """Errors (empty = pass). probe=True also runs driver_probe (needs tkc);
    `notes` (a list) collects the probe's informational note."""
    errs = []
    if doc.get("base") and doc["base"] != base:
        errs.append(f"file is for base {doc['base']!r}, expected {base!r}")
    tcs = doc.get("test_cases") or []
    if not (3 <= len(tcs) <= 5):
        errs.append(f"need 3-5 test cases, got {len(tcs)}")
    ref = doc.get("python_ref") or ""
    if "def " not in ref:
        errs.append("python_ref must define a function")
    if errs:
        return errs
    in_types = input_types(spec)
    ns = {}
    try:
        exec(ref, {"__builtins__": __builtins__}, ns)  # trusted-local audit tooling
    except Exception as e:
        return [f"python_ref does not execute: {e}"]
    fns = [v for v in ns.values() if callable(v)]
    if not fns:
        return ["python_ref defines no function"]
    fn = fns[-1]
    err_tags = declared_err_tags(spec)
    for k, tc in enumerate(tcs):
        ins = tc.get("inputs")
        a = arity_error(k, ins, in_types)
        if a:
            errs.append(a)
            continue
        en = drv.err_name(tc.get("expected"))
        if en is not None and err_tags and drv.render_err(en)[4:] not in err_tags:
            errs.append(f"case {k}: err marker {en!r} is not a variant of the spec's "
                        f"error type ({', '.join(sorted(err_tags))})")
        for v, t in zip(ins, in_types):
            if not _py_ok(v, t):
                errs.append(f"case {k}: input {v!r} not {t}")
            elif _neg_unsigned(v, t) is not None:
                errs.append(f"case {k}: negative input {_neg_unsigned(v, t)!r} for unsigned {t.lstrip('@')}")
        try:
            got = fn(*ins)
        except Exception as e:
            errs.append(f"case {k}: ref raised {type(e).__name__}: {e}")
            continue
        exp = tc.get("expected")
        ok = (abs(got - exp) < 1e-6 if isinstance(exp, float) and isinstance(got, (int, float))
              and not isinstance(exp, bool) else got == exp)
        if not ok:
            errs.append(f"case {k}: ref({ins}) = {got!r} but expected {exp!r}")
    if errs or not probe:
        return errs
    perrs, note = driver_probe(spec, tcs)
    if notes is not None and note:
        notes.append(note)
    return perrs


# ---------------------------------------------------------------------------
# 131.47: driver probe under the pinned tkc
# ---------------------------------------------------------------------------
def tk_type(t):
    """Toke spelling of a normalised type for a stub declaration: `$str` ->
    `str`, a map `@str:i64` -> `@(str:i64)`, arrays keep the `@T` form."""
    t = drv._norm_type(t)
    if t.startswith("@"):
        inner = t[1:]
        if ":" in inner and not inner.startswith("@"):
            return "@(" + tk_type(inner) + ")"
        return "@" + tk_type(inner)
    if ":" in t:
        k, v = t.split(":", 1)
        return tk_type(k) + ":" + tk_type(v)
    return "str" if t == "$str" else t


def _default_expr(t):
    t = drv._norm_type(t)
    if t.startswith("@"):
        return "@()"
    if t in drv._CAST_INT:
        return f"(0 as {t})"
    if t in drv._CAST_FLT:
        return f"(0.0 as {t})"
    return DEFAULTS.get(t, "0")


def stub_module(spec, cases=None):
    """A compilable module declaring ONLY the spec's target signature with a
    dummy body (plus the `t=$err{...}` type a `T!Err` return needs, taken
    from the description's `Define t=$name{...}` or, failing that, from the
    err markers in the cases). Its purpose is to give driver.append_main a
    declaration to render the test inputs against."""
    desc = spec.get("description", "") or ""
    m = drv._FSTART.search(desc)
    name = m.group(1) if m else "target"
    types = input_types(spec)
    params = ";".join(f"p{i}:{tk_type(t)}" for i, t in enumerate(types))
    ret = drv._norm_type(spec.get("output_type_v03") or spec.get("output_type") or "i64")
    decls = []
    if "!" in ret:
        okt, errt = ret.split("!", 1)
        ename = re.sub(r"[^a-z0-9]", "", errt.lower())
        variants = []
        for dm in _TDECL.finditer(desc):
            if dm.group(1).lower() == ename:
                for part in dm.group(2).split(";"):
                    if ":" in part:
                        tag, ft = part.split(":", 1)
                        variants.append((re.sub(r"[^a-z0-9]", "", tag.lower()), tk_type(ft.strip())))
        if not variants:
            seen = []
            for tc in cases or []:
                en = drv.err_name(tc.get("expected"))
                if en and en not in seen:
                    seen.append(en)
            variants = [(re.sub(r"[^a-z0-9]", "", en.lower()), "str") for en in seen] \
                or [("notfound", "str")]
        decls.append("t=$" + ename + "{" + ";".join(f"${t}:{ft}" for t, ft in variants) + "};")
        body = f"<${ename}{{${variants[0][0]}:{_default_expr(variants[0][1])}}}"
        ret = tk_type(okt) + "!$" + ename
    else:
        body = "<" + _default_expr(ret)
        ret = tk_type(ret)
    decls.append(f"f={name}({params}):{ret}{{{body}}};")
    return "m=harness;\ni=io:std.io;\ni=s:std.str;\n" + "\n".join(decls) + "\n"


def _check(src, tag):
    import validate
    with tempfile.NamedTemporaryFile("w", suffix=f".{tag}.tk", delete=False) as f:
        f.write(src)
        p = f.name
    try:
        rc, codes, diag = validate.tkc_check(p)
    finally:
        os.unlink(p)
    return rc, codes, diag


def driver_probe(spec, cases):
    """Render `cases` through driver.append_main against stub_module(spec)
    and `tkc --check` the result with validate.TKC (the pinned copy once
    tkc_pin.pin().install(validate) ran). Returns (errors, note): errors
    non-empty = the CASES are unrenderable (reject); a stub that does not
    compile on its own is a probe limitation (skipped, note only)."""
    types = input_types(spec)
    if any(":" in t for t in types):
        return [], "probe skipped: map-typed input (driver renders no map literals)"
    stub = stub_module(spec, cases)
    rc, codes, diag = _check(stub, "stub")
    if rc != 0:
        return [], f"probe skipped: stub module does not compile ({','.join(codes) or 'rc ' + str(rc)})"
    spec2 = dict(spec)
    spec2["test_cases"] = cases
    dsrc, err = drv.append_main(spec2, stub)
    if err:
        return [f"driver cannot render the cases: {err}"], None
    rc, codes, diag = _check(dsrc, "drv")
    if rc != 0:
        return [f"driver module fails tkc --check: {','.join(codes) or 'rc ' + str(rc)}"], None
    return [], f"probe ok: {len(cases)} cases render + type-check against f={stub.splitlines()[-1][2:].split('{')[0]}"


def main():
    ap = argparse.ArgumentParser(description="worker self-check for one a_tests file")
    ap.add_argument("base")
    ap.add_argument("--workdir", default=WD,
                    help="wave workdir holding specs/ and gen/ (default: the 129.7 "
                         "work/a_tests_129; the 131.47 wave uses work/atest_131)")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the driver render + pinned tkc --check probe")
    args = ap.parse_args()
    path = os.path.join(args.workdir, "gen", f"tests_{args.base}.json")
    if not os.path.exists(path):
        print(f"FAIL no file at {path}")
        sys.exit(1)
    try:
        doc = json.load(open(path))
    except json.JSONDecodeError as e:
        print(f"FAIL invalid JSON: {e}")
        sys.exit(1)
    spec_path = os.path.join(args.workdir, "specs", args.base + ".json")
    if not os.path.exists(spec_path):
        print(f"FAIL no spec at {spec_path}")
        sys.exit(1)
    spec = json.load(open(spec_path))
    notes = []
    if args.no_probe:
        errs = verify(args.base, doc, spec)
    else:
        import validate
        with tkc_pin.pin().install(validate):
            errs = verify(args.base, doc, spec, probe=True, notes=notes)
    if errs:
        print("FAIL " + "; ".join(errs))
        sys.exit(1)
    print(f"PASS {len(doc['test_cases'])} cases verified against the reference"
          + (f"; {notes[0]}" if notes else ""))


if __name__ == "__main__":
    main()
