"""Batch runner for the Rosetta Code ingestion pipeline.

Clones the RosettaCodeData repo, ingests Python solutions first
(higher transpile success rate), then C. Deduplicates: if both
Python and C produce valid toke for the same task, keeps the Python
version. Uses multiprocessing for transpile+validate.

Outputs data/corpus_transpiled_rosetta.jsonl.
"""

import json
import logging
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ingest.rosetta import (
    IngestReport,
    RosettaIngestor,
    RosettaTask,
)
from transpile.c_to_toke import TranspileError as CTranspileError
from transpile.py_to_toke import TranspileError as PyTranspileError

logger = logging.getLogger(__name__)

OUTPUT_PATH = Path("data/corpus_transpiled_rosetta.jsonl")
LANGUAGES = ["python", "c"]


def _process_single_task(
    task_name: str,
    task_slug: str,
    lang: str,
    source: str,
) -> dict | None:
    """Process one task/language combination in a worker process.

    Returns a corpus entry dict or None on failure. Does not use
    IngestReport (stats are aggregated in the main process).
    """
    ingestor = RosettaIngestor()
    try:
        tk_source = ingestor.transpile_solution(source, lang, task_name)
    except (PyTranspileError, CTranspileError, ValueError):
        return None

    passed, diag = ingestor.validate_toke(tk_source)

    repaired = False
    if not passed:
        fixed = ingestor.auto_repair(tk_source, diag)
        if fixed is not None:
            passed_after, _ = ingestor.validate_toke(fixed)
            if passed_after:
                tk_source = fixed
                passed = True
                repaired = True

    if not passed:
        return None

    tk_tokens = len(tk_source.split())
    return {
        "id": f"rosetta-{lang}-{task_slug}-001",
        "tk_source": tk_source,
        "tk_tokens": tk_tokens,
        "generation_method": "transpilation",
        "source": {
            "origin": "rosettacode",
            "task": task_name,
            "language": lang,
            "license": "GFDL-1.2",
            "retrieval_date": str(date.today()),
        },
        "validation": {"tkc_check": "pass"},
        "_repaired": repaired,
    }


def run_batch(max_workers: int = 4) -> IngestReport:
    """Clone, transpile, validate and write the Rosetta Code corpus.

    Args:
        max_workers: Number of parallel worker processes

    Returns:
        IngestReport with aggregate statistics
    """
    report = IngestReport()
    ingestor = RosettaIngestor()

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "RosettaCodeData"
        logger.info("Cloning Rosetta Code data repo...")
        ingestor.clone_repo(repo_dir)

        tasks = ingestor.list_tasks(repo_dir)
        report.total_tasks = len(tasks)
        logger.info("Found %d tasks with supported languages", len(tasks))

        # Collect work items: (task, lang, source) — Python first for
        # dedup priority
        work_items: list[tuple[RosettaTask, str, str]] = []
        for task in tasks:
            for lang in LANGUAGES:
                if lang in task.languages:
                    try:
                        source = ingestor.extract_solution(task, lang)
                        work_items.append((task, lang, source))
                    except Exception as exc:
                        logger.debug(
                            "Could not read %s/%s: %s", task.name, lang, exc
                        )

        logger.info("Submitting %d work items to %d workers", len(work_items), max_workers)

        # Process in parallel
        results_by_slug: dict[str, dict] = {}  # slug -> best entry (Python preferred)
        futures = {}

        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            for task, lang, source in work_items:
                future = pool.submit(
                    _process_single_task,
                    task.name,
                    task.slug,
                    lang,
                    source,
                )
                futures[future] = (task, lang)

            for future in as_completed(futures):
                task, lang = futures[future]
                try:
                    entry = future.result()
                except Exception as exc:
                    logger.debug("Worker error for %s/%s: %s", task.name, lang, exc)
                    report.transpile_fail += 1
                    continue

                if entry is None:
                    report.transpile_fail += 1
                    continue

                report.transpile_success += 1
                report.tkc_pass += 1
                if entry.get("_repaired"):
                    report.auto_repair_success += 1

                slug = task.slug
                # Deduplication: prefer Python over C
                if slug not in results_by_slug:
                    results_by_slug[slug] = entry
                elif entry["source"]["language"] == "python":
                    # Python replaces C
                    results_by_slug[slug] = entry
                # else: keep existing (Python already present)

        # tkc_fail = transpile successes that did not pass validation
        # (already counted as transpile_fail above for items returning None)
        # Recalculate: items that transpiled but failed tkc are in transpile_fail
        # count. We just track totals.

    # Write output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as out:
        for entry in results_by_slug.values():
            # Remove internal flag before writing
            entry.pop("_repaired", None)
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            report.entries_written += 1

    return report


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Ingest Rosetta Code tasks into toke corpus"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default: 4)",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help=f"Output JSONL path (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args()

    global OUTPUT_PATH
    OUTPUT_PATH = Path(args.output)

    result = run_batch(max_workers=args.workers)

    print(f"Total tasks:          {result.total_tasks}")
    print(f"Transpile success:    {result.transpile_success}")
    print(f"Transpile fail:       {result.transpile_fail}")
    print(f"tkc pass:             {result.tkc_pass}")
    print(f"tkc fail:             {result.tkc_fail}")
    print(f"Auto-repair success:  {result.auto_repair_success}")
    print(f"Entries written:      {result.entries_written}")


if __name__ == "__main__":
    main()
