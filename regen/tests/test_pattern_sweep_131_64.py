"""131.64: the nested-if-ladder counterfactual in pattern_sweep_131_64.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pattern_sweep_131_64 as m   # noqa: E402

CAT = {"by_id": {"cond-elif-chain": {"verdict": {"canonical": "a"},
                                     "candidates": [{"form": "a", "tokens": {"proxy8k": 21}},
                                                    {"form": "b", "tokens": {"proxy8k": 22}}]}},
       "perf_sensitive": {}}
TH = {"agent_saving": 3}
LADDER = {"rule": "nested-if-ladder", "severity": "hint", "span": None, "fix": True}


def _row(**kw):
    r = {"task_id": "D-X-1v1", "source": "regen", "category": "D-X", "task_type": "single_function",
         "bucket": "LEAVE", "violations": [], "exemptions": [], "other_lint": [], "perf_sensitive": False}
    r.update(kw)
    return r


def test_no_ladder_keeps_bucket():
    assert m.counterfactual(_row(bucket="AGENT"), CAT, TH) == ("AGENT", 0)


def test_fixable_ladder_alone_is_auto():
    assert m.counterfactual(_row(other_lint=[LADDER]), CAT, TH) == ("AUTO", 1)


def test_fixless_ladders_below_saving_stay_leave():
    lad = dict(LADDER, fix=False)
    assert m.counterfactual(_row(other_lint=[lad, lad]), CAT, TH) == ("LEAVE", 2)


def test_fixless_ladders_reaching_saving_go_agent():
    lad = dict(LADDER, fix=False)
    assert m.counterfactual(_row(other_lint=[lad] * 3), CAT, TH) == ("AGENT", 3)


def test_regen_class_stays_regen():
    row = _row(category="A-ARR", task_type="full_program", bucket="REGEN", other_lint=[LADDER])
    assert m.counterfactual(row, CAT, TH) == ("REGEN", 1)


def test_other_rules_are_ignored():
    row = _row(other_lint=[{"rule": "unused-let", "severity": "warning", "fix": False}])
    assert m.counterfactual(row, CAT, TH) == ("LEAVE", 0)
