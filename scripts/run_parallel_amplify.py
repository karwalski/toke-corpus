#!/usr/bin/env python3
"""Cross-language parallel corpus amplification (Story 9.2.5).

Transpiles Python reference source code from corpus entries into toke using
the Python-to-toke transpiler, creating Python<->toke parallel pairs.

Usage::

    cd /Users/matthew.watt/tk/toke-corpus
    .venv/bin/python scripts/run_parallel_amplify.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path

# Ensure repo root is on sys.path so transpile package is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from transpile.py_to_toke import PyToTokeTranspiler, TranspileError

TKC_PATH = "/Users/matthew.watt/tk/toke/tkc"
CORPUS_PATH = REPO_ROOT / "data" / "corpus_default.jsonl"
OUTPUT_PATH = REPO_ROOT / "data" / "parallel_corpus_expanded.jsonl"
TRANSPILER_VERSION = "0.1.0"


def _sanitize_module_name(task_id: str) -> str:
    """Convert a task_id like 'A-ARR-0001v3' into a valid toke module name."""
    name = task_id.lower().replace("-", "").replace("_", "")
    if not name:
        name = "mod"
    if name[0].isdigit():
        name = "m" + name
    return name


def _tkc_check(toke_source: str) -> bool:
    """Run tkc --check on toke source. Returns True if it passes."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toke", delete=True
        ) as tmp:
            tmp.write(toke_source)
            tmp.flush()
            result = subprocess.run(
                [TKC_PATH, "--check", tmp.name],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def process_entry(line: str) -> dict | None:
    """Process a single JSONL line. Returns output dict or None on skip."""
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None

    refs = entry.get("references", {})
    python_source = refs.get("python_source")
    if not python_source:
        return None

    task_id = entry.get("task_id", "unknown")
    module_name = _sanitize_module_name(task_id)

    transpiler = PyToTokeTranspiler()
    try:
        transpiled = transpiler.transpile(python_source, module_name)
    except TranspileError:
        return {"_status": "transpile_error"}
    except Exception:
        return {"_status": "transpile_error"}

    passed = _tkc_check(transpiled)

    return {
        "source_id": entry.get("id", task_id),
        "python_source": python_source,
        "transpiled_tk_source": transpiled,
        "original_tk_source": entry.get("tk_source", ""),
        "tkc_check": "pass" if passed else "fail",
        "transpiler_version": TRANSPILER_VERSION,
    }


def main() -> None:
    if not CORPUS_PATH.exists():
        print(f"ERROR: Corpus not found at {CORPUS_PATH}", file=sys.stderr)
        sys.exit(1)

    if not Path(TKC_PATH).exists():
        print(f"ERROR: tkc binary not found at {TKC_PATH}", file=sys.stderr)
        sys.exit(1)

    # Read all lines up front so we can use multiprocessing pool.map
    print(f"Reading corpus from {CORPUS_PATH} ...")
    with open(CORPUS_PATH, "r") as f:
        lines = f.readlines()

    total_entries = len(lines)
    print(f"Total corpus entries: {total_entries}")

    workers = os.cpu_count() or 4
    print(f"Processing with {workers} workers ...")

    start = time.time()

    total_with_python = 0
    transpile_success = 0
    tkc_pass = 0
    tkc_fail = 0
    transpile_error = 0
    written = 0

    with open(OUTPUT_PATH, "w") as out_f:
        with Pool(processes=workers) as pool:
            # Use imap for ordered, lazy results with progress tracking
            for i, result in enumerate(pool.imap(process_entry, lines, chunksize=200)):
                if (i + 1) % 5000 == 0:
                    elapsed = time.time() - start
                    rate = (i + 1) / elapsed if elapsed > 0 else 0
                    print(
                        f"  Progress: {i + 1}/{total_entries} "
                        f"({rate:.0f} entries/s) | "
                        f"transpiled={transpile_success} "
                        f"tkc_pass={tkc_pass} tkc_fail={tkc_fail} "
                        f"errors={transpile_error}"
                    )

                if result is None:
                    continue

                total_with_python += 1

                if result.get("_status") == "transpile_error":
                    transpile_error += 1
                    continue

                transpile_success += 1

                if result["tkc_check"] == "pass":
                    tkc_pass += 1
                else:
                    tkc_fail += 1

                out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
                written += 1

    elapsed = time.time() - start

    print()
    print("=" * 60)
    print("  Parallel Corpus Amplification - Final Stats")
    print("=" * 60)
    print(f"  Total corpus entries:      {total_entries:>8,}")
    print(f"  Entries with python_source: {total_with_python:>8,}")
    print(f"  Transpile success:         {transpile_success:>8,}")
    print(f"  Transpile error (skipped): {transpile_error:>8,}")
    print(f"  tkc --check pass:          {tkc_pass:>8,}")
    print(f"  tkc --check fail:          {tkc_fail:>8,}")
    print(f"  Written to output:         {written:>8,}")
    print(f"  Elapsed time:              {elapsed:>8.1f}s")
    print(f"  Output: {OUTPUT_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()
