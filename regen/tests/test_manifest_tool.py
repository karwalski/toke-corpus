"""131.35 — manifest_tool check / rebuild / stamp on a temp corpus.

Temp corpus: three records — one whose manifest line is fresh, one stale
(record replaced after stamping), one missing from the manifest — plus a
manifest line whose file is gone, and a freeze manifest that reflects the
on-disk files.
"""
import hashlib, json, os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import manifest_tool  # noqa: E402


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _record(tid, cat, src, min_bytes=100):
    return {"id": "P3-" + tid, "version": 2, "task_id": tid, "tk_source": src,
            "regen": {"category": cat, "task_type": "full_program", "difficulty": 1,
                      "source_sha256": _sha(src.encode()), "min_bytes": min_bytes}}


def _write(path, rec):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = json.dumps(rec).encode()
    with open(path, "wb") as f:
        f.write(data)
    return data


@pytest.fixture
def corpus(tmp_path):
    c = tmp_path / "regen_v04"
    ok = _write(c / "A-ARR" / "A-ARR-0001v1.json", _record("A-ARR-0001v1", "A-ARR", "f=a(){<1};\n"))
    old = _write(c / "A-ARR" / "A-ARR-0002v1.json", _record("A-ARR-0002v1", "A-ARR", "f=b(){<2};\n"))
    stale_line_sha = _sha(old)
    new = _write(c / "A-ARR" / "A-ARR-0002v1.json", _record("A-ARR-0002v1", "A-ARR", "f=b(){<22};\n"))
    missing = _write(c / "D-CLI" / "D-CLI-0003v1.json", _record("D-CLI-0003v1", "D-CLI", "f=c(){<3};\n"))
    manifest = c / "MANIFEST.jsonl"
    rows = [
        {"id": "P3-A-ARR-0001v1", "task_id": "A-ARR-0001v1", "category": "A-ARR",
         "task_type": "full_program", "difficulty": 1, "sha256": _sha(ok), "shard": "shard_00"},
        {"id": "P3-A-ARR-0002v1", "task_id": "A-ARR-0002v1", "category": "A-ARR",
         "task_type": "full_program", "difficulty": 1, "sha256": stale_line_sha, "shard": "shard_01"},
        {"id": "P3-A-ARR-0009v1", "task_id": "A-ARR-0009v1", "category": "A-ARR",
         "task_type": "full_program", "difficulty": 1, "sha256": "0" * 64, "shard": "shard_02"},
    ]
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows))
    freeze = tmp_path / "freeze_129_manifest.jsonl"
    freeze.write_text("".join(json.dumps({"task_id": t, "category": t[:5], "path": f"{t[:5]}/{t}.json",
                                          "sha256": s, "min_bytes": 100, "audit": "129-freeze"}) + "\n"
                              for t, s in [("A-ARR-0001v1", _sha(ok)), ("A-ARR-0002v1", _sha(new)),
                                           ("D-CLI-0003v1", _sha(missing))]))
    return {"dir": str(c), "manifest": str(manifest), "freeze": str(freeze)}


def test_check_buckets(corpus):
    rep = manifest_tool.check(corpus["dir"], corpus["manifest"], corpus["freeze"])
    c = rep["counts"]
    assert c["files"] == 3 and c["manifest_rows"] == 3
    assert c["ok"] == 1 and c["stale"] == 1 and c["missing_from_manifest"] == 1
    assert c["manifest_without_file"] == 1 and c["legacy_source_sha"] == 0
    assert rep["samples"]["stale"] == ["A-ARR-0002v1"]
    assert rep["samples"]["missing_from_manifest"] == ["D-CLI-0003v1"]
    assert rep["samples"]["manifest_without_file"] == ["A-ARR-0009v1"]
    assert c["freeze_match"] == 3 and c["freeze_diverged"] == 0 and c["freeze_orphan"] == 0
    assert c["stale_but_matches_freeze"] == 1
    assert rep["clean"] is False


def test_check_accepts_legacy_source_sha(corpus):
    # pre-131 lines carried sha256(tk_source); they are fresh-but-legacy, not stale
    rec = json.load(open(os.path.join(corpus["dir"], "A-ARR", "A-ARR-0001v1.json")))
    rows = manifest_tool.load_rows(corpus["manifest"])
    rows[0]["sha256"] = _sha(rec["tk_source"].encode())
    with open(corpus["manifest"], "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    c = manifest_tool.check(corpus["dir"], corpus["manifest"], corpus["freeze"])["counts"]
    assert c["ok"] == 0 and c["legacy_source_sha"] == 1 and c["stale"] == 1


def test_rebuild_then_check_clean(corpus):
    rep = manifest_tool.rebuild(corpus["dir"], corpus["manifest"])
    c = rep["counts"]
    assert c["rows_written"] == 3 and c["new_rows"] == 1 and c["dropped_old_rows"] == 1
    assert c["preserved_from_old"] == 2 and c["was_stale_in_old"] == 1
    backup = os.path.join(corpus["dir"], "MANIFEST.pre131.jsonl")
    assert rep["backup"] == backup and os.path.exists(backup)
    assert len(manifest_tool.load_rows(backup)) == 3          # old copy intact
    rows = {r["task_id"]: r for r in manifest_tool.load_rows(corpus["manifest"])}
    assert set(rows) == {"A-ARR-0001v1", "A-ARR-0002v1", "D-CLI-0003v1"}
    assert rows["A-ARR-0002v1"]["shard"] == "shard_01"        # preserved from old line
    assert rows["D-CLI-0003v1"]["shard"] is None and rows["D-CLI-0003v1"]["id"] == "P3-D-CLI-0003v1"
    assert rows["D-CLI-0003v1"]["category"] == "D-CLI" and rows["D-CLI-0003v1"]["task_type"] == "full_program"
    for r in rows.values():
        assert r["min_bytes"] == 100 and r["stamped_at"].endswith("Z") and r["path"].endswith(".json")
        assert r["sha256"] == manifest_tool.sha256_file(os.path.join(corpus["dir"], r["path"]))
    after = manifest_tool.check(corpus["dir"], corpus["manifest"], corpus["freeze"])
    assert after["clean"] is True
    assert after["counts"]["stale"] == 0 and after["counts"]["legacy_source_sha"] == 0
    assert after["counts"]["ok"] == 3 and after["counts"]["freeze_match"] == 3
    # a second rebuild never clobbers the backup
    old_backup = open(backup).read()
    rep2 = manifest_tool.rebuild(corpus["dir"], corpus["manifest"])
    assert rep2["backup"] is None and rep2["backup_existed"] is True
    assert open(backup).read() == old_backup


def test_stamp_updates_one_line_and_preserves_fields(corpus):
    rec_path = os.path.join(corpus["dir"], "A-ARR", "A-ARR-0002v1.json")
    before = manifest_tool.load_rows(corpus["manifest"])
    row = manifest_tool.stamp(corpus["manifest"], "A-ARR-0002v1", rec_path)
    after = manifest_tool.load_rows(corpus["manifest"])
    assert len(after) == len(before) == 3
    assert [r["task_id"] for r in after] == [r["task_id"] for r in before]   # in place
    assert after[0] == before[0] and after[2] == before[2]                   # others untouched
    assert row["sha256"] == manifest_tool.sha256_file(rec_path)
    assert row["shard"] == "shard_01" and row["id"] == "P3-A-ARR-0002v1"
    assert row["source_sha256"] == json.load(open(rec_path))["regen"]["source_sha256"]
    c = manifest_tool.check(corpus["dir"], corpus["manifest"], corpus["freeze"])["counts"]
    assert c["stale"] == 0 and c["ok"] == 2


def test_stamp_appends_new_and_collapses_dups(corpus):
    m = manifest_tool.Manifest(corpus["manifest"])
    # simulate a retried task that got two rows
    m.lines.append(m.lines[1]); m._reindex()
    m.save()
    assert len(manifest_tool.load_rows(corpus["manifest"])) == 4
    m = manifest_tool.Manifest(corpus["manifest"])
    m.stamp("A-ARR-0002v1", os.path.join(corpus["dir"], "A-ARR", "A-ARR-0002v1.json"))
    m.stamp("D-CLI-0003v1", os.path.join(corpus["dir"], "D-CLI", "D-CLI-0003v1.json"),
            extra={"shard": "library_131"})
    rows = manifest_tool.load_rows(corpus["manifest"])
    assert [r["task_id"] for r in rows] == ["A-ARR-0001v1", "A-ARR-0002v1", "A-ARR-0009v1", "D-CLI-0003v1"]
    assert rows[3]["shard"] == "library_131"
    c = manifest_tool.check(corpus["dir"], corpus["manifest"], corpus["freeze"])["counts"]
    assert c["manifest_dup_rows"] == 0 and c["missing_from_manifest"] == 0 and c["stale"] == 0


def test_cli_check_exit_code_and_json(corpus, tmp_path):
    out = tmp_path / "rep.json"
    rc = manifest_tool.main(["check", "--corpus", corpus["dir"], "--freeze", corpus["freeze"],
                             "--json", str(out)])
    assert rc == 1
    assert json.load(open(out))["counts"]["stale"] == 1
    assert manifest_tool.main(["rebuild", "--corpus", corpus["dir"]]) == 0
    assert manifest_tool.main(["check", "--corpus", corpus["dir"], "--freeze", corpus["freeze"]]) == 0
    assert manifest_tool.main(["stamp", "--corpus", corpus["dir"], "--task-id", "A-ARR-0001v1",
                               "--extra", "shard=shard_09"]) == 0
    rows = {r["task_id"]: r for r in manifest_tool.load_rows(corpus["manifest"])}
    assert rows["A-ARR-0001v1"]["shard"] == "shard_09"
