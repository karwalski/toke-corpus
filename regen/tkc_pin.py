#!/usr/bin/env python3
"""Pin the tkc compiler binary for the life of a harness run (story 131.39).

TWIN FILE: kept byte-identical in
    toke-corpus/regen/tkc_pin.py   and   toke/scripts/patterns/tkc_pin.py
Edit one, `cp` it over the other (the header is part of the file, so a plain
copy keeps them identical; regen/tests/test_tkc_pin.py asserts it).

Why. `~/tk/toke/tkc` is a symlink to `toke` that every `make` relinks. A
harness that execs tkc per record across a build sees ENOENT/ETXTBSY, or a
*different compiler* mid-run (131.22: one record flipped between passes;
131.10: one sweep scored on three build shas). Fix: copy the resolved binary
to a private temp dir once at start, exec the copy, and stamp its sha256 into
every output so a result can be tied to one exact binary.

API
    pin(toke_repo=None, tkc=None) -> Pinned
        Pinned.argv0        path to exec (the private copy) -- use this
        Pinned.path         same, the pinned copy
        Pinned.source       the path that was requested (usually the symlink)
        Pinned.resolved     symlink-followed real path at pin time
        Pinned.sha256       sha256 of the pinned copy (== of `resolved` at pin time)
        Pinned.version      first line of `tkc --version`
        Pinned.toke_head    toke repo HEAD sha (None outside a git repo)
        Pinned.toke_dirty   True when `git status --porcelain -- src` is non-empty
        Pinned.pin_dir      the private temp dir (None when reusing an env pin)
        Pinned.owned        True when this process made the copy (and cleans it up)
        Pinned.stamp()      dict for manifests: tkc_bin_sha, tkc_version, tkc_path,
                            tkc_pinned_copy, tkc_resolved, toke_head, toke_src_dirty
        Pinned.install(*modules)
                            export TOKE_TKC_PIN=<copy> for child processes and
                            rebind `mod.TKC` in each module given (type preserved:
                            a Path stays a Path)
        Pinned.close()      remove the temp dir (owner only); also a context manager
    default_tkc() -> str    what a module should use at import time:
                            $TOKE_TKC_PIN, else $TKC, else ~/tk/toke/tkc
    bin_sha(path=None)      cached sha256 of the binary at path (default default_tkc())

Multiprocessing. Pin once in the parent and call `p.install()`; workers
(spawn or fork) that call `pin()` or `default_tkc()` see TOKE_TKC_PIN and reuse
the copy (owned=False, so they never delete it). Across processes by hand:
    TOKE_TKC_PIN=/path/to/copy python3 harness.py

CLI: `python3 tkc_pin.py [--keep]` prints the stamp as JSON; with --keep the
copy is left in place (its path is in the JSON) for a shell script to exec.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ENV_PIN = "TOKE_TKC_PIN"
DEFAULT_TOKE = Path(os.path.expanduser("~/tk/toke"))
DEFAULT_TKC = DEFAULT_TOKE / "tkc"
_COPY_ATTEMPTS = 5          # a `make` in flight can hand us a half-written binary
_COPY_RETRY_S = 0.5


def default_tkc() -> str:
    """Path modules should bind `TKC` to at import time (pinned copy when one exists)."""
    return os.environ.get(ENV_PIN) or os.environ.get("TKC") or str(DEFAULT_TKC)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@functools.lru_cache(maxsize=None)
def _bin_sha_cached(path: str, size: int, mtime_ns: int) -> str:
    return sha256_file(path)


def bin_sha(path=None) -> str | None:
    """sha256 of the binary at `path` (default: default_tkc()); cached per
    (path, size, mtime) so a per-record call is free. None when it is missing."""
    p = str(path or default_tkc())
    try:
        st = os.stat(p)
    except OSError:
        return None
    return _bin_sha_cached(p, st.st_size, st.st_mtime_ns)


def _git(repo, *args) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _version(path) -> str | None:
    try:
        r = subprocess.run([str(path), "--version"], capture_output=True, text=True,
                           errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or r.stderr).strip()
    return out.splitlines()[0] if r.returncode == 0 and out else None


@dataclass
class Pinned:
    path: str
    source: str
    resolved: str | None
    sha256: str
    version: str | None
    toke_repo: str
    toke_head: str | None
    toke_dirty: bool
    pin_dir: str | None = None
    owned: bool = False
    _closed: bool = field(default=False, repr=False)

    @property
    def argv0(self) -> str:
        return self.path

    def stamp(self) -> dict:
        return {
            "tkc_bin_sha": self.sha256,
            "tkc_version": self.version,
            "tkc_path": self.source,
            "tkc_resolved": self.resolved,
            "tkc_pinned_copy": self.path,
            "toke_head": self.toke_head,
            "toke_src_dirty": self.toke_dirty,
        }

    def install(self, *modules) -> "Pinned":
        """Export the pin for child processes and rebind `TKC` in the modules given."""
        os.environ[ENV_PIN] = self.path
        for m in modules:
            cur = getattr(m, "TKC", None)
            setattr(m, "TKC", Path(self.path) if isinstance(cur, Path) else self.path)
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.owned and self.pin_dir:
            shutil.rmtree(self.pin_dir, ignore_errors=True)
            if os.environ.get(ENV_PIN) == self.path:
                del os.environ[ENV_PIN]

    def __enter__(self) -> "Pinned":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        # best effort: an owner that forgot close() still does not litter /tmp
        try:
            self.close()
        except Exception:
            pass


def _copy_verified(resolved: Path, dest: Path) -> str:
    """Copy resolved -> dest and prove the copy is a whole, runnable binary:
    sha256(dest) must equal sha256(resolved) re-read after the copy, and
    `dest --version` must succeed. Retries while a build is swapping the file."""
    last = None
    for attempt in range(_COPY_ATTEMPTS):
        try:
            shutil.copy2(resolved, dest, follow_symlinks=True)
            dest.chmod(0o755)
            got = sha256_file(dest)
            if got == sha256_file(resolved) and _version(dest) is not None:
                return got
            last = "binary changed under the copy" if got != sha256_file(resolved) else "copy does not run"
        except OSError as e:               # ENOENT/ETXTBSY mid-relink
            last = f"{type(e).__name__}: {e}"
        time.sleep(_COPY_RETRY_S * (attempt + 1))
    raise RuntimeError(f"tkc_pin: could not take a stable copy of {resolved} after "
                       f"{_COPY_ATTEMPTS} attempts ({last})")


def pin(toke_repo=None, tkc=None, prefix: str = "tkc_pin_", reuse_env: bool = True) -> Pinned:
    """Return a Pinned private copy of the tkc binary (see module docstring).

    toke_repo  compiler repo for the HEAD sha / dirty flag (default ~/tk/toke)
    tkc        binary to pin (default $TKC, else <toke_repo>/tkc); a symlink is followed
    reuse_env  honour $TOKE_TKC_PIN (a copy made by a parent process): no new
               copy, owned=False, close() is a no-op
    """
    repo = Path(toke_repo or DEFAULT_TOKE).expanduser()
    source = Path(tkc or os.environ.get("TKC") or (repo / "tkc")).expanduser()
    env_pin = os.environ.get(ENV_PIN) if reuse_env else None
    if env_pin and os.path.isfile(env_pin):
        p = Path(env_pin)
        resolved = str(source.resolve()) if source.exists() else None
        return Pinned(path=str(p), source=str(source), resolved=resolved, sha256=sha256_file(p),
                      version=_version(p), toke_repo=str(repo), toke_head=_git(repo, "rev-parse", "HEAD"),
                      toke_dirty=bool(_git(repo, "status", "--porcelain", "--", "src")),
                      pin_dir=None, owned=False)
    if not source.exists():
        raise FileNotFoundError(f"tkc not found at {source}")
    resolved = source.resolve()
    pin_dir = Path(tempfile.mkdtemp(prefix=prefix))
    dest = pin_dir / "tkc"
    try:
        sha = _copy_verified(resolved, dest)
    except Exception:
        shutil.rmtree(pin_dir, ignore_errors=True)
        raise
    return Pinned(path=str(dest), source=str(source), resolved=str(resolved), sha256=sha,
                  version=_version(dest), toke_repo=str(repo),
                  toke_head=_git(repo, "rev-parse", "HEAD"),
                  toke_dirty=bool(_git(repo, "status", "--porcelain", "--", "src")),
                  pin_dir=str(pin_dir), owned=True)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="pin the tkc binary; print the stamp as JSON")
    ap.add_argument("--tkc", default=None, help="binary to pin (default $TKC or ~/tk/toke/tkc)")
    ap.add_argument("--toke-repo", default=None)
    ap.add_argument("--keep", action="store_true",
                    help="leave the copy in place (for shell harnesses: exec `tkc_pinned_copy`, rm -rf its dir at the end)")
    a = ap.parse_args(argv)
    p = pin(toke_repo=a.toke_repo, tkc=a.tkc)
    stamp = p.stamp()
    stamp["pin_dir"] = p.pin_dir
    if a.keep:
        p.owned = False           # caller takes ownership of pin_dir
    print(json.dumps(stamp))
    p.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
