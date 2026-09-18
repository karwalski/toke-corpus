#!/usr/bin/env python3
"""131.35 — corpus/regen_v04/MANIFEST.jsonl integrity: check / rebuild / stamp.

Why: the 129.4/129.5 repair wave (bank_repairs.py) replaced ~2,600 record
files without re-stamping MANIFEST.jsonl, so ~11% of manifest `sha256`
values were stale. Every bank path now calls `stamp()` after it writes a
record file, and this tool can verify/rebuild the whole manifest.

Manifest line shape (one JSON object per line, one line per record):
  id, task_id, category, task_type, difficulty, shard   (kept from the old
                                                        line / run_shard)
  path          record path relative to the corpus dir (freeze convention)
  sha256        sha256 of the RECORD FILE BYTES — same meaning as
                regen/freeze/freeze_129_manifest.jsonl and
                ledger/rewrite_131.jsonl {prev,new}_sha256 (131.12)
  source_sha256 sha256 of record["tk_source"] (== regen.source_sha256; this
                is what the pre-131 manifest called `sha256`)
  min_bytes     regen.min_bytes when the record has it
  stamped_at    ISO-8601 UTC of the last stamp

`check` accepts a line as fresh when its `sha256` matches the file bytes OR
(legacy, pre-131 lines) the tk_source sha; legacy matches are counted
separately (`legacy_source_sha`) so a rebuilt manifest reports 0 of them.

Subcommands:
  check    walk {A-*,D-*,L-*}/*.json, diff against MANIFEST.jsonl and the
           freeze-129 manifest; exit 1 when stale/missing/orphan rows exist
  rebuild  write MANIFEST.jsonl fresh from the files (old copy kept as
           MANIFEST.pre131.jsonl unless that backup already exists)
  stamp    update/append the line for one --task-id (the bank-path primitive)

Library use (bank paths):
  m = manifest_tool.Manifest(manifest_path)   # load once
  m.stamp(task_id, rec_path, extra={...})     # per banked record; saves
or the one-shot manifest_tool.stamp(manifest_path, task_id, rec_path, extra).
"""
import argparse, fnmatch, glob, hashlib, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CORPUS = os.path.join(ROOT, "corpus", "regen_v04")
FREEZE_MANIFEST = os.path.join(HERE, "freeze", "freeze_129_manifest.jsonl")
MANIFEST_NAME = "MANIFEST.jsonl"
BACKUP_NAME = "MANIFEST.pre131.jsonl"
RECORD_DIR_GLOBS = ("A-*", "D-*", "L-*")
PRESERVED_FIELDS = ("id", "task_id", "category", "task_type", "difficulty", "shard")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def record_files(corpus):
    """Sorted list of record paths under corpus/{A-*,D-*,L-*}/*.json."""
    files = []
    for g in RECORD_DIR_GLOBS:
        files.extend(glob.glob(os.path.join(corpus, g, "*.json")))
    return sorted(files)


def task_id_of(path):
    return os.path.splitext(os.path.basename(path))[0]


def read_record(path):
    """Return (raw_bytes, record_dict or None)."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw, json.loads(raw)
    except ValueError:
        return raw, None


def build_row(rec_path, corpus, prev=None, extra=None):
    """Build a full manifest row for one record file. `prev` (old row for the
    same task_id) supplies id/category/task_type/difficulty/shard when the
    record itself does not; `extra` overrides (bank-path metadata)."""
    raw, rec = read_record(rec_path)
    rec = rec or {}
    regen = rec.get("regen") or {}
    rel = os.path.relpath(rec_path, corpus)
    tid = task_id_of(rec_path)
    row = {}
    for k in PRESERVED_FIELDS:
        if prev and k in prev:
            row[k] = prev[k]
    row.setdefault("id", rec.get("id") or "P3-" + tid)
    row["task_id"] = rec.get("task_id") or tid
    row.setdefault("category", regen.get("category") or rec.get("category") or rel.split(os.sep)[0])
    row.setdefault("task_type", regen.get("task_type"))
    row.setdefault("difficulty", regen.get("difficulty"))
    row.setdefault("shard", None)
    if extra:
        row.update(extra)
    row["path"] = rel.replace(os.sep, "/")
    row["sha256"] = sha256_bytes(raw)
    src = rec.get("tk_source")
    row["source_sha256"] = sha256_bytes(src.encode()) if isinstance(src, str) else None
    if regen.get("min_bytes") is not None:
        row["min_bytes"] = regen["min_bytes"]
    row["stamped_at"] = now_iso()
    return row


def load_rows(path):
    """All manifest rows in file order ([] when the file is absent)."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def atomic_write_lines(path, lines):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for l in lines:
            f.write(l + "\n")
    os.replace(tmp, path)


class Manifest:
    """MANIFEST.jsonl held in memory: raw lines cached so a stamp only
    re-serialises the touched row, and save() is one atomic rewrite."""

    def __init__(self, path):
        self.path = path
        self.lines = []            # raw line strings, file order
        self.index = {}            # task_id -> line position (last occurrence wins)
        self.positions = {}        # task_id -> [positions] (dups from retried tasks)
        self._load()

    def _load(self):
        self.lines, self.index, self.positions = [], {}, {}
        if not os.path.exists(self.path):
            return
        with open(self.path) as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                tid = json.loads(line)["task_id"]
                self.index[tid] = len(self.lines)
                self.positions.setdefault(tid, []).append(len(self.lines))
                self.lines.append(line)

    def __len__(self):
        return len(self.lines)

    def get(self, task_id):
        pos = self.index.get(task_id)
        return json.loads(self.lines[pos]) if pos is not None else None

    def stamp(self, task_id, rec_path, extra=None, save=True):
        """Replace (or append) the row for task_id with a fresh one computed
        from rec_path. Duplicate rows for the task_id are collapsed."""
        corpus = os.path.dirname(os.path.abspath(self.path))
        row = build_row(rec_path, corpus, prev=self.get(task_id), extra=extra)
        row["task_id"] = task_id
        dup = self.positions.get(task_id, [])
        text = json.dumps(row)
        if dup:
            self.lines[dup[0]] = text
            for i in reversed(dup[1:]):
                del self.lines[i]
            if len(dup) > 1:
                self._reindex()
        else:
            self.index[task_id] = len(self.lines)
            self.positions[task_id] = [len(self.lines)]
            self.lines.append(text)
        if save:
            self.save()
        return row

    def _reindex(self):
        self.index, self.positions = {}, {}
        for i, l in enumerate(self.lines):
            tid = json.loads(l)["task_id"]
            self.index[tid] = i
            self.positions.setdefault(tid, []).append(i)

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        atomic_write_lines(self.path, self.lines)


def stamp(manifest_path, task_id, rec_path, extra=None):
    """One-shot: load the manifest, stamp one task_id, save. Returns the row.
    Bank paths that loop should hold a Manifest() instead (loads once)."""
    return Manifest(manifest_path).stamp(task_id, rec_path, extra=extra)


def find_record(corpus, task_id):
    hits = [p for p in glob.glob(os.path.join(corpus, "*", task_id + ".json"))
            if any(fnmatch.fnmatch(os.path.basename(os.path.dirname(p)), g)
                   for g in RECORD_DIR_GLOBS)]
    if len(hits) != 1:
        raise SystemExit(f"stamp: expected exactly one record for {task_id}, found {hits}")
    return hits[0]


# ---------------------------------------------------------------- check

def check(corpus, manifest_path, freeze_path=None, max_list=50):
    """Diff MANIFEST.jsonl against the record files (and the freeze manifest).
    Returns a report dict; report['counts'] holds the integer buckets."""
    files = record_files(corpus)
    file_sha, src_sha = {}, {}
    for p in files:
        raw, rec = read_record(p)
        tid = task_id_of(p)
        file_sha[tid] = sha256_bytes(raw)
        s = (rec or {}).get("tk_source")
        src_sha[tid] = sha256_bytes(s.encode()) if isinstance(s, str) else None
    rows = load_rows(manifest_path)
    seen = {}
    dup_rows = 0
    for r in rows:
        if r["task_id"] in seen:
            dup_rows += 1
        seen[r["task_id"]] = r
    buckets = {"ok": [], "legacy_source_sha": [], "stale": [], "manifest_without_file": [],
               "missing_from_manifest": []}
    for tid, r in seen.items():
        if tid not in file_sha:
            buckets["manifest_without_file"].append(tid)
        elif r.get("sha256") == file_sha[tid]:
            buckets["ok"].append(tid)
        elif r.get("sha256") == src_sha[tid]:
            buckets["legacy_source_sha"].append(tid)
        else:
            buckets["stale"].append(tid)
    for tid in file_sha:
        if tid not in seen:
            buckets["missing_from_manifest"].append(tid)
    # cross-check against the freeze-129 manifest (file-bytes sha256)
    freeze = {}
    fz = {"freeze_match": [], "freeze_diverged": [], "not_in_freeze": [], "freeze_orphan": [],
          "stale_but_matches_freeze": []}
    if freeze_path and os.path.exists(freeze_path):
        for r in load_rows(freeze_path):
            freeze[r["task_id"]] = r["sha256"]
        stale_set = set(buckets["stale"])
        for tid, sha in file_sha.items():
            if tid not in freeze:
                fz["not_in_freeze"].append(tid)
            elif freeze[tid] == sha:
                fz["freeze_match"].append(tid)
                if tid in stale_set:
                    fz["stale_but_matches_freeze"].append(tid)
            else:
                fz["freeze_diverged"].append(tid)
        for tid in freeze:
            if tid not in file_sha:
                fz["freeze_orphan"].append(tid)
    counts = {k: len(v) for k, v in buckets.items()}
    counts.update({k: len(v) for k, v in fz.items()})
    counts.update({"files": len(files), "manifest_rows": len(rows),
                   "manifest_unique": len(seen), "manifest_dup_rows": dup_rows,
                   "freeze_rows": len(freeze)})
    total = len(files)
    counts["stale_pct"] = round(100.0 * counts["stale"] / total, 2) if total else 0.0
    samples = {k: sorted(v)[:max_list] for k, v in {**buckets, **fz}.items()}
    return {"corpus": corpus, "manifest": manifest_path,
            "freeze_manifest": freeze_path if freeze else None,
            "checked_at": now_iso(), "counts": counts, "samples": samples,
            "clean": counts["stale"] == 0 and counts["missing_from_manifest"] == 0
                     and counts["manifest_without_file"] == 0}


# ---------------------------------------------------------------- rebuild

def rebuild(corpus, manifest_path, backup_name=BACKUP_NAME):
    """Write MANIFEST.jsonl fresh from the record files. Per-task_id fields of
    the old manifest (id/category/task_type/difficulty/shard) are preserved;
    sha256 (file bytes), source_sha256, path, min_bytes, stamped_at are
    (re)computed. The old file is copied to backup_name first (never
    clobbering an existing backup)."""
    old_rows = load_rows(manifest_path)
    old = {}
    for r in old_rows:
        old[r["task_id"]] = r
    backup_path = os.path.join(os.path.dirname(os.path.abspath(manifest_path)), backup_name)
    backed_up = False
    if os.path.exists(manifest_path) and not os.path.exists(backup_path):
        with open(manifest_path, "rb") as s, open(backup_path, "wb") as d:
            d.write(s.read())
        backed_up = True
    files = record_files(corpus)
    lines, new_ids, sha_changed, src_changed = [], [], 0, 0
    written = set()
    for p in files:
        tid = task_id_of(p)
        prev = old.get(tid)
        row = build_row(p, corpus, prev=prev)
        if prev is None:
            new_ids.append(tid)
        else:
            if prev.get("sha256") != row["sha256"]:
                sha_changed += 1
            if prev.get("sha256") != row["source_sha256"] and prev.get("sha256") != row["sha256"]:
                src_changed += 1
        written.add(tid)
        lines.append(json.dumps(row))
    dropped = sorted(t for t in old if t not in written)
    atomic_write_lines(manifest_path, lines)
    return {"manifest": manifest_path, "backup": backup_path if backed_up else None,
            "backup_existed": (not backed_up) and os.path.exists(backup_path),
            "rebuilt_at": now_iso(),
            "counts": {"files": len(files), "rows_written": len(lines),
                       "old_rows": len(old_rows), "old_unique": len(old),
                       "preserved_from_old": len(lines) - len(new_ids),
                       "new_rows": len(new_ids), "dropped_old_rows": len(dropped),
                       "sha256_field_changed": sha_changed,
                       "was_stale_in_old": src_changed},
            "samples": {"new_rows": new_ids[:50], "dropped_old_rows": dropped[:50]}}


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["check", "rebuild", "stamp"])
    ap.add_argument("--corpus", default=CORPUS, help="corpus dir holding MANIFEST.jsonl + A-*/D-*/L-*")
    ap.add_argument("--manifest", help="manifest path (default <corpus>/MANIFEST.jsonl)")
    ap.add_argument("--freeze", default=FREEZE_MANIFEST,
                    help="freeze-129 manifest to cross-check (check only; '' to skip)")
    ap.add_argument("--json", help="also write the report to this path")
    ap.add_argument("--max-list", type=int, default=50, help="task_ids listed per bucket")
    ap.add_argument("--backup-name", default=BACKUP_NAME, help="rebuild: name of the old-manifest copy")
    ap.add_argument("--task-id", help="stamp: record to (re)stamp")
    ap.add_argument("--path", help="stamp: record file (default: <corpus>/*/<task_id>.json)")
    ap.add_argument("--extra", action="append", default=[], metavar="K=V",
                    help="stamp: extra manifest fields (e.g. shard=shard_09)")
    args = ap.parse_args(argv)
    manifest_path = args.manifest or os.path.join(args.corpus, MANIFEST_NAME)

    if args.cmd == "check":
        rep = check(args.corpus, manifest_path, args.freeze or None, args.max_list)
    elif args.cmd == "rebuild":
        rep = rebuild(args.corpus, manifest_path, args.backup_name)
    else:
        if not args.task_id:
            ap.error("stamp needs --task-id")
        rec_path = args.path or find_record(args.corpus, args.task_id)
        extra = dict(kv.split("=", 1) for kv in args.extra)
        rep = {"stamped": stamp(manifest_path, args.task_id, rec_path, extra or None)}
    if args.json:
        with open(args.json, "w") as f:
            json.dump(rep, f, indent=1)
            f.write("\n")
    print(json.dumps({k: v for k, v in rep.items() if k != "samples"}, indent=1))
    if args.cmd == "check" and not rep["clean"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
