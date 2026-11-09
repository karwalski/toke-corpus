"""135.34 -- owner-excluded records never reach training prep (owner decision 4.5)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_training_data import EXCLUDED_RECORD_IDS, is_excluded  # noqa: E402

RETIRED_W1010 = "BIFI-missing_semicolon-21952-7371771c"


def _record(rid: str) -> dict:
    # Shaped like the phase2 BIFI records: passes every quality gate on its own.
    return {
        "id": rid, "task_id": "BIFI-missing_semicolon", "category": "BIFI-missing_semicolon",
        "tk_source": "m=entry;f=greet():$str{<\"hello\"};",
        "validation": {"compiler_exit_code": 0, "error_codes": []},
        "syntax_audit": {"phase2_conformant": True},
        "compile_check": {"passed": True},
        "references": {"broken_source": "m=entry\nf=greet(): $str { < \"hello\" };"},
    }


def test_retired_w1010_record_is_excluded() -> None:
    assert RETIRED_W1010 in EXCLUDED_RECORD_IDS
    assert is_excluded({"id": RETIRED_W1010})
    assert not is_excluded({"id": "BIFI-missing_semicolon-1-aaaaaaaa"})
    assert not is_excluded({})


def test_prepare_training_data_skips_it(tmp_path: Path) -> None:
    cat = tmp_path / "corpus" / "BIFI-missing_semicolon"
    cat.mkdir(parents=True)
    for rid in (RETIRED_W1010, "BIFI-missing_semicolon-1-aaaaaaaa"):
        (cat / f"{rid}.json").write_text(json.dumps(_record(rid)))
    (tmp_path / "q.jsonl").write_text("")
    (tmp_path / "c.jsonl").write_text("")
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "prepare_training_data.py"),
         "--corpus-dir", str(tmp_path / "corpus"), "--quality-scores", str(tmp_path / "q.jsonl"),
         "--complexity", str(tmp_path / "c.jsonl"), "--output-dir", str(out)],
        capture_output=True, text=True, check=True)
    assert "'owner_excluded': 1" in proc.stdout
    assert "Total entries loaded (post quality gate): 1" in proc.stdout
    for name in ("train.jsonl", "eval.jsonl"):
        assert RETIRED_W1010 not in (out / name).read_text()
