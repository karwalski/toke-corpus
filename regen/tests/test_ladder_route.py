"""131.107 — `nested-if-ladder` is a pattern rule: judge weight, sweep mapping,
deterministic routing, and the "nested if/el blocks" style-mandate exemption.

Owner decision 2026-11-15 (register 4.2): map the rule to catalogue
`cond-elif-chain` form b, add its fix to DETERMINISTIC_RULES, judge weight 0.02
(the hint band, as single-use-let). Before this story the rule was absent from
idiom_judge.PATTERN_RULES, so the sweep filed every hit under `other_lint` and
routed none of them (Z91, regen/freeze/pattern_sweep_131.64.json).
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import idiom_judge as ij  # noqa: E402
import pattern_sweep as ps  # noqa: E402

RULE = "nested-if-ladder"
LADDER_MANDATE = "use parameter names first and second. Use nested if/el blocks for the control flow."
LADDER_SRC = "m=t;\nf=g(a:i64):i64{\n  if(a>1){<1}el{if(a>0){<2}el{<3}};\n  <0\n}\n"
HAVE_TKC = os.path.exists(ij.TKC)


def _diag(severity="hint", fix=True):
    d = {"rule": RULE, "code": RULE, "severity": severity, "message": "else block holds only another if",
         "span": {"start": 34, "end": 55}, "pos": {"line": 3}}
    if fix:
        d["fix"] = {"span": {"start": 34, "end": 55}, "replacement": "el if(a>0){<2}el{<3}"}
    return d


# ------------------------------------------------------------- mapping ---
def test_rule_is_a_pattern_rule_with_the_decided_weight():
    assert ij.PATTERN_RULES[RULE] == 0.02
    assert RULE not in ij.HARD_GATE_RULES            # a hint: costs score, never gates


def test_judge_charges_the_ladder_and_the_hint_passes_the_gate():
    s, notes = ij.score("m=t;", diags=[_diag()])
    assert s == pytest.approx(0.98) and notes == [f"{RULE}×1 (-0.02)"]
    s, _ = ij.score("m=t;", diags=[_diag()] * 5)     # capped at PER_RULE_CAP
    assert s == pytest.approx(1 - 0.02 * ij.PER_RULE_CAP)
    assert ij.hard_gate([_diag()]) == []


def test_sweep_maps_to_cond_elif_chain_b_and_is_deterministic():
    assert ps.RULE_PATTERNS[RULE] == [("cond-elif-chain", "b")]
    assert ps.choose_pattern(RULE, _diag(), LADDER_SRC) == ("cond-elif-chain", "b")
    assert RULE in ps.DETERMINISTIC_RULES
    assert RULE in ps.RULES


def test_fixed_ladder_routes_auto_and_fixless_ladder_does_not():
    det = {"rule": RULE, "severity": "hint", "deterministic": True, "fix": True, "saving": 1,
           "pattern_id": "cond-elif-chain"}
    assert ps.classify([det], [], 1, False, {"agent_saving": 3})[0] == "AUTO"
    nofix = dict(det, deterministic=False, fix=False)
    assert ps.classify([nofix], [], 1, False, {"agent_saving": 3})[0] == "LEAVE"


# ----------------------------------------------------------- exemption ---
def test_nested_if_mandate_exempts_only_the_ladder():
    assert ij.mandate_exempt_rules(LADDER_MANDATE) == (RULE,)
    assert RULE not in ij.mandate_exempt_rules(
        "use parameter names p and q. Accumulate the result in a mutable binding.")
    assert RULE not in ij.mandate_exempt_rules(None)


def test_exempt_only_ladder_row_is_exempt_bucket():
    ex = [{"rule": RULE, "reason": "style_mandate"}]
    assert ps.classify([], ex, 0, False, {"agent_saving": 3})[0] == "EXEMPT"


# ------------------------------------------------- end to end on tkc ---
def _sweep(tmp_path, mandate):
    cat = {"by_id": {"cond-elif-chain": {"verdict": {"canonical": "a", "hot_path": None},
                                         "candidates": [{"form": "a", "tokens": {"proxy8k": 10}},
                                                        {"form": "b", "tokens": {"proxy8k": 11}}]}},
           "perf_sensitive": {}}
    ps._W.clear()
    ps._W.update({"cat": cat, "opts": {"tmpdir": str(tmp_path), "preform_exempt": True,
                                       "concat_fix_armed": False, "thresholds": {"agent_saving": 3}}})
    rec = tmp_path / "rec.json"
    rec.write_text(json.dumps({"tk_source": LADDER_SRC}))
    spec = {"task_type": "single_function",
            "description": "Write g." + (f" Variant 4: {mandate}" if mandate else "")}
    meta = {"category": "D-CFG", "task_type": "single_function", "source": "regen"}
    prior = {"source_sha256": ps._sha_text(LADDER_SRC), "proxy_tokens": 20, "min_bytes": 40}
    return ps.sweep_one(("D-CFG-0000v1", str(rec), meta, spec, prior))


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_sweep_one_routes_a_real_ladder_hit(tmp_path):
    row = _sweep(tmp_path, None)
    assert "error" not in row, row.get("error")
    v = [x for x in row["violations"] if x["rule"] == RULE]
    assert len(v) == 1 and v[0]["pattern_id"] == "cond-elif-chain" and v[0]["flagged_form"] == "b"
    assert v[0]["deterministic"] and v[0]["saving"] == 1
    assert not [o for o in row["other_lint"] if o["rule"] == RULE]
    assert row["bucket"] == "AUTO"
    assert row["idiom_score_new"] == pytest.approx(0.98)


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_sweep_one_exempts_a_ladder_the_task_asks_for(tmp_path):
    row = _sweep(tmp_path, "Use nested if/el blocks for the control flow.")
    assert "error" not in row, row.get("error")
    assert not row["violations"]
    assert [e["reason"] for e in row["exemptions"] if e["rule"] == RULE] == ["style_mandate"]
    assert row["bucket"] == "EXEMPT"
