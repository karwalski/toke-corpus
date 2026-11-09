"""131.92 (c): the committed pre131 manifest claims no disjointness nobody checked."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import restamp_tokenizer_disjointness as rs

MANIFEST = ROOT / "data" / "tokenizer_manifest_pre131.json"
REPORT = ROOT / "docs" / "tokenizer_data_report_pre131.md"
KEYS = ("disjoint_benchmark", "disjoint_baseline", "family_disjoint_train")


def _man(tmp_path: Path) -> tuple[dict, Path, Path]:
    bench = tmp_path / "bench.txt"
    bench.write_text("# benchmark\n1\n2\nHumanEval/0\n")
    base = tmp_path / "base.txt"
    base.write_text("A-MTH-0002v1\n")
    recs = [{"task_id": "A-MTH-0001v1", "source": "regen", "base": "A-MTH-0001", "split": "holdout"},
            {"task_id": "A-MTH-0002v1", "source": "regen", "base": "A-MTH-0002", "split": "train"},
            {"task_id": "std.io", "source": "stdlib", "base": "std.io", "split": "train"}]
    man = {"schema": "x", "holdout": {"families_holdout": 1, "benchmark_ids_n": 3, "baseline_ids_n": 1,
                                      **{k: True for k in KEYS}}, "records": recs}
    return man, bench, base


def test_restamp_replaces_asserted_booleans_with_statuses(tmp_path):
    man, bench, base = _man(tmp_path)
    out = rs.restamp(man, bench, base)
    h = out["holdout"]
    assert h["disjoint_benchmark"] == "VACUOUS"          # ints / HumanEval/N vs corpus ids
    assert h["disjoint_baseline"] == "PASS" and h["family_disjoint_train"] == "PASS"
    assert h["disjointness_checks"][0]["check"] == "holdout_records_vs_benchmark"
    assert out["disjointness_restamp"]["prior"] == {k: True for k in KEYS}
    assert list(out)[:3] == ["schema", "holdout", "disjointness_restamp"]
    again = rs.restamp(out, bench, base)                  # idempotent: prior is the ORIGINAL claim
    assert rs.dump(again) == rs.dump(out)


def test_restamp_refuses_changed_id_files(tmp_path):
    man, bench, base = _man(tmp_path)
    bench.write_text("1\n")
    with pytest.raises(SystemExit):
        rs.restamp(man, bench, base)


def test_committed_manifest_carries_no_boolean_disjointness_claim():
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    h = man["holdout"]
    assert all(isinstance(h[k], str) for k in KEYS), {k: h[k] for k in KEYS}
    assert h["disjoint_benchmark"] == "VACUOUS"
    assert man["disjointness_restamp"]["story"] == "131.92"
    line = next(x for x in REPORT.read_text(encoding="utf-8").splitlines() if x.startswith(rs.ASSERTED_PREFIX))
    assert "= True" not in line and "VACUOUS" in line


BASELINE = Path.home() / "tk" / "toke-tokenizer" / "data" / "baseline_sample_ids_v04.txt"


@pytest.mark.skipif(not BASELINE.exists(), reason="toke-tokenizer baseline id list not checked out")
def test_committed_manifest_is_the_reproducible_restamp():
    rc = rs.main([str(MANIFEST), "--report", str(REPORT), "--check",
                  "--benchmark-ids", str(ROOT / "data" / "holdout_task_ids.txt"), "--baseline-ids", str(BASELINE)])
    assert rc == 0
