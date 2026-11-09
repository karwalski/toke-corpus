#!/usr/bin/env python3
"""Rebuild `audit/pattern_sweep.merged.jsonl` — the routing every Epic 131
wave preps from — over a FRESH sweep, with every routing patch replayed in
README order (131.15 prep, Z95).

Why not `cat sweep + patches`. A patch row is a whole sweep row (the record's
row as it stood when the story routed it, plus that story's violation and
bucket), and the later line wins. Concatenated after a NEWER sweep, a patch
row would silently put the OLD sweep's violations, metrics and bucket back —
e.g. the 131.43 rows were built on 131.13 rows whose single-use-let hits the
131.14 auto wave has since fixed. The on-disk merged file of 2026-09-19 was
also missing the 131.43 (209) and 131.83 (8) patches entirely (Z91, 131.64).

Replay instead. For each patch, in order, for each row p of it:
  * delta = p's violations that are not in the row the patch was built on
    (its ORIGIN sweep row for that record and every earlier patch row for
    it) — i.e. exactly what the routing story added;
  * the record's current row (fresh sweep + patches replayed so far) gets
    delta appended, p's pattern ids unioned in, p's bucket, p's own
    bucket_reason segment joined onto the current reason, story = p's story,
    would_bucket = the bucket it had before, and `patches` (the list of
    stories applied to it);
  * REGEN is sticky for the REGEN class (A-*/full_program regen records,
    pattern_sweep's carve-out): 131.16 regenerates the whole class, so the
    record meets any rewrite story's gate (signature_gate_131_43 and
    route_patch keep_buckets follow this). A REGEN row OUTSIDE the class has
    no regeneration wave (131.109), so a later patch may move it.
Everything else — metrics, lint, sweep violations, exemptions — comes from the
fresh sweep row.

Origins. The six 2026-09 patches (131.42, 131.67, 131.15.looprebuild, 131.43,
131.68, 131.83) were built on the 131.13 sweep (`audit/pattern_sweep.jsonl`).
Patches written after this tool (131.106, 131.105, 131.111, 131.114, 131.117) are built with
route_patch.merge_row on the merged file this tool writes, whose rows come
from the 131.64 re-run — their origin is that sweep.

Usage (from the toke-corpus root):
  python3 regen/route_merge.py                       # default paths, writes merged + summary
  python3 regen/route_merge.py --check               # rebuild in memory, compare to the file on disk
"""
import argparse
import collections
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import route_patch

CORPUS = os.environ.get("TOKE_CORPUS_DIR",           # same default as pattern_common.CORPUS
                        os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04"))
BUCKETS = ("EXEMPT", "AUTO", "AGENT", "LEAVE", "REGEN")
SCOPES = ("regen", "library")
OLD_SWEEP = "pattern_sweep.jsonl"                 # 131.13 (2026-09-18)
NEW_SWEEP = "pattern_sweep.131.64.jsonl"          # 131.64 re-run (Z91, tkc 9f1eb921)
# (story, origin sweep) in README apply order
PATCHES = (("131.42", OLD_SWEEP), ("131.67", OLD_SWEEP), ("131.15.looprebuild", OLD_SWEEP),
           ("131.43", OLD_SWEEP), ("131.68", OLD_SWEEP), ("131.83", OLD_SWEEP),
           ("131.106", NEW_SWEEP), ("131.105", NEW_SWEEP), ("131.111", NEW_SWEEP),
           ("131.114", NEW_SWEEP), ("131.117", NEW_SWEEP))
SUMMARY = os.path.join(HERE, "freeze", "route_merge_131.15.json")


def read_rows(path):
    """{task_id: row}, later line wins; {} when the file is absent."""
    rows = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    rows[r["task_id"]] = r
    return rows


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _vkey(v):
    return json.dumps(v, sort_keys=True) if isinstance(v, dict) else json.dumps(v)


def _segments(reason):
    return [s.strip() for s in (reason or "").split(" | ") if s.strip()]


def replay(base, patches, origins):
    """base: {tid: row} (the fresh sweep). patches: [(story, {tid: row}, origin_name)].
    origins: {origin_name: {tid: row}}. Returns (merged {tid: row}, stats)."""
    cur = {t: dict(r) for t, r in base.items()}
    built_on = {}                          # tid -> violation keys of every earlier patch row
    stats = collections.OrderedDict()
    for story, rows, origin in patches:
        st = collections.Counter()
        for tid in sorted(rows):
            p = rows[tid]
            if tid not in cur:
                st["unknown_task_id"] += 1
                continue
            seen = {_vkey(v) for v in (origins.get(origin, {}).get(tid, {}).get("violations") or [])}
            seen |= built_on.get(tid, set())
            delta = [v for v in (p.get("violations") or []) if _vkey(v) not in seen]
            row = dict(cur[tid])
            have = {_vkey(v) for v in row.get("violations") or []}
            row["violations"] = list(row.get("violations") or []) + [v for v in delta if _vkey(v) not in have]
            pids = list(row.get("pattern_ids") or [])
            pids += [x for x in (p.get("pattern_ids") or []) if x not in pids]
            row["pattern_ids"] = pids
            prev_bucket = row.get("bucket")
            want = p.get("bucket")
            if prev_bucket == "REGEN" and want != "REGEN" and regen_class(row):
                st["regen_kept"] += 1
                want = "REGEN"
            prev_reason = row.get("bucket_reason") if row.get("patches") else None
            segs = _segments(prev_reason)
            segs += [s for s in _segments(p.get("bucket_reason")) if s not in segs]
            row.update({"bucket": want, "bucket_reason": " | ".join(segs) or p.get("bucket_reason"),
                        "story": p.get("story", story), "would_bucket": prev_bucket,
                        "patches": list(row.get("patches") or []) + [story],
                        "patch_of": f"{base[tid].get('story')} sweep row ({NEW_SWEEP}) + replayed patches"})
            cur[tid] = row
            built_on.setdefault(tid, set()).update(_vkey(v) for v in p.get("violations") or [])
            st["rows"] += 1
            st["moved" if prev_bucket != want else "same_bucket"] += 1
            st[f"{prev_bucket}->{want}"] += 1
        stats[story] = dict(st)
    return cur, stats


def counts(rows):
    out = {}
    for s in SCOPES + ("all",):
        c = collections.Counter(r.get("bucket") for r in rows.values() if s == "all" or r.get("source") == s)
        out[s] = {b: c.get(b, 0) for b in BUCKETS}
        out[s]["records"] = sum(c.values())
    return out


regen_class = route_patch.regen_class          # the shared class rule (131.109)


def regen_outside_class(rows):
    """task_ids routed REGEN outside the REGEN class: 131.16 has no path for
    them, so every router must send them to AGENT (131.109). Must be []."""
    return sorted(t for t, r in rows.items() if r.get("bucket") == "REGEN" and not regen_class(r))


def build(corpus, patches=PATCHES):
    audit = os.path.join(corpus, "audit")
    base = read_rows(os.path.join(audit, NEW_SWEEP))
    if not base:
        raise SystemExit(f"route_merge: {os.path.join(audit, NEW_SWEEP)} missing or empty")
    origins = {OLD_SWEEP: read_rows(os.path.join(audit, OLD_SWEEP)), NEW_SWEEP: base}
    plist, files = [], []
    for story, origin in patches:
        path = route_patch.patch_path(corpus, story)
        rows = read_rows(path)
        plist.append((story, rows, origin))
        files.append({"story": story, "file": os.path.basename(path), "origin": origin,
                      "present": os.path.exists(path), "rows": len(rows),
                      "sha256": sha256_file(path) if os.path.exists(path) else None})
    merged, stats = replay(base, plist, origins)
    return base, merged, stats, files


def write_rows(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(json.dumps(rows[tid]) + "\n" for tid in sorted(rows))
    os.replace(tmp, path)


def summary(corpus, base, merged, stats, files, out_path):
    audit = os.path.join(corpus, "audit")
    trans = collections.Counter(f"{base[t]['bucket']}->{merged[t]['bucket']}" for t in merged
                                if base[t]["bucket"] != merged[t]["bucket"])
    regen_off_class = regen_outside_class(merged)
    return {
        "story": "131.15 (prep: routing before the wave; Z95)",
        "what": "audit/pattern_sweep.merged.jsonl rebuilt by regen/route_merge.py: the 131.64 re-run sweep "
                "with every routing patch replayed in README order (delta violations + bucket + reason; "
                "REGEN sticky). Replaces the 2026-09-19 merged file, which was the 131.13 sweep + 131.42 + "
                "131.67 + 131.15.looprebuild only (backed up as audit/pattern_sweep.merged.20260919.jsonl).",
        "base_sweep": {"file": NEW_SWEEP, "sha256": sha256_file(os.path.join(audit, NEW_SWEEP)),
                       "rows": len(base), "made_by": "Z91 131.64 re-run, tkc 3.1.0 9f1eb921 "
                                                     "(regen/freeze/pattern_sweep_131.64.json)"},
        "origin_sweep_131_13": {"file": OLD_SWEEP, "sha256": sha256_file(os.path.join(audit, OLD_SWEEP))},
        "patches": files,
        "replay": stats,
        "buckets": {"rerun": counts(base), "merged": counts(merged)},
        "transitions_rerun_to_merged": dict(sorted(trans.items(), key=lambda kv: -kv[1])),
        "regen_outside_regen_class": {
            "records": len(regen_off_class),
            "note": "REGEN rows that are not A-*/full_program. 131.16 regenerates only the A-*/full_program "
                    "class, so every router routes a record outside it to AGENT (route_patch.route_bucket, "
                    "131.109) and regen/tests/test_route_merge.py fails if this is ever non-zero",
            "by_category_prefix": dict(collections.Counter(t.split("-")[0] for t in regen_off_class)),
            "task_ids": regen_off_class},
        "merged_file": {"path": "corpus/regen_v04/audit/pattern_sweep.merged.jsonl", "rows": len(merged),
                        "sha256": sha256_file(os.path.join(audit, "pattern_sweep.merged.jsonl"))
                        if os.path.exists(os.path.join(audit, "pattern_sweep.merged.jsonl")) else None},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--out", default=None, help="default <corpus>/audit/pattern_sweep.merged.jsonl")
    ap.add_argument("--summary", default=SUMMARY)
    ap.add_argument("--check", action="store_true",
                    help="rebuild in memory and exit 1 if the merged file on disk differs")
    a = ap.parse_args(argv)
    out = a.out or os.path.join(a.corpus, "audit", "pattern_sweep.merged.jsonl")
    base, merged, stats, files = build(a.corpus)
    if a.check:
        disk = read_rows(out)
        diff = sorted(t for t in set(disk) | set(merged) if disk.get(t) != merged.get(t))
        print(json.dumps({"merged": out, "rows": len(merged), "differs": len(diff), "first": diff[:10]}))
        return 1 if diff else 0
    write_rows(out, merged)
    s = summary(a.corpus, base, merged, stats, files, a.summary)
    if a.summary:
        with open(a.summary, "w") as f:
            json.dump(s, f, indent=1)
            f.write("\n")
    print(json.dumps({"merged": out, "buckets": s["buckets"]["merged"]["all"], "replay": stats}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
