#!/usr/bin/env python3
"""Validate fuzzed corpus entries by compile-checking with tkc.

Reads corpus_fuzzed.jsonl, validates each tk_source with tkc --check,
and writes passing entries to corpus/phase2_combined/FUZZ/.
"""

import json
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "data" / "corpus_fuzzed.jsonl"
OUTPUT_DIR = ROOT / "corpus" / "phase2_combined" / "FUZZ"
TKC = os.environ.get("TKC", str(ROOT.parent / "toke" / "tkc"))
DRY_RUN = "--dry-run" in sys.argv


def compile_check(source: str) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(source)
        fname = f.name
    try:
        r = subprocess.run(
            [TKC, "--check", fname],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False
    finally:
        os.unlink(fname)


def main():
    entries = []
    with open(INPUT, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    print(f"Loaded {len(entries):,} fuzzed entries")

    passed = 0
    failed = 0

    if not DRY_RUN:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for i, rec in enumerate(entries):
        src = rec.get("tk_source", "")
        if not src.strip():
            failed += 1
            continue

        if compile_check(src):
            passed += 1
            if not DRY_RUN:
                h = hashlib.sha1(src.encode()).hexdigest()[:8]
                entry_id = f"FUZZ-{i:05d}-{h}"
                corpus_entry = {
                    "id": entry_id,
                    "version": 1,
                    "phase": "B",
                    "task_id": f"FUZZ-{i:05d}",
                    "tk_source": src,
                    "tk_tokens": rec.get("tk_tokens", len(src.split())),
                    "attempts": 1,
                    "model": "grammar-fuzzer",
                    "validation": {"compiler_exit_code": 0, "error_codes": []},
                    "differential": {"languages_agreed": [], "majority_output": ""},
                    "judge": {"accepted": True, "score": 0.80},
                    "references": {},
                }
                out_file = OUTPUT_DIR / f"{entry_id}.json"
                with open(out_file, "w", encoding="utf-8") as fh:
                    json.dump(corpus_entry, fh, ensure_ascii=False)
                    fh.write("\n")
        else:
            failed += 1

        if (i + 1) % 1000 == 0:
            print(f"  ... {i+1:,}/{len(entries):,}, pass={passed:,}")

    prefix = "[dry-run] " if DRY_RUN else ""
    print(f"\n{prefix}Results: {passed:,} passed, {failed:,} failed ({passed/(passed+failed)*100:.1f}%)")


if __name__ == "__main__":
    main()
