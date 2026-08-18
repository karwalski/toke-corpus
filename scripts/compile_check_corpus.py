#!/usr/bin/env python3
"""compile_check_corpus.py — Story 10.8.3.

Run `tkc --check` against every record's `tk_source` in the corpus and write
the result into the `compile_check` field in-place.

Parallelized with multiprocessing.Pool (default 16 workers, tunable).

For BIFI / ERR-TRIPLE records, `references.broken_source` is ALSO checked
(expected to fail with the diagnostic recorded in `references.diagnostic`),
and the result is stored in `compile_check.broken_compile`.

Output format per record (`compile_check`):

    {
      "passed": bool,           # tk_source compiled (exit==0)
      "exit_code": int,
      "error_codes": [str],     # e.g. ["E3011"]
      "stage": str or null,     # "lex" / "parse" / "name_resolution" / "type_check"
      "diagnostic": str or null,# first diagnostic message
      "inverted": false,        # tk_source is never inverted; always false
      "expected_error_code": null,
      "ran_at": ISO-8601,
      "tkc_version": str,
      "broken_compile": {       # only present for BIFI/ERR-TRIPLE
        "failed_as_expected": bool,
        "exit_code": int,
        "error_codes": [str],
        "diagnostic": str or null
      }
    }

Usage:
    python scripts/compile_check_corpus.py [--corpus-dir PATH] [--workers N]
                                           [--sample N] [--tkc PATH]
                                           [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import multiprocessing as mp
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"
DEFAULT_TKC = Path("/Users/matthew.watt/tk/toke/tkc")
TIMEOUT_SECONDS = 15


# ---------------------------------------------------------------------------
# tkc invocation
# ---------------------------------------------------------------------------

def run_tkc_check(tkc: str, source: str) -> dict:
    """Run `tkc --check --diag-json` on `source`. Returns parsed result."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tk", delete=False, dir="/tmp"
    ) as tf:
        tf.write(source)
        tmp_path = tf.name
    try:
        proc = subprocess.run(
            [tkc, "--check", "--diag-json", tmp_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "error_codes": ["TIMEOUT"],
            "stage": None,
            "diagnostic": f"tkc --check timed out after {TIMEOUT_SECONDS}s",
        }
    except Exception as exc:
        return {
            "exit_code": -1,
            "error_codes": ["RUNERR"],
            "stage": None,
            "diagnostic": f"runner error: {exc}",
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Parse diagnostics from stdout (one JSON object per line)
    error_codes: list[str] = []
    first_stage: str | None = None
    first_msg: str | None = None
    combined = (stdout or "") + (stderr or "")
    for line in combined.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        ec = d.get("error_code")
        if ec:
            error_codes.append(ec)
        if first_stage is None:
            first_stage = d.get("stage")
        if first_msg is None:
            first_msg = d.get("message")

    if exit_code != 0 and not error_codes:
        # Exit code non-zero but no parsed diagnostic — record raw stderr tail
        tail = (combined.strip().splitlines() or ["<no output>"])[-1]
        if not first_msg:
            first_msg = tail[:200]
        if not error_codes:
            error_codes.append("UNKNOWN")

    return {
        "exit_code": exit_code,
        "error_codes": error_codes,
        "stage": first_stage,
        "diagnostic": first_msg,
    }


# ---------------------------------------------------------------------------
# Worker — checks one record
# ---------------------------------------------------------------------------

# Globals populated in each worker.
_tkc_path: str | None = None
_tkc_version: str | None = None
_now_iso: str | None = None
_dry_run: bool = False


def _worker_init(tkc: str, version: str, now_iso: str, dry: bool) -> None:
    global _tkc_path, _tkc_version, _now_iso, _dry_run
    _tkc_path = tkc
    _tkc_version = version
    _now_iso = now_iso
    _dry_run = dry


def _check_record(path_str: str) -> tuple[str, str, bool, bool]:
    """Return (category, result_code, is_inverted_record, passed_tk_source).
    result_code: "pass" / "fail" / "broken_ok" / "broken_bad" / "skip".
    """
    path = Path(path_str)
    try:
        with open(path) as f:
            record = json.load(f)
    except (json.JSONDecodeError, OSError):
        return (path.parent.name, "skip", False, False)

    src = record.get("tk_source", "")
    if not isinstance(src, str) or not src:
        return (path.parent.name, "skip", False, False)

    result = run_tkc_check(_tkc_path, src)
    passed = result["exit_code"] == 0

    compile_check = {
        "passed": passed,
        "exit_code": result["exit_code"],
        "error_codes": result["error_codes"],
        "stage": result["stage"],
        "diagnostic": result["diagnostic"],
        "inverted": False,
        "expected_error_code": None,
        "ran_at": _now_iso,
        "tkc_version": _tkc_version,
    }

    # BIFI/ERR-TRIPLE: also check broken_source
    is_inverted = False
    cat = record.get("category", path.parent.name)
    if cat.startswith("BIFI-") or cat.startswith("ERR-TRIPLE-"):
        is_inverted = True
        broken_src = record.get("references", {}).get("broken_source")
        if isinstance(broken_src, str) and broken_src:
            br = run_tkc_check(_tkc_path, broken_src)
            failed_as_expected = br["exit_code"] != 0
            compile_check["broken_compile"] = {
                "failed_as_expected": failed_as_expected,
                "exit_code": br["exit_code"],
                "error_codes": br["error_codes"],
                "diagnostic": br["diagnostic"],
            }

    record["compile_check"] = compile_check

    if not _dry_run:
        try:
            with open(path, "w") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            return (cat, "skip", is_inverted, passed)

    if is_inverted:
        bc = compile_check.get("broken_compile", {})
        broken_ok = bc.get("failed_as_expected", False)
        if passed and broken_ok:
            return (cat, "broken_ok", True, True)
        if passed and not broken_ok:
            return (cat, "broken_bad", True, True)

    return (cat, "pass" if passed else "fail", is_inverted, passed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def iter_paths(corpus_dir: Path, sample_per_cat: int | None):
    for cat_dir in sorted(corpus_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        files = [p for p in cat_dir.iterdir() if p.suffix == ".json"]
        if sample_per_cat is not None and len(files) > sample_per_cat:
            files = random.sample(files, sample_per_cat)
        for p in files:
            yield str(p)


def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel tkc --check on corpus")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--tkc", type=Path, default=DEFAULT_TKC)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--sample", type=int, default=None,
                        help="Only check N random records per category")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run checks but do not rewrite files")
    args = parser.parse_args()

    random.seed(42)
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

    # Get tkc version
    try:
        v = subprocess.run(
            [str(args.tkc), "--version"], capture_output=True, text=True, timeout=5
        )
        tkc_version = v.stdout.strip() or v.stderr.strip() or "unknown"
    except Exception as exc:
        print(f"ERROR: cannot run tkc at {args.tkc}: {exc}", file=sys.stderr)
        return 2

    print(f"  tkc:     {args.tkc}", file=sys.stderr)
    print(f"  version: {tkc_version}", file=sys.stderr)
    print(f"  workers: {args.workers}", file=sys.stderr)
    print(f"  corpus:  {args.corpus_dir}", file=sys.stderr)
    if args.dry_run:
        print(f"  DRY RUN — no files will be modified", file=sys.stderr)

    paths = list(iter_paths(args.corpus_dir, args.sample))
    total = len(paths)
    print(f"  records: {total}", file=sys.stderr)

    # Progress reporting
    cat_stats: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    result_counter: collections.Counter = collections.Counter()
    done = 0
    last_report = 0

    with mp.Pool(
        args.workers,
        initializer=_worker_init,
        initargs=(str(args.tkc), tkc_version, now_iso, args.dry_run),
    ) as pool:
        for cat, code, _inv, _passed in pool.imap_unordered(
            _check_record, paths, chunksize=32
        ):
            done += 1
            result_counter[code] += 1
            cat_stats[cat][code] += 1
            cat_stats[cat]["scanned"] += 1
            if done - last_report >= 5000 or done == total:
                pct = 100 * done / total if total else 0
                print(
                    f"    progress: {done}/{total} ({pct:.1f}%)  "
                    f"pass={result_counter['pass']} "
                    f"fail={result_counter['fail']} "
                    f"broken_ok={result_counter['broken_ok']} "
                    f"broken_bad={result_counter['broken_bad']} "
                    f"skip={result_counter['skip']}",
                    file=sys.stderr,
                )
                last_report = done

    # Summary
    print("\n" + "=" * 70, file=sys.stderr)
    print("  Compile-check summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  Total scanned:   {total}", file=sys.stderr)
    for k in ("pass", "fail", "broken_ok", "broken_bad", "skip"):
        print(f"  {k:13s}    {result_counter[k]:8d}", file=sys.stderr)

    total_pass = result_counter["pass"] + result_counter["broken_ok"]
    pass_rate = 100 * total_pass / total if total else 0
    print(
        f"\n  Effective pass rate (incl. broken_ok): {pass_rate:.2f}%",
        file=sys.stderr,
    )

    # Per-category ranked by failure count
    print(f"\n  Per category (bottom 20 by pass rate):", file=sys.stderr)

    def _effective_pass(cnts: collections.Counter) -> float:
        total = cnts.get("scanned", 0)
        if not total:
            return 0.0
        passes = cnts.get("pass", 0) + cnts.get("broken_ok", 0)
        return passes / total

    ranked = sorted(cat_stats.items(), key=lambda kv: _effective_pass(kv[1]))
    print(
        f"    {'category':35s} {'scanned':>8s} {'pass':>8s} {'fail':>8s} "
        f"{'bok':>6s} {'bbad':>6s} {'rate%':>8s}",
        file=sys.stderr,
    )
    for cat, cnts in ranked[:20]:
        total_c = cnts.get("scanned", 0)
        rate = 100 * _effective_pass(cnts)
        print(
            f"    {cat:35s} "
            f"{total_c:8d} "
            f"{cnts.get('pass', 0):8d} "
            f"{cnts.get('fail', 0):8d} "
            f"{cnts.get('broken_ok', 0):6d} "
            f"{cnts.get('broken_bad', 0):6d} "
            f"{rate:7.1f}%",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
