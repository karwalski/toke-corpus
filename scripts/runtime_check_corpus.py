#!/usr/bin/env python3
"""runtime_check_corpus.py — Story 10.8.6.

Compile every record's `tk_source` with `tkc` and, where the program defines
an `f=main()` entry point, run the resulting binary with a short timeout and
capture stdout / stderr / exit code. Records without `main` are recorded as
skipped (reason: no_main) — they are library-style snippets and have no
runtime semantics.

Writes a `runtime_check` field in-place on every record:

    runtime_check = {
      "ran": bool,                # binary was actually executed
      "skipped_reason": str|null, # "no_main" / "compile_fail" / "link_fail" / "mutation" / null
      "exit_code": int|null,
      "timed_out": bool,
      "stdout": str|null,         # truncated to 4 KB
      "stderr": str|null,         # truncated to 4 KB
      "duration_ms": int|null,
      "link_error_first": str|null,  # first undefined-symbol line, if link failed
      "ran_at": ISO-8601,
      "tkc_version": str
    }

Strategy:
- Only process records where `compile_check.passed` is true. Records that
  already fail `tkc --check` cannot be turned into a binary.
- Skip mutation-category records wholesale (output is semantically irrelevant
  — the mutated code may change output intentionally).
- Parallelized with multiprocessing.Pool.

Usage:
    python scripts/runtime_check_corpus.py [--corpus-dir PATH] [--workers N]
                                           [--sample N] [--tkc PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import multiprocessing as mp
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"
DEFAULT_TKC = Path("/Users/matthew.watt/tk/toke/tkc")
COMPILE_TIMEOUT = 20
RUN_TIMEOUT = 5
OUTPUT_TRUNCATE = 4096

RE_MAIN = re.compile(r"\bf\s*=\s*main\s*\(")

MUTATION_PREFIXES = ("MUT-",)
INVERTED_PREFIXES = ("BIFI-", "ERR-TRIPLE-")


# ---------------------------------------------------------------------------
# tkc compile + run
# ---------------------------------------------------------------------------

def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    if len(s) <= OUTPUT_TRUNCATE:
        return s
    return s[:OUTPUT_TRUNCATE] + f"\n...<truncated, total {len(s)} bytes>"


def _first_link_error(stderr: str) -> str | None:
    """Return the first undefined-symbol line from a clang link failure."""
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith('"_') and "referenced from" not in line:
            return line
        if "Undefined symbols" in line:
            return line
    return None


def compile_and_run(tkc: str, source: str) -> dict:
    """Compile `source` to a native binary and run it. Returns a dict with
    the runtime_check fields (minus ran_at/tkc_version which are added
    by the caller).
    """
    tmpdir = tempfile.mkdtemp(prefix="tkc_runtime_", dir="/tmp")
    src_path = os.path.join(tmpdir, "prog.tk")
    bin_path = os.path.join(tmpdir, "prog")
    with open(src_path, "w") as f:
        f.write(source)

    try:
        # Step 1: compile
        try:
            cproc = subprocess.run(
                [tkc, "--out", bin_path, src_path],
                capture_output=True,
                text=True,
                timeout=COMPILE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return {
                "ran": False,
                "skipped_reason": "compile_timeout",
                "exit_code": None,
                "timed_out": False,
                "stdout": None,
                "stderr": None,
                "duration_ms": None,
                "link_error_first": None,
            }
        if cproc.returncode != 0 or not os.path.exists(bin_path):
            combined = (cproc.stdout or "") + (cproc.stderr or "")
            link_err = _first_link_error(combined)
            reason = "link_fail" if link_err else "compile_fail"
            return {
                "ran": False,
                "skipped_reason": reason,
                "exit_code": None,
                "timed_out": False,
                "stdout": None,
                "stderr": _truncate(combined.strip()),
                "duration_ms": None,
                "link_error_first": link_err,
            }

        # Step 2: run
        import time
        t0 = time.monotonic()
        try:
            rproc = subprocess.run(
                [bin_path],
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.monotonic() - t0) * 1000)
            return {
                "ran": True,
                "skipped_reason": None,
                "exit_code": None,
                "timed_out": True,
                "stdout": _truncate(e.stdout if isinstance(e.stdout, str) else None),
                "stderr": _truncate(e.stderr if isinstance(e.stderr, str) else None),
                "duration_ms": duration_ms,
                "link_error_first": None,
            }
        duration_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ran": True,
            "skipped_reason": None,
            "exit_code": rproc.returncode,
            "timed_out": False,
            "stdout": _truncate(rproc.stdout),
            "stderr": _truncate(rproc.stderr),
            "duration_ms": duration_ms,
            "link_error_first": None,
        }
    finally:
        # Cleanup
        for fn in (src_path, bin_path):
            try:
                os.unlink(fn)
            except OSError:
                pass
        # Also clean .ll file tkc might emit
        for extra in os.listdir(tmpdir):
            try:
                os.unlink(os.path.join(tmpdir, extra))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Worker — checks one record
# ---------------------------------------------------------------------------

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


def _check_record(path_str: str) -> tuple[str, str]:
    """Return (category, result_code). result_code one of:
    'ran_ok' / 'ran_nonzero' / 'ran_timeout' / 'link_fail' / 'compile_fail' /
    'no_main' / 'mutation' / 'skip_not_passed' / 'skip_error'.
    """
    path = Path(path_str)
    try:
        with open(path) as f:
            record = json.load(f)
    except (json.JSONDecodeError, OSError):
        return (path.parent.name, "skip_error")

    cat = record.get("category", path.parent.name)
    src = record.get("tk_source", "")
    if not isinstance(src, str) or not src:
        return (cat, "skip_error")

    # Must have passed tkc --check already
    cc = record.get("compile_check") or {}
    if not cc.get("passed"):
        return (cat, "skip_not_passed")

    # Skip mutations — semantics intentionally altered
    if cat.startswith(MUTATION_PREFIXES):
        runtime_check = {
            "ran": False,
            "skipped_reason": "mutation",
            "exit_code": None,
            "timed_out": False,
            "stdout": None,
            "stderr": None,
            "duration_ms": None,
            "link_error_first": None,
            "ran_at": _now_iso,
            "tkc_version": _tkc_version,
        }
        record["runtime_check"] = runtime_check
        if not _dry_run:
            _write(path, record)
        return (cat, "mutation")

    # No main → library-style, skip
    if not RE_MAIN.search(src):
        runtime_check = {
            "ran": False,
            "skipped_reason": "no_main",
            "exit_code": None,
            "timed_out": False,
            "stdout": None,
            "stderr": None,
            "duration_ms": None,
            "link_error_first": None,
            "ran_at": _now_iso,
            "tkc_version": _tkc_version,
        }
        record["runtime_check"] = runtime_check
        if not _dry_run:
            _write(path, record)
        return (cat, "no_main")

    # Run it
    rc = compile_and_run(_tkc_path, src)
    rc["ran_at"] = _now_iso
    rc["tkc_version"] = _tkc_version
    record["runtime_check"] = rc
    if not _dry_run:
        _write(path, record)

    if rc.get("ran"):
        if rc.get("timed_out"):
            return (cat, "ran_timeout")
        return (cat, "ran_ok" if rc.get("exit_code") == 0 else "ran_nonzero")
    reason = rc.get("skipped_reason")
    if reason == "link_fail":
        return (cat, "link_fail")
    if reason == "compile_fail":
        return (cat, "compile_fail")
    return (cat, "skip_error")


def _write(path: Path, record: dict) -> None:
    try:
        with open(path, "w") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


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
    parser = argparse.ArgumentParser(description="Corpus runtime check")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--tkc", type=Path, default=DEFAULT_TKC)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--sample", type=int, default=None,
                        help="Only check N random records per category")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run checks but do not rewrite files")
    args = parser.parse_args()

    random.seed(42)
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

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

    result_counter: collections.Counter = collections.Counter()
    cat_stats: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    link_err_counter: collections.Counter = collections.Counter()
    done = 0
    last_report = 0

    with mp.Pool(
        args.workers,
        initializer=_worker_init,
        initargs=(str(args.tkc), tkc_version, now_iso, args.dry_run),
    ) as pool:
        for cat, code in pool.imap_unordered(
            _check_record, paths, chunksize=16
        ):
            done += 1
            result_counter[code] += 1
            cat_stats[cat][code] += 1
            cat_stats[cat]["scanned"] += 1
            if done - last_report >= 2000 or done == total:
                pct = 100 * done / total if total else 0
                print(
                    f"    progress: {done}/{total} ({pct:.1f}%)  "
                    f"ran_ok={result_counter['ran_ok']} "
                    f"ran_nonzero={result_counter['ran_nonzero']} "
                    f"link_fail={result_counter['link_fail']} "
                    f"no_main={result_counter['no_main']} "
                    f"mutation={result_counter['mutation']} "
                    f"skip_np={result_counter['skip_not_passed']}",
                    file=sys.stderr,
                )
                last_report = done

    # Summary
    print("\n" + "=" * 70, file=sys.stderr)
    print("  Runtime-check summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"  Total scanned:      {total}", file=sys.stderr)
    for k in ("ran_ok", "ran_nonzero", "ran_timeout", "link_fail",
              "compile_fail", "no_main", "mutation", "skip_not_passed",
              "skip_error"):
        print(f"  {k:17s}  {result_counter[k]:8d}", file=sys.stderr)

    attempted = sum(result_counter[k] for k in ("ran_ok", "ran_nonzero", "ran_timeout", "link_fail", "compile_fail"))
    ran = result_counter["ran_ok"] + result_counter["ran_nonzero"] + result_counter["ran_timeout"]
    if attempted:
        print(
            f"\n  Runtime success rate (ran_ok / attempted): "
            f"{100*result_counter['ran_ok']/attempted:.1f}% "
            f"({result_counter['ran_ok']}/{attempted})",
            file=sys.stderr,
        )
    print(
        f"  Non-zero exits: {result_counter['ran_nonzero']}  "
        f"Timeouts: {result_counter['ran_timeout']}  "
        f"Link failures: {result_counter['link_fail']}",
        file=sys.stderr,
    )

    # Top categories with runtime coverage
    print(f"\n  Top 20 categories by ran_ok:", file=sys.stderr)
    ranked = sorted(
        cat_stats.items(), key=lambda kv: -kv[1].get("ran_ok", 0)
    )
    print(
        f"    {'category':35s} {'scanned':>8s} {'ranok':>8s} "
        f"{'rnzero':>8s} {'linkfl':>8s} {'nomain':>8s}",
        file=sys.stderr,
    )
    for cat, cnts in ranked[:20]:
        print(
            f"    {cat:35s} "
            f"{cnts.get('scanned', 0):8d} "
            f"{cnts.get('ran_ok', 0):8d} "
            f"{cnts.get('ran_nonzero', 0):8d} "
            f"{cnts.get('link_fail', 0):8d} "
            f"{cnts.get('no_main', 0):8d}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
