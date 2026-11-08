"""131.103: the corpus compiler pin (~/tk/archive/pins/tkc-131.103) is
self-contained, and toke-corpus names it, not the retired 131.73 pin."""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
sys.path.insert(0, REGEN)
import corpus_pin
import tkc_pin

needs_pin = pytest.mark.skipif(not corpus_pin.present(), reason="corpus pin not on this machine (local archive)")


def test_constants_are_consistent():
    assert len(corpus_pin.SHA256) == 64 and int(corpus_pin.SHA256, 16) >= 0
    assert corpus_pin.VERSION.endswith(f"(git {corpus_pin.GIT_SHA})")
    assert corpus_pin.TKC == os.path.join(corpus_pin.PIN_DIR, "tkc")
    assert corpus_pin.PIN_DIR.endswith("tkc-131.103") and corpus_pin.STORY == "131.103"


def test_default_tkc_prefers_env_then_pin(monkeypatch):
    monkeypatch.setenv("TKC", "/x/tkc")
    assert corpus_pin.default_tkc() == "/x/tkc"
    monkeypatch.delenv("TKC")
    monkeypatch.delenv(tkc_pin.ENV_PIN, raising=False)
    want = corpus_pin.TKC if corpus_pin.present() else tkc_pin.default_tkc()
    assert corpus_pin.default_tkc() == want


def test_verify_rejects_a_missing_binary(tmp_path):
    assert corpus_pin.verify(str(tmp_path / "nope")) == [f"{tmp_path / 'nope'}: missing"]


def test_no_live_reference_to_the_retired_pin():
    """Only corpus_pin.py (SUPERSEDES) and history may name tkc-131.73."""
    hits = subprocess.run(["git", "grep", "--untracked", "-l", "tkc-131.73", "--", "regen", ":!regen/tests"],
                          cwd=os.path.dirname(REGEN), capture_output=True, text=True, check=False).stdout.split()
    assert hits == ["regen/corpus_pin.py"]


@needs_pin
def test_pin_is_the_recorded_clean_binary():
    assert corpus_pin.verify() == []
    assert tkc_pin.pin(tkc=corpus_pin.TKC, reuse_env=False, require_clean=True).close() is None


@needs_pin
def test_pin_manifest_records_the_binary():
    with open(os.path.join(corpus_pin.PIN_DIR, "MANIFEST.md")) as f:
        m = f.read()
    assert corpus_pin.SHA256 in m and corpus_pin.GIT_SHA in m and "131.103" in m


@needs_pin
def test_pin_selftest_passes():
    """hello world + a std.str program build and run, `\\(true)` prints true,
    with TKC_STDLIB_DIR unset (the pin's own stdlib)."""
    env = {k: v for k, v in os.environ.items() if k != "TKC_STDLIB_DIR"}
    r = subprocess.run(["sh", os.path.join(corpus_pin.PIN_DIR, "selftest.sh")], capture_output=True,
                       text=True, env=env, timeout=300, check=False)
    assert r.returncode == 0 and "selftest: PASS" in r.stdout, r.stdout + r.stderr
