"""The corpus compiler pin: the one tkc binary Epic 131 waves and gates run against (131.103).

The pin is a directory in the local archive (not in git), cut from a verified
clean `git archive` of toke main with its stdlib inside it, so the binary is
self-contained:

    ~/tk/archive/pins/tkc-131.103/
        toke, tkc -> toke     the binary (sha256 below)
        src/stdlib/, stdlib/  the stdlib it was built against (baked path)
        MANIFEST.md           commit, sha, build command, restore hint
        selftest.sh           hello world + std.str + `\\(true)` -> true

It supersedes the 131.73 pin (toke 2.8.0, commit 3e8971c, sha256 57394dad...).
That pin's baked stdlib path (/private/tmp/pinbuild/src/stdlib) is gone, so it
cannot link a program, and it prints bools as 1/0 (131.102).

`tkc_pin.py` is a byte-identical twin of toke/scripts/patterns/tkc_pin.py, so
the pin's location lives here instead. Harness defaults stay `$TKC` and then
`~/tk/toke/tkc`. A gate that should run on the pin takes `default_tkc()` from
this module.
"""
from __future__ import annotations

import os

import tkc_pin

STORY = "131.103"
PIN_DIR = os.path.expanduser("~/tk/archive/pins/tkc-131.103")
TKC = os.path.join(PIN_DIR, "tkc")
SHA256 = "d400375946fbae74dac24adc52bbc45bb1d4c94678d6ba0d297aef3c3dd35bb2"
GIT_SHA = "1f39c2998110"            # toke main 1f39c2998110d4806eee18a6afe2376c31a7e86e
VERSION = "toke 3.1.0 (git 1f39c2998110)"
SUPERSEDES = {"story": "131.73", "dir": os.path.expanduser("~/tk/archive/pins/tkc-131.73"),
              "sha256_prefix": "57394dad1950e2da", "commit": "3e8971c"}


def present() -> bool:
    return os.path.isfile(TKC)


def default_tkc() -> str:
    """$TKC when set, else the pin when it is present, else tkc_pin's default."""
    return os.environ.get("TKC") or (TKC if present() else tkc_pin.default_tkc())


def verify(path: str = TKC) -> list[str]:
    """Problems with the binary at path as the corpus pin ([] = it is the pin)."""
    if not os.path.isfile(path):
        return [f"{path}: missing"]
    probs = []
    sha = tkc_pin.sha256_file(path)
    if sha != SHA256:
        probs.append(f"sha256 {sha[:16]} != pinned {SHA256[:16]}")
    gsha, gdirty = tkc_pin.build_info(path)
    if gsha != GIT_SHA:
        probs.append(f"git_sha {gsha} != {GIT_SHA}")
    if gdirty is not False:
        probs.append(f"git_dirty {gdirty} (want false)")
    return probs
