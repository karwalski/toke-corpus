"""131.15 prep (Z95): route_merge replays routing patches over a fresh sweep."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import route_merge as rm
import route_patch

LINT = {"rule": "single-use-let", "severity": "hint", "span": [1, 2], "fix": True, "saving": 1}
LOOP = {"rule": "loop-rebuilds-array", "severity": "warning", "span": [3, 4], "fix": None, "saving": 4}


def _row(tid, bucket, viol=(), **kw):
    r = {"task_id": tid, "story": "131.13", "source": "regen", "category": "D-X",
         "task_type": "single_function", "bucket": bucket, "violations": list(viol), "pattern_ids": [],
         "proxy_tokens": 10}
    r.update(kw)
    return r


def _corpus(tmp_path, old, new, patches):
    a = tmp_path / "audit"
    a.mkdir()
    (a / rm.OLD_SWEEP).write_text("".join(json.dumps(r) + "\n" for r in old))
    (a / rm.NEW_SWEEP).write_text("".join(json.dumps(r) + "\n" for r in new))
    for story, rows in patches.items():
        (a / f"pattern_sweep.{story}.patch.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(tmp_path)


def test_patch_order_extends_route_patch_order():
    stories = [s for s, _ in rm.PATCHES]
    assert stories[:6] == list(route_patch.BASE_PATCHES + route_patch.APPLY_ORDER)
    assert stories[6:] == ["131.106", "131.105"]
    assert all(o == rm.OLD_SWEEP for _, o in rm.PATCHES[:6])
    assert all(o == rm.NEW_SWEEP for _, o in rm.PATCHES[6:])


def test_replay_keeps_fresh_sweep_data_and_adds_only_the_delta(tmp_path):
    old = [_row("a", "AUTO", [LINT], proxy_tokens=12)]
    new = [_row("a", "LEAVE", [], proxy_tokens=9)]           # 131.14 fixed the let since
    p = route_patch.merge_row(old[0], "a", "131.43", "signature-drift", "AGENT", "drift", ts=1)
    _, merged, stats, files = rm.build(_corpus(tmp_path, old, new, {"131.43": [p]}),
                                       patches=(("131.43", rm.OLD_SWEEP),))
    r = merged["a"]
    assert [v["rule"] for v in r["violations"]] == ["signature-drift"]   # stale single-use-let NOT restored
    assert r["proxy_tokens"] == 9 and r["bucket"] == "AGENT" and r["would_bucket"] == "LEAVE"
    assert r["bucket_reason"] == "131.43: drift" and r["patches"] == ["131.43"]
    assert stats["131.43"]["LEAVE->AGENT"] == 1 and files[0]["rows"] == 1


def test_cat_merge_would_have_restored_stale_rows(tmp_path):
    """The defect route_merge exists for: a later full patch row wins under cat."""
    old = _row("a", "AUTO", [LINT])
    p = route_patch.merge_row(old, "a", "131.43", "signature-drift", "AGENT", "drift", ts=1)
    assert [v["rule"] for v in p["violations"]] == ["single-use-let", "signature-drift"]


def test_chained_patches_join_reasons_once(tmp_path):
    old = [_row("a", "LEAVE")]
    new = [_row("a", "LEAVE", [LOOP])]
    p1 = route_patch.merge_row(old[0], "a", "131.42", "gamed-err-marker", "AGENT", "gamed", ts=1)
    p2 = route_patch.merge_row(p1, "a", "131.43", "signature-drift", "AGENT", "drift", ts=1)
    assert p2["bucket_reason"] == "131.42: gamed | 131.43: drift"
    corpus = _corpus(tmp_path, old, new, {"131.42": [p1], "131.43": [p2]})
    _, merged, _, _ = rm.build(corpus, patches=(("131.42", rm.OLD_SWEEP), ("131.43", rm.OLD_SWEEP)))
    r = merged["a"]
    assert r["bucket_reason"] == "131.42: gamed | 131.43: drift"
    assert [v["rule"] for v in r["violations"]] == ["loop-rebuilds-array", "gamed-err-marker", "signature-drift"]
    assert r["patches"] == ["131.42", "131.43"] and r["would_bucket"] == "AGENT"


def test_regen_is_sticky_for_the_regen_class_only(tmp_path):
    a_full = {"category": "A-ARR", "task_type": "full_program"}
    old = [_row("a", "REGEN", **a_full), _row("m", "REGEN", task_type="migrate_fix")]
    new = [_row("a", "REGEN", **a_full), _row("m", "REGEN", task_type="migrate_fix")]
    ps = [dict(route_patch.merge_row(r, r["task_id"], "131.42", "gamed-err-marker", "AGENT", "x", ts=1),
               bucket="AGENT") for r in old]
    _, merged, stats, _ = rm.build(_corpus(tmp_path, old, new, {"131.42": ps}),
                                   patches=(("131.42", rm.OLD_SWEEP),))
    assert merged["a"]["bucket"] == "REGEN" and stats["131.42"]["regen_kept"] == 1
    assert merged["m"]["bucket"] == "AGENT"          # no regeneration path outside the class (131.109)


def test_new_patch_origin_is_the_rerun(tmp_path):
    old = [_row("a", "AUTO", [LINT])]
    new = [_row("a", "LEAVE", [LOOP])]
    corpus = _corpus(tmp_path, old, new, {})
    _, cur, _, _ = rm.build(corpus, patches=())
    p = route_patch.merge_row(cur["a"], "a", "131.106", "does-not-compile", "AGENT", "E4031", ts=1)
    (tmp_path / "audit" / "pattern_sweep.131.106.patch.jsonl").write_text(json.dumps(p) + "\n")
    _, merged, _, _ = rm.build(corpus, patches=(("131.106", rm.NEW_SWEEP),))
    assert [v["rule"] for v in merged["a"]["violations"]] == ["loop-rebuilds-array", "does-not-compile"]


def test_missing_patch_file_is_empty_and_rows_unknown_are_counted(tmp_path):
    old = [_row("a", "LEAVE")]
    new = [_row("a", "LEAVE")]
    stray = route_patch.merge_row(None, "zz", "131.83", "r", "REGEN", "x", ts=1)
    corpus = _corpus(tmp_path, old, new, {"131.83": [stray]})
    _, merged, stats, files = rm.build(corpus, patches=(("131.68", rm.OLD_SWEEP), ("131.83", rm.OLD_SWEEP)))
    assert files[0]["present"] is False and stats["131.68"] == {}
    assert stats["131.83"]["unknown_task_id"] == 1 and "zz" not in merged


def test_cli_writes_then_check_agrees(tmp_path):
    old = [_row("a", "LEAVE"), _row("b", "AUTO")]
    new = [_row("a", "LEAVE"), _row("b", "LEAVE")]
    p = route_patch.merge_row(old[0], "a", "131.42", "gamed-err-marker", "AGENT", "x", ts=1)
    corpus = _corpus(tmp_path, old, new, {"131.42": [p]})
    summ = tmp_path / "s.json"
    assert rm.main(["--corpus", corpus, "--summary", str(summ)]) == 0
    s = json.loads(summ.read_text())
    assert s["buckets"]["merged"]["all"]["AGENT"] == 1 and s["buckets"]["merged"]["all"]["LEAVE"] == 1
    assert rm.main(["--corpus", corpus, "--check", "--summary", ""]) == 0
    with open(os.path.join(corpus, "audit", "pattern_sweep.merged.jsonl"), "a") as f:
        f.write(json.dumps(_row("a", "LEAVE")) + "\n")
    assert rm.main(["--corpus", corpus, "--check", "--summary", ""]) == 1


REAL = os.path.join(rm.CORPUS, "audit", "pattern_sweep.merged.jsonl")


@pytest.mark.skipif(not os.path.exists(os.path.join(rm.CORPUS, "audit", rm.NEW_SWEEP)),
                    reason="on-disk corpus (gitignored) not present")
def test_on_disk_merged_is_the_reproducible_build():
    """The merged file the 131.15 prep reads is exactly route_merge's output."""
    assert rm.main(["--corpus", rm.CORPUS, "--check", "--summary", ""]) == 0
