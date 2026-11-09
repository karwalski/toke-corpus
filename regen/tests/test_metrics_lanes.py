"""131.50 -- metrics.py token lanes (o200k, superword8k, bytepatch).

The lanes are computed by toke's scripts/patterns/count_tokens.py, imported
through metrics.PATTERNS_DIR exactly as the proxy is; nothing here re-implements
a lane.  These tests pin the plumbing with a stand-in count_tokens module (no
tokenizer, no artefact, no number), plus one optional check against the real
toke module when the checkout at TOKE_PATTERNS_DIR already has the lanes."""
import importlib.util
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import metrics

TEMSPEC_KEYS = {"metric_type", "tokenizer", "toke_trained", "baseline", "N"}


def _fake_count_tokens(with_lanes=True):
    """A count_tokens stand-in with the 131.50 surface metrics relies on."""
    m = types.ModuleType("count_tokens")
    seen = {}

    class Counter:
        def __init__(self, proxy=None, external=True, lanes=False):
            seen.update(proxy=proxy, external=external, lanes=lanes)
            self.lanes = lanes

        def measure_snippet(self, text):
            r = {"tokens": {"proxy8k": 7}, "unavailable": {
                "superword8k": "SKIP superword8k: no artefact", "v03": "not a lane"}}
            if self.lanes:
                r["lanes"] = {"o200k": 5, "superword8k": None, "bytepatch": 3}
                r["lane_artefacts"] = {"superword8k": None, "bytepatch": "bytepatch-x.json"}
            return r

    m.Counter = Counter
    m.seen = seen
    if with_lanes:
        m.LANES = ("o200k", "superword8k", "bytepatch")
        names = {"o200k": ("o200k_base", False), "superword8k": ("superword8k", True),
                 "bytepatch": ("bytepatch", True)}

        def temspec_fields(column, n, baseline=None):
            return {"metric_type": "same-tokenizer token count", "tokenizer": names[column][0],
                    "toke_trained": names[column][1],
                    "baseline": baseline or "none (absolute count; no comparison made)", "N": n}
        m.temspec_fields = temspec_fields
    return m


@pytest.fixture
def fresh(monkeypatch):
    """Reset metrics' lazy counters so each test loads its own module."""
    for name in ("_lane_counter", "_lane_err", "_counter", "_counter_err"):
        monkeypatch.setattr(metrics, name, None)
    return monkeypatch


def test_lane_tokens_reuses_toke_counter_with_temspec(fresh) -> None:
    fake = _fake_count_tokens()
    fresh.setitem(sys.modules, "count_tokens", fake)
    got = metrics.lane_tokens("m=p;f=main():i64{<0};")
    assert fake.seen == {"proxy": metrics.PROXY_PATH, "external": False, "lanes": True}
    assert got["lanes"] == {"o200k": 5, "superword8k": None, "bytepatch": 3}
    assert set(got["temspec"]) == {"o200k", "superword8k", "bytepatch"}
    for lane, t in got["temspec"].items():
        assert set(t) == TEMSPEC_KEYS, lane
        assert t["N"] == 1
    assert got["temspec"]["superword8k"]["toke_trained"] is True
    assert got["temspec"]["o200k"]["toke_trained"] is False
    # only lane reasons are carried; a missing lane says why
    assert got["unavailable"] == {"superword8k": "SKIP superword8k: no artefact"}
    assert got["artefacts"]["bytepatch"] == "bytepatch-x.json"
    assert "error" not in got


def test_lane_tokens_none_for_no_min_text(fresh) -> None:
    fresh.setitem(sys.modules, "count_tokens", _fake_count_tokens())
    assert metrics.lane_tokens(None) is None


def test_checkout_without_lanes_says_so(fresh) -> None:
    fresh.setitem(sys.modules, "count_tokens", _fake_count_tokens(with_lanes=False))
    got = metrics.lane_tokens("m=p;")
    assert got["lanes"] is None and got["temspec"] is None
    assert "predates the 131.50 lanes" in got["error"]


def test_analyse_lanes_are_opt_in(fresh) -> None:
    fresh.setitem(sys.modules, "count_tokens", _fake_count_tokens())
    fresh.setattr(metrics, "dump_ast", lambda p, tkc=None: {"kind": "MODULE", "children": []})
    fresh.setattr(metrics, "min_form", lambda p, tkc=None: "m=p;")
    fresh.setattr(metrics, "lint", lambda p, src=None, tkc=None: [])
    plain = metrics.analyse("x.tk")
    assert "lanes" not in plain                   # default record shape unchanged
    with_lanes = metrics.analyse("x.tk", lanes=True)
    assert with_lanes["lanes"]["lanes"]["bytepatch"] == 3
    assert {k: v for k, v in with_lanes.items() if k != "lanes"} == plain


def _real_has_lanes() -> bool:
    p = os.path.join(metrics.PATTERNS_DIR, "count_tokens.py")
    try:
        return "LANES" in open(p, encoding="utf-8").read()
    except OSError:
        return False


@pytest.mark.skipif(not _real_has_lanes() or importlib.util.find_spec("tokenizers") is None,
                    reason="toke checkout at TOKE_PATTERNS_DIR predates 131.50, or no tokenizers")
def test_real_toke_lanes_carry_temspec(fresh) -> None:
    fresh.delitem(sys.modules, "count_tokens", raising=False)
    got = metrics.lane_tokens("m=p;f=main():i64{<0};")
    if got.get("error"):
        pytest.skip(f"toke lane code not loadable here: {got['error']}")
    assert set(got["lanes"]) == {"o200k", "superword8k", "bytepatch"}
    for lane, n in got["lanes"].items():
        assert set(got["temspec"][lane]) == TEMSPEC_KEYS
        assert isinstance(n, int) or lane in got["unavailable"], lane
