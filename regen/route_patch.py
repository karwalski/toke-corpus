#!/usr/bin/env python3
"""Bucket-routing patches for the 131.13 pattern sweep (131.43 / 131.68 / 131.83).

A story that routes banked records into a wave does it the way 131.42 and
131.67 did: it writes `audit/pattern_sweep.<story>.patch.jsonl`, rows in the
131.13 sweep schema, and the wave prep reads the concatenation
(`pattern_common.load_bucket`: a later line for the same task_id wins). No
record is touched.

Because the later line WINS, a patch row must carry everything the earlier
lines said about that record, or applying it silently drops another story's
routing (a 131.42 gamed-err row overwritten by a 131.43 signature row would
lose its gamed-err instruction). `merge_row` therefore starts from the
record's CURRENT row — `pattern_sweep.merged.jsonl` (sweep + every applied
patch) plus any patches named in `extra` — appends its violation, and joins
the bucket reasons.

Apply order, after the patches already in pattern_sweep.merged.jsonl
(131.42, 131.67, 131.15.looprebuild): APPLY_ORDER below.
"""
import json
import os
import time

APPLY_ORDER = ("131.43", "131.68", "131.83")
BASE_PATCHES = ("131.42", "131.67", "131.15.looprebuild")


def patch_path(corpus, story):
    return os.path.join(corpus, "audit", f"pattern_sweep.{story}.patch.jsonl")


def apply_command(stories=APPLY_ORDER):
    parts = ["audit/pattern_sweep.jsonl"] + [f"audit/pattern_sweep.{s}.patch.jsonl"
                                             for s in BASE_PATCHES + tuple(stories)]
    return "cat " + " ".join(parts) + " > audit/pattern_sweep.merged.jsonl"


def _read(path, out):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                out[r["task_id"]] = r


def current_rows(corpus, extra=()):
    """{task_id: latest sweep row}: pattern_sweep.merged.jsonl when present
    (else pattern_sweep.jsonl), then each story patch in `extra`, in order."""
    audit = os.path.join(corpus, "audit")
    rows = {}
    merged = os.path.join(audit, "pattern_sweep.merged.jsonl")
    _read(merged if os.path.exists(merged) else os.path.join(audit, "pattern_sweep.jsonl"), rows)
    for story in extra:
        _read(patch_path(corpus, story), rows)
    return rows


def merge_row(base, tid, story, rule, bucket, reason, pattern_ids=(), category=None,
              task_type=None, ts=None, keep_buckets=()):
    """Patch row for tid: base row (or a minimal one) + one violation per
    pattern id (or one bare one), bucket set unless the base bucket is in
    keep_buckets, bucket_reason joined with the base's own story reason."""
    row = dict(base or {"task_id": tid, "category": category, "task_type": task_type,
                        "source": "regen"})
    viol = list(row.get("violations") or [])
    for pid in (pattern_ids or (None,)):
        v = {"rule": rule, "severity": "error", "pattern_id": pid}
        if v not in viol:
            viol.append(v)
    pids = list(row.get("pattern_ids") or [])
    pids += [p for p in pattern_ids if p not in pids]
    prev_reason = row.get("bucket_reason") if str(row.get("story")) not in ("131.13", "None") else None
    new_reason = f"{story}: {reason}"
    prev_bucket = row.get("bucket")
    row.update({
        "task_id": tid, "ts": int(ts or time.time()), "story": story,
        "violations": viol, "pattern_ids": pids,
        "bucket": prev_bucket if prev_bucket in keep_buckets else bucket,
        "bucket_reason": f"{prev_reason} | {new_reason}" if prev_reason else new_reason,
        "would_bucket": prev_bucket if base else None,
        "patch_of": (f"{base.get('story')} row" if base else None),
    })
    return row


def write_patch(corpus, story, rows):
    path = patch_path(corpus, story)
    with open(path, "w") as f:
        for r in sorted(rows, key=lambda r: r["task_id"]):
            f.write(json.dumps(r) + "\n")
    return path
