"""131.85: the writer, the quality gate and main.py report a VACUOUS holdout check.

All three compared corpus task_ids against holdout_task_ids.txt (bare integers
and HumanEval/N), which cannot match a corpus id, so their holdout block could
never fire and read as a pass. They now go through scripts/id_disjoint.py: a
real overlap still blocks, and a comparison that could not have matched is
reported as VACUOUS. Which list is compared is unchanged (owner doc §4.12).
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from id_disjoint import FAIL, PASS, VACUOUS, HoldoutGuard, check_disjoint

from store.tests.test_store import SCHEMA_PATH, _make_entry
from store.writer import CorpusWriter
from validate.quality import QualityScorer
from validate.tests.test_validate import _make_compile_result, _make_diff_result

# The shape of the real holdout file: bare integers and HumanEval/N.
BENCH_SHAPED = {"974", "12", "HumanEval/7"}
GOOD_TOKE = "m=t;\nf=add(a:i64;b:i64):i64{<a+b};"


@pytest.mark.parametrize("tid", ["A-MTH-0001", "A-MTH-0002", "974", "HumanEval/8", "x"])
@pytest.mark.parametrize("holdout", [BENCH_SHAPED, {"A-MTH-0001"}, {"A-MTH-0001", "12"}])
def test_guard_status_equals_check_disjoint(tid: str, holdout: set[str]) -> None:
    guard = HoldoutGuard("h", holdout)
    assert guard.status(tid) == check_disjoint("h", {tid}, holdout)["status"]


def test_guard_counts_and_warns_once_per_shape() -> None:
    guard = HoldoutGuard("h", BENCH_SHAPED)
    assert [guard.status(t) for t in ("A-MTH-0001", "A-ARR-0002", "13", "974")] == [
        VACUOUS, VACUOUS, PASS, FAIL]
    assert guard.counts == {VACUOUS: 2, PASS: 1, FAIL: 1}
    assert guard.first_vacuous("A-MTH-0001") is True
    assert guard.first_vacuous("A-ARR-0002") is False     # same shape A-A-9
    assert "cannot match by construction" in guard.vacuous_reason("A-MTH-0001")


def _score(scorer: QualityScorer, tid: str):
    return scorer.score(tid, GOOD_TOKE, "def add(a, b):\n    return a + b\n" * 3,
                        _make_compile_result(success=True), _make_diff_result())


def test_quality_real_overlap_still_rejects() -> None:
    q = _score(QualityScorer(holdout_task_ids={"A-MTH-0001"}), "A-MTH-0001")
    assert q.accepted is False
    assert q.holdout_status == FAIL
    assert any("holdout" in r for r in q.reasons)


def test_quality_vacuous_is_reported_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    scorer = QualityScorer(holdout_task_ids=set(BENCH_SHAPED))
    with caplog.at_level(logging.WARNING, logger="validate.quality"):
        first = _score(scorer, "A-MTH-0001")
        _score(scorer, "A-MTH-0002")
    assert first.holdout_status == VACUOUS
    assert first.accepted is True               # VACUOUS warns; it does not block
    warnings = [r for r in caplog.records if "VACUOUS" in r.getMessage()]
    assert len(warnings) == 1                   # once per id shape
    assert scorer.holdout_guard.counts[VACUOUS] == 2


def test_quality_comparable_non_member_passes() -> None:
    q = _score(QualityScorer(holdout_task_ids={"A-MTH-0009"}), "A-MTH-0001")
    assert q.holdout_status == PASS


def test_writer_real_overlap_still_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        w = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH,
                         holdout_task_ids={"A-MTH-0001"})
        with pytest.raises(ValueError, match="HOLDOUT VIOLATION"):
            w.write(_make_entry(task_id="A-MTH-0001"))
        assert w.holdout_guard.counts[FAIL] == 1


def test_writer_vacuous_writes_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        w = CorpusWriter(corpus_dir=tmpdir, schema_path=SCHEMA_PATH,
                         holdout_task_ids=set(BENCH_SHAPED))
        with caplog.at_level(logging.WARNING, logger="store.writer"):
            path = w.write(_make_entry(task_id="A-MTH-0001"))
        assert os.path.isfile(path)
        assert w.holdout_guard.counts[VACUOUS] == 1
        assert any("VACUOUS" in r.getMessage() for r in caplog.records)


async def _dry_run(tmpdir: str, holdout_ids: list[str]) -> None:
    import yaml

    from tests.test_integration import _write_config, _write_prompts
    from main import parse_args, run_pipeline

    _write_prompts(tmpdir)
    cfg_path = _write_config(tmpdir, total_tasks=10)
    cfg = yaml.safe_load(open(cfg_path))
    cfg["holdout"] = {"task_ids": holdout_ids}
    with open(cfg_path, "w") as fh:
        yaml.dump(cfg, fh)
    args = parse_args(["--config", cfg_path, "--dry-run", "--skip-trial"])
    with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}):
        await run_pipeline(args)


def _first_curriculum_id() -> str:
    from generator.curriculum import CurriculumGenerator
    return CurriculumGenerator(seed=42, total_tasks=10).generate()[0].task_id


@pytest.mark.asyncio
async def test_main_vacuous_holdout_is_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        with caplog.at_level(logging.INFO, logger="main"):
            await _dry_run(tmpdir, sorted(BENCH_SHAPED))
    msgs = [r for r in caplog.records if "curriculum_vs_holdout" in r.getMessage()]
    assert len(msgs) == 1
    assert msgs[0].levelno == logging.WARNING
    assert "VACUOUS" in msgs[0].getMessage()


@pytest.mark.asyncio
async def test_main_comparable_holdout_passes(caplog: pytest.LogCaptureFixture) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        with caplog.at_level(logging.INFO, logger="main"):
            await _dry_run(tmpdir, ["HOLDOUT-TEST-001"])
    msgs = [r.getMessage() for r in caplog.records if "curriculum_vs_holdout" in r.getMessage()]
    assert len(msgs) == 1 and msgs[0].startswith("Holdout isolation: PASS")


@pytest.mark.asyncio
async def test_main_real_overlap_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(SystemExit) as exc:
            await _dry_run(tmpdir, [_first_curriculum_id()])
    assert exc.value.code == 1
