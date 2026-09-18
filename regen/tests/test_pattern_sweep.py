"""131.13 — pattern sweep: bucket protocol, rule -> pattern mapping, saving,
exemptions and the budget-factor search. Pure tests (no tkc): synthetic
violation rows and a synthetic catalogue.
"""
import os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pattern_sweep as ps  # noqa: E402

TH = {"agent_saving": 3}


def viol(rule, severity="warning", det=False, saving=0, pid="x"):
    return {"rule": rule, "severity": severity, "deterministic": det, "fix": det,
            "saving": saving, "pattern_id": pid}


CAT = {"by_id": {
    "cond-bind-if": {"verdict": {"canonical": "c", "hot_path": None},
                     "candidates": [{"form": "b", "tokens": {"proxy8k": 20}, "runtime_verdict": "tied"},
                                    {"form": "c", "tokens": {"proxy8k": 15}, "runtime_verdict": "tied"}]},
    "fn-chain-vs-let": {"verdict": {"canonical": "a", "hot_path": None},
                        "candidates": [{"form": "a", "tokens": {"proxy8k": 11}, "runtime_verdict": "best"},
                                       {"form": "b", "tokens": {"proxy8k": 11}, "runtime_verdict": "tied"}]},
    "blocked": {"verdict": {"canonical": "a"},
                "candidates": [{"form": "a", "tokens": {"proxy8k": 5}}, {"form": "b", "tokens": {"proxy8k": None}}]},
}}


# ------------------------------------------------------------- buckets ---
def test_leave_on_zero_hits():
    assert ps.classify([], [], 0, False, TH)[0] == "LEAVE"


def test_stub_prefix_exemption_is_not_a_hit():
    ex = [{"rule": "unused-import", "reason": "stub_prefix"}]
    assert ps.classify([], ex, 0, False, TH)[0] == "LEAVE"


def test_exempt_when_every_hit_exempt():
    ex = [{"rule": "mut-flag-if", "reason": "style_mandate"}]
    assert ps.classify([], ex, 0, False, TH)[0] == "EXEMPT"


def test_auto_when_all_deterministic_even_with_saving():
    v = [viol("single-use-let", "hint", det=True), viol("discarded-value-result", "error", det=True)]
    b, why, would = ps.classify(v, [], 10, False, TH)
    assert b == "AUTO" and would is None


def test_agent_on_gate_violation_without_fix():
    b, why, _ = ps.classify([viol("mut-flag-if", saving=0)], [], 0, False, TH)
    assert b == "AGENT" and "without fix" in why


def test_agent_on_saving_threshold_and_perf():
    hints = [viol("loop-rebuilds-array", "hint", saving=3)]
    assert ps.classify(hints, [], 3, False, TH)[0] == "AGENT"
    assert ps.classify(hints, [], 2, False, TH)[0] == "LEAVE"
    assert ps.classify(hints, [], 2, True, TH)[0] == "AGENT"


def test_mixed_deterministic_and_hint_is_not_auto():
    v = [viol("single-use-let", "hint", det=True), viol("single-use-let", "hint", det=False)]
    assert ps.classify(v, [], 0, False, TH)[0] == "LEAVE"


def test_regen_carve_out_keeps_would_bucket():
    b, why, would = ps.classify([viol("flag-soup")], [], 7, False, TH, regen_class=True)
    assert b == "REGEN" and would == "AGENT"


def test_library_gate_overrides():
    lib = {"gate_fail": ["structure"], "lint_all_fixable": True, "lint_nofix": []}
    assert ps.classify([], [], 0, False, TH, library=lib)[0] == "AGENT"
    lib = {"gate_fail": ["lint"], "lint_all_fixable": True, "lint_nofix": []}
    assert ps.classify([], [], 0, False, TH, library=lib)[0] == "AUTO"
    lib = {"gate_fail": ["lint"], "lint_all_fixable": False, "lint_nofix": ["unused-let"]}
    assert ps.classify([], [], 0, False, TH, library=lib)[0] == "AGENT"
    lib = {"gate_fail": [], "lint_all_fixable": True, "lint_nofix": []}
    assert ps.classify([viol("single-use-let", "hint", det=True)], [], 0, False, TH, library=lib)[0] == "AUTO"


# ------------------------------------------------------------- mapping ---
def test_choose_pattern_hand_maps():
    d = {"message": "loop only copies 'a' elements into 'b'", "span": {"start": 0}}
    assert ps.choose_pattern("loop-rebuilds-array", d, "") == ("iter-filter", "b")
    d = {"message": "loop only appends f(xs.get(i)) to 'out'", "span": {"start": 0}}
    assert ps.choose_pattern("loop-rebuilds-array", d, "") == ("iter-map", "b")
    assert ps.choose_pattern("single-use-let", {"message": ""}, "") == ("fn-chain-vs-let", "b")
    assert ps.choose_pattern("discarded-value-result", {"message": ""}, "") == (None, None)


def test_concat_inside_loop_maps_to_str_build_loop():
    src = ('m=d;i=s:std.str;f=g(xs:@str):str{let acc=mut."";'
           'lp(let i=0;i<xs.len;i=i+1){acc=acc.concat(",").concat(xs.get(i))};<acc};'
           'f=h(a:str;b:str):str{<s.concat(a;s.concat(",";b))};')
    in_loop = src.index("acc=acc.concat")
    outside = src.index("<s.concat")
    assert ps._inside_loop(src, in_loop)
    assert not ps._inside_loop(src, outside + 1)
    d = {"message": "concat nested 2 deep", "span": {"start": in_loop}}
    assert ps.choose_pattern("string-concat-chain", d, src) == ("str-build-loop", "a")
    d = {"message": "concat nested 2 deep", "span": {"start": outside + 1}}
    assert ps.choose_pattern("string-concat-chain", d, src) == ("str-interp-vs-join", "c")


def test_inside_loop_ignores_lp_in_strings():
    src = 'm=d;f=g():str{let t="lp(";<t.concat("a").concat("b")};'
    assert not ps._inside_loop(src, src.index("<t.concat") + 1)


def test_pattern_saving_floor_and_blocked():
    assert ps.pattern_saving(CAT, "cond-bind-if", "b") == 5
    assert ps.pattern_saving(CAT, "fn-chain-vs-let", "b") == 0
    assert ps.pattern_saving(CAT, "blocked", "b") is None
    assert ps.pattern_saving(CAT, "nope", "a") is None


def test_entry_perf_reason():
    assert ps.entry_perf_reason({"verdict": {"canonical": "a", "hot_path": "c"}, "candidates": []}) == "hot_path=c"
    e = {"verdict": {"canonical": "a"}, "candidates": [{"form": "a", "runtime_verdict": "tied"},
                                                       {"form": "b", "runtime_verdict": "best"}]}
    assert ps.entry_perf_reason(e)
    e = {"verdict": {"canonical": "a"}, "candidates": [{"form": "a", "runtime_verdict": "best"},
                                                       {"form": "b", "runtime_verdict": "slower"}]}
    assert ps.entry_perf_reason(e) is None
    assert ps.entry_perf_reason(CAT["by_id"]["cond-bind-if"]) is None


def test_rule_patterns_cover_every_rule():
    for ru in ps.RULES:
        assert ru in ps.RULE_PATTERNS


# ---------------------------------------------------------- exemptions ---
def test_preform_exempt_rules():
    spec = {"task_type": "migrate_fix",
            "legacy_source": "m=x;f=f(a:i64):i64{let g=mut.0;if(a>0){g=1}el{g=2};<g}"}
    assert ps.preform_exempt_rules(spec) == ("mut-flag-if",)
    assert ps.preform_exempt_rules({"task_type": "full_program", "legacy_source": spec["legacy_source"]}) == ()
    assert ps.preform_exempt_rules({"task_type": "migrate_fix"}) == ()


# -------------------------------------------------------------- budget ---
def test_budget_grid_exact_factors_and_choice():
    rows = [{"source": "regen", "category": "A", "task_type": "t", "proxy_tokens": v}
            for v in [10, 12, 15, 15, 16, 20, 30, 40, 60, 90]]
    grid, chosen, n = ps.budget_grid(rows, {"A/t": 15.0}, 0.10, 0.05, lo=1.0, hi=4.0)
    assert n == 10
    assert [g["factor"] for g in grid[:3]] == [1.0, 1.05, 1.1]
    g15 = next(g for g in grid if g["factor"] == 1.5)
    assert g15["over"] == 4                    # > round(22.5) = 22 -> 30, 40, 60, 90
    # smallest factor with <= 10% over: 60 > round(f*15) fails until f*15 >= 59.5 -> f = 4.0 -> 1 over (90)
    assert chosen == 4.0
    # 1.0 -> budget 15 -> 6 over (60%); 1.05 -> round(15.75) = 16 -> 5 over (50%)
    grid, chosen, _ = ps.budget_grid(rows, {"A/t": 15.0}, 0.5, 0.05)
    assert chosen == 1.05


def test_vec_handle_receiver_suspected_fp():
    src = "m=x;i=v:std.vec;f=main():i64{let eu=v.new();eu.push(1);let a=mut.@();a.push(2);<0};"
    d = {"message": "result discarded — write `eu=eu.push(...)` (a bare call never mutates)"}
    assert ps.vec_handle_receiver(d, src)
    d = {"message": "result discarded — write `a=a.push(...)` (a bare call never mutates)"}
    assert not ps.vec_handle_receiver(d, src)
    assert not ps.vec_handle_receiver({"message": "x"}, src)
