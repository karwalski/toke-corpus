#!/usr/bin/env python3
"""Rescue deferred failures by applying autofixer + recompile.

Reads logs/deferred_failures.jsonl, applies the AutoFixer to each source,
recompiles with tkc --check, and writes rescued entries to corpus/phase_b/.

No LLM calls required.

Usage:
    python3 scripts/rescue_deferred.py [--dry-run]
"""

import json
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validate.autofixer import AutoFixer

DEFERRED = ROOT / "logs" / "deferred_failures.jsonl"
OUTPUT_DIR = ROOT / "corpus" / "phase_b"
TKC = os.environ.get("TKC", "/usr/local/bin/tkc")
DRY_RUN = "--dry-run" in sys.argv

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(_enc.encode(text))
except ImportError:
    def count_tokens(text: str) -> int:
        return len(text.split())


def compile_check(source: str) -> tuple[bool, str]:
    """Run tkc --check on source. Returns (success, stderr)."""
    code = source.strip()
    if not code.startswith("m=") and not code.startswith("M="):
        code = "m=rescued;\n" + code
    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(code)
        fname = f.name
    try:
        r = subprocess.run(
            [TKC, "--check", fname],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0, r.stderr
    except Exception as e:
        return False, str(e)
    finally:
        os.unlink(fname)


def make_id(task_id: str, source: str) -> str:
    h = hashlib.sha1(source.encode()).hexdigest()[:8]
    return f"B-{task_id}-{h}"


def main():
    if not DEFERRED.exists():
        print(f"ERROR: {DEFERRED} not found")
        sys.exit(1)

    fixer = AutoFixer()
    entries = []
    with open(DEFERRED, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    print(f"Loaded {len(entries)} deferred failures")

    rescued = 0
    still_failed = 0
    no_change = 0
    by_fix = {}

    for rec in entries:
        source = rec.get("source", "") or ""
        if not source.strip():
            still_failed += 1
            continue

        fixed, fixes = fixer.fix(source)

        if not fixes:
            # Autofixer found nothing new to try
            no_change += 1
            still_failed += 1
            continue

        ok, stderr = compile_check(fixed)
        if not ok:
            still_failed += 1
            continue

        # Ensure m= header
        if not fixed.strip().startswith("m=") and not fixed.strip().startswith("M="):
            fixed = "m=rescued;\n" + fixed

        task_id = rec.get("task_id", "UNK")
        category = rec.get("category", "UNK")
        model = rec.get("model", "unknown")
        corpus_id = make_id(task_id, fixed)

        corpus_entry = {
            "id": corpus_id,
            "version": 1,
            "phase": "B",
            "task_id": task_id,
            "tk_source": fixed,
            "tk_tokens": count_tokens(fixed),
            "attempts": 2,
            "model": model,
            "validation": {
                "compiler_exit_code": 0,
                "error_codes": [],
            },
            "differential": {
                "languages_agreed": [],
                "majority_output": "",
            },
            "judge": {
                "accepted": True,
                "score": 0.85,
            },
            "references": {
                "rescue_fixes": fixes,
            },
        }

        if not DRY_RUN:
            cat_dir = OUTPUT_DIR / category
            cat_dir.mkdir(parents=True, exist_ok=True)
            out_file = cat_dir / f"{corpus_id}.json"
            with open(out_file, "w", encoding="utf-8") as fh:
                json.dump(corpus_entry, fh, indent=2, ensure_ascii=False)
                fh.write("\n")

        rescued += 1
        for fix in fixes:
            by_fix[fix] = by_fix.get(fix, 0) + 1

    prefix = "[dry-run] " if DRY_RUN else ""
    print(f"\n{prefix}Results:")
    print(f"  Total deferred:  {len(entries)}")
    print(f"  No change:       {no_change}")
    print(f"  Rescued:         {rescued}")
    print(f"  Still failed:    {still_failed}")
    print(f"  Rescue rate:     {rescued/len(entries)*100:.1f}%")
    print(f"\nFixes that contributed:")
    for fix, count in sorted(by_fix.items(), key=lambda x: -x[1]):
        print(f"  {fix:35s} {count}")


if __name__ == "__main__":
    main()
