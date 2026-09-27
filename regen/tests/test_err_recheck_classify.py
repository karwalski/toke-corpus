"""136.72 (4) — err_recheck_131_67.classify() on records with no test cases.

A record whose bare build fails on BOTH compilers has no output on either
side, so "output unchanged under the fix" is not something anyone observed.
The 131.67 run labelled 20 such records `unverified_behaviour_same`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import err_recheck_131_67 as er        # noqa: E402


def view(bare):
    return {"compile": True, "executed": False, "build": None, "tests_old": None,
            "tests_new": None, "reason": None, "driver_fail": None, "exit": None,
            "got": None, "want": None, "bare": bare}


RAN = {"build": True, "exit": 0, "got": ["1"]}
RAN2 = {"build": True, "exit": 0, "got": ["2"]}
NOBUILD = {"build": False}
TIMEOUT = {"build": True, "timeout": True}


def verdict(old, new):
    return er.classify(view(old), view(new), has_tests=False, err_expect=False)[0]


def test_built_on_neither_compiler_is_not_behaviour_same():
    assert verdict(NOBUILD, NOBUILD) == "unverified_no_output"


def test_timed_out_on_both_is_not_behaviour_same():
    assert verdict(TIMEOUT, TIMEOUT) == "unverified_no_output"


def test_output_on_one_side_only():
    assert verdict(NOBUILD, RAN) == "unverified_output_one_side"
    assert verdict(RAN, NOBUILD) == "unverified_output_one_side"


def test_same_and_changed_still_classified():
    assert verdict(RAN, RAN) == "unverified_behaviour_same"
    assert verdict(RAN, RAN2) == "unverified_behaviour_changed"


def test_compile_fail_still_first():
    new = dict(view(NOBUILD), compile=False)
    assert er.classify(view(NOBUILD), new, False, False)[0] == "compile_fail"
