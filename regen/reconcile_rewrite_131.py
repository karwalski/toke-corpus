#!/usr/bin/env python3
"""131.80 — reconcile the Epic 131 rewrite ledger against the archived originals.

The deterministic auto wave (131.14) banked 424 records on 2026-09-19 and the
tracker went on describing it as unrun because the wrong path was checked. This
tool is what AUDIT_131 (131.27) cites for "how many banked records did each
Epic 131 wave rewrite, and does every one of them still trace back to its
frozen original". It reads only; it writes the summary JSON and nothing else.

For every task id in `corpus/regen_v04/ledger/rewrite_131.jsonl` it checks:

  chain     the `rewritten` rows for the id, in ledger order, form one sha
            chain: the first `prev_sha256` is the record's freeze-129 file
            sha (`regen/freeze/freeze_129_manifest.jsonl`), each later
            `prev_sha256` is the previous row's `new_sha256`, and the last
            `new_sha256` is the sha of the record file on disk now and of its
            `MANIFEST.jsonl` line.
  archive   `audit/replaced/131/<task_id>.tk` exists and its bytes are the
            frozen `tk_source`, read from the freeze-129 byte snapshot
            (`--freeze-tarball`, sha256-verified against its .sha256 file; each
            record's file sha is checked against the freeze manifest first).
            `MANIFEST.pre131.jsonl` is NOT usable for this: it is the manifest
            131.35 found 2,618 stale lines in. The banks archive on FIRST touch
            only, so for a record rewritten by two waves the archive is the
            freeze original and the intermediate source is not kept as bytes.
  provenance  the record's `regen.rewrite131` block names the LAST wave that
            rewrote it. A record rewritten by two waves carries only the second
            block, so counting `regen.rewrite131.wave` under-counts the first
            wave — the ledger is the count of record.

Rows whose status is not `rewritten` (e.g. struct-layout `flagged`) changed no
bytes and are reported, not chained.

Ids with no freeze-129 manifest line AND no MANIFEST.jsonl line (the 131.78
struct-layout wave also repaired mutation-store records, `MUT-*`) are outside
regen_v04: counted under `outside_regen_v04`, not chained.

Usage: reconcile_rewrite_131.py [--corpus DIR] [--freeze-manifest F]
                                [--freeze-tarball F] [--out F]
"""
import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORY = "131.80"
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
FREEZE_MANIFEST = os.path.join(HERE, "freeze", "freeze_129_manifest.jsonl")
OUT = os.path.join(HERE, "freeze", f"rewrite_131_reconcile.{STORY}.json")
FREEZE_TARBALL = os.path.expanduser(
    "~/tk/archive/toke-corpus-regen_v04-freeze129-20260819/regen_v04-freeze129.tar.zst")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path):
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def by_task(rows):
    return {r["task_id"]: r for r in rows}


def frozen_sources(tarball, paths):
    """{path: (file_sha256, tk_source)} for the record paths wanted, streamed
    out of the freeze-129 zstd tarball (paths inside are relative to
    regen_v04/). Raises when the tarball's sha256 does not match its
    `.sha256` file."""
    want = set(paths)
    side = tarball + ".sha256"
    if os.path.exists(side):
        expect = open(side).read().split()[0]
        if sha256_file(tarball) != expect:
            raise ValueError(f"{tarball}: sha256 does not match {side}")
    out = {}
    z = subprocess.Popen(["zstd", "-dc", tarball], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=z.stdout, mode="r|") as tf:
            for m in tf:
                if m.name in want and m.isfile():
                    data = tf.extractfile(m).read()
                    out[m.name] = (hashlib.sha256(data).hexdigest(), json.loads(data)["tk_source"])
    finally:
        z.stdout.close()
        z.wait()
    return out


def reconcile(corpus, freeze_manifest, freeze_tarball=None):
    ledger_path = os.path.join(corpus, "ledger", "rewrite_131.jsonl")
    if not os.path.exists(ledger_path):
        raise FileNotFoundError(ledger_path)
    ledger = read_jsonl(ledger_path)
    freeze = by_task(read_jsonl(freeze_manifest))
    man = by_task(read_jsonl(os.path.join(corpus, "MANIFEST.jsonl")))
    replaced = os.path.join(corpus, "audit", "replaced", "131")
    archived = {f[:-3] for f in os.listdir(replaced) if f.endswith(".tk")} if os.path.isdir(replaced) else set()

    waves = collections.OrderedDict()
    rows_by_id = collections.OrderedDict()
    for r in ledger:
        w = waves.setdefault(r["wave"], {"rows": 0, "status": collections.Counter(), "ids": set(),
                                         "first_ts": r.get("ts"), "last_ts": r.get("ts"),
                                         "stories": collections.Counter()})
        w["rows"] += 1
        w["status"][r["status"]] += 1
        w["ids"].add(r["task_id"])
        w["stories"][r.get("story") or "(none)"] += 1
        w["first_ts"] = min(w["first_ts"], r.get("ts")) if r.get("ts") else w["first_ts"]
        w["last_ts"] = max(w["last_ts"], r.get("ts")) if r.get("ts") else w["last_ts"]
        rows_by_id.setdefault(r["task_id"], []).append(r)

    rewritten_all = {t for t, rows in rows_by_id.items() if any(r["status"] == "rewritten" for r in rows)}
    frozen = {}
    if freeze_tarball:
        frozen = frozen_sources(freeze_tarball, [freeze[t]["path"] for t in rewritten_all if t in freeze])
    problems = []
    outside = []
    multi_wave = []
    prov_wave = collections.Counter()
    rewritten_ids = []
    for tid, rows in rows_by_id.items():
        rw = [r for r in rows if r["status"] == "rewritten"]
        if not rw:
            continue
        rewritten_ids.append(tid)
        fz = freeze.get(tid)
        if fz is None and tid not in man:
            outside.append({"task_id": tid, "waves": [r["wave"] for r in rw],
                            "archived": tid in archived})
            continue
        if len({r["wave"] for r in rw}) > 1 or len(rw) > 1:
            multi_wave.append({"task_id": tid, "waves": [r["wave"] for r in rw]})
        if fz is None:
            problems.append({"task_id": tid, "check": "chain", "detail": "not in freeze_129 manifest"})
        elif rw[0]["prev_sha256"] != fz["sha256"]:
            problems.append({"task_id": tid, "check": "chain",
                             "detail": "first prev_sha256 != freeze_129 sha"})
        for a, b in zip(rw, rw[1:]):
            if b["prev_sha256"] != a["new_sha256"]:
                problems.append({"task_id": tid, "check": "chain",
                                 "detail": f"{b['wave']} prev_sha256 != {a['wave']} new_sha256"})
        m = man.get(tid)
        if m is None:
            problems.append({"task_id": tid, "check": "manifest", "detail": "not in MANIFEST.jsonl"})
            continue
        path = os.path.join(corpus, m["path"])
        if not os.path.exists(path):
            problems.append({"task_id": tid, "check": "record", "detail": "record file missing"})
            continue
        disk = sha256_file(path)
        if disk != rw[-1]["new_sha256"]:
            problems.append({"task_id": tid, "check": "chain", "detail": "record file sha != last new_sha256"})
        if m.get("sha256") != disk:
            problems.append({"task_id": tid, "check": "manifest", "detail": "MANIFEST sha != record file sha"})
        if tid not in archived:
            problems.append({"task_id": tid, "check": "archive", "detail": "no audit/replaced/131 original"})
        elif freeze_tarball and fz is not None:
            got = frozen.get(fz["path"])
            if got is None:
                problems.append({"task_id": tid, "check": "archive", "detail": "record not in freeze tarball"})
            elif got[0] != fz["sha256"]:
                problems.append({"task_id": tid, "check": "archive",
                                 "detail": "freeze tarball record sha != freeze_129 manifest sha"})
            elif open(os.path.join(replaced, tid + ".tk"), encoding="utf-8").read() != got[1]:
                problems.append({"task_id": tid, "check": "archive",
                                 "detail": "archived bytes != frozen tk_source"})
        rec = json.load(open(path))
        block = (rec.get("regen") or {}).get("rewrite131") or {}
        prov_wave[block.get("wave") or "(none)"] += 1
        if block.get("wave") != rw[-1]["wave"]:
            problems.append({"task_id": tid, "check": "provenance",
                             "detail": f"regen.rewrite131.wave {block.get('wave')!r} != last ledger wave {rw[-1]['wave']!r}"})

    stray = sorted(archived - rewritten_all)
    for tid in stray:
        problems.append({"task_id": tid, "check": "archive", "detail": "archived original with no rewritten ledger row"})
    wave_out = {}
    for name, w in waves.items():
        wave_out[name] = {"ledger_rows": w["rows"], "status": dict(w["status"]),
                          "unique_task_ids": len(w["ids"]), "stories": dict(w["stories"]),
                          "first_ts": w["first_ts"], "last_ts": w["last_ts"]}
    problem_counts = collections.Counter(p["check"] for p in problems)
    return {
        "story": STORY,
        "date": time.strftime("%Y-%m-%d"),
        "ledger": os.path.relpath(ledger_path, os.path.dirname(os.path.dirname(corpus))),
        "ledger_rows": len(ledger),
        "waves": wave_out,
        "freeze_tarball": os.path.basename(freeze_tarball) if freeze_tarball else None,
        "archive_checked_against_freeze_bytes": len(frozen),
        "rewritten_records": len(rewritten_ids),
        "rewritten_records_in_regen_v04": len(rewritten_ids) - len(outside),
        "outside_regen_v04": outside,
        "archived_originals": len(archived),
        "rewritten_by_more_than_one_wave": multi_wave,
        "provenance_block_wave_counts": dict(prov_wave),
        "problems": problems,
        "problem_counts": dict(problem_counts),
        "ok": not problems,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--freeze-manifest", default=FREEZE_MANIFEST)
    ap.add_argument("--freeze-tarball", default=FREEZE_TARBALL,
                    help="freeze-129 byte snapshot; '' skips the archived-bytes check")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)
    res = reconcile(a.corpus, a.freeze_manifest, a.freeze_tarball or None)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
        f.write("\n")
    brief = {k: res[k] for k in ("ledger_rows", "rewritten_records", "rewritten_records_in_regen_v04",
                                 "archived_originals", "archive_checked_against_freeze_bytes",
                                 "provenance_block_wave_counts", "problem_counts", "ok")}
    brief["waves"] = {k: {"rows": v["ledger_rows"], "status": v["status"], "ids": v["unique_task_ids"]}
                      for k, v in res["waves"].items()}
    print(json.dumps(brief, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
