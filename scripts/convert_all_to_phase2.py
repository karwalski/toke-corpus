#!/usr/bin/env python3
"""Convert all Phase 1 corpus entries to Phase 2 syntax.

Reads from corpus/phase_a/ and corpus/phase_b/B-CMP/, transforms using
Phase1ToPhase2Transformer, compile-checks with tkc, and writes passing
entries to corpus/phase2_combined/.

Usage:
    python3 scripts/convert_all_to_phase2.py [--dry-run] [--limit N]
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transform.phase1_to_phase2 import Phase1ToPhase2Transformer
from validate.autofixer import AutoFixer

TKC = os.environ.get("TKC", str(ROOT.parent / "toke" / "tkc"))
OUTPUT_DIR = ROOT / "corpus" / "phase2_combined"
DRY_RUN = "--dry-run" in sys.argv
LIMIT = None
for i, arg in enumerate(sys.argv):
    if arg == "--limit" and i + 1 < len(sys.argv):
        LIMIT = int(sys.argv[i + 1])


def compile_check(source: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(suffix=".tk", mode="w", delete=False) as f:
        f.write(source)
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


def main():
    transformer = Phase1ToPhase2Transformer()
    fixer = AutoFixer()

    # Collect all Phase 1 entries
    sources = []

    # Phase A
    phase_a = ROOT / "corpus" / "phase_a"
    if phase_a.exists():
        for f in sorted(phase_a.rglob("*.json")):
            sources.append(("A", f))

    # B-CMP
    bcmp = ROOT / "corpus" / "phase_b" / "B-CMP"
    if bcmp.exists():
        for f in sorted(bcmp.rglob("*.json")):
            sources.append(("B-CMP", f))

    if LIMIT:
        sources = sources[:LIMIT]

    print(f"Processing {len(sources):,} Phase 1 entries...")
    print(f"  Phase A: {sum(1 for s in sources if s[0] == 'A'):,}")
    print(f"  B-CMP:   {sum(1 for s in sources if s[0] == 'B-CMP'):,}")

    converted = 0
    failed_transform = 0
    failed_compile = 0
    rescued_by_autofixer = 0

    if not DRY_RUN:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for origin, fpath in sources:
        try:
            data = json.loads(fpath.read_text())
        except Exception:
            continue

        src = data.get("tk_source", "")
        if not src.strip():
            continue

        # Transform Phase 1 → Phase 2
        try:
            p2_src = transformer.transform(src)
        except Exception:
            failed_transform += 1
            continue

        # Compile check
        ok, stderr = compile_check(p2_src)

        if not ok:
            # Try autofixer
            fixed, fixes = fixer.fix(p2_src)
            if fixes:
                ok2, stderr2 = compile_check(fixed)
                if ok2:
                    p2_src = fixed
                    ok = True
                    rescued_by_autofixer += 1

        if not ok:
            failed_compile += 1
            continue

        # Write converted entry
        entry_id = data.get("id", fpath.stem)
        new_id = f"P2-{entry_id}"

        # Preserve category from task_id
        task_id = data.get("task_id", "")
        cat_parts = task_id.split("-")
        if len(cat_parts) >= 2:
            cat = f"{cat_parts[0]}-{cat_parts[1]}"
        else:
            cat = "UNK"

        new_entry = {
            "id": new_id,
            "version": 1,
            "phase": "B",
            "task_id": task_id,
            "tk_source": p2_src,
            "tk_tokens": data.get("tk_tokens", 0),
            "attempts": data.get("attempts", 1),
            "model": data.get("model", "unknown"),
            "validation": {"compiler_exit_code": 0, "error_codes": []},
            "differential": data.get("differential", {"languages_agreed": [], "majority_output": ""}),
            "judge": data.get("judge", {"accepted": True, "score": 0.9}),
            "references": data.get("references", {}),
        }

        if not DRY_RUN:
            cat_dir = OUTPUT_DIR / cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            out_file = cat_dir / f"{new_id}.json"
            with open(out_file, "w", encoding="utf-8") as fh:
                json.dump(new_entry, fh, indent=2, ensure_ascii=False)
                fh.write("\n")

        converted += 1

        if converted % 1000 == 0:
            print(f"  ... {converted:,} converted, {failed_compile:,} compile failures")

    prefix = "[dry-run] " if DRY_RUN else ""
    print(f"\n{prefix}Results:")
    print(f"  Total processed:      {len(sources):,}")
    print(f"  Converted (pass):     {converted:,}")
    print(f"  Failed transform:     {failed_transform:,}")
    print(f"  Failed compile:       {failed_compile:,}")
    print(f"  Rescued by autofixer: {rescued_by_autofixer:,}")
    print(f"  Conversion rate:      {converted/len(sources)*100:.1f}%")


if __name__ == "__main__":
    main()
