#!/usr/bin/env python3
"""Epic 131.12 — build the freeze-129 record manifest.

Walks corpus/regen_v04/{A-*,D-*}/*.json and emits one line per record:
  task_id, category, path (relative to regen_v04/), sha256 (of the record
  file bytes), min_bytes (regen.min_bytes), audit (regen.audit stamp).

Writes:
  corpus/regen_v04/audit/freeze_129_manifest.jsonl          (gitignored)
  corpus/regen_v04/audit/freeze_129_manifest_summary.json   (gitignored)
  regen/freeze/freeze_129_manifest.jsonl[.gz]               (tracked copy)
  regen/freeze/freeze_129_summary.json                      (tracked copy)
"""
import collections, datetime, glob, gzip, hashlib, json, os, shutil, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RV4 = os.path.join(ROOT, "corpus", "regen_v04")
ARC = os.path.expanduser("~/tk/archive/toke-corpus-regen_v04-freeze129-20260819")
TARBALL = os.path.join(ARC, "regen_v04-freeze129.tar.zst")

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    files = sorted(glob.glob(os.path.join(RV4, "[AD]-*", "*.json")))
    lines, per_cat, audit_stamps = [], collections.Counter(), collections.Counter()
    for p in files:
        rel = os.path.relpath(p, RV4)
        with open(p, "rb") as f:
            raw = f.read()
        rec = json.loads(raw)
        regen = rec.get("regen") or {}
        cat = regen.get("category") or rec.get("category") or rel.split("/")[0]
        row = {
            "task_id": rec.get("task_id") or os.path.splitext(os.path.basename(p))[0],
            "category": cat,
            "path": rel,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "min_bytes": regen.get("min_bytes"),
            "audit": regen.get("audit"),
        }
        lines.append(json.dumps(row, separators=(",", ":")))
        per_cat[cat] += 1
        audit_stamps[str(row["audit"])] += 1

    out_dir = os.path.join(RV4, "audit")
    man_path = os.path.join(out_dir, "freeze_129_manifest.jsonl")
    with open(man_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    head = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip()
    tkc = subprocess.check_output([os.path.expanduser("~/tk/toke/tkc"), "--version"], text=True).strip()
    summary = {
        "story": "131.12",
        "freeze": "129-freeze-2026-08-19",
        "date": datetime.date.today().isoformat(),
        "total": len(lines),
        "per_category": dict(sorted(per_cat.items())),
        "audit_stamps": dict(audit_stamps),
        "tarball": TARBALL,
        "tarball_sha256": sha256_file(TARBALL) if os.path.exists(TARBALL) else None,
        "tarball_bytes": os.path.getsize(TARBALL) if os.path.exists(TARBALL) else None,
        "tkc_version": tkc,
        "toke_corpus_head": head,
        "manifest_sha256": sha256_file(man_path),
    }
    with open(os.path.join(out_dir, "freeze_129_manifest_summary.json"), "w") as f:
        json.dump(summary, f, indent=2); f.write("\n")

    # tracked copies
    fz = os.path.join(ROOT, "regen", "freeze")
    os.makedirs(fz, exist_ok=True)
    if os.path.getsize(man_path) > 5 * 1024 * 1024:
        with open(man_path, "rb") as src, gzip.open(os.path.join(fz, "freeze_129_manifest.jsonl.gz"), "wb", mtime=0) as dst:
            shutil.copyfileobj(src, dst)
    else:
        shutil.copyfile(man_path, os.path.join(fz, "freeze_129_manifest.jsonl"))
    with open(os.path.join(fz, "freeze_129_summary.json"), "w") as f:
        json.dump(summary, f, indent=2); f.write("\n")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
