#!/usr/bin/env python3
"""Batch runner for snippet collection ingestion (Story 9.4.3).

Clones snippet repos (30-seconds-of-python, go-by-example), extracts
small self-contained functions, transpiles Python snippets to toke,
validates with tkc, and outputs a JSONL corpus file.

Usage::

    cd /Users/matthew.watt/tk/toke-corpus
    .venv/bin/python scripts/run_snippet_ingest.py
    .venv/bin/python scripts/run_snippet_ingest.py --output data/snippets.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
from multiprocessing import Pool
from pathlib import Path

# Ensure repo root is on sys.path so packages are importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ingest.snippets import (
    DEFAULT_REPOS,
    IngestReport,
    Snippet,
    SnippetIngestor,
    SnippetRepo,
)
from transpile.py_to_toke import PyToTokeTranspiler, TranspileError

DEFAULT_OUTPUT = REPO_ROOT / "data" / "corpus_transpiled_snippets.jsonl"
TKC_BINARY = Path("/Users/matthew.watt/tk/toke/tkc")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker function for multiprocessing (must be top-level for pickling)
# ---------------------------------------------------------------------------


def _process_one(args: tuple[dict, str]) -> dict | None:
    """Process a single (snippet_dict, tkc_path) tuple.

    Reconstructs Snippet from dict and runs transpile + validate.
    """
    snippet_dict, tkc_path = args
    snippet = Snippet(**snippet_dict)

    ingestor = SnippetIngestor(tkc_path=Path(tkc_path))

    # Go snippets not yet transpilable
    if snippet.language == "go":
        return None

    tk_source = ingestor.transpile_snippet(snippet)
    if tk_source is None:
        return None

    passed, diag = ingestor.validate_toke(tk_source)

    if not passed:
        repaired = ingestor.auto_repair(tk_source, diag)
        if repaired is not None:
            passed_after, _ = ingestor.validate_toke(repaired)
            if passed_after:
                tk_source = repaired
                passed = True

    if not passed:
        return None

    tk_tokens = len(tk_source.split())
    sanitized_name = re.sub(r"[^a-z0-9]", "", snippet.name.lower())
    return {
        "id": f"snippet-{snippet.repo_name}-{sanitized_name}-001",
        "tk_source": tk_source,
        "tk_tokens": tk_tokens,
        "generation_method": "transpilation",
        "source": {
            "origin": snippet.repo_name,
            "snippet": snippet.name,
            "language": snippet.language,
            "license": snippet.license,
            "retrieval_date": str(date.today()),
        },
        "validation": {"tkc_check": "pass"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch snippet ingestion into toke corpus"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSONL path (default: data/corpus_transpiled_snippets.jsonl)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="Number of parallel workers (default: min(4, cpu_count))",
    )
    parser.add_argument(
        "--tkc",
        type=Path,
        default=TKC_BINARY,
        help="Path to tkc binary",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    start = time.time()
    ingestor = SnippetIngestor(tkc_path=args.tkc)

    # --- Phase 1: Clone repos and extract snippets ---
    logger.info("Phase 1: Cloning repos and extracting snippets")
    all_snippets: list[Snippet] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for repo in DEFAULT_REPOS:
            repo_dir = Path(tmpdir) / repo.name
            try:
                ingestor.clone_repo(repo, repo_dir)
            except subprocess.CalledProcessError as exc:
                logger.error("Failed to clone %s: %s", repo.name, exc)
                continue

            if repo.language == "python":
                snippets = ingestor.extract_snippets_python_md(
                    repo_dir, repo.snippet_glob
                )
            elif repo.language == "go":
                snippets = ingestor.extract_snippets_go(repo_dir)
            else:
                logger.warning("Unsupported language: %s", repo.language)
                continue

            all_snippets.extend(snippets)
            logger.info(
                "Extracted %d snippets from %s", len(snippets), repo.name
            )

    logger.info("Total snippets extracted: %d", len(all_snippets))

    # --- Phase 2: Transpile + validate using multiprocessing ---
    logger.info(
        "Phase 2: Transpiling and validating with %d workers", args.workers
    )

    work_items = [
        (
            {
                "name": s.name,
                "source": s.source,
                "language": s.language,
                "repo_name": s.repo_name,
                "license": s.license,
            },
            str(args.tkc),
        )
        for s in all_snippets
    ]

    results: list[dict | None] = []
    if args.workers > 1 and len(work_items) > 10:
        with Pool(processes=args.workers) as pool:
            results = pool.map(_process_one, work_items)
    else:
        results = [_process_one(item) for item in work_items]

    # --- Phase 3: Write output ---
    args.output.parent.mkdir(parents=True, exist_ok=True)

    report = IngestReport()
    report.total_snippets = len(all_snippets)

    entries_written = 0
    with open(args.output, "w", encoding="utf-8") as out:
        for snippet, result in zip(all_snippets, results):
            if snippet.language == "go":
                report.skipped_go += 1
                continue
            if result is not None:
                out.write(json.dumps(result, ensure_ascii=False) + "\n")
                entries_written += 1
                report.transpile_success += 1
                report.tkc_pass += 1
            else:
                if snippet.language == "python":
                    report.transpile_fail += 1

    report.entries_written = entries_written
    elapsed = time.time() - start

    # --- Stats report ---
    print("\n" + "=" * 50)
    print("Snippet Ingestion Report")
    print("=" * 50)
    print(f"Total snippets:       {report.total_snippets}")
    print(f"Transpile success:    {report.transpile_success}")
    print(f"Transpile fail:       {report.transpile_fail}")
    print(f"tkc pass:             {report.tkc_pass}")
    print(f"tkc fail:             {report.tkc_fail}")
    print(f"Auto-repair success:  {report.auto_repair_success}")
    print(f"Skipped (Go):         {report.skipped_go}")
    print(f"Entries written:      {report.entries_written}")
    print(f"Output:               {args.output}")
    print(f"Elapsed:              {elapsed:.1f}s")
    print("=" * 50)


if __name__ == "__main__":
    main()
