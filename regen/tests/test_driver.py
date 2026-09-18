"""131.40 — driver.lit literal rendering: unsigned scalars/arrays must render
as `(N as u64)` (an integer literal types as i64 → E4031 on a u64 parameter,
probe-verified tkc 2.8.0), while i64/bool/f64/str renderings are unchanged.
Also pins append_main's use of lit and one real tkc round-trip (skipped when
tkc is absent)."""
import os, subprocess, sys, tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import driver as drv  # noqa: E402
import tkc_pin        # noqa: E402

TKC = tkc_pin.default_tkc()
HAVE_TKC = os.path.exists(TKC)


# ---------------------------------------------------------------- scalars ---
def test_u64_scalar_is_cast():
    assert drv.lit(6, "u64") == "(6 as u64)"
    assert drv.lit(0, "u64") == "(0 as u64)"
    assert drv.lit(3, "u32") == "(3 as u32)"


def test_u64_negative_rejected():
    with pytest.raises(ValueError):
        drv.lit(-1, "u64")


def test_bool_for_int_rejected():
    with pytest.raises(ValueError):
        drv.lit(True, "u64")
    with pytest.raises(ValueError):
        drv.lit(False, "i64")


def test_i64_unchanged_including_negative():
    assert drv.lit(-7, "i64") == "-7"
    assert drv.lit(0, "i64") == "0"
    assert drv.lit(2 ** 40, "i64") == str(2 ** 40)


def test_bool_f64_str_unchanged():
    assert drv.lit(True, "bool") == "true"
    assert drv.lit(False, "bool") == "false"
    assert drv.lit(1.5, "f64") == "1.5"
    assert drv.lit(2, "f64") == "2.0"
    assert drv.lit(1e20, "f64") == "1e+20"
    assert drv.lit('a"b\n', "str") == '"a\\"b\\n"'
    assert drv.lit("x", "$str") == '"x"'


def test_f32_is_cast():
    assert drv.lit(1.5, "f32") == "(1.5 as f32)"


# ----------------------------------------------------------------- arrays ---
def test_u64_array_elements_cast():
    assert drv.lit([1, 2], "@u64") == "@((1 as u64);(2 as u64))"


def test_u64_array_mangled_spelling_normalised():
    # the 129.7 sampler spelling `@(u64)` is accepted like `@u64`
    assert drv.lit([1], "@(u64)") == "@((1 as u64))"


def test_empty_arrays_render_bare():
    assert drv.lit([], "@u64") == "@()"
    assert drv.lit([], "@i64") == "@()"


def test_nested_u64_arrays():
    assert drv.lit([[1], []], "@@u64") == "@(@((1 as u64));@())"


def test_i64_and_str_arrays_unchanged():
    assert drv.lit([-1, 2], "@i64") == "@(-1;2)"
    assert drv.lit(["a", "b"], "@str") == '@("a";"b")'
    assert drv.lit([True, False], "@bool") == "@(true;false)"
    assert drv.lit([1.0, 2.5], "@f64") == "@(1.0;2.5)"


def test_array_type_with_scalar_input_rejected():
    with pytest.raises(ValueError):
        drv.lit(3, "@u64")


# ------------------------------------------------------------ append_main ---
SPEC = {"task_id": "T-U64-0001v1", "category": "A-MTH", "task_type": "single_function",
        "description": "Write a function that: f=addlen(a:u64;b:@u64):u64 — a plus the length of b.",
        "input_types_v03": ["u64", "@u64"], "output_type_v03": "u64",
        "domain_context_v03": "",
        "test_cases": [{"inputs": [6, [1, 2]], "expected": 8},
                       {"inputs": [0, []], "expected": 0}]}
SRC = ("m=harness;\ni=io:std.io;\ni=s:std.str;\n"
       "f=addlen(a:u64;b:@u64):u64{<a+b.len as u64};\n")


def test_append_main_uses_cast_literals():
    out, err = drv.append_main(SPEC, SRC)
    assert err is None
    assert "addlen((6 as u64);@((1 as u64);(2 as u64)))" in out
    assert "addlen((0 as u64);@())" in out


SRC_I64 = ("m=harness;\ni=io:std.io;\ni=s:std.str;\n"
           "f=addlen(a:i64;b:@i64):i64{<a+b.len as i64};\n")


def test_declared_input_types_parsed_and_normalised():
    assert drv.declared_input_types(SRC, "addlen") == ["u64", "@u64"]
    assert drv.declared_input_types(SRC_I64, "addlen") == ["i64", "@i64"]
    legacy = "f=g(a:@(u64);b:@(@(i64))):u64{<a.len as u64};"
    assert drv.declared_input_types(legacy, "g") == ["@u64", "@@i64"]
    assert drv.declared_input_types(SRC, "missing") is None


def test_append_main_renders_against_declared_signature():
    # spec says u64 but the frozen record declares i64 (signature drift):
    # the literal must match the CALLEE, or the cast itself is E4031
    out, err = drv.append_main(SPEC, SRC_I64)
    assert err is None
    assert "addlen(6;@(1;2))" in out and "as u64" not in out
    # and the reverse: spec i64, record declares u64
    spec = dict(SPEC, input_types_v03=["i64", "@i64"],
                description="Write a function that: f=addlen(a:i64;b:@i64):u64 — sum.")
    out, err = drv.append_main(spec, SRC)
    assert err is None and "addlen((6 as u64);@((1 as u64);(2 as u64)))" in out


def test_append_main_reports_bad_literal_as_driver_error():
    spec = dict(SPEC, test_cases=[{"inputs": [-1, []], "expected": 0}])
    out, err = drv.append_main(spec, SRC)
    assert out is None and "negative input" in err


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not available")
def test_u64_driver_compiles_and_runs():
    out, err = drv.append_main(SPEC, SRC)
    assert err is None
    with tempfile.TemporaryDirectory() as d:
        tk = os.path.join(d, "u.tk")
        with open(tk, "w") as f:
            f.write(out)
        chk = subprocess.run([TKC, "--check", tk], capture_output=True, text=True, timeout=60)
        assert chk.returncode == 0, chk.stdout + chk.stderr
        b = subprocess.run([TKC, tk, "-o", os.path.join(d, "u")], capture_output=True, text=True, timeout=90)
        assert b.returncode == 0, b.stdout + b.stderr
        r = subprocess.run([os.path.join(d, "u")], capture_output=True, text=True, timeout=15)
        assert r.returncode == 0 and r.stdout.splitlines() == drv.expected_lines(SPEC)
