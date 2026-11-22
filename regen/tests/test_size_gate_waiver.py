"""131.108 (register 4.1 (a)): the size gates are waived only for rows whose
merged routing patches are in the declared correctness set, recorded as
`size_gate_note: waived(<story>)` plus the delta; every other row keeps both.

The unit tests need no tkc. The wave test builds two records with the same
lint-clean but LARGER candidate (`bigger`): the one routed by 131.105 banks
with the note and its byte delta, the unrouted one fails `min_bytes` exactly as
before."""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import library_common as lc
import pattern_common as pc
import size_gate_waiver as sgw


def _json(path):
    with open(path) as f:
        return json.load(f)


def _text(path):
    with open(path) as f:
        return f.read()


# ------------------------------------------------------------------ units ---
def test_selftest():
    assert sgw.selftest()


def test_declared_set_is_the_owner_answer():
    """Register 4.1 (a), recorded 2026-11-15, plus 131.99 (register 4.3 (a)).
    Changing it needs an owner decision."""
    assert sgw.CORRECTNESS_SET["regen"] == ("131.42", "131.43", "131.67", "131.105", "131.106",
                                            "131.111", "131.114", "131.99")
    assert sgw.CORRECTNESS_SET["library"] == ("131.106", "131.117")
    assert set(sgw.CORRECTNESS_SET) == {"regen", "library"}


def test_every_declared_story_is_a_replayed_patch():
    """A story can only waive through `patches`, which route_merge writes, so
    every declared story must be one route_merge replays."""
    import route_merge
    replayed = {s for s, _ in route_merge.PATCHES}
    for src, stories in sgw.CORRECTNESS_SET.items():
        assert set(stories) <= replayed, (src, set(stories) - replayed)


def test_pattern_size_gates_waived_only_for_the_set():
    before = {"min_bytes": 77, "proxy_tokens": 7}
    res = {"gates": {}, "reason": None, "min_bytes": 85, "proxy_tokens": 9}
    pc.size_gates(res, before, sgw.waiver_stories({"source": "regen", "patches": ["131.105"]}))
    assert res["gates"]["min_bytes"] is None and res["gates"]["proxy_tokens"] is None
    assert res["reason"] is None
    assert res["size_gate_note"] == "waived(131.105)"
    assert res["size_delta"] == {"min_bytes": 8, "proxy_tokens": 2}
    # same numbers, routed by a non-correctness story: both gates apply
    for row in ({"source": "regen", "patches": ["131.83"]},
                {"source": "regen", "patches": [], "bucket_reason": "131.105: restore"},
                {"source": "library", "patches": ["131.105"]}):
        res = {"gates": {}, "reason": None, "min_bytes": 85, "proxy_tokens": 9}
        pc.size_gates(res, before, sgw.waiver_stories(row))
        assert res["gates"]["min_bytes"] is False and res["gates"]["proxy_tokens"] is False, row
        assert res["reason"] == "min_bytes: 85 > 77 before" and "size_gate_note" not in res
    res = {"gates": {}, "reason": None, "min_bytes": 85, "proxy_tokens": 9}
    pc.size_gates(res, before)                         # default: no waiver
    assert res["gates"]["min_bytes"] is False


def test_library_size_gates_waived_only_for_the_set():
    before = {"min_bytes": 841, "proxy_tokens": 215}
    res = {"gates": {}, "reason": None, "min_bytes": 850, "proxy_tokens": 216}
    lc.size_gates(res, before, waiver=sgw.waiver_stories({"patches": ["131.117"]}, source="library"))
    assert res["gates"] == {"min_bytes": None, "proxy_tokens": None}
    assert res["size_gate_note"] == "waived(131.117)" and res["size_delta"]["min_bytes"] == 9
    res = {"gates": {}, "reason": None, "min_bytes": 850, "proxy_tokens": 216}
    lc.size_gates(res, before, waiver=sgw.waiver_stories({"patches": ["131.42"]}, source="library"))
    assert res["gates"]["min_bytes"] is False and "size_gate_note" not in res
    # the 131.32 NUL carve-out keeps its own note
    res = {"gates": {}, "reason": None, "min_bytes": 850, "proxy_tokens": 216}
    lc.size_gates(res, before, nul_carve_out=True, waiver=("131.117",))
    assert res["size_gate_note"] == lc.NUL_CARVE_REASON and "size_delta" not in res


# ------------------------------------------------------------- the wave ---
@pytest.mark.skipif(not os.path.exists(pc.TKC), reason="tkc not built")
def test_wave_banks_a_growing_repair_only_when_routed_by_the_set(tmp_path):
    import hashlib

    import bank_pattern
    import check_pattern
    import manifest_tool
    import pattern_prep
    import test_pattern_wave as tw

    saved = {m: m.TKC for m in tw._PIN_MODS}
    try:
        with pc.pin_tkc(), pytest.MonkeyPatch.context() as mp:
            tw._fix_clock(mp)
            c = tmp_path / "regen_v04"
            (c / "A-CND").mkdir(parents=True)
            (c / "shards").mkdir()
            (c / "ledger").mkdir()
            man = manifest_tool.Manifest(str(c / "MANIFEST.jsonl"))
            rows, specs = [], []
            for k, patches in ((0, ["131.105"]), (1, ["131.83"])):
                src = tw._src(k, "flag")
                p = c / "A-CND" / (tw._tid(k) + ".json")
                p.write_text(json.dumps(tw._record(k, src)))
                man.stamp(tw._tid(k), str(p), save=False)
                specs.append(tw._spec(k))
                rows.append({**tw._row(k), "source": "regen", "patches": patches})
            man.save()
            (c / "shards" / "shard_synth.jsonl").write_text("".join(json.dumps(s) + "\n" for s in specs))
            bucket = tmp_path / "merged.jsonl"
            bucket.write_text("".join(json.dumps(r) + "\n" for r in rows))
            paths = pc.Paths(corpus=str(c), workdir=str(tmp_path / "work"))
            pattern_prep.prepare(paths, str(bucket), skip_source=None)
            m0 = _json(paths.sub("meta", tw._tid(0) + ".json"))
            m1 = _json(paths.sub("meta", tw._tid(1) + ".json"))
            assert m0["patches"] == ["131.105"] and m0["size_gate_waiver"] == ["131.105"]
            assert m1["patches"] == ["131.83"] and m1["size_gate_waiver"] == []
            assert "size gates waived(131.105)" in _text(paths.sub("prompts", tw._tid(0) + ".txt"))
            assert "the rewrite must be <= both" in _text(paths.sub("prompts", tw._tid(1) + ".txt"))
            for k in (0, 1):
                tw._write_candidate(paths, k, "bigger")
            ok, lines = check_pattern.check(paths, tw._tid(0))
            assert ok, lines
            assert "min_bytes=n/a" in lines[1] and any("waived(131.105)" in l for l in lines)
            ok, lines = check_pattern.check(paths, tw._tid(1))
            assert not ok and lines[0].startswith("FAIL min_bytes:"), lines

            s = bank_pattern.run(paths, bank=True, shas={"tkc_sha": "t", "card_sha": "c",
                                                         "catalogue_sha": "k"})
            assert (s["rewritten"], s["failed"]) == (1, 1), s
            rec = _json(paths.record("A-CND", tw._tid(0)))
            rw = rec["regen"]["rewrite131"]
            assert rw["size_gate_note"] == "waived(131.105)"
            d = rw["size_delta"]
            assert d["min_bytes"] == rw["min_bytes_after"] - rw["min_bytes_before"] > 0
            assert d["proxy_tokens"] == rw["proxy_tokens_after"] - rw["proxy_tokens_before"]
            led = [json.loads(line) for line in _text(paths.ledger).splitlines() if line]
            byid = {e["task_id"]: e for e in led}
            assert byid[tw._tid(0)]["size_gate_note"] == "waived(131.105)"
            assert byid[tw._tid(1)]["status"] == "failed" and "size_gate_note" not in byid[tw._tid(1)]
            assert byid[tw._tid(1)]["reason"].startswith("min_bytes:")
            rec1 = _json(paths.record("A-CND", tw._tid(1)))
            assert rec1["tk_source"] == tw._src(1, "flag")
            assert hashlib.sha256(rec1["tk_source"].encode()).hexdigest() == rec1["regen"]["source_sha256"]
    finally:
        for m, v in saved.items():
            m.TKC = v


# ------------------------------------------------- the library wave (131.17) ---
import test_library_wave as _lw

world = _lw.world        # the synthetic library tree (test_library_wave), reused

# lint-clean, passes the manifest, and LARGER than test_library_wave.ORIG
LIB_BIGGER = ('m=echo;i=io:std.io;i=s:std.str;f=main():$i64{let t=s.trim(io.readln());'
              'if(s.len(t)==0){io.println("empty");<0};if(s.len(t)>0){io.println(t);<0};<0}\n')


@pytest.mark.skipif(not os.path.exists(lc.TKC), reason="tkc not built")
def test_default_library_prep_keys_the_waiver_to_the_merged_routing(world):
    """The default --sweep is the 131.13 sweep, whose rows carry no `patches`.
    Prep looks each row's patches up in the merged routing by task_id, for the
    waiver key only: the prepped set is unchanged, a 131.117 row is waived, a
    row routed by a story outside the set (or not routed) keeps both gates."""
    import bank_library
    import library_prep
    import test_library_wave as lw

    paths, specs = world
    assert paths.sweep.endswith("pattern_sweep.jsonl")
    assert paths.routing.endswith("pattern_sweep.merged.jsonl")
    routed = [{**lw._row(tid), "patches": patches}
              for tid, patches in (("L-TST-001", ["131.117"]), ("L-TST-002", ["131.83"]),
                                   ("L-TST-777", ["131.117"]))]   # L-TST-777: not in the sweep
    routed.append({**lw._row("A-CND-900", source="regen"), "patches": ["131.105"]})
    with open(paths.routing, "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in routed)
    m = library_prep.prepare(paths, embed_card=False)
    assert m["prepared"] == 3, "the routing file must not change the prepped set"
    meta = {t: _json(paths.sub("meta", t + ".json")) for t in ("L-TST-001", "L-TST-002", "L-TST-003")}
    assert meta["L-TST-001"]["patches"] == ["131.117"]
    assert meta["L-TST-001"]["size_gate_waiver"] == ["131.117"]
    assert meta["L-TST-002"]["size_gate_waiver"] == []
    assert meta["L-TST-003"]["patches"] == [] and meta["L-TST-003"]["size_gate_waiver"] == []
    assert not os.path.exists(paths.sub("meta", "L-TST-777.json"))
    assert "size gates waived(131.117)" in _text(paths.sub("prompts", "L-TST-001.txt"))
    assert "the rewrite must be <= both" in _text(paths.sub("prompts", "L-TST-002.txt"))

    for tid in ("L-TST-001", "L-TST-002"):
        lw._cand(paths, tid, specs, LIB_BIGGER)
    bank_library.run(paths, bank=False, run_perf=False, only=["L-TST-001", "L-TST-002"])
    ev0 = _json(paths.sub("dryrun", "L-TST-001.json"))
    ev1 = _json(paths.sub("dryrun", "L-TST-002.json"))
    assert ev0["verdict"] == "rewritten", ev0["reason"]
    assert ev0["size_gate_note"] == "waived(131.117)" and ev0["size_delta"]["min_bytes"] > 0
    assert ev1["verdict"] != "rewritten" and ev1["reason"].startswith("min_bytes:"), ev1


def test_routing_lookup_is_by_task_id_and_source(tmp_path):
    p = tmp_path / "merged.jsonl"
    p.write_text(json.dumps({"task_id": "L-X-1", "source": "library", "patches": ["131.106"]}) + "\n"
                 + json.dumps({"task_id": "A-X-1", "source": "regen", "patches": ["131.106"]}) + "\n")
    r = lc.load_routing_patches(str(p))
    assert r == {"L-X-1": ["131.106"]}
    assert lc.row_patches({"task_id": "L-X-1"}, r) == ["131.106"]
    assert lc.row_patches({"task_id": "L-X-2", "patches": ["131.117"]}, r) == ["131.117"]
    assert lc.row_patches({"task_id": "L-X-3"}, r) == []
    assert lc.load_routing_patches(str(tmp_path / "missing.jsonl")) == {}
