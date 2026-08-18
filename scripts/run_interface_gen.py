"""Batch runner for interface-first generation pipeline — Story 9.2.3.

Generates N interfaces deterministically, writes them to data/interfaces/,
creates LLM prompts in data/body_prompts/, and optionally assembles complete
programs from LLM responses.

Usage:
    # Dry run: generate interfaces and prompts only
    python -m scripts.run_interface_gen --count 20 --dry-run

    # Assemble from pre-generated LLM responses
    python -m scripts.run_interface_gen --count 20 --from-responses data/responses/

    # Full run with validation
    python -m scripts.run_interface_gen --count 20 --from-responses data/responses/ --validate
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from pipeline.body_gen import BodyGenerator
from pipeline.interface_gen import ALL_CATEGORIES, InterfaceGenerator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output directories
# ---------------------------------------------------------------------------

DATA_DIR = Path("data")
INTERFACES_DIR = DATA_DIR / "interfaces"
PROMPTS_DIR = DATA_DIR / "body_prompts"
CORPUS_PATH = DATA_DIR / "corpus_interface_gen.jsonl"


# ---------------------------------------------------------------------------
# Validation worker (for multiprocessing)
# ---------------------------------------------------------------------------

def _validate_one(args: tuple[str, str, str]) -> tuple[str, bool, str]:
    """Validate a single complete .tk source.  Runs in a worker process.

    Args:
        args: Tuple of (entry_id, complete_source, tkc_path_str).

    Returns:
        (entry_id, passed, diagnostic).
    """
    entry_id, source, tkc_path = args
    gen = BodyGenerator(tkc_path=Path(tkc_path))
    passed, diag = gen.validate(source)
    return entry_id, passed, diag


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def generate_batch(
    count: int,
    seed: int,
    categories: list[str] | None = None,
    difficulty_range: tuple[int, int] = (1, 5),
) -> list[tuple[str, str, str, int, int]]:
    """Generate N interface specs deterministically.

    Returns list of (entry_id, category, interface_str, difficulty, seed).
    """
    gen = InterfaceGenerator()
    cats = categories or ALL_CATEGORIES

    results: list[tuple[str, str, str, int, int]] = []
    for i in range(count):
        cat = cats[i % len(cats)]
        diff_lo, diff_hi = difficulty_range
        diff = (i % (diff_hi - diff_lo + 1)) + diff_lo
        item_seed = seed + i
        interface = gen.generate_interface(cat, diff, item_seed)
        entry_id = f"igen-{cat}-{diff}-{item_seed:06d}"
        results.append((entry_id, cat, interface, diff, item_seed))

    return results


def run_dry(
    count: int,
    seed: int,
    categories: list[str] | None = None,
    difficulty_range: tuple[int, int] = (1, 5),
) -> None:
    """Dry run: generate interfaces and prompts, write to disk."""
    INTERFACES_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    batch = generate_batch(count, seed, categories, difficulty_range)
    body_gen = BodyGenerator()

    for entry_id, cat, interface, diff, item_seed in batch:
        # Write interface
        iface_path = INTERFACES_DIR / f"{entry_id}.tki"
        iface_path.write_text(interface, encoding="utf-8")

        # Write prompt
        prompt = body_gen.generate_body_prompt(interface)
        prompt_path = PROMPTS_DIR / f"{entry_id}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

    logger.info("Dry run complete: %d interfaces, %d prompts", count, count)


def run_from_responses(
    count: int,
    seed: int,
    responses_dir: Path,
    do_validate: bool = False,
    workers: int = 4,
    categories: list[str] | None = None,
    difficulty_range: tuple[int, int] = (1, 5),
) -> None:
    """Assemble complete programs from LLM responses and optionally validate."""
    batch = generate_batch(count, seed, categories, difficulty_range)
    body_gen = BodyGenerator()

    entries: list[dict] = []
    validation_work: list[tuple[str, str, str]] = []

    for entry_id, cat, interface, diff, item_seed in batch:
        resp_path = responses_dir / f"{entry_id}.txt"
        if not resp_path.is_file():
            logger.warning("No response file for %s, skipping", entry_id)
            continue

        response_text = resp_path.read_text(encoding="utf-8")
        complete = body_gen.parse_response(response_text, interface)

        entry = {
            "id": entry_id,
            "tk_source": complete,
            "tk_tokens": len(complete.split()),
            "category": cat,
            "difficulty": diff,
            "seed": item_seed,
            "generation_method": "interface_first",
            "interface": interface,
            "source": {
                "origin": "interface_gen",
                "pipeline": "9.2.3",
                "retrieval_date": str(date.today()),
            },
            "validation": {"tkc_check": "pending"},
        }
        entries.append(entry)

        if do_validate:
            validation_work.append(
                (entry_id, complete, str(body_gen.tkc_path))
            )

    # Run validation in parallel
    if do_validate and validation_work:
        logger.info("Validating %d entries with %d workers", len(validation_work), workers)
        results_map: dict[str, tuple[bool, str]] = {}

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_validate_one, work): work[0]
                for work in validation_work
            }
            for future in as_completed(futures):
                eid, passed, diag = future.result()
                results_map[eid] = (passed, diag)

        # Update entries with validation results
        pass_count = 0
        fail_count = 0
        for entry in entries:
            eid = entry["id"]
            if eid in results_map:
                passed, diag = results_map[eid]
                entry["validation"]["tkc_check"] = "pass" if passed else "fail"
                if not passed:
                    entry["validation"]["diagnostic"] = diag
                    fail_count += 1
                else:
                    pass_count += 1

        logger.info("Validation: %d pass, %d fail", pass_count, fail_count)

    # Write JSONL
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CORPUS_PATH, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("Wrote %d entries to %s", len(entries), CORPUS_PATH)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Interface-first generation pipeline batch runner"
    )
    parser.add_argument(
        "--count", "-n", type=int, default=20,
        help="Number of interfaces to generate (default: 20)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Base random seed (default: 42)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate interfaces and prompts only (no LLM responses needed)",
    )
    parser.add_argument(
        "--from-responses", type=str, default=None,
        help="Directory containing LLM response files for assembly",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Run tkc --check validation on assembled programs",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel validation workers (default: 4)",
    )
    parser.add_argument(
        "--categories", type=str, nargs="+", default=None,
        help="Restrict to specific categories (e.g. A-MTH D-WEB)",
    )
    parser.add_argument(
        "--difficulty-min", type=int, default=1,
        help="Minimum difficulty (default: 1)",
    )
    parser.add_argument(
        "--difficulty-max", type=int, default=5,
        help="Maximum difficulty (default: 5)",
    )

    args = parser.parse_args()
    diff_range = (args.difficulty_min, args.difficulty_max)

    if args.dry_run:
        run_dry(args.count, args.seed, args.categories, diff_range)
    elif args.from_responses:
        run_from_responses(
            args.count,
            args.seed,
            Path(args.from_responses),
            do_validate=args.validate,
            workers=args.workers,
            categories=args.categories,
            difficulty_range=diff_range,
        )
    else:
        # Default to dry run if no mode specified
        logger.info("No mode specified, running dry run")
        run_dry(args.count, args.seed, args.categories, diff_range)


if __name__ == "__main__":
    main()
