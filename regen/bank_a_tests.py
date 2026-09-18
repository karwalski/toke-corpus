#!/usr/bin/env python3
"""Main-thread banking for the A-category a_tests waves (129.7, 131.47):
independently re-verify each authored test file (re-executes the Python
reference — verify_a_tests.verify — and, with tkc on disk, renders the cases
through the audit driver under the PINNED compiler) and, on pass, install it
as audit/a_tests/<base>.json. Variant propagation happens at audit time
(audit.load_specs injects a base's cases into variants with matching
normalised input types). Processed files are archived .done.

131.47 additions
  --workdir     wave workdir (default work/a_tests_129 for 129.7 compatibility;
                the re-author wave uses work/atest_131)
  --provenance  stamp (default `129.7-agent+ref-verified`; the re-author wave
                banks as `131.47-agent+ref-verified`)
  --dry-run     verify + report, write nothing (no bank file, no archive, no .done)
  archive-before-replace: a base that already has audit/a_tests/<base>.json is
                never overwritten in place — the old file is moved (copied, with
                a `replaced_by` note) to audit/a_tests/replaced_131/<base>.json
                first, and the new record carries `replaces`. The archive is
                never clobbered (a second replacement lands as <base>.2.json).
  input_types   banked NORMALISED (`@u64`, not the sampler-mangled `@(u64`).
  verified      {date, workdir, tkc_bin_sha, tkc_version, probe} — which binary
                the driver probe ran under.

131.35 note: this path writes audit/a_tests/ only — it never rewrites a corpus
record file, so there is nothing to re-stamp in MANIFEST.jsonl. If it ever
starts touching <CATEGORY>/<task_id>.json, call
manifest_tool.Manifest(...).stamp(task_id, rec_path) right after the write
(see bank_repairs.py / run_shard.cmd_validate)."""
import argparse, glob, json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify_a_tests as va                              # noqa: E402
from verify_a_tests import verify, WD, CORPUS            # noqa: E402
import tkc_pin                                           # noqa: E402  (131.39)

DEFAULT_PROVENANCE = "129.7-agent+ref-verified"
BANK_DIR = os.path.join(CORPUS, "audit", "a_tests")
ARCHIVE_SUBDIR = "replaced_131"


def reauthor_reasons(workdir):
    """base -> reason from the workdir's batch manifests (a_tests_prep --reauthor)."""
    out = {}
    for fn in sorted(glob.glob(os.path.join(workdir, "batches", "batch_*.json"))):
        try:
            out.update(json.load(open(fn)).get("reasons") or {})
        except (OSError, json.JSONDecodeError):
            pass
    return out


def _rel(path):
    """Path relative to the corpus dir when inside it, else absolute (tests)."""
    r = os.path.relpath(path, CORPUS)
    return path if r.startswith("..") else r


def _archive_path(archive_dir, base):
    p = os.path.join(archive_dir, base + ".json")
    n = 2
    while os.path.exists(p):
        p = os.path.join(archive_dir, f"{base}.{n}.json")
        n += 1
    return p


def bank_record(base, doc, spec, provenance, verified):
    return {"base": base, "python_ref": doc.get("python_ref"),
            "test_cases": doc["test_cases"],
            "input_types": va.input_types(spec),
            "output_type": spec.get("output_type_v03") or spec.get("output_type"),
            "provenance": provenance, "verified": verified}


def bank(workdir=WD, bank_dir=BANK_DIR, provenance=DEFAULT_PROVENANCE, dry_run=False,
         archive_dir=None, probe=True, tkc_stamp=None, verbose=False):
    """Bank every gen/tests_<base>.json in workdir. Returns the summary dict.
    With dry_run nothing on disk changes (the summary says what would)."""
    gen = os.path.join(workdir, "gen")
    archive_dir = archive_dir or os.path.join(bank_dir, ARCHIVE_SUBDIR)
    if not dry_run:
        os.makedirs(bank_dir, exist_ok=True)
    reasons = reauthor_reasons(workdir)
    today = time.strftime("%Y-%m-%d")
    s = {"workdir": workdir, "bank_dir": bank_dir, "provenance": provenance,
         "dry_run": dry_run, "probe": probe, "banked": 0, "rejected": 0, "archived": 0,
         "banked_bases": [], "archived_bases": [], "rejects": {}, "probe_notes": {}}
    for fn in sorted(os.listdir(gen)) if os.path.isdir(gen) else []:
        if not fn.startswith("tests_") or not fn.endswith(".json"):
            continue
        base = fn[6:-5]
        path = os.path.join(gen, fn)
        spec_path = os.path.join(workdir, "specs", base + ".json")
        errs, doc, spec, notes = ["invalid JSON"], None, None, []
        if os.path.exists(spec_path):
            spec = json.load(open(spec_path))
            try:
                doc = json.load(open(path))
                errs = verify(base, doc, spec, probe=probe, notes=notes)
            except json.JSONDecodeError:
                pass
        else:
            errs = ["no spec"]
        if notes:
            s["probe_notes"][base] = notes[0]
        if errs:
            s["rejected"] += 1
            s["rejects"][base] = errs[:3]
            if verbose:
                print(f"REJECT {base}: {'; '.join(errs[:3])}", file=sys.stderr)
        else:
            verified = {"date": today, "workdir": _rel(workdir),
                        "probe": notes[0] if notes else "not run",
                        **({"tkc_bin_sha": tkc_stamp.get("tkc_bin_sha"),
                            "tkc_version": tkc_stamp.get("tkc_version")} if tkc_stamp else {})}
            rec = bank_record(base, doc, spec, provenance, verified)
            out = os.path.join(bank_dir, base + ".json")
            if os.path.exists(out):
                old = json.load(open(out))
                old["replaced_by"] = {"provenance": provenance, "date": today,
                                      "workdir": _rel(workdir),
                                      "reason": reasons.get(base, "re-authored")}
                apath = _archive_path(archive_dir, base)
                rec["replaces"] = {"provenance": old.get("provenance"),
                                   "archived": _rel(apath)}
                if not dry_run:
                    os.makedirs(archive_dir, exist_ok=True)
                    with open(apath, "w") as f:
                        json.dump(old, f)
                s["archived"] += 1
                s["archived_bases"].append(base)
            if not dry_run:
                tmp = out + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(rec, f)
                os.replace(tmp, out)
            s["banked"] += 1
            s["banked_bases"].append(base)
            if verbose:
                print(f"{'WOULD BANK' if dry_run else 'BANKED'} {base}"
                      + (f" (archived {rec['replaces']['archived']})" if "replaces" in rec else ""),
                      file=sys.stderr)
        if not dry_run:
            os.rename(path, path + ".done")
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workdir", default=WD)
    ap.add_argument("--bank-dir", default=BANK_DIR, help="audit/a_tests (tests point elsewhere)")
    ap.add_argument("--archive-dir", default=None,
                    help=f"where replaced bank files go (default <bank-dir>/{ARCHIVE_SUBDIR})")
    ap.add_argument("--provenance", default=DEFAULT_PROVENANCE)
    ap.add_argument("--dry-run", action="store_true", help="verify + report; write nothing")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the driver render + pinned tkc --check probe")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.no_probe:
        s = bank(args.workdir, args.bank_dir, args.provenance, args.dry_run, args.archive_dir,
                 probe=False, verbose=args.verbose)
    else:
        import validate
        with tkc_pin.pin().install(validate) as pinned:
            print(f"tkc {pinned.version} sha {pinned.sha256[:12]} (pinned copy)", file=sys.stderr)
            s = bank(args.workdir, args.bank_dir, args.provenance, args.dry_run, args.archive_dir,
                     probe=True, tkc_stamp=pinned.stamp(), verbose=args.verbose)
    s["sample_rejects"] = dict(list(s["rejects"].items())[:10])
    print(json.dumps({k: v for k, v in s.items() if k not in ("rejects", "probe_notes")}))


if __name__ == "__main__":
    main()
