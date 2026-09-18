"""131.44 (b) — a single_function record must declare its target function.
driver.function_decls is paren-aware (the pre-131.44 `[^)]*` regex could not
see `f=flatten(p:@(@u64)):@u64` and reported 6 A-ARR-0077 records as
function-less); validate.has_target_function is the gate predicate."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import driver as drv                      # noqa: E402
import record_shape_check as rsc          # noqa: E402
from validate import has_target_function, target_name  # noqa: E402

PREFIX = "m=harness;\ni=io:std.io;\ni=s:std.str;\n"
SRC_NESTED = PREFIX + "f=flatten(p:@(@u64)):@u64{\n  let out=mut.@();\n  <out\n};\n"
SPEC = {"task_id": "A-ARR-0077v13", "category": "A-ARR", "task_type": "single_function",
        "description": "Write a function f=flatten(arrs:@(@(u64))):@(u64) that flattens. Variant 13: p and q.",
        "description_v03": "Write a function f=flatten(arrs:@(@u64)):@u64 that flattens. Variant 13: p and q.",
        "input_types_v03": ["@(@(u64"], "output_type_v03": "@u64", "domain_context_v03": ""}


def test_function_decls_nested_parens():
    ds = drv.function_decls(SRC_NESTED + "f=g(a:@u64;b:@(str:i64)):u64!$e{<0};\n")
    assert [(d["name"], d["params"], d["ret"]) for d in ds] == [
        ("flatten", ["p:@(@u64)"], "@u64"),
        ("g", ["a:@u64", "b:@(str:i64)"], "u64!$e")]


def test_function_decls_ignores_unterminated():
    assert drv.function_decls("f=broken(a:@(u64") == []


def test_target_name():
    assert target_name(SPEC) == "flatten"
    assert target_name({"description": "no signature"}) is None


def test_has_target_function_nested_ok():
    assert has_target_function(SRC_NESTED, SPEC) == (True, "ok")


def test_has_target_function_missing():
    ok, detail = has_target_function(PREFIX + "f=flat(p:@(@u64)):@u64{<@()};\n", SPEC)
    assert not ok and "flatten not declared" in detail and "flat" in detail
    ok, detail = has_target_function(PREFIX, SPEC)
    assert not ok and "no functions" in detail


def test_has_target_function_unnamed_target_needs_a_real_decl():
    spec = {"task_type": "single_function", "description": "Sum the inputs. Variant 1.",
            "domain_context_v03": "f=len(a:@i64):i64"}
    stub_only = PREFIX + "f=len(v0:@i64):i64{<v0.len as i64};\nf=main():i64{<0};\n"
    ok, detail = has_target_function(stub_only, spec)
    assert not ok and "no target function" in detail
    ok, detail = has_target_function(stub_only + "f=total(a:@i64):i64{<0};\n", spec)
    assert ok and "total" in detail


def test_has_target_function_not_applicable_elsewhere():
    assert has_target_function("m=x;", dict(SPEC, task_type="full_program"))[0] is True
    assert has_target_function("m=x;", {"description": "f=g(a:i64):i64"})[0] is True  # default type


def test_find_target_sees_nested_paren_params():
    assert drv.find_target(SPEC, SRC_NESTED) == "flatten"
    assert drv.declared_input_types(SRC_NESTED, "flatten") == ["@@u64"]
    assert drv.declared_return_type(SRC_NESTED, "flatten") == "@u64"


def test_classify_reports_legacy_false_negative():
    c = rsc.classify(SRC_NESTED, SPEC)
    assert c["ok"] and not c["legacy_ok"]
    assert c["driver_target"] == "flatten" and c["nested_paren_params"]
    c = rsc.classify(PREFIX + "f=flatten(p:@u64):@u64{<p};\n", SPEC)
    assert c["ok"] and c["legacy_ok"] and not c["nested_paren_params"]
    c = rsc.classify(PREFIX, SPEC)
    assert not c["ok"] and not c["legacy_ok"] and c["driver_target"] is None
