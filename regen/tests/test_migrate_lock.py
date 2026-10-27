"""131.60 — migrate_fix records get an a_tests test lock (route (a)).

Pure parts (id parsing, lock selection and every no-lock reason) need no
compiler; the diff_check round-trip — a migrate_fix record that used to be
`unverifiable: no main()` is now `identical` against itself and `diverged`
against a behaviour change — is skipped without tkc."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import diff_check as dc               # noqa: E402
import migrate_lock as ml             # noqa: E402
import tkc_pin                        # noqa: E402

TKC = tkc_pin.default_tkc()
HAVE_TKC = os.path.exists(TKC)

TID = "MIG-P2-A-A-ARR-0004v7-e5a31410"
SPEC = {"task_id": TID, "category": "A-ARR", "difficulty": 2, "task_type": "migrate_fix",
        "legacy_source": "m=product;f=product(arr:@i64):i64{...};", "description": "migrate P2-A-A-ARR-0004v7"}
A_TEST = {"base": "A-ARR-0004",
          "python_ref": "def arr_product(arr):\n    r = 1\n    for v in arr:\n        r *= v\n    return r\n",
          "test_cases": [{"inputs": [[2, 3, 4]], "expected": 24}, {"inputs": [[]], "expected": 1},
                         {"inputs": [[-2, 5]], "expected": -10}],
          "input_types": ["@(i64"], "output_type": "i64"}
SRC = ("m=product;\nf=product(arr:@i64):i64{\n  let r=mut.1;\n"
       "  lp(let i=0;i<arr.len;i=i+1){r=r*arr.get(i)};\n  <r\n};\n")


def test_ids():
    assert ml.legacy_task_id(TID) == "A-ARR-0004v7"
    assert ml.base_id(TID) == "A-ARR-0004"
    assert ml.base_id("MIG-P2-B-D-CFG-0003v302-1ec5a5b4") is None          # D-side: no a_tests bank
    assert ml.base_id("MIG-P2-A-ARR-N0029-9403d98c") is None               # N-series
    assert ml.base_id("MIG-B-D-CFG-0001v339-0b56801c") is None
    assert ml.base_id("A-ARR-0004v7") is None


def test_lock_built_from_a_tests():
    lspec, info = ml.lock_spec(SPEC, SRC, a_test=A_TEST)
    assert info["lock"] == "a_tests:A-ARR-0004" and info["target"] == "product"
    assert lspec["task_type"] == "single_function" and lspec["input_types"] == ["@i64"]
    assert lspec["test_cases"] == A_TEST["test_cases"]
    assert lspec["description"].startswith("f=product(")
    assert SPEC["task_type"] == "migrate_fix"                               # input not mutated


@pytest.mark.parametrize("src,a_test,why", [
    (SRC + "f=main():i64{<0};\n", A_TEST, "has main()"),
    (SRC, {**A_TEST, "test_cases": []}, "no a_tests"),
    (SRC.replace("arr:@i64", "arr:@str"), A_TEST, "no function matches"),
    (SRC.replace("):i64{", "):str{", 1), A_TEST, "no function matches"),
    (SRC + "f=other(xs:@str):i64{<0};\n", A_TEST, "ambiguous"),
])
def test_no_lock_reasons(src, a_test, why):
    lspec, info = ml.lock_spec(SPEC, src, a_test=a_test)
    assert lspec is None and why in info["reason"]


def test_d_side_has_no_lock():
    spec = dict(SPEC, task_id="MIG-P2-B-D-CFG-0003v302-1ec5a5b4", category="D-CFG")
    lspec, info = ml.lock_spec(spec, SRC, a_test=A_TEST)
    assert lspec is None and "no A-side base" in info["reason"]


def test_drive_spec_passthrough_and_swap():
    full = {"task_id": "A-ARR-0004v1", "task_type": "full_program"}
    assert ml.drive_spec(full, SRC) == (full, None, None)
    dspec, at, lock = ml.drive_spec(SPEC, SRC, A_TEST)
    assert dspec["task_type"] == "single_function" and at is A_TEST and lock["lock"] == "a_tests:A-ARR-0004"
    assert "a_test" not in lock
    dspec, at, lock = ml.drive_spec(SPEC, SRC + "f=main():i64{<0};\n", A_TEST)
    assert dspec is SPEC and lock["lock"] is None


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not available")
def test_diff_check_drives_a_locked_migrate_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(ml, "load_a_test", lambda base, corpus=None: A_TEST)
    res = dc.diff_check(SRC, SRC, SPEC, TID, str(tmp_path), n_generated=6)
    assert res["verdict"] == "identical", res["reason"]
    assert res["migrate_lock"]["lock"] == "a_tests:A-ARR-0004"
    assert res["checks"]["spec_cases"]["runs"] == 1 and res["checks"]["generated"]["n"] == 6
    wrong = SRC.replace("let r=mut.1;", "let r=mut.2;")
    res = dc.diff_check(SRC, wrong, SPEC, TID, str(tmp_path), n_generated=6)
    assert res["verdict"] == "diverged"


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not available")
def test_unlocked_migrate_fix_stays_unverifiable(tmp_path, monkeypatch):
    monkeypatch.setattr(ml, "load_a_test", lambda base, corpus=None: None)
    res = dc.diff_check(SRC, SRC, SPEC, TID, str(tmp_path), n_generated=6)
    assert res["verdict"] == "unverifiable" and "no main()" in res["reason"]
    assert res["migrate_lock"]["lock"] is None


def test_bool_render_only_is_spelling_and_nothing_else():
    r = {"verdict": "lock_fail", "got": ["true", "false"], "want": ["1", "0"]}
    assert ml.bool_render_only(r)
    assert not ml.bool_render_only(dict(r, got=["false", "false"]))       # a real wrong answer
    assert not ml.bool_render_only(dict(r, verdict="lock_pass"))
    assert not ml.bool_render_only(dict(r, got=["true"] * 12, want=["1"] * 12))   # truncated: not judged


def test_summarise_splits_lock_fail_and_compile_only():
    rows = [{"task_id": "MIG-P2-A-A-ARR-0001v1-0000000a", "verdict": "lock_fail", "got": ["true"], "want": ["1"]},
            {"task_id": "MIG-P2-A-A-ARR-0001v2-0000000b", "verdict": "lock_fail", "got": ["2"], "want": ["1"]},
            {"task_id": "MIG-P2-B-D-CFG-0001v3-0000000c", "verdict": "no_lock", "has_main": False,
             "reason": "no A-side base task in the id"},
            {"task_id": "MIG-P2-B-D-CFG-0001v4-0000000d", "verdict": "no_lock", "has_main": True,
             "reason": "has main(): runs as a full program"}]
    s = ml.summarise(rows, {"tkc_bin_sha": "x"}, {"MIG-P2-A-A-ARR-0001v1-0000000a"})
    assert s["lock_fail_bool_render_only_task_ids"] == ["MIG-P2-A-A-ARR-0001v1-0000000a"]
    assert s["lock_fail_task_ids"] == ["MIG-P2-A-A-ARR-0001v2-0000000b"] and s["lock_fail_genuine"] == 1
    assert s["compile_only"] == 1 and s["has_main_task_ids"] == ["MIG-P2-B-D-CFG-0001v4-0000000d"]
    assert s["auto_bucket_131_14"]["records"] == 1 and s["by_group"]["B"]["records"] == 2
