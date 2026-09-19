"""131.69 — the baseline-aware differential gate.

`bank_pattern` required a candidate's stdout to be byte-identical to the
original's. That is correct for a style rewrite and wrong when the ORIGINAL is
the defect: the 429 A-ERR records that fake their error case by returning the
a_tests marker as a `str` must change their error line — that IS the fix — so
byte-identity rejected exactly the candidate that matches `render_expected`.

The gate now picks its rule from the baseline:
  original passes its own tests            -> identity (byte-identity, as before)
  original fails them + an err expectation -> repair   (candidate must PASS its
                                              cases; only err lines may change)

The regression that matters is the second half of that rule: where the
original passes, a behaviour-changing candidate must still be rejected.
"""
import json, os, sys, tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import diff_check as dc      # noqa: E402
import driver as drv         # noqa: E402
import bank_pattern          # noqa: E402

HAVE_TKC = os.path.exists(dc.TKC)

# A-ERR-0001-shaped spec: four value cases then one err case, exactly the shape
# of the 429 gamed records (A-ERR-0001v11 / v37 are this spec).
ERR_SPEC = {
    "task_id": "A-ERR-0001v99", "category": "A-ERR", "task_type": "single_function",
    "description": "Write a function f=safediv(a:i64;b:i64):i64!$matherr that returns "
                   "DivByZero error when b is 0, otherwise returns a / b. "
                   "Define t=$matherr{DivByZero:bool;Overflow:str}.",
    "description_v03": "Write a function f=safediv(a:i64;b:i64):i64!$matherr that returns "
                       "DivByZero error when b is 0, otherwise returns a / b. "
                       "Define t=$matherr{DivByZero:bool;Overflow:str}.",
    "input_types_v03": ["i64", "i64"], "output_type_v03": "i64!MathErr",
    "domain_context_v03": "",
    "test_cases": [{"inputs": [10, 2], "expected": 5},
                   {"inputs": [7, 2], "expected": 3},
                   {"inputs": [-8, 2], "expected": -4},
                   {"inputs": [0, 5], "expected": 0},
                   {"inputs": [5, 0], "expected": {"error": "DivByZero"}}]}

# the gaming shape: the callee is a real error union, the target flattens it to
# `str` and prints the harness's own marker for the error case
GAMED = ("m=harness;\ni=io:std.io;\n"
         "t=$matherr{$divbyzero:bool;$overflow:str};\n"
         "f=divide(p:i64;q:i64):i64!$matherr{\n  if(q==0){<$matherr{$divbyzero:true}};\n  <p/q\n};\n"
         'f=safediv(p:i64;q:i64):str{\n'
         '  <mt divide(p;q) {$ok:v "\\(v)";$err:e "{\'error\': \'DivByZero\'}"}\n};\n')
# the correct rewrite: the target itself returns the union
FIXED = ("m=harness;\ni=io:std.io;\n"
         "t=$matherr{$divbyzero:bool;$overflow:str};\n"
         "f=safediv(p:i64;q:i64):i64!$matherr{\n  if(q==0){<$matherr{$divbyzero:true}};\n  <p/q\n};\n")
# correct error handling but a changed VALUE line (4/2 -> 4/2+1): must be rejected
# in either mode, and it is the negative test for the repair rule
FIXED_BUT_WRONG_VALUE = (
    "m=harness;\ni=io:std.io;\n"
    "t=$matherr{$divbyzero:bool;$overflow:str};\n"
    "f=safediv(p:i64;q:i64):i64!$matherr{\n  if(q==0){<$matherr{$divbyzero:true}};\n  <p/q+1\n};\n")
# a correct original (baseline passes) and a behaviour-changing rewrite of it:
# the regression that matters — byte-identity must still be enforced here
CORRECT = FIXED
CORRECT_CHANGED = (
    "m=harness;\ni=io:std.io;\n"
    "t=$matherr{$divbyzero:bool;$overflow:str};\n"
    "f=safediv(p:i64;q:i64):i64!$matherr{\n"
    "  if(q==0){<$matherr{$overflow:\"nope\"}};\n  <p/q\n};\n")


# ------------------------------------------------------------ err flags ---
def test_expected_line_err_flags_is_parallel_to_expected_lines():
    lines = drv.expected_lines(ERR_SPEC)
    flags = drv.expected_line_err_flags(ERR_SPEC)
    assert len(lines) == len(flags) == 5
    assert flags == [False, False, False, False, True]
    assert lines[-1] == "err:divbyzero"


def test_expected_line_err_flags_multiline_and_array_expectations():
    spec = {"test_cases": [{"expected": [1, 2, 3]},
                           {"expected": "a\nb"},
                           {"expected": {"err": "NotFound"}},
                           {"expected": 7}]}
    lines = drv.expected_lines(spec)
    flags = drv.expected_line_err_flags(spec)
    assert len(lines) == len(flags) == 7
    assert flags == [False, False, False, False, False, True, False]


# ------------------------------------------------------- baseline verdict ---
def _run(stdout=b"", exit_code=0, timed_out=False):
    return {"stdout": stdout, "stderr": b"", "exit": exit_code, "wall_ms": 1.0,
            "rss_kb": 100.0, "timed_out": timed_out}


def _prog(tag="orig", test_bin="bin", mode="driver", reason=None):
    p = dc.Program(tag)
    p.mode, p.test_bin, p.reason = mode, test_bin, reason
    return p


GOOD_OUT = b"5\n3\n-4\n0\nerr:divbyzero\n"
GAMED_OUT = b"5\n3\n-4\n0\n{'error': 'DivByZero'}\n"


def test_baseline_pass_fail_unknown():
    base = dc.baseline_verdict(_prog(), ERR_SPEC, [("spec cases", _run(GOOD_OUT))])
    assert base["verdict"] == "pass" and base["err_lines"] == 1
    base = dc.baseline_verdict(_prog(), ERR_SPEC, [("spec cases", _run(GAMED_OUT))])
    assert base["verdict"] == "fail" and base["failing_runs"] == [0]
    base = dc.baseline_verdict(_prog(test_bin=None, reason="no main() — not executable"),
                               ERR_SPEC, [])
    assert base["verdict"] == "unknown" and "not executable" in base["detail"]


def test_baseline_fail_on_nonzero_exit_or_timeout():
    assert dc.baseline_verdict(_prog(), ERR_SPEC,
                               [("spec cases", _run(GOOD_OUT, exit_code=1))])["verdict"] == "fail"
    assert dc.baseline_verdict(_prog(), ERR_SPEC,
                               [("spec cases", _run(GOOD_OUT, timed_out=True))])["verdict"] == "fail"


def test_baseline_unknown_when_expectation_not_derivable():
    base = dc.baseline_verdict(_prog(), {"test_cases": []}, [("spec cases", _run(b""))])
    assert base["verdict"] == "unknown" and base["err_lines"] == 0


# ------------------------------------------------------- repair_compare ---
def _exp():
    return [(drv.expected_lines(ERR_SPEC), drv.expected_line_err_flags(ERR_SPEC))]


def test_repair_compare_accepts_only_the_error_line_moving():
    ok, why, changed, bad = dc.repair_compare(
        _exp(), [("spec cases", _run(GAMED_OUT))], [("spec cases", _run(GOOD_OUT))])
    assert ok and bad is None and "error line repaired" in why
    assert changed == [{"run": "spec cases", "line": 5,
                        "orig": "{'error': 'DivByZero'}", "cand": "err:divbyzero"}]


def test_repair_compare_rejects_a_changed_value_line():
    cand = b"6\n3\n-4\n0\nerr:divbyzero\n"          # line 1 moved: not an error line
    ok, why, _changed, bad = dc.repair_compare(
        _exp(), [("spec cases", _run(GAMED_OUT))], [("spec cases", _run(cand))])
    assert not ok and bad == 0 and "fails its own test cases" in why


def test_repair_compare_rejects_a_candidate_that_does_not_pass():
    cand = b"5\n3\n-4\n0\nerr:overflow\n"           # error line moved to the WRONG variant
    ok, why, _changed, _bad = dc.repair_compare(
        _exp(), [("spec cases", _run(GAMED_OUT))], [("spec cases", _run(cand))])
    assert not ok and "fails its own test cases" in why


def test_repair_compare_rejects_a_crashing_or_line_dropping_candidate():
    ok, why, _c, _b = dc.repair_compare(_exp(), [("spec cases", _run(GAMED_OUT))],
                                        [("spec cases", _run(GOOD_OUT, exit_code=2))])
    assert not ok and "did not complete cleanly" in why
    # passes its cases, but the original printed an extra line the rewrite dropped
    orig = b"5\n3\n-4\n0\n{'error': 'DivByZero'}\ntrailing\n"
    ok, why, _c, _b = dc.repair_compare(_exp(), [("spec cases", _run(orig))],
                                        [("spec cases", _run(GOOD_OUT))])
    assert not ok and "line count changed" in why


# ---------------------------------------------------- compare() wiring ---
def _mock_spec_runs(monkeypatch, out_by_tag):
    monkeypatch.setattr(dc, "run_spec_cases",
                        lambda prog, wd, timeout=dc.RUN_TIMEOUT: [("spec cases", _run(out_by_tag[prog.tag]))])


def test_compare_is_byte_identical_unless_asked(monkeypatch):
    """The default is unchanged: pattern_autofix's 131.14 AUTO wave and every
    other caller keep plain byte-identity even on a defective baseline."""
    _mock_spec_runs(monkeypatch, {"orig": GAMED_OUT, "cand": GOOD_OUT})
    r = dc.compare(_prog("orig"), _prog("cand"), ERR_SPEC, "t", "/tmp")
    assert r["verdict"] == "diverged" and r["mode"] == "identity" and r["baseline"] is None


def test_compare_repair_mode_accepts_the_corrected_error_line(monkeypatch):
    _mock_spec_runs(monkeypatch, {"orig": GAMED_OUT, "cand": GOOD_OUT})
    r = dc.compare(_prog("orig"), _prog("cand"), ERR_SPEC, "t", "/tmp", baseline_aware=True)
    assert r["verdict"] == "identical" and r["mode"] == "repair"
    assert r["baseline"]["verdict"] == "fail"
    sc = r["checks"]["spec_cases"]
    assert sc["gate"] == "repair" and len(sc["error_lines_changed"]) == 1


def test_compare_keeps_identity_when_the_baseline_passes(monkeypatch):
    """The regression that matters: a passing original still pins its output."""
    _mock_spec_runs(monkeypatch, {"orig": GOOD_OUT, "cand": b"5\n3\n-4\n0\nerr:overflow\n"})
    r = dc.compare(_prog("orig"), _prog("cand"), ERR_SPEC, "t", "/tmp", baseline_aware=True)
    assert r["verdict"] == "diverged" and r["mode"] == "identity"
    assert r["baseline"]["verdict"] == "pass"
    _mock_spec_runs(monkeypatch, {"orig": GOOD_OUT, "cand": GOOD_OUT})
    r = dc.compare(_prog("orig"), _prog("cand"), ERR_SPEC, "t", "/tmp", baseline_aware=True)
    assert r["verdict"] == "identical" and r["mode"] == "identity"


def test_compare_keeps_identity_when_a_failing_baseline_has_no_err_case(monkeypatch):
    """A failing baseline with nothing an err rule could relax stays strict —
    the repair rule would have nothing to permit, so the stricter one holds."""
    spec = dict(ERR_SPEC, test_cases=[{"inputs": [10, 2], "expected": 5}])
    _mock_spec_runs(monkeypatch, {"orig": b"9\n", "cand": b"5\n"})
    r = dc.compare(_prog("orig"), _prog("cand"), spec, "t", "/tmp", baseline_aware=True)
    assert r["verdict"] == "diverged" and r["mode"] == "identity"
    assert r["baseline"]["verdict"] == "fail" and r["baseline"]["err_lines"] == 0


def test_compare_repair_mode_still_rejects_a_crashing_candidate(monkeypatch):
    _mock_spec_runs(monkeypatch, {"orig": GAMED_OUT, "cand": b""})
    r = dc.compare(_prog("orig"), _prog("cand"), ERR_SPEC, "t", "/tmp", baseline_aware=True)
    assert r["verdict"] == "diverged" and r["mode"] == "repair"


# ------------------------------------------------------------ provenance ---
def test_bank_provenance_records_the_mode():
    ev = {"diff": {"mode": "repair", "checks": {"spec_cases": {"error_lines_changed": [
        {"run": "spec cases", "line": 5, "orig": "{'error': 'DivByZero'}", "cand": "err:divbyzero"}]}}}}
    assert bank_pattern._error_lines_changed(ev)[0]["cand"] == "err:divbyzero"
    assert bank_pattern._error_lines_changed({"diff": {"mode": "identity", "checks": {}}}) == []


# ---------------------------------------------------------- real tkc e2e ---
@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_end_to_end_gamed_record_banks_only_the_correct_rewrite():
    """The whole point, through real binaries: the gaming shape fails its own
    tests, the correct rewrite changes only the error line and is accepted,
    and a rewrite that also moves a value line is still rejected."""
    with tempfile.TemporaryDirectory(prefix="b69_") as td:
        r = dc.diff_check(GAMED, FIXED, ERR_SPEC, ERR_SPEC["task_id"], td, baseline_aware=True)
        assert r["verdict"] == "identical", json.dumps(r, indent=1)
        assert r["mode"] == "repair" and r["baseline"]["verdict"] == "fail"
        changed = r["checks"]["spec_cases"]["error_lines_changed"]
        assert [c["orig"] for c in changed] == ["{'error': 'DivByZero'}"]
        assert [c["cand"] for c in changed] == ["err:divbyzero"]

        r = dc.diff_check(GAMED, FIXED_BUT_WRONG_VALUE, ERR_SPEC, ERR_SPEC["task_id"], td,
                          baseline_aware=True)
        assert r["verdict"] == "diverged" and r["mode"] == "repair"
        assert "fails its own test cases" in r["reason"]

        # and without opting in, the correct rewrite is still rejected — the
        # defect this story fixes, kept as the proof it was real
        r = dc.diff_check(GAMED, FIXED, ERR_SPEC, ERR_SPEC["task_id"], td)
        assert r["verdict"] == "diverged" and r["mode"] == "identity"
        assert not os.listdir(td)


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_end_to_end_passing_baseline_still_pins_behaviour():
    """The regression: where the original passes, byte-identity holds and a
    behaviour-changing candidate is rejected even with baseline_aware on."""
    with tempfile.TemporaryDirectory(prefix="b69_") as td:
        r = dc.diff_check(CORRECT, CORRECT_CHANGED, ERR_SPEC, ERR_SPEC["task_id"], td,
                          baseline_aware=True)
        assert r["verdict"] == "diverged", json.dumps(r, indent=1)
        assert r["mode"] == "identity" and r["baseline"]["verdict"] == "pass"
        assert r["first_diff"]["orig_stdout"].splitlines()[-1] == "err:divbyzero"
        assert r["first_diff"]["cand_stdout"].splitlines()[-1] == "err:overflow"
        assert not os.listdir(td)
