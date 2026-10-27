"""131.43 — the signature gate compares parameter types, not just counts.

Pure text: no compiler needed. Covers norm_type, the lossless widening
allowlist, the paren-aware parameter scan (the old `[^)]*` regex read
`@(@u64)` as `@(@u64`), the A/B against the verbatim legacy gate, and the
route_patch merge that keeps an earlier story's routing intact."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import regen_validate as rv            # noqa: E402
import route_patch                     # noqa: E402
import signature_gate_131_43 as sg     # noqa: E402

PREFIX = "m=harness;\ni=io:std.io;\ni=s:std.str;\n"


def spec(sig, ttype="single_function"):
    return {"task_id": "A-ARR-9000v1", "category": "A-ARR", "task_type": ttype,
            "description": f"Write a function {sig} that does a thing. Variant 1."}


def test_norm_type_every_level():
    assert rv.norm_type("@(u64)") == "@u64"
    assert rv.norm_type("@(@(u64))") == "@@u64"
    assert rv.norm_type("@($str:i64)") == "@str:i64"
    assert rv.norm_type("@(str:@(i64))") == "@str:@i64"
    assert rv.norm_type("$Str") == "str"
    assert rv.norm_type("@(i64)!$LookupErr") == "@i64!lookuperr"


def test_param_type_conforms_exact_and_widening():
    assert rv.param_type_conforms("u64", "u64")
    assert rv.param_type_conforms("@(u64)", "@u64")
    assert rv.param_type_conforms("i32", "i64")
    assert rv.param_type_conforms("u32", "u64") and rv.param_type_conforms("u32", "i64")
    assert rv.param_type_conforms("@(f32)", "@f64")          # widening holds element-wise
    assert not rv.param_type_conforms("u64", "i64")          # a sign change is not widening
    assert not rv.param_type_conforms("@u64", "@i64")
    assert not rv.param_type_conforms("i64", "i32")          # narrowing
    assert not rv.param_type_conforms("@i64", "i64")         # shape change


def test_drift_rejected_with_the_parameter_named():
    s = spec("f=total(xs:@(u64);n:u64):u64")
    ok, why = rv.signature_conforms(s, PREFIX + "f=total(xs:@i64;n:u64):u64{<0 as u64};\n")
    assert not ok and why == "total: param 1 `xs:@i64` declared `@i64`, spec `@(u64)`"
    ok, _ = rv.signature_conforms(s, PREFIX + "f=total(xs:@u64;n:u64):u64{<0 as u64};\n")
    assert ok


def test_arity_still_enforced_and_paren_aware():
    s = spec("f=flatten(arrs:@(@(u64))):@(u64)")
    assert rv.signature_conforms(s, "f=flatten(arrs:@(@u64)):@u64{<@()};")[0]
    ok, why = rv.signature_conforms(s, "f=flatten(a:@(@u64);b:i64):@u64{<@()};")
    assert not ok and "expected 1 params, got 2" in why


def test_legacy_regex_could_not_see_nested_types():
    s = spec("f=flatten(arrs:@(@(u64))):@(u64)")
    src = "f=flatten(a:@(@i64)):@i64{<@()};"
    assert sg.legacy_signature_conforms(s, src)[0]           # count 1 == 1: passed
    assert not rv.signature_conforms(s, src)[0]              # @@u64 vs @@i64: rejected


def test_declaration_preferred_over_a_call():
    s = spec("f=charfreq(s:$str):@($str:i64)")
    src = 'f=charfreq(s:str):@(str:i64){<@($str:$i64)};\nf=main():i64{let m=charfreq("aab");<0};\n'
    assert rv.signature_conforms(s, src) == (True, "ok")


def test_untyped_parameters_fall_back_to_count():
    s = spec("f=g(a;b):i64")
    assert rv.signature_conforms(s, "f=g(a:i64;b:str):i64{<0};") == (True, "ok")


def test_missing_function_and_no_spec_signature():
    assert rv.signature_conforms(spec("f=g(a:i64):i64"), "f=h(a:i64):i64{<a};")[0] is False
    assert rv.signature_conforms({"description": "no sig"}, "f=h():i64{<0};") == (True, "no target signature in spec")


def test_merge_row_keeps_earlier_story_routing():
    base = {"task_id": "A-ERR-0001v11", "story": "131.42", "bucket": "AGENT",
            "bucket_reason": "131.42 gamed_err_marker: ...",
            "violations": [{"rule": "gamed-err-marker", "severity": "error", "pattern_id": "err-propagate"}],
            "pattern_ids": ["err-propagate"]}
    row = route_patch.merge_row(base, "A-ERR-0001v11", "131.43", "signature-drift", "AGENT", "re-type n", ts=1)
    rules = [v["rule"] for v in row["violations"]]
    assert rules == ["gamed-err-marker", "signature-drift"]
    assert row["bucket_reason"].startswith("131.42 gamed_err_marker") and "131.43: re-type n" in row["bucket_reason"]
    assert row["pattern_ids"] == ["err-propagate"] and row["story"] == "131.43"
    assert base["violations"] == [{"rule": "gamed-err-marker", "severity": "error", "pattern_id": "err-propagate"}]


def test_merge_row_keeps_regen_and_ignores_sweep_reason():
    base = {"task_id": "x", "story": "131.13", "bucket": "REGEN", "bucket_reason": "A-side full_program"}
    row = route_patch.merge_row(base, "x", "131.43", "signature-drift", "AGENT", "why", ts=1,
                                keep_buckets=("REGEN",))
    assert row["bucket"] == "REGEN" and row["bucket_reason"] == "131.43: why"
    assert row["would_bucket"] == "REGEN"


def test_merge_row_without_base_and_idempotent_violation():
    row = route_patch.merge_row(None, "x", "131.83", "r", "AGENT", "why", pattern_ids=("p",),
                                category="A-ARR", task_type="full_program", ts=1)
    again = route_patch.merge_row(row, "x", "131.83", "r", "AGENT", "why", pattern_ids=("p",), ts=1)
    assert again["violations"] == [{"rule": "r", "severity": "error", "pattern_id": "p"}]
    assert row["would_bucket"] is None and row["category"] == "A-ARR"


def test_apply_command_order():
    cmd = route_patch.apply_command()
    order = [cmd.index(f"pattern_sweep.{s}.patch") for s in route_patch.BASE_PATCHES + route_patch.APPLY_ORDER]
    assert order == sorted(order) and cmd.startswith("cat audit/pattern_sweep.jsonl ")
