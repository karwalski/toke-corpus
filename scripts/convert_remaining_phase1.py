#!/usr/bin/env python3
"""Convert remaining Phase 1 corpus entries to Phase 2 syntax.

Targets:
1. corpus/phase_b_ec2/phase_b/ (8 domain dirs, ~5,863 Phase 1 files)
2. corpus/phase_b_ec2/ top-level D-* dirs (402 already-Phase-2 files, copy only)

COMPOSE-C and COMPOSE-D are already converted (2,178 and 1,580 files) - skipped.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from transform.phase1_to_phase2 import Phase1ToPhase2Transformer
from validate.autofixer import AutoFixer

TKC = str(ROOT.parent / "toke" / "tkc")
OUTPUT_DIR = ROOT / "corpus" / "phase2_combined"
DRY_RUN = "--dry-run" in sys.argv


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


def convert_phase1_entries():
    """Convert Phase 1 entries from phase_b_ec2/phase_b/."""
    transformer = Phase1ToPhase2Transformer()
    fixer = AutoFixer()

    phase_b = ROOT / "corpus" / "phase_b_ec2" / "phase_b"
    sources = []
    for domain_dir in sorted(phase_b.iterdir()):
        if domain_dir.is_dir():
            for f in sorted(domain_dir.glob("*.json")):
                cat = domain_dir.name  # e.g. D-CFG
                sources.append((cat, f))

    print(f"=== Phase 1 Conversion: phase_b_ec2/phase_b/ ===")
    print(f"Total Phase 1 entries: {len(sources):,}")
    per_cat = {}
    for cat, _ in sources:
        per_cat[cat] = per_cat.get(cat, 0) + 1
    for cat in sorted(per_cat):
        print(f"  {cat}: {per_cat[cat]:,}")

    converted = 0
    failed_transform = 0
    failed_compile = 0
    rescued_by_autofixer = 0
    skipped_empty = 0
    skipped_exists = 0
    results_per_cat = {}

    for cat, fpath in sources:
        if cat not in results_per_cat:
            results_per_cat[cat] = {"converted": 0, "failed": 0, "rescued": 0}

        try:
            data = json.loads(fpath.read_text())
        except Exception:
            failed_transform += 1
            results_per_cat[cat]["failed"] += 1
            continue

        src = data.get("tk_source", "")
        if not src.strip():
            skipped_empty += 1
            continue

        entry_id = data.get("id", fpath.stem)
        new_id = f"P2-{entry_id}"

        # Check if output already exists
        cat_dir = OUTPUT_DIR / f"EC2-{cat}"
        out_file = cat_dir / f"{new_id}.json"
        if out_file.exists():
            skipped_exists += 1
            converted += 1
            results_per_cat[cat]["converted"] += 1
            continue

        # Transform Phase 1 -> Phase 2
        try:
            p2_src = transformer.transform(src)
        except Exception as e:
            failed_transform += 1
            results_per_cat[cat]["failed"] += 1
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
                    results_per_cat[cat]["rescued"] += 1

        if not ok:
            failed_compile += 1
            results_per_cat[cat]["failed"] += 1
            continue

        # Build output entry
        task_id = data.get("task_id", "")
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
            cat_dir.mkdir(parents=True, exist_ok=True)
            with open(out_file, "w", encoding="utf-8") as fh:
                json.dump(new_entry, fh, indent=2, ensure_ascii=False)
                fh.write("\n")

        converted += 1
        results_per_cat[cat]["converted"] += 1

        total_done = converted + failed_compile + failed_transform
        if total_done % 500 == 0:
            print(f"  ... {total_done:,}/{len(sources):,} processed, {converted:,} converted")

    prefix = "[dry-run] " if DRY_RUN else ""
    print(f"\n{prefix}Phase 1 Conversion Results:")
    print(f"  Total entries:        {len(sources):,}")
    print(f"  Converted (pass):     {converted:,}")
    print(f"  Skipped (exists):     {skipped_exists:,}")
    print(f"  Skipped (empty):      {skipped_empty:,}")
    print(f"  Failed transform:     {failed_transform:,}")
    print(f"  Failed compile:       {failed_compile:,}")
    print(f"  Rescued by autofixer: {rescued_by_autofixer:,}")
    if len(sources) > 0:
        print(f"  Conversion rate:      {converted/len(sources)*100:.1f}%")

    print(f"\n  Per-category breakdown:")
    for cat in sorted(results_per_cat):
        r = results_per_cat[cat]
        total = r["converted"] + r["failed"]
        rate = r["converted"] / total * 100 if total > 0 else 0
        print(f"    {cat}: {r['converted']:,} converted, {r['failed']:,} failed, "
              f"{r['rescued']:,} rescued ({rate:.1f}%)")

    return converted


def copy_phase2_entries():
    """Copy already-Phase-2 entries from phase_b_ec2/ top-level D-* dirs."""
    ec2_base = ROOT / "corpus" / "phase_b_ec2"

    print(f"\n=== Phase 2 Copy: phase_b_ec2/ top-level D-* dirs ===")
    copied = 0
    skipped = 0
    failed = 0

    for domain_dir in sorted(ec2_base.iterdir()):
        if not domain_dir.is_dir() or domain_dir.name == "phase_b":
            continue
        if not domain_dir.name.startswith("D-"):
            continue

        cat = domain_dir.name  # e.g. D-CFG
        out_dir = OUTPUT_DIR / f"EC2-{cat}"

        for f in sorted(domain_dir.glob("*.json")):
            out_file = out_dir / f.name
            if out_file.exists():
                skipped += 1
                continue

            try:
                # Verify it compiles before copying
                data = json.loads(f.read_text())
                src = data.get("tk_source", "")
                if not src.strip():
                    continue

                ok, _ = compile_check(src)
                if not ok:
                    failed += 1
                    continue

                if not DRY_RUN:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, out_file)
                copied += 1
            except Exception:
                failed += 1
                continue

    prefix = "[dry-run] " if DRY_RUN else ""
    print(f"{prefix}Phase 2 Copy Results:")
    print(f"  Copied:  {copied:,}")
    print(f"  Skipped: {skipped:,} (already exist)")
    print(f"  Failed:  {failed:,} (compile check)")
    return copied


def main():
    print(f"TKC: {TKC}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Dry run: {DRY_RUN}")
    print()

    # Verify tkc exists
    if not os.path.isfile(TKC):
        print(f"ERROR: tkc not found at {TKC}")
        sys.exit(1)

    # Part 1: Convert Phase 1 entries from phase_b_ec2/phase_b/
    p1_count = convert_phase1_entries()

    # Part 2: Copy Phase 2 entries from phase_b_ec2/ top-level D-*
    p2_count = copy_phase2_entries()

    print(f"\n=== TOTAL ===")
    print(f"  New Phase 2 entries from conversion: {p1_count:,}")
    print(f"  Phase 2 entries copied: {p2_count:,}")
    print(f"  Grand total new entries: {p1_count + p2_count:,}")


if __name__ == "__main__":
    main()
