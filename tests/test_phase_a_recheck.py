"""131.87 -- scripts/phase_a_recheck.py re-judges banked Phase A records with
the stricter differential test and only reads the corpus."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import phase_a_recheck as par  # noqa: E402

from validate.compiler import CompileResult, CompilerValidator  # noqa: E402
from validate.diff_test import (  # noqa: E402
    REASON_REFS_DISAGREE,
    REASON_TOKE_FAILED,
    DifferentialTester,
)

REFS = {"python_source": "py", "c_source": "c", "java_source": "java",
        "python_tokens": 1, "c_tokens": 1, "java_tokens": 1}


def _rec(rid: str, tk: str = "m=t;f=add(a:i64):i64{<a};", agreed=("c", "java", "python"),
         refs: dict | None = REFS, cat: str = "A-MTH") -> dict:
    r = {"id": rid, "task_id": rid, "category": cat, "tk_source": tk,
         "differential": {"languages_agreed": list(agreed), "majority_output": "1"}}
    if refs is not None:
        r["references"] = dict(refs)
    return r


def test_is_phase_a() -> None:
    assert par.is_phase_a(_rec("a"))
    assert not par.is_phase_a(_rec("b", agreed=()))            # regen_v04 shape
    assert not par.is_phase_a(_rec("c", refs=None))
    assert not par.is_phase_a(_rec("d", refs={**REFS, "java_source": ""}))


def test_has_main() -> None:
    assert par.has_main("m=t;\nf=main():i64{<0};")
    assert par.has_main("M=t;F=main():i64{<0};")
    assert not par.has_main("m=t;f=add(a:i64):i64{<a};")
    assert not par.has_main('m=t;f=g():str{<"f=domain("};')


def test_min_refs_for() -> None:
    assert par.min_refs_for(_rec("x", cat="A-ERR")) == 1
    assert par.min_refs_for(_rec("x", cat="A-MTH")) == 2


def _tester(outs: dict[str, tuple[bool, str]]) -> DifferentialTester:
    t = DifferentialTester(CompilerValidator(tkc_path="tkc"))

    def mk(lang: str):
        async def run(src: str, stdin: str = "") -> CompileResult:
            ok, out = outs[lang]
            return CompileResult(lang, ok, 0 if ok else 1, out, "", 1.0)
        return run
    for lang in ("python", "c", "java", "toke"):
        setattr(t.compiler, f"run_{lang}", mk(lang))
    return t


def test_full_mode_rejects_a_toke_program_that_cannot_run() -> None:
    t = _tester({"python": (True, "1"), "c": (True, "1"), "java": (True, "1"),
                 "toke": (False, "")})
    row = asyncio.run(par.check_one(t, _rec("a"), refs_only=False))
    assert row["passed"] is False and row["reason"] == REASON_TOKE_FAILED
    assert row["has_main"] is False


def test_refs_only_mode_flags_disagreement_and_stored_mismatch() -> None:
    t = _tester({"python": (True, "1"), "c": (True, "2"), "java": (True, "3"),
                 "toke": (True, "1")})
    row = asyncio.run(par.check_one(t, _rec("a"), refs_only=True))
    assert row["passed"] is False and row["reason"] == REASON_REFS_DISAGREE
    t = _tester({"python": (True, "2"), "c": (True, "2"), "java": (True, "3"),
                 "toke": (True, "2")})
    row = asyncio.run(par.check_one(t, _rec("b"), refs_only=True))
    assert row["passed"] is True and row["matches_stored_majority"] is False


def test_toke_shape_mode_reads_only(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    cat = tmp_path / "A-MTH"
    cat.mkdir()
    files = {
        "a.json": _rec("a"),
        "b.json": _rec("b", tk="m=t;f=main():i64{<0};"),
        "c.json": _rec("c", agreed=()),
    }
    for name, rec in files.items():
        (cat / name).write_text(json.dumps(rec))
    before = {p.name: p.read_bytes() for p in cat.iterdir()}
    assert par.main(["--corpus", str(tmp_path), "--toke-shape"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["phase_a_population"] == 2 and s["no_main"] == 1
    assert {p.name: p.read_bytes() for p in cat.iterdir()} == before


def test_summarise() -> None:
    res = [{"passed": True, "reason": None, "category": "A-MTH"},
           {"passed": False, "reason": REASON_TOKE_FAILED, "category": "A-MTH"},
           {"passed": False, "reason": REASON_REFS_DISAGREE, "category": "A-SRT"}]
    s = par.summarise(res, 10, "full")
    assert s["checked"] == 3 and s["rejected"] == 2 and s["phase_a_population"] == 10
    assert s["reasons"] == {REASON_TOKE_FAILED: 1, REASON_REFS_DISAGREE: 1}


def test_strict_vs_canonical_disagreement() -> None:
    outs = {"python": "True", "c": "", "java": "true"}
    assert par.strict_disagrees(outs, 2) is True       # exact bytes: a tie
    t = _tester({"python": (True, "True"), "c": (True, "0"), "java": (True, "true"),
                 "toke": (True, "true")})
    row = asyncio.run(par.check_one(t, _rec("a"), refs_only=True))
    assert row["passed"] is True and row["languages_agreed"] == ["java", "python"]
    assert row["strict_disagrees"] is True
    assert row["outputs"]["c"] == "0"
