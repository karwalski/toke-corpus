"""131.39 -- tkc_pin: a pinned copy survives the `make` symlink swap.

Fake binaries are tiny shell scripts so the tests run without a compiler; the
one integration test pins the real tkc and is skipped when it is absent.
"""
import hashlib, os, stat, subprocess, sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import tkc_pin  # noqa: E402

# TKC_PIN_TWIN lets a branch be checked against an unmerged toke-side twin.
TWIN = Path(os.environ.get("TKC_PIN_TWIN")
            or os.path.expanduser("~/tk/toke/scripts/patterns/tkc_pin.py"))


def _fake(dirp: Path, name: str, text: str) -> Path:
    p = dirp / name
    p.write_text(f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo '{text}'; exit 0; fi\necho '{text}'\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    monkeypatch.delenv(tkc_pin.ENV_PIN, raising=False)
    monkeypatch.delenv("TKC", raising=False)
    v1 = _fake(tmp_path, "toke", "fake 1")
    link = tmp_path / "tkc"
    link.symlink_to("toke")
    return tmp_path, link, v1


def test_pin_survives_symlink_swap(fake_repo):
    repo, link, v1 = fake_repo
    p = tkc_pin.pin(toke_repo=repo, tkc=link)
    try:
        assert p.owned and p.pin_dir and os.path.isfile(p.path)
        assert p.resolved == str(v1.resolve())
        assert p.sha256 == _sha(v1)
        assert p.version == "fake 1"
        assert p.argv0 == p.path
        assert p.toke_head is None            # tmp_path is not a git repo
        # `make` relinks: new binary, symlink now points elsewhere, old file gone
        v2 = _fake(repo, "toke.new", "fake 2")
        link.unlink()
        link.symlink_to("toke.new")
        v1.unlink()
        assert _sha(link) == _sha(v2) != p.sha256
        r = subprocess.run([p.argv0, "--version"], capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout.strip() == "fake 1"
        assert _sha(p.path) == p.sha256
        assert tkc_pin.bin_sha(p.path) == p.sha256
    finally:
        p.close()
    assert not os.path.exists(p.pin_dir)


def test_context_manager_cleans_up(fake_repo):
    repo, link, _ = fake_repo
    with tkc_pin.pin(toke_repo=repo, tkc=link) as p:
        d = p.pin_dir
        assert os.path.isdir(d)
    assert not os.path.exists(d)


def test_env_reuse_across_processes(fake_repo, monkeypatch):
    repo, link, v1 = fake_repo
    parent = tkc_pin.pin(toke_repo=repo, tkc=link)
    try:
        parent.install()
        assert os.environ[tkc_pin.ENV_PIN] == parent.path
        assert tkc_pin.default_tkc() == parent.path
        child = tkc_pin.pin(toke_repo=repo, tkc=link)      # what a pool worker does
        assert not child.owned and child.pin_dir is None
        assert child.path == parent.path and child.sha256 == parent.sha256
        child.close()
        assert os.path.isfile(parent.path)                # non-owner never deletes
        # a real child process sees the same copy
        code = ("import sys; sys.path.insert(0, %r); import tkc_pin; p = tkc_pin.pin(toke_repo=%r); "
                "print(p.path, p.owned)" % (os.path.dirname(HERE), str(repo)))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**os.environ, tkc_pin.ENV_PIN: parent.path})
        assert r.returncode == 0, r.stderr
        assert r.stdout.split() == [parent.path, "False"]
    finally:
        parent.close()
    assert tkc_pin.ENV_PIN not in os.environ


def test_install_rebinds_modules_preserving_type(fake_repo):
    import types
    repo, link, _ = fake_repo
    m_str = types.SimpleNamespace(TKC="/x/tkc")
    m_path = types.SimpleNamespace(TKC=Path("/x/tkc"))
    with tkc_pin.pin(toke_repo=repo, tkc=link) as p:
        p.install(m_str, m_path)
        assert m_str.TKC == p.path and isinstance(m_str.TKC, str)
        assert m_path.TKC == Path(p.path) and isinstance(m_path.TKC, Path)


def test_stamp_keys(fake_repo):
    repo, link, _ = fake_repo
    with tkc_pin.pin(toke_repo=repo, tkc=link) as p:
        s = p.stamp()
        assert set(s) == {"tkc_bin_sha", "tkc_version", "tkc_path", "tkc_resolved",
                          "tkc_pinned_copy", "toke_head", "toke_src_dirty",
                          "tkc_git_sha", "tkc_git_dirty"}
        assert s["tkc_bin_sha"] == p.sha256 and s["tkc_path"] == str(link)
        assert s["toke_src_dirty"] is False


def _fake_json(dirp: Path, name: str, payload: str) -> Path:
    """A fake tkc answering `--version --json` with `payload` (131.90)."""
    p = dirp / name
    p.write_text("#!/bin/sh\n"
                 f"if [ \"$1\" = --version ] && [ \"$2\" = --json ]; then echo '{payload}'; exit 0; fi\n"
                 "if [ \"$1\" = --version ]; then echo 'fake json'; exit 0; fi\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(tkc_pin.ENV_PIN, raising=False)
    monkeypatch.delenv("TKC", raising=False)


CLEAN = '{"name":"toke","version":"3.1.0","git_sha":"8bb9f63e6e64","git_dirty":false}'
DIRTY = '{"name":"toke","version":"3.1.0","git_sha":"8bb9f63e6e64","git_dirty":true}'


def test_build_info_reads_version_json(tmp_path, no_env):
    clean = _fake_json(tmp_path, "clean", CLEAN)
    assert tkc_pin.build_info(clean) == ("8bb9f63e6e64", False)
    with tkc_pin.pin(toke_repo=tmp_path, tkc=clean) as p:
        assert p.tkc_git_sha == "8bb9f63e6e64" and p.tkc_git_dirty is False
        assert p.stamp()["tkc_git_sha"] == "8bb9f63e6e64"
        assert p.require_clean() is p


def test_build_info_unknown_for_old_binary(fake_repo):
    # negative control: a binary without --version --json reports nothing
    repo, link, _ = fake_repo
    assert tkc_pin.build_info(link) == (None, None)
    assert tkc_pin.build_info(repo / "missing") == (None, None)


def test_require_clean_refuses_dirty_and_cleans_up(tmp_path, no_env, monkeypatch):
    dirty = _fake_json(tmp_path, "dirty", DIRTY)
    made = []
    real = tkc_pin.tempfile.mkdtemp
    monkeypatch.setattr(tkc_pin.tempfile, "mkdtemp", lambda **k: made.append(real(**k)) or made[-1])
    with pytest.raises(tkc_pin.DirtyPinError, match="dirty"):
        tkc_pin.pin(toke_repo=tmp_path, tkc=dirty, require_clean=True)
    assert made and not os.path.exists(made[0])
    # without require_clean the dirty pin is allowed (dry runs) and says so
    with tkc_pin.pin(toke_repo=tmp_path, tkc=dirty) as p:
        assert p.tkc_git_dirty is True
        with pytest.raises(tkc_pin.DirtyPinError):
            p.require_clean()


def test_require_clean_refuses_unknown(fake_repo):
    repo, link, _ = fake_repo
    with pytest.raises(tkc_pin.DirtyPinError, match="does not report"):
        tkc_pin.pin(toke_repo=repo, tkc=link, require_clean=True)


def test_require_clean_on_env_reuse(tmp_path, no_env, monkeypatch):
    dirty = _fake_json(tmp_path, "dirty", DIRTY)
    monkeypatch.setenv(tkc_pin.ENV_PIN, str(dirty))
    with pytest.raises(tkc_pin.DirtyPinError):
        tkc_pin.pin(toke_repo=tmp_path, tkc=dirty, require_clean=True)


def test_cli_require_clean(tmp_path, no_env):
    dirty = _fake_json(tmp_path, "dirty", DIRTY)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "tkc_pin.py"),
                        "--tkc", str(dirty), "--toke-repo", str(tmp_path), "--require-clean"],
                       capture_output=True, text=True)
    assert r.returncode == 3 and "dirty" in r.stderr


def test_missing_binary_raises(tmp_path, monkeypatch):
    monkeypatch.delenv(tkc_pin.ENV_PIN, raising=False)
    with pytest.raises(FileNotFoundError):
        tkc_pin.pin(toke_repo=tmp_path, tkc=tmp_path / "nope")


def test_cli_keep(fake_repo):
    import json, shutil
    repo, link, _ = fake_repo
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "tkc_pin.py"),
                        "--tkc", str(link), "--toke-repo", str(repo), "--keep"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    st = json.loads(r.stdout)
    try:
        assert os.path.isfile(st["tkc_pinned_copy"]) and st["tkc_bin_sha"] == _sha(link)
    finally:
        shutil.rmtree(st["pin_dir"], ignore_errors=True)


@pytest.mark.skipif(not TWIN.exists(), reason="toke repo twin not checked out")
def test_twin_is_identical():
    here = Path(os.path.dirname(HERE)) / "tkc_pin.py"
    assert here.read_bytes() == TWIN.read_bytes(), "regen/tkc_pin.py and toke/scripts/patterns/tkc_pin.py differ"


@pytest.mark.skipif(not tkc_pin.DEFAULT_TKC.exists(), reason="real tkc not built")
def test_real_tkc_pins(monkeypatch):
    monkeypatch.delenv(tkc_pin.ENV_PIN, raising=False)
    with tkc_pin.pin() as p:
        assert p.version and p.version.startswith("toke")
        assert len(p.sha256) == 64 and p.toke_head
        assert p.tkc_git_sha and p.tkc_git_dirty in (True, False)   # 131.90
        r = subprocess.run([p.argv0, "--version"], capture_output=True, text=True)
        assert r.returncode == 0
