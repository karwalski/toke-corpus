"""131.44 (d) — `T!Err` err-case expectations. An a_tests case expecting
`{'err': 'NotFound'}` (or `{'error': ...}`) renders as the stdout line
`err:notfound`; the driver's main() matches a `T!$err` result with mt and a
nested mt on the err binding prints `err:<variant>` (probe-verified tkc
2.8.0: interpolating the union prints the ok payload, `"\\(e)"` a pointer)."""
import os, subprocess, sys, tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import driver as drv  # noqa: E402
import tkc_pin        # noqa: E402
import validate       # noqa: E402

TKC = tkc_pin.default_tkc()
HAVE_TKC = os.path.exists(TKC)

PREFIX = "m=harness;\ni=io:std.io;\ni=s:std.str;\n"
ERRT = "t=$lookuperr{$notfound:str;$emptycollection:bool};\n"
SRC_SCALAR = PREFIX + ERRT + (
    "f=safefirst(a:@u64):u64!$lookuperr{\n"
    "  if(a.len==0){<$lookuperr{$emptycollection:true}};\n  <a.get(0)\n};\n")
SRC_ARRAY = PREFIX + ERRT + (
    "f=tail(a:@u64):@u64!$lookuperr{\n"
    "  if(a.len==0){<$lookuperr{$emptycollection:true}};\n"
    "  let out=mut.@();\n  lp(let i=1;i<a.len;i=i+1){out=out.append(a.get(i))};\n  <out\n};\n")
SRC_STR = PREFIX + ERRT + (
    "f=firstword(a:@str):str!$lookuperr{\n"
    "  if(a.len==0){<$lookuperr{$emptycollection:true}};\n  <a.get(0)\n};\n")


def _spec(name, in_t, out_t, cases):
    return {"task_id": "A-ERR-0009v1", "category": "A-ERR", "task_type": "single_function",
            "description": f"Write a function f={name}({in_t[0]}):{out_t} that x. Variant 1.",
            "input_types_v03": [t.split(":")[1] for t in in_t], "output_type_v03": out_t,
            "domain_context_v03": "", "test_cases": cases}


SPEC_SCALAR = _spec("safefirst", ["arr:@u64"], "u64!LookupErr",
                    [{"inputs": [[8, 1, 8]], "expected": 8},
                     {"inputs": [[]], "expected": {"err": "EmptyCollection"}}])
SPEC_ARRAY = _spec("tail", ["arr:@u64"], "@u64!LookupErr",
                   [{"inputs": [[8, 1, 9]], "expected": [1, 9]},
                    {"inputs": [[]], "expected": {"error": "EmptyCollection"}},
                    {"inputs": [[5]], "expected": []}])
SPEC_STR = _spec("firstword", ["arr:@str"], "str!LookupErr",
                 [{"inputs": [["ab", "c"]], "expected": "ab"},
                  {"inputs": [[]], "expected": {"err": "EmptyCollection", "msg": "empty"}}])


# ------------------------------------------------------------- rendering ---
def test_err_name_and_render():
    assert drv.err_name({"err": "NotFound"}) == "NotFound"
    assert drv.err_name({"error": "DivByZero"}) == "DivByZero"
    assert drv.err_name({"err": "Overflow", "msg": "negative input"}) == "Overflow"
    assert drv.err_name({"a": 2, "b": 1}) is None          # a map expectation
    assert drv.err_name({}) is None and drv.err_name(5) is None and drv.err_name("err") is None
    assert drv.render_err("NotFound") == "err:notfound"
    assert drv.render_err("Empty_Collection") == "err:emptycollection"
    assert drv.render_err("DivByZero") == "err:divbyzero"


def test_expected_lines_render_err_cases():
    assert drv.expected_lines(SPEC_SCALAR) == ["8", "err:emptycollection"]
    assert drv.expected_lines(SPEC_ARRAY) == ["1", "9", "err:emptycollection"]
    assert drv.expected_lines(SPEC_STR) == ["ab", "err:emptycollection"]


def test_validate_render_expected_is_err_aware():
    assert validate.render_expected({"err": "NotFound"}) == "err:notfound"
    assert validate.render_expected({"error": "DivByZero"}) == "err:divbyzero"
    assert validate.render_expected(True) == "true"        # full_program convention kept
    assert validate.render_expected(2.0) == "2"
    assert validate.render_expected("x") == "x"


def test_declared_err_variants_and_return_type():
    assert drv.declared_return_type(SRC_SCALAR, "safefirst") == "u64!$lookuperr"
    assert drv.declared_err_variants(SRC_SCALAR, "$lookuperr") == ["notfound", "emptycollection"]
    assert drv.declared_err_variants(SRC_SCALAR, "lookuperr") == ["notfound", "emptycollection"]
    assert drv.declared_err_variants(PREFIX, "$lookuperr") is None
    arm, err = drv.err_arm(SRC_SCALAR, "u64!$lookuperr", 3)
    assert err is None
    assert arm == 'mt q3 {$notfound:w3 "err:notfound";$emptycollection:w3 "err:emptycollection"}'


# ----------------------------------------------------------- append_main ---
def test_append_main_scalar_err_form():
    out, err = drv.append_main(SPEC_SCALAR, SRC_SCALAR)
    assert err is None
    assert ('let r1=mt safefirst(@()) {$ok:v1 "\\(v1)";$err:q1 mt q1 {$notfound:w1 '
            '"err:notfound";$emptycollection:w1 "err:emptycollection"}};io.println(r1)') in out


def test_append_main_array_err_form_calls_twice_guarded():
    out, err = drv.append_main(SPEC_ARRAY, SRC_ARRAY)
    assert err is None
    assert 'let e1=mt tail(@()) {$ok:v1 "";$err:q1 mt q1 {' in out
    assert 'if(e1==""){let r1=mt tail(@()) {$ok:v1 v1;$err:q1 @()};' in out
    assert 'io.println("\\(r1.get(x1))")' in out and "el{io.println(e1)}" in out


def test_append_main_str_err_prints_direct():
    out, err = drv.append_main(SPEC_STR, SRC_STR)
    assert err is None
    assert 'let r0=mt firstword(@("ab";"c")) {$ok:v0 v0;$err:q0 mt q0 {' in out


def test_append_main_err_union_without_type_decl_is_driver_error():
    src = SRC_SCALAR.replace(ERRT, "")
    out, err = drv.append_main(SPEC_SCALAR, src)
    assert out is None and "no t=$lookuperr{...} declaration" in err


def test_drifted_str_return_takes_plain_path_and_fails_expectation():
    # 131.42: the record returns the marker as a str — printed directly, so
    # the `err:emptycollection` expectation is a real (visible) mismatch
    src = PREFIX + 'f=safefirst(a:@u64):str{if(a.len==0){<"{\'err\': \'EmptyCollection\'}"};<"\\(a.get(0))"};\n'
    out, err = drv.append_main(SPEC_SCALAR, src)
    assert err is None
    assert 'io.println("\\(safefirst(@()))")' in out and "mt " not in out
    assert drv.expected_lines(SPEC_SCALAR)[1] == "err:emptycollection"


# ------------------------------------------------------------- tkc probe ---
def _run(src):
    with tempfile.TemporaryDirectory() as d:
        tk = os.path.join(d, "e.tk")
        with open(tk, "w") as f:
            f.write(src)
        b = subprocess.run([TKC, tk, "-o", os.path.join(d, "e")], capture_output=True, text=True, timeout=90)
        assert b.returncode == 0, b.stdout + b.stderr
        r = subprocess.run([os.path.join(d, "e")], capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout.splitlines()


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not available")
@pytest.mark.parametrize("spec,src", [(SPEC_SCALAR, SRC_SCALAR), (SPEC_ARRAY, SRC_ARRAY),
                                      (SPEC_STR, SRC_STR)])
def test_err_driver_compiles_and_matches(spec, src):
    out, err = drv.append_main(spec, src)
    assert err is None
    rc, lines = _run(out)
    assert rc == 0 and lines == drv.expected_lines(spec)
