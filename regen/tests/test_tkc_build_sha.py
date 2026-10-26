"""131.90 (1) -- provenance names the commit the tkc binary reports for itself
(`tkc --version --json` -> git_sha, git_dirty; 131.33), not `git rev-parse`
of the toke checkout, and a banked run refuses a dirty pin.

Fake binaries are shell scripts; the negative control puts the toke "repo" at
a real git commit that differs from the binary's git_sha and checks the stamp
does not pick up the HEAD.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REGEN = os.path.dirname(HERE)
sys.path.insert(0, REGEN)
import tkc_pin                      # noqa: E402
import pattern_common as pc         # noqa: E402
import library_common as lc         # noqa: E402
import pattern_autofix as pa        # noqa: E402
import ingest_library as il         # noqa: E402

BIN_SHA = "8bb9f63e6e64"


def _fake(dirp: Path, name: str, dirty: bool | None) -> Path:
    if dirty is None:
        payload = "toke 3.1.0"                      # pre-131.33 binary: no JSON
    else:
        payload = ('{"name":"toke","version":"3.1.0","git_sha":"%s","git_dirty":%s}'
                   % (BIN_SHA, "true" if dirty else "false"))
    p = dirp / name
    p.write_text("#!/bin/sh\n"
                 f"if [ \"$1\" = --version ] && [ \"$2\" = --json ]; then echo '{payload}'; exit 0; fi\n"
                 "if [ \"$1\" = --version ]; then echo 'toke 3.1.0'; exit 0; fi\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


@pytest.fixture
def toke_repo(tmp_path, monkeypatch):
    """A git repo whose HEAD is NOT the binary's git_sha (negative control)."""
    monkeypatch.delenv(tkc_pin.ENV_PIN, raising=False)
    monkeypatch.delenv("TKC", raising=False)
    repo = tmp_path / "toke"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env=env)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()
    assert not head.startswith(BIN_SHA)
    return repo, head


@pytest.mark.parametrize("mod", [pc, lc])
def test_tkc_stamp_uses_binary_sha_not_head(mod, toke_repo):
    repo, head = toke_repo
    tkc = _fake(repo, "tkc", dirty=False)
    st = mod.tkc_stamp(str(tkc), toke_root=str(repo))
    assert st["toke_git"] == BIN_SHA and st["toke_git"] != head
    assert st["tkc_git_dirty"] is False


@pytest.mark.parametrize("mod", [pc, lc])
def test_tkc_stamp_old_binary_reports_unknown(mod, toke_repo):
    repo, _ = toke_repo
    tkc = _fake(repo, "tkc", dirty=None)
    st = mod.tkc_stamp(str(tkc), toke_root=str(repo))
    assert st["toke_git"] is None and st["tkc_git_dirty"] is None


def test_autofix_tool_shas_uses_binary_sha(toke_repo, monkeypatch):
    repo, head = toke_repo
    tkc = _fake(repo, "tkc", dirty=False)
    monkeypatch.setattr(pa, "TKC", str(tkc))
    monkeypatch.setattr(pa, "TOKE_REPO", str(repo))
    assert pa.tool_shas()[2] == BIN_SHA


def test_ingest_library_tool_shas_uses_binary_sha(toke_repo, monkeypatch):
    repo, _ = toke_repo
    monkeypatch.setattr(il, "TOKE_REPO", str(repo))
    assert il.tool_shas(str(_fake(repo, "tkc", dirty=False)))[0] == BIN_SHA
    assert il.tool_shas(str(_fake(repo, "old", dirty=None)))[0] == "unknown"


@pytest.mark.parametrize("mod", [pc, lc])
def test_pin_tkc_require_clean(mod, toke_repo, monkeypatch):
    repo, _ = toke_repo
    # pin_tkc() rebinds TKC in every sibling module; keep this test from
    # leaking a deleted pinned copy into later tests
    monkeypatch.setattr(tkc_pin.Pinned, "install", lambda self, *mods: self)
    monkeypatch.setenv("TKC", str(_fake(repo, "dirty", dirty=True)))
    with pytest.raises(tkc_pin.DirtyPinError):
        mod.pin_tkc(require_clean=True)
    with mod.pin_tkc() as p:                          # dry runs may use a dirty binary
        assert p.tkc_git_dirty is True
    monkeypatch.setenv("TKC", str(_fake(repo, "clean", dirty=False)))
    with mod.pin_tkc(require_clean=True) as p:
        assert p.tkc_git_sha == BIN_SHA


def test_no_regen_tool_derives_tkc_sha_from_rev_parse():
    """The four provenance sites named by 131.90 no longer call rev-parse."""
    for name in ("ingest_library.py", "pattern_autofix.py", "pattern_common.py",
                 "library_common.py", "metrics.py"):
        src = Path(REGEN, name).read_text()
        assert '"rev-parse"' not in src, name
