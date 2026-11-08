"""131.106: banked records that fail `tkc --check` are routed to a wave that
fixes them, and the gate that keeps it so. 131.105: the three RT003->RT002
records are routed to the AGENT wave."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_routing_131_106 as cr
import route_131_105 as r105
import route_merge as rm
import tkc_pin

E = [{"code": "E4031", "line": 7, "msg": "type mismatch: cannot index into 'str'; expected array type"}]


def _chk(tid, rc, source="regen", banked=True, errors=None, **kw):
    r = {"task_id": tid, "source": source, "category": "D-X", "task_type": "single_function",
         "banked_accepted": banked, "rc": rc, "errors": (E if rc else []) if errors is None else errors}
    r.update(kw)
    return r


def _cur(tid, bucket, story="131.13"):
    return {"task_id": tid, "story": story, "bucket": bucket, "violations": [], "source": "regen"}


def test_decide_regen_class_only_goes_regen():
    a_full = _chk("A-ARR-1v1", 1, category="A-ARR", task_type="full_program")
    assert cr.decide(a_full) == "REGEN"
    assert cr.decide(dict(a_full, task_type="single_function")) == "AGENT"
    assert cr.decide(_chk("MIG-P2-B-D-CFG-1", 1, category="D-CFG", task_type="migrate_fix")) == "AGENT"
    assert cr.decide(_chk("D-CFG-1v1", 1, category="D-CFG", task_type="full_program")) == "AGENT"
    assert cr.decide(_chk("L-SYS-1", 1, source="library", category="L-SYS", task_type="stdin_program")) == "AGENT"


def test_failing_counts_only_banked_nonzero():
    rows = {"a": _chk("a", 1), "b": _chk("b", 0), "c": _chk("c", 1, banked=False), "d": _chk("d", None)}
    assert sorted(cr.failing(rows)) == ["a", "d"]


def test_route_rows_reason_carries_the_diagnostic_and_tkc():
    fail = {"a": _chk("a", 1), "b": _chk("b", 1, task_type="migrate_fix"),
            "c": _chk("c", 1, category="A-ARR", task_type="full_program")}
    cur = {"a": _cur("a", "LEAVE"), "b": _cur("b", "REGEN"), "c": _cur("c", "REGEN")}
    specs = {"a": {"test_cases": [{"input": "1"}]}, "b": {}, "c": {}}
    rows, dest = cr.route_rows(fail, cur, specs, "toke 3.1.0 (bin abc)")
    by = {r["task_id"]: r for r in rows}
    assert by["a"]["bucket"] == "AGENT" and by["b"]["bucket"] == "AGENT" and by["c"]["bucket"] == "REGEN"
    assert dest[("LEAVE", "AGENT")] == 1 and dest[("REGEN", "AGENT")] == 1 and dest[("REGEN", "REGEN")] == 1
    rsn = by["a"]["bucket_reason"]
    assert rsn.startswith("131.106: banked as accepted but does not compile on toke 3.1.0 (bin abc): E4031 line 7")
    assert "no test cases" not in rsn and "no test cases" in by["b"]["bucket_reason"]
    assert by["a"]["violations"][-1]["rule"] == cr.RULE and by["a"]["ts"] == cr.TS


def test_reason_truncates_after_three_errors():
    errs = [{"code": f"E40{i}", "line": i, "msg": "m"} for i in range(5)]
    assert "(+2 more)" in cr.reason_for(_chk("a", 1, errors=errs), "t")


def test_gate_flags_leave_exempt_auto_and_unrouted():
    rows = {t: _chk(t, 1) for t in ("l", "e", "u", "g", "r", "x")}
    rows["ok"] = _chk("ok", 0)
    rows["nb"] = _chk("nb", 1, banked=False)
    merged = {"l": _cur("l", "LEAVE"), "e": _cur("e", "EXEMPT"), "u": _cur("u", "AUTO"),
              "g": _cur("g", "AGENT"), "r": _cur("r", "REGEN"), "ok": _cur("ok", "LEAVE"),
              "nb": _cur("nb", "LEAVE")}
    bad = cr.gate(rows, merged)
    assert [(t, b) for t, b, _ in bad] == [("e", "EXEMPT"), ("l", "LEAVE"), ("u", "AUTO"), ("x", None)]
    assert bad[0][2] == ["E4031"]


def test_route_131_105_targets_the_three_records_with_the_archive_path():
    cur = {t: _cur(t, "LEAVE") for t in r105.RECORDS}
    rows = r105.route_rows(cur)
    assert [r["task_id"] for r in rows] == sorted(r105.RECORDS)
    for r in rows:
        assert r["bucket"] == "AGENT" and r["would_bucket"] == "LEAVE"
        assert f"audit/replaced/131/{r['task_id']}.tk" in r["bucket_reason"]
        assert "RT003" in r["bucket_reason"] and r["violations"][-1]["rule"] == r105.RULE


# ----------------------------------------------- the gate over real data ---
ROWS = cr.rows_path(rm.CORPUS)
MERGED = os.path.join(rm.CORPUS, "audit", "pattern_sweep.merged.jsonl")


@pytest.mark.skipif(not (os.path.exists(ROWS) and os.path.exists(MERGED)),
                    reason="on-disk corpus check rows (gitignored) not present")
def test_no_failing_banked_record_is_routed_leave_exempt_or_auto():
    """THE GATE. The stored --check rows must come from the tkc this suite
    runs against ($TKC / ~/tk/toke/tkc); when they do not, re-run
    `check_routing_131_106.py gate --live --tkc <tkc>` (a 25k-record check,
    too slow for this suite) — the test says so instead of passing on stale
    rows."""
    meta, rows = cr.read_check(ROWS)
    want = os.environ.get("TKC") or os.path.expanduser("~/tk/toke/tkc")
    have = (meta.get("tkc") or {}).get("tkc_bin_sha")
    cur = tkc_pin.bin_sha(want) if os.path.exists(want) else None
    if cur and have != cur:
        pytest.skip(f"check rows were made with tkc {str(have)[:12]}, the current tkc is {cur[:12]}: "
                    f"run regen/check_routing_131_106.py gate --live --tkc {want}")
    bad = cr.gate(rows, rm.read_rows(MERGED))
    assert not bad, f"{len(bad)} banked records fail --check and are routed LEAVE/EXEMPT/AUTO: {bad[:10]}"


@pytest.mark.skipif(not (os.path.exists(ROWS) and os.path.exists(MERGED)),
                    reason="on-disk corpus check rows (gitignored) not present")
def test_stored_rows_route_every_failure_regardless_of_tkc():
    """Compiler-independent half: whatever tkc made the stored rows, the
    merged routing must cover every failure they record."""
    _, rows = cr.read_check(ROWS)
    assert not cr.gate(rows, rm.read_rows(MERGED))
