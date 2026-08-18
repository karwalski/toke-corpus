#!/usr/bin/env python3
"""Batch companion-file generation and code pipeline runner (Story 9.2.4).

Usage::

    cd /Users/matthew.watt/tk/toke-corpus
    .venv/bin/python scripts/run_companion_gen.py --count 100 --dry-run
    .venv/bin/python scripts/run_companion_gen.py --count 100 --from-responses data/companion_responses/

Generates companion .tkc.md files, LLM prompts, and (when responses are
provided) processes them into a JSONL corpus file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

# Ensure repo root is on sys.path.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.companion_gen import CATEGORIES, CompanionGenerator
from pipeline.companion_to_code import CompanionToCodePipeline

TKC_PATH = Path("/Users/matthew.watt/tk/toke/tkc")

COMPANIONS_DIR = REPO_ROOT / "data" / "companions"
PROMPTS_DIR = REPO_ROOT / "data" / "companion_prompts"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "corpus_companion.jsonl"


def _generate_companions(count: int, base_seed: int) -> list[dict]:
    """Generate companion files deterministically.

    Returns a list of dicts with keys: index, category, difficulty,
    seed, module_name, companion_md.
    """
    gen = CompanionGenerator()
    results: list[dict] = []
    num_categories = len(CATEGORIES)

    for i in range(count):
        cat_idx = i % num_categories
        category = CATEGORIES[cat_idx]
        difficulty = (i % 5) + 1
        seed = base_seed + i

        module_name, companion_md = gen.generate_companion(category, difficulty, seed)

        results.append({
            "index": i,
            "category": category,
            "difficulty": difficulty,
            "seed": seed,
            "module_name": module_name,
            "companion_md": companion_md,
        })

    return results


def _write_companions(companions: list[dict]) -> None:
    """Write companion markdown files to data/companions/."""
    COMPANIONS_DIR.mkdir(parents=True, exist_ok=True)
    for comp in companions:
        path = COMPANIONS_DIR / f"{comp['module_name']}.tkc.md"
        path.write_text(comp["companion_md"], encoding="utf-8")


def _write_prompts(companions: list[dict]) -> None:
    """Write LLM prompts to data/companion_prompts/."""
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    pipeline = CompanionToCodePipeline()
    for comp in companions:
        prompt = pipeline.generate_code_prompt(comp["companion_md"])
        path = PROMPTS_DIR / f"{comp['module_name']}.prompt.txt"
        path.write_text(prompt, encoding="utf-8")


def _process_single(args: tuple[str, str, str, str]) -> dict | None:
    """Process a single companion + response pair.  Runs in worker process."""
    companion_md, response_text, entry_id, tkc_path = args
    pipeline = CompanionToCodePipeline(tkc_path=Path(tkc_path))
    return pipeline.process(companion_md, response_text, entry_id=entry_id)


def _process_responses(
    companions: list[dict],
    responses_dir: Path,
    output_path: Path,
) -> None:
    """Read LLM responses, validate, and write corpus JSONL."""
    pairs: list[tuple[str, str, str, str]] = []

    for comp in companions:
        resp_path = responses_dir / f"{comp['module_name']}.response.txt"
        if not resp_path.is_file():
            continue
        response_text = resp_path.read_text(encoding="utf-8")
        entry_id = f"companion-{comp['index']}-001"
        pairs.append((
            comp["companion_md"],
            response_text,
            entry_id,
            str(TKC_PATH),
        ))

    if not pairs:
        print("No response files found — nothing to process.")
        return

    workers = min(os.cpu_count() or 4, len(pairs))
    print(f"Processing {len(pairs)} responses with {workers} workers ...")

    start = time.time()
    written = 0
    passed = 0
    failed = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as out:
        with Pool(processes=workers) as pool:
            for result in pool.imap(_process_single, pairs, chunksize=50):
                if result is not None:
                    out.write(json.dumps(result, ensure_ascii=False) + "\n")
                    written += 1
                    passed += 1
                else:
                    failed += 1

    elapsed = time.time() - start

    print()
    print("=" * 60)
    print("  Companion-to-Code Pipeline - Results")
    print("=" * 60)
    print(f"  Total responses:   {len(pairs):>8,}")
    print(f"  tkc pass (written):{passed:>8,}")
    print(f"  tkc fail (dropped):{failed:>8,}")
    print(f"  Elapsed time:      {elapsed:>8.1f}s")
    print(f"  Output: {output_path}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate toke companion files and process LLM responses"
    )
    parser.add_argument(
        "--count", type=int, default=100,
        help="Number of companion files to generate (default: 100)",
    )
    parser.add_argument(
        "--seed", type=int, default=42000,
        help="Base seed for deterministic generation (default: 42000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate companions and prompts only (no code processing)",
    )
    parser.add_argument(
        "--from-responses", type=str, default=None,
        help="Directory containing LLM response files to process",
    )
    parser.add_argument(
        "--output", type=str, default=str(DEFAULT_OUTPUT),
        help="Output JSONL path",
    )
    args = parser.parse_args()

    # Step 1: Generate companions
    print(f"Generating {args.count} companion files (seed={args.seed}) ...")
    companions = _generate_companions(args.count, args.seed)
    print(f"  Generated {len(companions)} companions across {len(CATEGORIES)} categories")

    # Step 2: Write companions and prompts
    _write_companions(companions)
    print(f"  Companions written to {COMPANIONS_DIR}")

    _write_prompts(companions)
    print(f"  Prompts written to {PROMPTS_DIR}")

    if args.dry_run:
        print("\n--dry-run: stopping after companion/prompt generation.")
        return

    # Step 3: Process responses if provided
    if args.from_responses:
        responses_dir = Path(args.from_responses)
        if not responses_dir.is_dir():
            print(f"ERROR: Responses directory not found: {responses_dir}", file=sys.stderr)
            sys.exit(1)
        _process_responses(companions, responses_dir, Path(args.output))
    else:
        print("\nNo --from-responses provided. Use the generated prompts to")
        print("obtain LLM responses, then re-run with --from-responses DIR.")


if __name__ == "__main__":
    main()
