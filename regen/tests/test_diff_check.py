"""131.14 — diff_check: generated inputs, marker parsing, python_ref
cross-check, compare() verdicts (mocked runs), differential() entrypoint, and
one real tiny end-to-end pair through tkc (skipped when tkc is absent)."""
import json, os, sys, tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import diff_check as dc  # noqa: E402
import tkc_pin           # noqa: E402  (131.39)

HAVE_TKC = os.path.exists(dc.TKC)

SF_SPEC = {"task_id": "T-SUM-0001v1", "category": "A-ARR", "task_type": "single_function",
           "description": "Write a function that: Sum the elements of an array.",
           "description_v03": "Write a function that: Sum the elements of an array.",
           "input_types_v03": ["@i64"], "output_type_v03": "i64",
           "domain_context_v03": "",
           "test_cases": [{"inputs": [[1, 2, 3]], "expected": 6},
                          {"inputs": [[]], "expected": 0},
                          {"inputs": [[-5, 10, -2]], "expected": 3}]}

ORIG = ("m=harness;\ni=io:std.io;\ni=s:std.str;\n"
        "f=sum(xs:@i64):i64{\n  let t=mut.0;\n  lp(let i=0;i<xs.len;i=i+1){t=t+xs.get(i)};\n  <t\n};\n")
# same behaviour, harness imports dropped (what --fix does), different loop var
SAME = ("m=harness;\n"
        "f=sum(xs:@i64):i64{\n  let t=mut.0;\n  lp(let k=0;k<xs.len;k=k+1){t=t+xs.get(k)};\n  <t\n};\n")
# off by one per element: identical on the empty array, diverges on the first spec case
DIVERGENT = ("m=harness;\ni=io:std.io;\ni=s:std.str;\n"
             "f=sum(xs:@i64):i64{\n  let t=mut.0;\n  lp(let i=0;i<xs.len;i=i+1){t=t+xs.get(i)+1};\n  <t\n};\n")
A_TEST = {"base": "T-SUM-0001", "python_ref": "def arr_sum(xs):\n    return sum(xs)\n",
          "input_types": ["@i64"], "output_type": "i64"}


# ------------------------------------------------------------ generated ---
def test_generate_inputs_deterministic_and_typed():
    spec = {"task_id": "X-1", "input_types_v03": ["@i64", "str", "bool", "f64"]}
    a, why = dc.generate_inputs(spec, "X-1")
    b, _ = dc.generate_inputs(spec, "X-1")
    assert why is None and a == b and len(a) == dc.N_GENERATED
    assert all(isinstance(t[0], list) and isinstance(t[1], str) and isinstance(t[2], bool)
               and isinstance(t[3], float) for t in a)
    edge = a[dc.N_PLAIN:]
    assert any(t[0] == [] for t in edge) or any(len(t[0]) == 1 for t in edge)
    c, _ = dc.generate_inputs(spec, "X-2")
    assert c != a                                  # seed is per task id


def test_generate_inputs_unsupported_type():
    inputs, why = dc.generate_inputs({"input_types_v03": ["@@i64"]}, "X")
    assert inputs is None and "unsupported" in why
    inputs, why = dc.generate_inputs({"input_types_v03": []}, "X")
    assert inputs is None and why == "no input types"


def test_split_marked_attributes_lines_and_truncation():
    outs = dc.split_marked(b"#0\n1\n#1\n#2\na\nb\n", 4)
    assert outs == [["1"], [], ["a", "b"], None]


def test_lines_equal_float_tolerance():
    assert dc.lines_equal(["1.0000001", " x "], ["1", "x"])
    assert not dc.lines_equal(["a"], ["b"])
    assert not dc.lines_equal(["a"], ["a", "b"])


def test_render_ref_lines():
    assert dc.render_ref_lines(True) == ["1"]
    assert dc.render_ref_lines(2.0) == ["2"]
    assert dc.render_ref_lines([1, "x", False]) == ["1", "x", "0"]
    assert dc.render_ref_lines("a\nb") == ["a", "b"]
    assert dc.render_ref_lines(None) is None
    assert dc.render_ref_lines([[1]]) is None


def test_ref_signature_ok():
    assert dc.ref_signature_ok(SF_SPEC, None) == "no python_ref"
    assert dc.ref_signature_ok(SF_SPEC, A_TEST) is None
    bad = dict(A_TEST, input_types=["str"])
    assert "signature" in dc.ref_signature_ok(SF_SPEC, bad)
    assert "return type" in dc.ref_signature_ok(dict(SF_SPEC, output_type_v03="!i64"), A_TEST)


def test_ref_check_match_mismatch_shared():
    ref = "def f(a):\n    return a + 1\n"
    ins = [[1], [2]]
    assert dc._ref_check(ref, ins, [["2"], ["3"]], [["2"], ["3"]])["status"] == "match"
    r = dc._ref_check(ref, ins, [["2"], ["3"]], [["2"], ["4"]])
    assert r["status"] == "mismatch" and r["first_mismatch"] == 1 and r["first_mismatch_ref"] == "3"
    assert dc._ref_check(ref, ins, [["2"], ["4"]], [["2"], ["4"]])["status"] == "mismatch_shared"
    assert dc._ref_check("def f(a):\n    raise ValueError\n", ins, [["2"]], [["2"]])["status"] == "skipped"
    assert "error" in dc.run_python_ref("this is not python", ins)


def test_ensure_imports():
    src = 'm=harness;\nf=x(a:i64):i64{<a};\nf=main():i64{io.println("\\(x(1))");<0};\n'
    out = dc.ensure_imports(src)
    assert out.startswith("m=harness;\ni=io:std.io;\n") and "i=s:std.str;" not in out
    assert dc.ensure_imports(out) == out                       # idempotent
    stub = "m=harness;\nf=split(v0:str;v1:str):@str{<s.split(v0;v1)};\n"
    assert "i=s:std.str;" in dc.ensure_imports(stub)
    untouched = "m=harness;\ni=io:std.io;\nf=x(xs:@i64):i64{<xs.len as i64};\n"
    assert dc.ensure_imports(untouched) == untouched            # `xs.len` is not `s.`


# --------------------------------------------------------- compare mocked ---
def _run(stdout=b"", exit_code=0, timed_out=False):
    return {"stdout": stdout, "stderr": b"", "exit": exit_code, "wall_ms": 1.0, "rss_kb": 100.0,
            "timed_out": timed_out}


def _prog(tag, test_bin="bin", gen_bin="gbin", n_gen=2, mode="driver", reason=None, gen_reason=None):
    p = dc.Program(tag)
    p.mode, p.test_bin, p.gen_bin, p.n_gen, p.reason, p.gen_reason = mode, test_bin, gen_bin, n_gen, reason, gen_reason
    return p


def _mock_runs(monkeypatch, spec_out, gen_out):
    monkeypatch.setattr(dc, "run_spec_cases",
                        lambda prog, wd, timeout=dc.RUN_TIMEOUT: [("spec cases", _run(spec_out[prog.tag]))])
    monkeypatch.setattr(dc, "run_measured",
                        lambda binary, stdin_text="", timeout=dc.RUN_TIMEOUT, cwd=None, env=None:
                        _run(gen_out[binary]))


def test_compare_identical(monkeypatch):
    _mock_runs(monkeypatch, {"orig": b"6\n", "cand": b"6\n"}, {"go": b"#0\n1\n#1\n2\n", "gc": b"#0\n1\n#1\n2\n"})
    r = dc.compare(_prog("orig", gen_bin="go"), _prog("cand", gen_bin="gc"), SF_SPEC, "t", "/tmp",
                   gen_inputs=[[[1]], [[2]]], a_test=None)
    assert r["verdict"] == "identical"
    assert r["checks"]["spec_cases"]["identical"] and r["checks"]["generated"]["reached"] == 2
    assert r["checks"]["ref"]["status"] == "skipped"


def test_compare_spec_divergence(monkeypatch):
    _mock_runs(monkeypatch, {"orig": b"6\n", "cand": b"7\n"}, {})
    r = dc.compare(_prog("orig"), _prog("cand"), SF_SPEC, "t", "/tmp")
    assert r["verdict"] == "diverged" and r["first_diff"]["stage"] == "spec_cases"
    assert r["first_diff"]["orig_stdout"] == "6\n" and r["first_diff"]["cand_stdout"] == "7\n"


def test_compare_generated_divergence_names_input(monkeypatch):
    _mock_runs(monkeypatch, {"orig": b"6\n", "cand": b"6\n"}, {"go": b"#0\n1\n#1\n2\n", "gc": b"#0\n1\n#1\n3\n"})
    r = dc.compare(_prog("orig", gen_bin="go"), _prog("cand", gen_bin="gc"), SF_SPEC, "t", "/tmp",
                   gen_inputs=[[[1]], [[2]]])
    assert r["verdict"] == "diverged" and r["first_diff"]["input_index"] == 1
    assert r["first_diff"]["input"] == [[2]] and r["first_diff"]["cand_stdout"] == "3"


def test_compare_ref_mismatch_only_on_candidate(monkeypatch):
    _mock_runs(monkeypatch, {"orig": b"6\n", "cand": b"6\n"}, {"go": b"#0\n1\n#1\n2\n", "gc": b"#0\n1\n#1\n5\n"})
    # the generated run is byte-identical? no: cand differs -> diverged before ref
    r = dc.compare(_prog("orig", gen_bin="go"), _prog("cand", gen_bin="gc"), SF_SPEC, "t", "/tmp",
                   gen_inputs=[[[1]], [[2]]], a_test=A_TEST)
    assert r["verdict"] == "diverged" and r["first_diff"]["stage"] == "generated"
    # identical outputs that both disagree with the ref -> mismatch_shared, still identical
    _mock_runs(monkeypatch, {"orig": b"6\n", "cand": b"6\n"}, {"go": b"#0\n9\n#1\n9\n", "gc": b"#0\n9\n#1\n9\n"})
    r = dc.compare(_prog("orig", gen_bin="go"), _prog("cand", gen_bin="gc"), SF_SPEC, "t", "/tmp",
                   gen_inputs=[[[1]], [[2]]], a_test=A_TEST)
    assert r["verdict"] == "identical" and r["checks"]["ref"]["status"] == "mismatch_shared"


def test_compare_unverifiable_and_candidate_not_executable(monkeypatch):
    _mock_runs(monkeypatch, {}, {})
    po = _prog("orig", test_bin=None, gen_bin=None, reason="no spec test cases", gen_reason="no generated inputs")
    pc = _prog("cand", test_bin=None, gen_bin=None, reason="no spec test cases", gen_reason="no generated inputs")
    r = dc.compare(po, pc, SF_SPEC, "t", "/tmp")
    assert r["verdict"] == "unverifiable" and "no spec test cases" in r["reason"]
    r = dc.compare(_prog("orig", gen_bin=None), _prog("cand", test_bin=None, gen_bin=None, reason="build failed: x"),
                   SF_SPEC, "t", "/tmp")
    assert r["verdict"] == "diverged" and r["first_diff"]["stage"] == "build"


def test_compare_both_timeout_is_unverifiable(monkeypatch):
    monkeypatch.setattr(dc, "run_spec_cases",
                        lambda prog, wd, timeout=dc.RUN_TIMEOUT: [("spec cases", _run(b"", -9, True))])
    r = dc.compare(_prog("orig", gen_bin=None), _prog("cand", gen_bin=None), SF_SPEC, "t", "/tmp")
    assert r["verdict"] == "unverifiable" and "timeout" in r["reason"]


def test_differential_entrypoint_maps_verdict(monkeypatch):
    for verdict, ident in (("identical", True), ("diverged", False), ("unverifiable", None)):
        monkeypatch.setattr(dc, "diff_check", lambda *a, **k: {"verdict": verdict, "reason": "r", "checks": {}})
        out = dc.differential(SF_SPEC, "a", "b", "/tmp")
        assert out["identical"] is ident and out["verdict"] == verdict


# ---------------------------------------------------------- real tkc e2e ---
@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_end_to_end_identical_and_divergent():
    with tempfile.TemporaryDirectory(prefix="dc_e2e_") as td:
        r = dc.diff_check(ORIG, SAME, SF_SPEC, SF_SPEC["task_id"], td, A_TEST)
        assert r["verdict"] == "identical", json.dumps(r, indent=1)
        assert r["tkc_bin_sha"] == tkc_pin.bin_sha(dc.TKC) and len(r["tkc_bin_sha"]) == 64   # 131.39
        assert r["checks"]["spec_cases"]["identical"] and r["checks"]["spec_cases"]["mode"] == "driver"
        gen = r["checks"]["generated"]
        assert gen["identical"] and gen["reached"] == dc.N_GENERATED and gen["cand_exit"] == 0
        assert r["checks"]["ref"]["status"] == "match" and r["checks"]["ref"]["mismatched"] == 0
        r2 = dc.diff_check(ORIG, DIVERGENT, SF_SPEC, SF_SPEC["task_id"], td, A_TEST)
        assert r2["verdict"] == "diverged" and r2["first_diff"]["stage"] == "spec_cases"
        assert r2["first_diff"]["orig_stdout"].splitlines()[0] == "6"
        assert r2["first_diff"]["cand_stdout"].splitlines()[0] == "9"
        assert not os.listdir(td)                       # every artefact removed
