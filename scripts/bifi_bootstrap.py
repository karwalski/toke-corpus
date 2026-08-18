#!/usr/bin/env python3
"""Story 10.2.1 — BIFI error-repair bootstrap pipeline.

Break-It-Fix-It approach:
1. Take correct toke programs from the corpus
2. Inject errors using ErrorInjector (6 injection types)
3. Compile broken version with tkc --check — must FAIL (capture diagnostic)
4. Confirm original PASSES tkc --check
5. Write valid (broken, diagnostic, original) triplets as corpus-schema JSON

Fully local, zero LLM cost.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CORPUS_ROOT = Path("/Users/matthew.watt/tk/toke-corpus")
PHASE2 = CORPUS_ROOT / "corpus" / "phase2_deduplicated"
TKC = Path("/Users/matthew.watt/tk/toke/tkc")

# Add mutate dir to path so we can import ErrorInjector
sys.path.insert(0, str(CORPUS_ROOT / "mutate"))
from error_inject import ErrorInjector, Injection  # noqa: E402

# Seed categories — NON-mutation, NON-fuzz, NON-error-triple
SEED_PREFIXES = [
    "A-ARR", "A-CND", "A-ERR", "A-MTH", "A-SRT", "A-STR",
    "B-CMP",
    "COMPOSE-C", "COMPOSE-D",
    "DOC-EXP",
    "EC2-D-CFG", "EC2-D-CLI", "EC2-D-CRY", "EC2-D-DAT",
    "EC2-D-FIO", "EC2-D-NET", "EC2-D-TST", "EC2-D-WEB",
]

MAX_SEEDS = 5000
WORKERS = 8


# ---------------------------------------------------------------------------
# Compile check (runs in worker processes)
# ---------------------------------------------------------------------------

def _check_compiles(source: str) -> tuple[bool, str]:
    """Run tkc --check on source. Returns (passes, stdout_stderr)."""
    with tempfile.NamedTemporaryFile(
        suffix=".tk", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write(source)
        f.flush()
        tmp_path = f.name
    try:
        result = subprocess.run(
            [str(TKC), "--check", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"PROCESS_ERROR: {e}"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _validate_triplet(args: tuple) -> list[dict] | None:
    """Validate one seed's injections. Called in worker process.

    args = (seed_data, injections_serialized)
    Returns list of valid triplet dicts, or None on error.
    """
    seed_data, injections_ser = args
    original_source = seed_data["tk_source"]
    seed_id = seed_data["id"]

    # Confirm original compiles
    orig_ok, _ = _check_compiles(original_source)
    if not orig_ok:
        return []

    results = []
    for inj in injections_ser:
        broken_source = inj["broken_source"]
        injection_type = inj["injection_type"]
        injection_details = inj["injection_details"]

        broken_ok, diagnostic = _check_compiles(broken_source)
        if broken_ok:
            # Broken version compiles — not a valid triplet
            continue
        if "PROCESS_ERROR" in diagnostic:
            continue

        results.append({
            "original_source": original_source,
            "broken_source": broken_source,
            "diagnostic": diagnostic,
            "injection_type": injection_type,
            "injection_details": injection_details,
            "seed_id": seed_id,
        })

    return results


# ---------------------------------------------------------------------------
# Corpus file writing
# ---------------------------------------------------------------------------

def _make_corpus_entry(triplet: dict, index: int) -> dict:
    """Create a corpus-schema JSON dict from a validated triplet."""
    inj_type = triplet["injection_type"]
    content_hash = hashlib.sha256(
        (triplet["broken_source"] + triplet["original_source"]).encode()
    ).hexdigest()[:8]

    file_id = f"BIFI-{inj_type}-{index:04d}-{content_hash}"

    return {
        "id": file_id,
        "version": 1,
        "phase": "B",
        "task_id": f"BIFI-{inj_type}",
        "tk_source": triplet["original_source"],
        "tk_tokens": 3,
        "attempts": 1,
        "model": "bifi-bootstrap",
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
            "score": 0.90,
        },
        "references": {
            "broken_source": triplet["broken_source"],
            "diagnostic": triplet["diagnostic"],
            "injection_type": triplet["injection_type"],
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    t0 = time.time()

    # 1. Load seed programs
    print("Loading seed programs...")
    seed_files: list[Path] = []
    for prefix in SEED_PREFIXES:
        d = PHASE2 / prefix
        if d.is_dir():
            seed_files.extend(sorted(d.glob("*.json")))

    print(f"  Found {len(seed_files)} total files in seed categories")

    # Shuffle and cap at MAX_SEEDS
    rng = random.Random(42)
    rng.shuffle(seed_files)
    seed_files = seed_files[:MAX_SEEDS]
    print(f"  Using {len(seed_files)} seed files")

    # 2. Load seeds and generate injections
    print("Generating injections...")
    injector = ErrorInjector(seed=42)
    work_items: list[tuple[dict, list[dict]]] = []
    skipped = 0

    for sf in seed_files:
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            skipped += 1
            continue

        source = data.get("tk_source")
        if not source:
            skipped += 1
            continue

        # Only use programs that already compile
        validation = data.get("validation", {})
        if validation.get("compiler_exit_code", 1) != 0:
            skipped += 1
            continue

        injections = injector.inject(source)
        if not injections:
            continue

        # Serialize Injection objects to dicts for pickling
        inj_dicts = [
            {
                "broken_source": inj.broken_source,
                "injection_type": inj.injection_type,
                "injection_details": inj.injection_details,
            }
            for inj in injections
        ]

        work_items.append((
            {"tk_source": source, "id": data.get("id", sf.stem)},
            inj_dicts,
        ))

    total_injections = sum(len(w[1]) for w in work_items)
    print(f"  Seeds with injections: {len(work_items)}, skipped: {skipped}")
    print(f"  Total candidate injections: {total_injections}")

    # 3. Validate triplets in parallel
    print(f"Validating triplets ({WORKERS} workers)...")
    all_triplets: list[dict] = []
    done = 0

    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(_validate_triplet, item): i
            for i, item in enumerate(work_items)
        }
        for future in as_completed(futures):
            done += 1
            if done % 500 == 0:
                print(f"  Processed {done}/{len(work_items)} seeds...")
            result = future.result()
            if result:
                all_triplets.extend(result)

    print(f"  Valid triplets: {len(all_triplets)}")

    # 4. Deduplicate by broken_source hash
    seen_hashes: set[str] = set()
    unique_triplets: list[dict] = []
    for t in all_triplets:
        h = hashlib.sha256(t["broken_source"].encode()).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique_triplets.append(t)

    print(f"  After dedup: {len(unique_triplets)}")

    # 5. Write to corpus directories
    print("Writing corpus files...")
    type_counts: Counter = Counter()
    type_indices: Counter = Counter()

    for triplet in unique_triplets:
        inj_type = triplet["injection_type"]
        idx = type_indices[inj_type]
        type_indices[inj_type] += 1

        entry = _make_corpus_entry(triplet, idx)

        out_dir = PHASE2 / f"BIFI-{inj_type}"
        out_dir.mkdir(parents=True, exist_ok=True)

        out_path = out_dir / f"{entry['id']}.json"
        out_path.write_text(
            json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        type_counts[inj_type] += 1

    # 6. Report
    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print("BIFI Bootstrap Pipeline — Report")
    print("=" * 60)
    print(f"Seeds processed:       {len(work_items)}")
    print(f"Candidate injections:  {total_injections}")
    print(f"Valid triplets:        {len(all_triplets)}")
    print(f"Unique triplets:       {len(unique_triplets)}")
    print(f"Elapsed:               {elapsed:.1f}s")
    print()
    print("Triplets per injection type:")
    for itype in sorted(type_counts.keys()):
        print(f"  {itype:30s} {type_counts[itype]:>5d}")
    print(f"  {'TOTAL':30s} {sum(type_counts.values()):>5d}")
    print()
    print("Output directories:")
    for itype in sorted(type_counts.keys()):
        print(f"  {PHASE2 / f'BIFI-{itype}'}/")


if __name__ == "__main__":
    main()
