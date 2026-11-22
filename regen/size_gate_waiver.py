#!/usr/bin/env python3
"""Story 131.108 (owner register 2026-11-12 item 4.1, answer (a)): the
correctness set whose routed rows are exempt from the 131.15 / 131.17 size
gates (`tkc --min` bytes <= before, proxy tokens <= before).

Why. Those gates are compaction gates: they compare two forms of the SAME
behaviour (patterns protocol §5.1). A row routed by a correctness story is a
repair: the banked record is wrong (gamed error marker, wrong signature, does
not compile, changed failure) and the fix may have to grow it. 131.105's
faithful restores are +8 `--min` bytes each; 131.106's compile fixes +3..+9.
Under the gates they are rejected by construction and burn their one retry.

What. A row is waived iff its merged routing (`pattern_sweep.merged.jsonl`,
built by route_merge.py) lists, in `patches`, a story in CORRECTNESS_SET for
the row's `source` (regen or library). Nothing else — not the bucket_reason
text, not a violation rule name, not a worker's say-so — can waive a gate.
Every other candidate keeps both gates.

The banked record carries `size_gate_note: "waived(<story>)"` and
`size_delta: {min_bytes, proxy_tokens}` (after - before) in its rewrite131
provenance, so the waiver stays visible per record.

Imported by pattern_common (131.15) and library_common (131.17). This module
holds no differential machinery, so library_common may import it (see
library_common's import contract).

Self-test: python3 regen/size_gate_waiver.py --selftest
"""
import sys

STORY = "131.108"

# The declared set (register 4.1 (a), recorded 2026-11-15), keyed by the
# merged row's `source`. A story appears here only by owner decision.
CORRECTNESS_SET = {
    "regen": ("131.42", "131.43", "131.67", "131.105", "131.106", "131.111", "131.114"),
    "library": ("131.106", "131.117"),
}


def waiver_stories(row, source=None):
    """Stories in the row's merged `patches` that are in the correctness set
    for its source, in patch order. () when none (the gates apply).

    row: a merged sweep row or a prep meta dict carrying `patches` and
    `source` (or `bucket_source`). source overrides the row's own."""
    if not row:
        return ()
    src = source or row.get("source") or row.get("bucket_source")
    allowed = CORRECTNESS_SET.get(src, ())
    out = []
    for s in row.get("patches") or ():
        if isinstance(s, str) and s in allowed and s not in out:
            out.append(s)
    return tuple(out)


def note(stories):
    """`waived(<story>)`; several stories are joined with `,` in patch order."""
    return f"waived({','.join(stories)})" if stories else None


def _delta(after, before):
    return None if after is None or before is None else after - before


def apply(res, before, stories):
    """Mark both size gates n/a (None) on a waived row and record the note and
    the delta. Returns True when it waived; False (res untouched) otherwise."""
    if not stories:
        return False
    b = before or {}
    res["gates"]["min_bytes"] = None
    res["gates"]["proxy_tokens"] = None
    res["size_gate_note"] = note(stories)
    res["size_delta"] = {"min_bytes": _delta(res.get("min_bytes"), b.get("min_bytes")),
                         "proxy_tokens": _delta(res.get("proxy_tokens"), b.get("proxy_tokens"))}
    return True


def selftest():
    """Holds the constant to the owner's answer and the key to `patches`."""
    assert CORRECTNESS_SET == {
        "regen": ("131.42", "131.43", "131.67", "131.105", "131.106", "131.111", "131.114"),
        "library": ("131.106", "131.117"),
    }, CORRECTNESS_SET
    # keyed to the merged patches, scoped by source
    assert waiver_stories({"source": "regen", "patches": ["131.105"]}) == ("131.105",)
    assert waiver_stories({"source": "library", "patches": ["131.117"]}) == ("131.117",)
    assert waiver_stories({"source": "library", "patches": ["131.105"]}) == ()
    assert waiver_stories({"source": "regen", "patches": ["131.117"]}) == ()
    assert waiver_stories({"bucket_source": "regen", "patches": ["131.42", "131.43"]}) == ("131.42", "131.43")
    # routing stories outside the set never waive
    for s in ("131.15.looprebuild", "131.68", "131.83", "131.127"):
        assert waiver_stories({"source": "regen", "patches": [s]}) == (), s
    # free text never waives
    assert waiver_stories({"source": "regen", "patches": [],
                           "bucket_reason": "131.105: restore the let", "story": "131.105"}) == ()
    assert waiver_stories({"source": "regen"}) == () and waiver_stories(None) == ()
    # apply
    res = {"gates": {}, "min_bytes": 85, "proxy_tokens": 9}
    assert apply(res, {"min_bytes": 77, "proxy_tokens": 7}, ("131.105",))
    assert res["gates"] == {"min_bytes": None, "proxy_tokens": None}
    assert res["size_gate_note"] == "waived(131.105)"
    assert res["size_delta"] == {"min_bytes": 8, "proxy_tokens": 2}
    res = {"gates": {}, "min_bytes": 85}
    assert not apply(res, {"min_bytes": 77}, ()) and res == {"gates": {}, "min_bytes": 85}
    return True


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        selftest()
        print("size_gate_waiver selftest: ok")
        sys.exit(0)
    print(__doc__)
