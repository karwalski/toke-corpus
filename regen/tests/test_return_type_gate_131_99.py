"""131.99 (register 4.3 (a)): the return-type gate is an exact match plus an
allowlist of i32->i64 and u32->u64/i64 only; mismatches route through the
131.99 route_merge patch, which is in the 4.1 correctness set."""
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import regen_validate as rv
import return_type_gate_131_99 as rt
import route_merge
import size_gate_waiver as sgw


def _spec(ret, params="a:i64"):
    return {"task_id": "A-MTH-9001v1", "category": "A-MTH", "task_type": "single_function",
            "description": f"Write a function f=calc({params}):{ret} that computes it."}


def _src(ret, params="a:i64", body="<a"):
    return f"m=harness;\ni=io:std.io;\nf=calc({params}):{ret}{{{body}}};\n"


def test_allowlist_is_exactly_the_owner_answer():
    assert rv.RETURN_TYPE_ALLOWLIST == {"i32": {"i64"}, "u32": {"u64", "i64"}}
    assert "f32" not in rv.RETURN_TYPE_ALLOWLIST


@pytest.mark.parametrize("want,got", [("i32", "i64"), ("u32", "u64"), ("u32", "i64"),
                                      ("i64", "i64"), ("@(i64)", "@i64"), ("i64!$e", "i64!E")])
def test_exact_or_allowlisted_passes(want, got):
    ok, detail, soft, decl = rv.return_type_conforms(_spec(want), _src(got))
    assert ok is True and soft is None and decl == got, detail


@pytest.mark.parametrize("want,got", [("u64", "i64"), ("f64", "str"), ("f32", "f64"),
                                      ("i64", "i32"), ("i64", "u64"), ("@(i32)", "@(i64)"),
                                      ("@(u64)", "@(i64)"), ("@(f64)", "@(str)"),
                                      ("@($str:i64)", "@(str)"), ("i32!$e", "i64!$e")])
def test_every_other_difference_is_a_hard_reject(want, got):
    ok, detail, soft, decl = rv.return_type_conforms(_spec(want), _src(got))
    assert ok is False and soft is None and decl == got
    assert detail == f"calc declared `{got}`, spec `{want}` (131.99: exact match)"
    # the pre-131.99 gate only flagged it
    lok, ldetail, lsoft, _ = rt.legacy_return_type_conforms(_spec(want), _src(got))
    assert lok is True and ldetail == "ok (soft mismatch)" and lsoft


def test_131_42_hard_cases_are_unchanged():
    ok, detail, _, _ = rv.return_type_conforms(_spec("i64!$parseerr"), _src("str"))
    assert ok is False and detail.endswith("requires an error union")
    assert rt.legacy_return_type_conforms(_spec("i64!$parseerr"), _src("str"))[:2] == (ok, detail)


def test_99_joins_the_correctness_set_and_is_replayed_last():
    assert "131.99" in sgw.CORRECTNESS_SET["regen"]
    assert "131.99" not in sgw.CORRECTNESS_SET["library"]
    assert route_merge.PATCHES[-1] == ("131.99", route_merge.NEW_SWEEP)
    assert sgw.waiver_stories({"source": "regen", "patches": ["131.43", "131.99"]}) == ("131.43", "131.99")


def _row(tid, bucket, **kw):
    r = {"task_id": tid, "story": "131.13", "source": "regen", "category": "A-ARR",
         "task_type": "single_function", "bucket": bucket, "violations": [], "pattern_ids": []}
    r.update(kw)
    return r


def test_route_rows_send_every_flip_to_agent_and_keep_earlier_routing():
    flips = [{"task_id": t, "category": "A-ARR", "task_type": "single_function",
              "spec": "u64", "declared": "i64"} for t in ("a", "b", "c", "d")]
    cur = {"a": _row("a", "AGENT", story="131.43", bucket_reason="131.43: drift", patches=["131.43"]),
           "b": _row("b", "LEAVE"),
           "c": _row("c", "REGEN", task_type="full_program"),      # REGEN class: stays
           "d": _row("d", "REGEN")}                                 # outside the class: AGENT (131.109)
    out, dest = rt.route_rows(flips, cur, ts=1)
    by = {r["task_id"]: r for r in out}
    assert [by[t]["bucket"] for t in "abcd"] == ["AGENT", "AGENT", "REGEN", "AGENT"]
    assert by["a"]["bucket_reason"].startswith("131.43: drift | 131.99: return-type drift")
    assert all(r["story"] == "131.99" and r["violations"][-1]["rule"] == rt.RULE for r in out)
    assert "`i64` but the spec says `u64`" in by["b"]["bucket_reason"]
    assert dest == {"AGENT->AGENT": 1, "LEAVE->AGENT": 1, "REGEN->REGEN": 1, "REGEN->AGENT": 1}


def _have_tkc():
    tkc = rv.TKC
    return bool(tkc) and (os.path.exists(tkc) or shutil.which(tkc))


@pytest.mark.skipif(not _have_tkc(), reason="tkc not built")
def test_allowlist_is_justified_by_identical_stdout(tmp_path):
    j = rt.justify(str(tmp_path))
    pairs = {(e["spec"], e["declared"]): e for e in j["entries"]}
    assert set(pairs) == {("i32", "i64"), ("u32", "u64"), ("u32", "i64")}
    for e in pairs.values():
        assert e["verdict"] == "identical" and e["spec_cases"] is True, e
        assert e["generated"]["n"] == 20 and e["generated"]["identical"] is True, e
    # a narrowing is not identical: the differential can tell
    assert j["negative_control"]["verdict"] == "diverged" and j["negative_control_diverged"]
