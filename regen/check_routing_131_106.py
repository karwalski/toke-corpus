#!/usr/bin/env python3
"""Story 131.106 — banked records that fail `tkc --check` must be routed to a
wave that will fix them, and a gate that keeps it that way.

Z91 (127.304 sweep) found 176 live records (171 regen + 5 library) banked as
accepted that fail `tkc --check` on toke main and integrate alike, and 53 of
them routed LEAVE / AUTO / AGENT-without-saying-why. Neither LEAVE nor EXEMPT
changes a record, and the AUTO wave (`tkc --lint --fix`) cannot fix a type
error (131.14 rejects them as `preexisting:compile`).

  run    `tkc --check` (pinned binary, 131.39) over every live source:
         MANIFEST.jsonl records (tk_source) + shards/library_131.jsonl
         source_program (with its --allow-* build_flags).
         -> corpus/regen_v04/audit/check_routing_131.106.jsonl
            (first line: {"_meta": tkc stamp}; then one row per record)
  route  every failing banked record that is not already REGEN gets a patch row
         (route_patch.merge_row on its current merged row — the 131.64 re-run
         plus the six 2026-09 patches, replayed by route_merge.py), carrying a
         `does-not-compile` violation and the diagnostics in bucket_reason, so
         the 131.15 prompt says why ("Why this record was routed here").
         Bucket by record class (the error classes are listed in the summary):
           REGEN  the REGEN class (regen A-*/full_program): 131.16 regenerates
                  the whole class with card v2, whatever its error
           AGENT  everything else. 131.42/131.68/131.83 sent records WITHOUT
                  test cases to REGEN, but 131.16 regenerates only the class
                  above, so for a migrate_fix or D-side record REGEN is a dead
                  end (131.109). The 131.15 bank (check_pattern) and the 131.17
                  bank (bank_library) compile and build every candidate and
                  run its tests when the spec has them, so a banked rewrite
                  is guaranteed to pass --check; compile-only records get the
                  same (compile-only) verification every AGENT rewrite of a
                  record without tests gets (diff_check `unverifiable`).
         -> corpus/regen_v04/audit/pattern_sweep.131.106.patch.jsonl
  gate   exit 1 if any banked record that fails --check is routed LEAVE,
         EXEMPT or AUTO in audit/pattern_sweep.merged.jsonl. `--live` re-runs
         the check first with the tkc given (the gate for "the current tkc").
         regen/tests/test_check_routing_131_106.py runs the same gate over the
         on-disk rows.

Summary -> regen/freeze/check_routing_<story>.json. Reads records; writes
nothing under corpus/regen_v04/<CAT>/.

Later checker changes (131.111). When a newer tkc rejects more banked
records, route them under that story: `route --story 131.111` writes
audit/pattern_sweep.131.111.patch.jsonl holding ONLY the failing records that
no earlier check-routing patch (CHECK_STORIES before it) routed, so the
131.106 patch stays the record of what 131.106 found. route_merge.PATCHES
replays it after 131.105. 131.114 (2026-11-09) did the same for the three
records toke 138.22 (expression-if tails must agree, past the 131.103 pin)
newly rejects; its check ran with a tkc built from toke main 034332b8.

--tkc defaults to the corpus pin (corpus_pin.default_tkc(): $TKC, else
~/tk/archive/pins/tkc-131.103/tkc, else ~/tk/toke/tkc).

Usage (toke-corpus root):
  python3 regen/check_routing_131_106.py run   [--tkc PATH] [--toke-repo DIR] [--jobs 2]
  python3 regen/check_routing_131_106.py route [--story 131.111 --compare IDS]
  python3 regen/route_merge.py                 # rebuild merged with the new patch
  python3 regen/check_routing_131_106.py gate  [--live --tkc PATH]
"""
import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus_pin
import route_merge
import route_patch
import tkc_pin

STORY = "131.106"
RULE = "does-not-compile"
CORPUS = route_merge.CORPUS
FORBIDDEN = ("LEAVE", "EXEMPT", "AUTO")           # buckets that cannot fix a compile failure
ROWS_NAME = f"check_routing_{STORY}.jsonl"
# every story that routes --check failures with this tool, in route_merge order
CHECK_STORIES = ("131.106", "131.111", "131.114")


def summary_path(story=STORY):
    return os.path.join(HERE, "freeze", f"check_routing_{story}.json")


SUMMARY = summary_path()
TS = 1794182400                                   # 2026-11-09T00:00:00Z: fixed so the patch is reproducible


def rows_path(corpus):
    return os.path.join(corpus, "audit", ROWS_NAME)


# ------------------------------------------------------------------ run ---
def jobs(corpus):
    with open(os.path.join(corpus, "MANIFEST.jsonl")) as f:
        for line in f:
            e = json.loads(line)
            yield {"task_id": e["task_id"], "source": "regen", "category": e.get("category"),
                   "task_type": e.get("task_type"),
                   "path": os.path.join(corpus, e.get("path") or f"{e['category']}/{e['task_id']}.json"),
                   "flags": []}
    lib = os.path.join(corpus, "shards", "library_131.jsonl")
    if os.path.exists(lib):
        with open(lib) as f:
            for line in f:
                s = json.loads(line)
                yield {"task_id": s["task_id"], "source": "library", "category": s.get("category"),
                       "task_type": s.get("task_type", "stdin_program"), "path": s["source_program"],
                       "flags": [x for x in (s.get("build_flags") or []) if x.startswith("--allow")]}


def diagnostics(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("error_code") and d.get("severity") == "error":
            pos = d.get("pos") or {}
            out.append({"code": d["error_code"], "line": pos.get("line"), "msg": d.get("message")})
    return out


def check_one(job, tkc, tmpdir):
    row = {k: job[k] for k in ("task_id", "source", "category", "task_type")}
    try:
        if job["source"] == "library":
            with open(job["path"], "rb") as f:
                src = f.read().decode("utf-8", "replace")
            row["banked_accepted"] = True          # in shards/library_131.jsonl = ingested (131.18)
        else:
            with open(job["path"]) as f:
                rec = json.load(f)
            src = rec.get("tk_source") or ""
            v, j = rec.get("validation") or {}, rec.get("judge") or {}
            row["banked_accepted"] = v.get("compiler_exit_code") == 0 and j.get("accepted") is True
    except (OSError, ValueError) as e:
        row.update({"rc": None, "error": f"read: {e}"})
        return row
    row["source_sha256"] = hashlib.sha256(src.encode()).hexdigest()
    fd, fn = tempfile.mkstemp(suffix=".tk", dir=tmpdir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        p = subprocess.run([tkc, "--check", *job["flags"], fn], capture_output=True, text=True,
                           errors="replace", timeout=120, cwd=tmpdir, check=False)
        row["rc"] = p.returncode
        row["errors"] = diagnostics(p.stdout + "\n" + p.stderr) if p.returncode else []
    except subprocess.TimeoutExpired:
        row.update({"rc": None, "error": "timeout"})
    finally:
        os.unlink(fn)
    return row


def run_check(corpus, tkc, toke_repo, n_jobs=2):
    with tkc_pin.pin(toke_repo=toke_repo, tkc=tkc) as p:
        stamp = p.stamp()
        stamp.pop("tkc_pinned_copy", None)
        js = list(jobs(corpus))
        with tempfile.TemporaryDirectory(prefix="check_131_106_") as tmp, \
                ThreadPoolExecutor(max_workers=n_jobs) as ex:
            rows = list(ex.map(lambda j: check_one(j, p.argv0, tmp), js))
    rows.sort(key=lambda r: (r["source"], r["task_id"]))
    out = rows_path(corpus)
    with open(out + ".tmp", "w") as f:
        f.write(json.dumps({"_meta": {"story": STORY, "tkc": stamp, "records": len(rows)}}) + "\n")
        f.writelines(json.dumps(r) + "\n" for r in rows)
    os.replace(out + ".tmp", out)
    return stamp, rows


def read_check(path):
    meta, rows = {}, {}
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if "_meta" in r:
                meta = r["_meta"]
            else:
                rows[r["task_id"]] = r
    return meta, rows


def failing(rows):
    """Banked records whose check failed (rc != 0, incl. timeouts/unreadable)."""
    return {t: r for t, r in rows.items() if r.get("banked_accepted") and r.get("rc") != 0}


# ---------------------------------------------------------------- route ---
def has_tests(tid, row, specs):
    return bool((specs.get(tid) or {}).get("test_cases"))


def in_regen_class(row):
    """pattern_sweep's REGEN carve-out: regen-source A-*/full_program."""
    return (row.get("source") == "regen" and (row.get("category") or "").startswith("A-")
            and row.get("task_type") == "full_program")


def decide(row):
    """REGEN for the REGEN class (131.16 regenerates it whole); AGENT for
    everything else — 131.16 has no path for other records, so REGEN would
    leave them broken (131.109), while the 131.15 / 131.17 banks compile and
    build every candidate (and run its tests when the spec has them)."""
    return "REGEN" if in_regen_class(row) else "AGENT"


def reason_for(r, tkc_desc, tests=True, story=STORY):
    errs = r.get("errors") or []
    if not errs:
        what = r.get("error") or f"exit {r.get('rc')}"
    else:
        shown = "; ".join(f"{e['code']} line {e['line']}: {e['msg']}" for e in errs[:3])
        what = shown + (f" (+{len(errs) - 3} more)" if len(errs) > 3 else "")
    return (f"banked as accepted but does not compile on {tkc_desc}: {what}. It compiled on the tkc "
            "it was banked with; a later checker change rejects it. The rewrite MUST make it pass "
            "`tkc --check` with every printed line unchanged"
            + ("" if tests else " (no test cases: keep the program's logic exactly, change only what the "
               "diagnostic names)")
            + f" (regen/freeze/check_routing_{story}.json)")


def route_rows(fail, cur, specs, tkc_desc, ts=TS, story=STORY):
    out, dest = [], collections.Counter()
    for tid in sorted(fail):
        r = fail[tid]
        base = cur.get(tid)
        bucket = decide(r)
        row = route_patch.merge_row(base, tid, story, RULE, bucket,
                                    reason_for(r, tkc_desc, has_tests(tid, r, specs), story),
                                    category=r.get("category"), task_type=r.get("task_type"), ts=ts)
        row.setdefault("source", r.get("source"))
        out.append(row)
        dest[((base or {}).get("bucket"), row["bucket"])] += 1
    return out, dest


def merged_before(corpus, story=STORY):
    """Current routing without this story's patch (and anything after it)."""
    _, merged, _, _ = route_merge.build(corpus, patches=_patches_upto(story, include=False))
    return merged


def routed_earlier(corpus, story):
    """task_ids an earlier check-routing story already routed (its patch on disk)."""
    ids = set()
    for s in CHECK_STORIES[:CHECK_STORIES.index(story)]:
        ids |= set(route_merge.read_rows(route_patch.patch_path(corpus, s)))
    return ids


def read_compare(path):
    """Expected failing ids: a Z91 check_delta_summary.json (127.304) or a text
    file with one id per line (Z92's corpus_mapget_ids.txt; a MANIFEST `id`
    prefix such as `P3-` is dropped)."""
    if path.endswith(".json"):
        with open(path) as f:
            return json.load(f)["preexisting_fail_both"]["task_ids"]
    with open(path) as f:
        ids = [x.strip() for x in f if x.strip()]
    return [x.split("-", 1)[1] if x.startswith("P") and x[1:2].isdigit() and "-" in x else x for x in ids]


def _patches_upto(story, include):
    i = [s for s, _ in route_merge.PATCHES].index(story)
    return route_merge.PATCHES[:i + 1 if include else i]


# ----------------------------------------------------------------- gate ---
def gate(check_rows, merged):
    """[(task_id, bucket, codes)] for every banked failing record routed to a
    bucket that cannot fix it (FORBIDDEN) or not routed at all."""
    bad = []
    for tid, r in sorted(failing(check_rows).items()):
        b = (merged.get(tid) or {}).get("bucket")
        if b is None or b in FORBIDDEN:
            bad.append((tid, b, sorted({e["code"] for e in r.get("errors") or []})))
    return bad


# -------------------------------------------------------------- summary ---
def summarise(meta, rows, cur, patch, dest, specs, merged_after=None, compare_ids=None,
              story=STORY, earlier=()):
    fail_all = failing(rows)
    fail = {t: r for t, r in fail_all.items() if t not in set(earlier)}
    codes = collections.Counter()
    first = collections.Counter()
    for r in fail.values():
        cs = sorted({e["code"] for e in r.get("errors") or []})
        codes.update(cs)
        first[(r.get("errors") or [{"code": r.get("error") or "?"}])[0]["code"]] += 1
    by_class = collections.Counter()
    for t, r in fail.items():
        by_class[f"{r['source']}/{r.get('task_type')}/{(cur.get(t) or {}).get('bucket')}"] += 1
    z91 = None
    if compare_ids is not None:
        ids = set(compare_ids)
        z91 = {"expected_ids": len(ids), "still_failing": len(ids & set(fail)),
               "now_passing": sorted(ids - set(fail)), "new_failures": sorted(set(fail) - ids)}
    out = {
        "story": story,
        "date": time.strftime("%Y-%m-%d", time.gmtime(TS)),
        "tkc": meta.get("tkc"),
        "records_checked": len(rows),
        "rc": dict(collections.Counter(f"{r['source']} rc={r.get('rc')}" for r in rows.values())),
        "banked_failing": {"records": len(fail),
                           "by_source": dict(collections.Counter(r["source"] for r in fail.values())),
                           "records_by_error_code": dict(codes.most_common()),
                           "records_by_first_error_code": dict(first.most_common()),
                           "by_source_task_type_bucket_before": dict(sorted(by_class.items())),
                           "with_test_cases": sum(1 for t in fail if (specs.get(t) or {}).get("test_cases")),
                           "task_ids": sorted(fail)},
        "vs_expected": z91,
        "routing": {"patch": f"audit/pattern_sweep.{story}.patch.jsonl", "rows": len(patch),
                    "bucket_before_to_after": {f"{a}->{b}": n for (a, b), n in sorted(dest.items(), key=str)},
                    "rule": "REGEN for the REGEN class (regen A-*/full_program, regenerated whole by 131.16); "
                            "AGENT for every other record (no regeneration path exists for them: 131.109)"},
    }
    if earlier:
        stale = sorted(set(earlier) - set(fail_all))
        out["earlier_check_routing"] = {
            "stories": [s for s in CHECK_STORIES[:CHECK_STORIES.index(story)]],
            "records_routed": len(set(earlier)),
            "still_failing": len(set(earlier) & set(fail_all)),
            "now_passing": stale}
    if merged_after is not None:
        bad = gate(rows, merged_after)
        out["gate_after"] = {"forbidden": list(FORBIDDEN), "violations": len(bad), "first": bad[:20],
                             "failing_by_bucket": dict(collections.Counter(
                                 (merged_after.get(t) or {}).get("bucket") for t in fail_all))}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("run", "route", "gate"))
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--tkc", default=None, help="default: corpus_pin.default_tkc() (the 131.103 pin)")
    ap.add_argument("--toke-repo", default=None)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--live", action="store_true", help="gate: re-run the check first")
    ap.add_argument("--story", default=STORY, choices=CHECK_STORIES,
                    help="route: the story routing the failures (a later one routes only new failures)")
    ap.add_argument("--summary", default=None, help="default regen/freeze/check_routing_<story>.json")
    ap.add_argument("--compare", default=None,
                    help="route: expected failing ids (Z91 check_delta_summary.json, or one id per line)")
    a = ap.parse_args(argv)
    if a.cmd == "run" or (a.cmd == "gate" and a.live):
        stamp, _ = run_check(a.corpus, a.tkc or corpus_pin.default_tkc(), a.toke_repo, a.jobs)
        print(json.dumps({"rows": rows_path(a.corpus), "tkc": stamp.get("tkc_version")}))
        if a.cmd == "run":
            return 0
    meta, rows = read_check(rows_path(a.corpus))
    if a.cmd == "route":
        import audit
        specs = audit.load_specs(a.corpus)
        story = a.story
        cur = merged_before(a.corpus, story)
        tkc = meta.get("tkc") or {}
        desc = f"{tkc.get('tkc_version')} (bin {str(tkc.get('tkc_bin_sha'))[:12]})"
        earlier = routed_earlier(a.corpus, story)
        fail = {t: r for t, r in failing(rows).items() if t not in earlier}
        patch, dest = route_rows(fail, cur, specs, desc, story=story)
        path = route_patch.write_patch(a.corpus, story, patch)
        _, merged_after, _, _ = route_merge.build(a.corpus, patches=_patches_upto(story, include=True))
        ids = read_compare(a.compare) if a.compare else None
        s = summarise(meta, rows, cur, patch, dest, specs, merged_after, ids, story, earlier)
        with open(a.summary or summary_path(story), "w") as f:
            json.dump(s, f, indent=1)
            f.write("\n")
        print(json.dumps({"patch": path, "rows": len(patch), "dest": s["routing"]["bucket_before_to_after"],
                          "gate_after": s["gate_after"]["violations"]}))
        return 0
    merged = route_merge.read_rows(os.path.join(a.corpus, "audit", "pattern_sweep.merged.jsonl"))
    bad = gate(rows, merged)
    print(json.dumps({"failing_banked": len(failing(rows)), "violations": len(bad), "first": bad[:20],
                      "tkc": (meta.get("tkc") or {}).get("tkc_version")}))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
