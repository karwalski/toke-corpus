"""131.39 Part B -- the pattern scripts (sweep / autofix / prep / check / bank /
diff_check via pattern_common) pin the compiler once and stamp `tkc_bin_sha`.

Fake binaries are tiny shell scripts so these run without a compiler; the
wave / autofix / diff_check end-to-end tests assert the stamp on real output.
"""
import hashlib, json, os, stat, subprocess, sys, types
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
sys.path.insert(0, REGEN)
import tkc_pin                      # noqa: E402
import pattern_common as pc         # noqa: E402
import validate                     # noqa: E402
import metrics                      # noqa: E402
import idiom_judge                  # noqa: E402
import run_shard                    # noqa: E402
import audit                        # noqa: E402
import diff_check                   # noqa: E402
import pattern_autofix as pa        # noqa: E402
import manifest_tool                # noqa: E402

MODS = (pc, validate, metrics, idiom_judge, run_shard, audit, diff_check, pa)


def _fake(dirp: Path, name: str, text: str) -> Path:
    p = dirp / name
    p.write_text(f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo '{text}'; exit 0; fi\necho '{text}'\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def fake_tkc(tmp_path, monkeypatch):
    monkeypatch.delenv(tkc_pin.ENV_PIN, raising=False)
    real = _fake(tmp_path, "toke", "fake 1")
    link = tmp_path / "tkc"
    link.symlink_to("toke")
    monkeypatch.setenv("TKC", str(link))
    for m in MODS:                                   # restore every module's TKC after the test
        monkeypatch.setattr(m, "TKC", m.TKC)
    return tmp_path, link, real


def test_tkc_stamp_carries_bin_sha(fake_tkc):
    _, link, real = fake_tkc
    full = _sha(real)
    st = pc.tkc_stamp(str(link), toke_root=str(link.parent))
    assert st["tkc_bin_sha"] == full and st["tkc_sha256"] == full     # kept beside each other
    assert st["tkc_sha"] == full[:12] and st["tkc_version"] == "fake 1" and st["toke_git"] is None
    assert pc.tkc_bin_sha(str(link)) == full
    missing = pc.tkc_stamp(str(link.parent / "nope"))
    assert missing["tkc_bin_sha"] is None and missing["tkc_sha"] is None


def test_pin_tkc_rebinds_every_module_and_survives_a_relink(fake_tkc):
    repo, link, real = fake_tkc
    extra = types.ModuleType("extra_harness")
    extra.TKC = "unbound"
    with pc.pin_tkc(extra) as p:
        assert p.owned and p.sha256 == _sha(real) and p.version == "fake 1"
        assert os.environ[tkc_pin.ENV_PIN] == p.path
        for m in MODS + (extra,):
            assert m.TKC == p.path, m
        assert pc.Paths(corpus=str(repo)).tkc == p.path          # resolved at construction
        assert pc.Paths(corpus=str(repo), tkc="/explicit").tkc == "/explicit"
        assert pc.tkc_bin_sha() == p.sha256
        # `make` relinks the symlink mid-run: the copy, its sha and the stamp do not move
        v2 = _fake(repo, "toke.new", "fake 2")
        link.unlink()
        link.symlink_to("toke.new")
        real.unlink()
        assert _sha(link) == _sha(v2) != p.sha256
        assert pc.tkc_bin_sha() == p.sha256 and _sha(p.path) == p.sha256
        assert pc.tkc_stamp(pc.TKC)["tkc_bin_sha"] == p.sha256
        r = subprocess.run([pc.TKC, "--version"], capture_output=True, text=True)
        assert r.stdout.strip() == "fake 1"
        # a child process (a pool worker) binds to the same pinned copy via $TOKE_TKC_PIN
        code = (f"import sys; sys.path.insert(0, {REGEN!r}); import tkc_pin, diff_check; "
                "print(tkc_pin.default_tkc()); print(diff_check.TKC)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        assert out.stdout.split() == [p.path, p.path], out.stderr
        # a nested pin() reuses it (owned=False) rather than copying again
        child = tkc_pin.pin()
        assert child.path == p.path and not child.owned and child.sha256 == p.sha256
        child.close()
        assert os.path.exists(p.path)
    assert tkc_pin.ENV_PIN not in os.environ and not os.path.exists(p.path)


def test_autofix_tool_shas_summary_and_bank_one_stamp(fake_tkc, tmp_path, monkeypatch):
    _, link, real = fake_tkc
    full = _sha(real)
    monkeypatch.setattr(pa, "TKC", str(link))
    shas = pa.tool_shas()
    assert len(shas) == 4 and shas[3] == full and shas[0] == full[:12]     # git-ish short sha kept beside
    assert pa._bin_sha_of(shas) == full
    assert pa._bin_sha_of(("t", "c", "g")) == full                          # pre-131.39 3-tuple: live binary
    res = [{"task_id": "a", "decision": "noop", "reason": "harness_prefix_only", "changed": False,
            "task_type": "full_program", "elapsed_s": 0.1}]
    s = pa.summarise(res, {"shas": shas, "perf_runs": 1, "n_generated": 1})
    assert s["tkc_bin_sha"] == full and s["tkc_sha"] == full[:12]
    # bank_one: rewrite131 carries the pinned sha beside tkc_sha
    corpus = str(tmp_path / "corpus")
    rec_path = os.path.join(corpus, "D-TST", "D-TST-0001v1.json")
    os.makedirs(os.path.dirname(rec_path))
    src = "m=t;\ni=s:std.str;\nf=main():i64{<0};\n"
    with open(rec_path, "w") as f:
        json.dump({"id": "P3-D-TST-0001v1", "version": 2, "task_id": "D-TST-0001v1", "tk_source": src,
                   "tk_tokens": 50, "judge": {"accepted": True, "score": 0.9},
                   "regen": {"category": "D-TST", "task_type": "full_program", "min_bytes": 100,
                             "proxy_tokens": 50}}, f)
    mpath = os.path.join(corpus, "MANIFEST.jsonl")
    manifest_tool.Manifest(mpath).stamp("D-TST-0001v1", rec_path)
    cand = tmp_path / "cand.tk"
    cand.write_text("m=t;\nf=main():i64{<0};\n")
    r = {"task_id": "D-TST-0001v1", "category": "D-TST", "decision": "bank", "reason": "fixed:unused-import",
         "prev_sha256": pa.sha256_file(rec_path), "candidate_path": str(cand),
         "fix": {"rules_with_fix": ["unused-import"]}, "min_bytes": {"before": 100, "after": 90},
         "proxy_tokens": {"before": 50, "after": 45}, "perf": {"ratio": 1.0, "verdict": "pass"},
         "diff": {"verdict": "identical"}}
    entry = pa.bank_one(r, corpus, manifest_tool.Manifest(mpath), os.path.join(corpus, "ledger", "rw.jsonl"),
                        ("tkcsha", "catsha", "gitsha", "binsha"), now="2026-09-19T00:00:00Z")
    assert entry["status"] == "rewritten"
    rw = json.load(open(rec_path))["regen"]["rewrite131"]
    assert rw["tkc_sha"] == "tkcsha" and rw["tkc_bin_sha"] == "binsha" and rw["toke_git"] == "gitsha"


def test_tkc_pin_twin_is_untouched():
    twin = Path(os.path.expanduser("~/tk/toke/scripts/patterns/tkc_pin.py"))
    if not twin.exists():
        pytest.skip("toke checkout absent")
    assert twin.read_bytes() == Path(REGEN, "tkc_pin.py").read_bytes()
