"""131.22a: the holdout disjointness check reports VACUOUS, not PASS, when it cannot fail."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from id_disjoint import FAIL, PASS, VACUOUS, check_disjoint, id_shape, main


def test_shape_classes() -> None:
    assert id_shape("A-MTH-0016v40") == "A-A-9a9"
    assert id_shape("A-MTH-0016") == "A-A-9"
    assert id_shape("HumanEval/7") == "AaAa/9"
    assert id_shape("974") == "9"


def test_integers_and_humaneval_vs_corpus_ids_is_vacuous() -> None:
    # the exact shape of the pre-131.22a defect: 974 ints + HumanEval/N vs corpus record ids
    bench = {str(i) for i in range(1, 975)} | {f"HumanEval/{i}" for i in range(164)}
    hold = {"A-MTH-0016v40", "A-ARR-0001v68", "L-CLI-HTTP-0001v1-a1b2"}
    r = check_disjoint("records_vs_bench", hold, bench)
    assert r["status"] == VACUOUS
    assert r["shared_shapes"] == []


def test_empty_operand_is_vacuous_not_pass() -> None:
    # a missing --benchmark-ids file used to read as an empty set and still stamp `true`
    assert check_disjoint("x", {"A-MTH-0016v40"}, set())["status"] == VACUOUS


def test_like_for_like_disjoint_is_pass() -> None:
    r = check_disjoint("x", {"A-MTH-0016v40", "A-ARR-0002v3"}, {"A-MTH-0017v1", "HumanEval/1"})
    assert r["status"] == PASS
    assert r["left_comparable"] == 2 and r["right_comparable"] == 1


def test_overlap_is_fail() -> None:
    r = check_disjoint("x", {"A-MTH-0016v40"}, {"A-MTH-0016v40", "A-MTH-0001v1"})
    assert r["status"] == FAIL and r["overlap_examples"] == ["A-MTH-0016v40"]


def test_cli_exit_codes(tmp_path: Path) -> None:
    ints = tmp_path / "ints.txt"
    ints.write_text("# bench\n1\n2\nHumanEval/0\n")
    hold = tmp_path / "hold.txt"
    hold.write_text("A-MTH-0016v40\n")
    assert main([str(hold), str(ints)]) == 0            # vacuous is reported, not fatal ...
    assert main([str(hold), str(ints), "--strict"]) == 1  # ... unless --strict
    assert main([str(hold), str(hold)]) == 1


def test_curate_holdout_disjointness_statuses() -> None:
    curate = pytest.importorskip("curate_tokenizer_set")
    checks = {r["check"]: r["status"] for r in curate.holdout_disjointness(
        hold_ids={"A-MTH-0016v40"}, hold_bases={"A-MTH-0016"}, train_bases={"A-MTH-0001"},
        bench_ids={"1", "HumanEval/0"}, baseline_ids={"A-ARR-0002v3"})}
    assert checks == {
        "holdout_records_vs_benchmark": VACUOUS,
        "holdout_families_vs_benchmark": VACUOUS,
        "holdout_records_vs_baseline": PASS,
        "holdout_families_vs_train_families": PASS,
    }
    assert curate.worst_status(*[{"status": s} for s in (PASS, VACUOUS)]) == VACUOUS
    assert curate.worst_status(*[{"status": s} for s in (VACUOUS, FAIL)]) == FAIL
