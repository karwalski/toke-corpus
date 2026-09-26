"""127.187 — a full_program is built whether or not it has test cases, and a
build failure rejects it.

Three A-ERR records (P3-A-ERR-0028v76, 0033v41, 0061v14) passed `tkc --check`
and did not build, and were banked: `validate_one_gates` built a program only
when its spec carried test cases, and when it did build one that failed, it
left `runtime` without a `match` key, so `ok` stayed True. The fixture is a
program with no `main`: `--check` accepts it (a library module is valid) and
the build rejects it (E9023) -- a check/build disagreement that holds by
design, so this test does not depend on a compiler bug staying unfixed."""
import os, sys, tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import run_shard as rs   # noqa: E402
import tkc_pin           # noqa: E402

needs_tkc = pytest.mark.skipif(not os.path.exists(tkc_pin.default_tkc()),
                               reason="tkc not built")

NO_MAIN = "m=prog;\nf=twice(x:i64):i64{\n  <x*2\n};\n"
GOOD = "m=prog;\ni=io:std.io;\nf=main():i64{\n  io.println(\"hi\");\n  <0\n};\n"
SPEC = {"task_id": "A-TST-0187", "category": "A-TST", "task_type": "full_program",
        "difficulty": 1}


def gates(spec, src):
    with tempfile.TemporaryDirectory() as td:
        rec, ok, reason, g = rs.validate_one_gates(spec, src, td)
    return ok, reason, g


@needs_tkc
def test_no_test_cases_build_failure_rejects():
    ok, reason, g = gates(dict(SPEC), NO_MAIN)
    assert g["compile"] is True          # --check accepts it
    assert g["build"] is False
    assert not ok and reason.startswith("build: ")


@needs_tkc
def test_with_test_cases_build_failure_rejects():
    spec = dict(SPEC, test_cases=[{"input": [], "expected": "4"}])
    ok, reason, g = gates(spec, NO_MAIN)
    assert g["build"] is False and g["tests"] is False
    assert not ok and reason.startswith("build: ")


@needs_tkc
def test_no_test_cases_good_program_is_built():
    ok, reason, g = gates(dict(SPEC), GOOD)
    assert g["build"] is True
    assert reason is None or not reason.startswith("build")
