"""131.71 — driver.lit renders map inputs. The 29 A-ERR records whose specs
take an `@(str:i64)` map could not be driven at all ("array type @str:i64 but
non-list input {...}" on test case 0), so no wave could build or test them.
A JSON object now renders as a toke map literal in key order, and an empty
object as the typed-empty map `@($str:$i64)` (`@()` types as `@(?)`, E4031;
toke 127.226 / C171). One real tkc round-trip, skipped without tkc."""
import os
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import driver as drv  # noqa: E402
import tkc_pin        # noqa: E402

TKC = tkc_pin.default_tkc()
HAVE_TKC = os.path.exists(TKC)


def test_map_types_parsed_from_every_spelling():
    for t in ("@(str:i64)", "@($str:i64", "@str:i64", "@($str:i64)"):
        assert drv.map_types(t) == ("str", "i64"), t
    assert drv.map_types("@(str:@(i64))") == ("str", "@i64")
    assert drv.map_types("@(i64)") is None
    assert drv.map_types("str") is None


def test_array_of_maps_is_not_a_map():
    assert drv.map_types("@(@(str:i64))") is None


def test_map_literal_in_key_order():
    assert drv.lit({"a": 1, "b": 2}, "@($str:i64") == '@("a":1;"b":2)'
    assert drv.lit({"b": 2, "a": 1}, "@(str:i64)") == '@("b":2;"a":1)'
    assert drv.lit({"x": -4}, "@(str:i64)") == '@("x":-4)'


def test_map_values_and_keys_go_through_lit():
    assert drv.lit({"k": [1, 2]}, "@(str:@(i64))") == '@("k":@(1;2))'
    assert drv.lit({"n": 3}, "@(str:u64)") == '@("n":(3 as u64))'
    assert drv.lit({"1": "one"}, "@(i64:str)") == '@(1:"one")'


def test_empty_map_is_typed_empty():
    assert drv.lit({}, "@(str:i64)") == "@($str:$i64)"
    assert drv.lit({}, "@(str:@(i64))") == "@($str:@($i64))"


def test_map_type_with_non_object_rejected():
    with pytest.raises(ValueError):
        drv.lit([1, 2], "@(str:i64)")


def test_type_value_rejects_unspellable():
    with pytest.raises(ValueError):
        drv.type_value("i64!$err")


SPEC = {"task_id": "A-ERR-0030v81", "category": "A-ERR", "task_type": "single_function",
        "description": "Write a function f=look(m:@($str:i64);key:$str;fb:i64):i64 that looks up key.",
        "input_types": ["@($str:i64", "$str", "i64"], "input_types_v03": ["@(str:i64", "str", "i64"],
        "output_type": "i64", "domain_context_v03": "",
        "test_cases": [{"inputs": [{"a": 1, "b": 2}, "b", 9], "expected": 2},
                       {"inputs": [{"a": 1}, "z", 9], "expected": 9},
                       {"inputs": [{}, "a", 9], "expected": 0}]}
SRC = ("m=harness;\ni=io:std.io;\ni=s:std.str;\n"
       "f=look(m:@(str:i64);key:str;fb:i64):i64{\n"
       "  if(m.len==0){<0};\n  <m.getor(key;fb)\n};\n")


def test_append_main_renders_maps():
    out, err = drv.append_main(SPEC, SRC)
    assert err is None, err
    assert 'look(@("a":1;"b":2);"b";9)' in out
    assert 'look(@($str:$i64);"a";9)' in out


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not available")
def test_map_driver_compiles_and_runs():
    out, err = drv.append_main(SPEC, SRC)
    assert err is None
    with tempfile.TemporaryDirectory() as d:
        tk = os.path.join(d, "m.tk")
        with open(tk, "w") as f:
            f.write(out)
        b = subprocess.run([TKC, tk, "-o", os.path.join(d, "m")], capture_output=True, text=True, timeout=90)
        assert b.returncode == 0, b.stdout + b.stderr
        r = subprocess.run([os.path.join(d, "m")], capture_output=True, text=True, timeout=15)
        assert r.returncode == 0 and r.stdout.splitlines() == drv.expected_lines(SPEC) == ["2", "9", "0"]
