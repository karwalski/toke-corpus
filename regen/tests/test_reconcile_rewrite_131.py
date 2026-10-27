"""131.80 — reconcile_rewrite_131: ledger chain, archive bytes, provenance.

A synthetic regen_v04 tree (two records, one rewritten by two waves, one
mutation-store id outside regen_v04) is built in tmp_path, with a freeze
tarball when `zstd` is available."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import reconcile_rewrite_131 as rr  # noqa: E402


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _rec(src, wave=None):
    r = {"task_id": "x", "tk_source": src, "regen": {}}
    if wave:
        r["regen"]["rewrite131"] = {"wave": wave}
    return (json.dumps(r) + "\n").encode()


def build(tmp, tarball=True):
    corpus = tmp / "regen_v04"
    for d in ("A-ARR", "ledger", "audit/replaced/131"):
        (corpus / d).mkdir(parents=True)
    orig = {"A-ARR-0001v1": _rec("f=a():i64{<1};"), "A-ARR-0002v1": _rec("f=b():i64{<2};")}
    mid = _rec("f=a():i64{<1 };", "auto")
    final = {"A-ARR-0001v1": _rec("f=a():i64{<1};;", "struct-layout"),
             "A-ARR-0002v1": _rec("f=b():i64{<2 };", "auto")}
    freeze, man, ledger = [], [], []
    for tid, b in orig.items():
        path = f"A-ARR/{tid}.json"
        freeze.append({"task_id": tid, "path": path, "sha256": _sha(b)})
        (corpus / path).write_bytes(final[tid])
        man.append({"task_id": tid, "path": path, "sha256": _sha(final[tid])})
        (corpus / "audit/replaced/131" / f"{tid}.tk").write_text(json.loads(b)["tk_source"])
    ledger.append({"task_id": "A-ARR-0001v1", "wave": "auto", "status": "rewritten",
                   "prev_sha256": _sha(orig["A-ARR-0001v1"]), "new_sha256": _sha(mid), "ts": "1"})
    ledger.append({"task_id": "A-ARR-0002v1", "wave": "auto", "status": "rewritten",
                   "prev_sha256": _sha(orig["A-ARR-0002v1"]), "new_sha256": _sha(final["A-ARR-0002v1"]), "ts": "1"})
    ledger.append({"task_id": "A-ARR-0001v1", "wave": "struct-layout", "status": "rewritten",
                   "prev_sha256": _sha(mid), "new_sha256": _sha(final["A-ARR-0001v1"]), "ts": "2"})
    ledger.append({"task_id": "A-STR-0034v7", "wave": "struct-layout", "status": "flagged",
                   "prev_sha256": "0", "new_sha256": None, "ts": "2"})
    ledger.append({"task_id": "MUT-x-1", "wave": "struct-layout", "status": "rewritten",
                   "prev_sha256": "a", "new_sha256": "b", "ts": "2"})
    (corpus / "audit/replaced/131/MUT-x-1.tk").write_text("x")
    w = lambda p, rows: p.write_text("".join(json.dumps(r) + "\n" for r in rows))  # noqa: E731
    w(corpus / "ledger/rewrite_131.jsonl", ledger)
    w(corpus / "MANIFEST.jsonl", man)
    fm = tmp / "freeze_129_manifest.jsonl"
    w(fm, freeze)
    tb = None
    if tarball:
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tf:
            for tid, b in orig.items():
                ti = tarfile.TarInfo(f"A-ARR/{tid}.json")
                ti.size = len(b)
                tf.addfile(ti, io.BytesIO(b))
        tb = tmp / "freeze.tar.zst"
        tb.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=raw.getvalue(),
                                      capture_output=True, check=True).stdout)
        (tmp / "freeze.tar.zst.sha256").write_text(_sha(tb.read_bytes()) + "  freeze.tar.zst\n")
    return corpus, fm, tb


def test_clean_tree_reconciles_without_tarball(tmp_path):
    corpus, fm, _ = build(tmp_path, tarball=False)
    res = rr.reconcile(str(corpus), str(fm))
    assert res["ok"], res["problems"]
    assert res["waves"]["auto"]["ledger_rows"] == 2
    assert res["waves"]["struct-layout"]["status"] == {"rewritten": 2, "flagged": 1}
    assert res["rewritten_records"] == 3 and res["rewritten_records_in_regen_v04"] == 2
    assert [o["task_id"] for o in res["outside_regen_v04"]] == ["MUT-x-1"]
    # the provenance block names only the LAST wave: auto under-counts by one
    assert res["provenance_block_wave_counts"] == {"auto": 1, "struct-layout": 1}
    assert res["rewritten_by_more_than_one_wave"] == [{"task_id": "A-ARR-0001v1",
                                                       "waves": ["auto", "struct-layout"]}]


needs_zstd = pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")


@needs_zstd
def test_archive_bytes_checked_against_freeze_tarball(tmp_path):
    corpus, fm, tb = build(tmp_path)
    res = rr.reconcile(str(corpus), str(fm), str(tb))
    assert res["ok"], res["problems"]
    assert res["archive_checked_against_freeze_bytes"] == 2
    (corpus / "audit/replaced/131/A-ARR-0002v1.tk").write_text("f=b():i64{<3};")
    res = rr.reconcile(str(corpus), str(fm), str(tb))
    assert res["problem_counts"] == {"archive": 1}


@needs_zstd
def test_tarball_checksum_enforced(tmp_path):
    corpus, fm, tb = build(tmp_path)
    (tmp_path / "freeze.tar.zst.sha256").write_text("0" * 64 + "  freeze.tar.zst\n")
    with pytest.raises(ValueError):
        rr.reconcile(str(corpus), str(fm), str(tb))


def test_broken_chain_and_stale_manifest_reported(tmp_path):
    corpus, fm, _ = build(tmp_path, tarball=False)
    (corpus / "A-ARR/A-ARR-0002v1.json").write_bytes(b"{}\n")
    res = rr.reconcile(str(corpus), str(fm))
    checks = {(p["task_id"], p["check"]) for p in res["problems"]}
    assert ("A-ARR-0002v1", "chain") in checks and ("A-ARR-0002v1", "manifest") in checks


def test_missing_ledger_is_an_error_not_a_zero(tmp_path):
    corpus, fm, _ = build(tmp_path, tarball=False)
    os.unlink(corpus / "ledger/rewrite_131.jsonl")
    with pytest.raises(FileNotFoundError):
        rr.reconcile(str(corpus), str(fm))
