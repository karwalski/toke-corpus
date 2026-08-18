#!/usr/bin/env python3
"""License compliance checker for the toke-corpus.

Validates that all corpus entries with third-party source material have
proper license attribution, and that required license/attribution files
are present and complete.

Usage:
    python scripts/license_check.py
    python scripts/license_check.py --verbose
    python scripts/license_check.py --corpus-dir data/
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Known valid SPDX-style license identifiers used in the corpus
KNOWN_LICENSES = {
    "CC-BY-SA-3.0",
    "CC-BY-SA-4.0",
    "CC-BY-4.0",
    "CC-BY-3.0",
    "GFDL-1.2",
    "MIT",
    "Apache-2.0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "Unlicense",
    "MPL-2.0",
}

# Licenses that require manual review before corpus inclusion
COPYLEFT_LICENSES = {
    "GPL",
    "GPL-2.0",
    "GPL-3.0",
    "LGPL",
    "LGPL-2.1",
    "LGPL-3.0",
    "AGPL",
    "AGPL-3.0",
}

# Sources that must have a license field in their corpus entries
SOURCES_REQUIRING_LICENSE = {
    "exercism",
    "rosettacode",
    "30-seconds-of-python",
    "go-by-example",
}

# Expected license per known source
EXPECTED_SOURCE_LICENSES = {
    "exercism": "CC-BY-SA-3.0",
    "rosettacode": "GFDL-1.2",
    "30-seconds-of-python": "CC-BY-4.0",
    "go-by-example": "CC-BY-3.0",
}

# Required project-level files
REQUIRED_FILES = [
    "LICENSE",
    "NOTICE",
    "LICENSES.md",
    "ATTRIBUTION.md",
]

# Benchmark directories and their expected licenses
BENCHMARK_LICENSES = {
    "human-eval": "MIT",
    "apps": "MIT",
    "code_contests": "Apache-2.0",
    "TACO": "Apache-2.0",
    "leetcode": "CC-BY-SA-4.0",
}


class CheckResult:
    """Accumulates pass/fail/warn results."""

    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.warnings: list[str] = []

    def ok(self, msg: str) -> None:
        self.passed.append(msg)

    def fail(self, msg: str) -> None:
        self.failed.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0


def check_required_files(result: CheckResult) -> None:
    """Verify that all required license/attribution files exist."""
    for filename in REQUIRED_FILES:
        path = PROJECT_ROOT / filename
        if path.is_file():
            size = path.stat().st_size
            if size > 100:
                result.ok(f"  {filename} exists ({size} bytes)")
            else:
                result.fail(f"  {filename} exists but appears empty ({size} bytes)")
        else:
            result.fail(f"  {filename} is MISSING")


def check_benchmark_licenses(result: CheckResult) -> None:
    """Verify each benchmark directory has a LICENSE file."""
    benchmarks_dir = PROJECT_ROOT / "data" / "benchmarks"
    if not benchmarks_dir.is_dir():
        result.warn("  data/benchmarks/ directory not found, skipping")
        return

    for name, expected_license in BENCHMARK_LICENSES.items():
        bench_dir = benchmarks_dir / name
        if not bench_dir.is_dir():
            result.warn(f"  Benchmark {name}/ not found, skipping")
            continue

        license_path = bench_dir / "LICENSE"
        if license_path.is_file():
            result.ok(f"  {name}/LICENSE exists (expected: {expected_license})")
        else:
            result.fail(f"  {name}/LICENSE is MISSING (expected: {expected_license})")

    # Check for any benchmark dirs without a known license mapping
    for bench_dir in sorted(benchmarks_dir.iterdir()):
        if bench_dir.is_dir() and bench_dir.name not in BENCHMARK_LICENSES:
            license_path = bench_dir / "LICENSE"
            if license_path.is_file():
                result.warn(
                    f"  {bench_dir.name}/ has LICENSE but is not in "
                    f"BENCHMARK_LICENSES mapping"
                )
            else:
                result.fail(
                    f"  {bench_dir.name}/ has no LICENSE and is not in "
                    f"BENCHMARK_LICENSES mapping"
                )


def check_corpus_entries(
    result: CheckResult, corpus_dir: Path, verbose: bool = False
) -> None:
    """Scan JSONL corpus files for entries with source metadata and validate license fields."""
    jsonl_files = sorted(corpus_dir.glob("*.jsonl"))
    if not jsonl_files:
        result.warn(f"  No .jsonl files found in {corpus_dir}")
        return

    total_entries = 0
    entries_with_source = 0
    entries_missing_license = 0
    entries_unknown_license = 0
    entries_copyleft = 0
    license_counts: dict[str, int] = {}
    origin_counts: dict[str, int] = {}
    problems: list[str] = []

    for jsonl_path in jsonl_files:
        try:
            with open(jsonl_path, encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        problems.append(
                            f"  {jsonl_path.name}:{line_num} - invalid JSON"
                        )
                        continue

                    total_entries += 1
                    entry_id = entry.get("id", f"line-{line_num}")
                    source = entry.get("source")

                    if source is None:
                        # No source field -- this is LLM-generated content, OK
                        continue

                    entries_with_source += 1
                    origin = source.get("origin", "unknown")
                    origin_counts[origin] = origin_counts.get(origin, 0) + 1
                    license_val = source.get("license")

                    if not license_val:
                        entries_missing_license += 1
                        if verbose:
                            problems.append(
                                f"  {jsonl_path.name}: {entry_id} "
                                f"(origin={origin}) -- MISSING license field"
                            )
                        continue

                    license_counts[license_val] = (
                        license_counts.get(license_val, 0) + 1
                    )

                    if license_val == "unknown":
                        entries_unknown_license += 1
                        if verbose:
                            problems.append(
                                f"  {jsonl_path.name}: {entry_id} "
                                f"(origin={origin}) -- license is 'unknown'"
                            )

                    elif license_val in COPYLEFT_LICENSES:
                        entries_copyleft += 1
                        problems.append(
                            f"  {jsonl_path.name}: {entry_id} "
                            f"(origin={origin}) -- COPYLEFT license: {license_val}"
                        )

                    elif license_val not in KNOWN_LICENSES:
                        if verbose:
                            problems.append(
                                f"  {jsonl_path.name}: {entry_id} "
                                f"(origin={origin}) -- unrecognised license: "
                                f"{license_val}"
                            )

                    # Check expected license for known sources
                    if origin in EXPECTED_SOURCE_LICENSES:
                        expected = EXPECTED_SOURCE_LICENSES[origin]
                        if license_val != expected:
                            problems.append(
                                f"  {jsonl_path.name}: {entry_id} "
                                f"(origin={origin}) -- expected license "
                                f"{expected}, got {license_val}"
                            )

        except OSError as exc:
            result.fail(f"  Cannot read {jsonl_path.name}: {exc}")

    # Report summary
    result.ok(f"  Scanned {len(jsonl_files)} JSONL files, {total_entries} entries")
    result.ok(f"  {entries_with_source} entries have source metadata")

    if entries_missing_license > 0:
        result.fail(
            f"  {entries_missing_license} sourced entries MISSING license field"
        )
    else:
        if entries_with_source > 0:
            result.ok("  All sourced entries have a license field")

    if entries_unknown_license > 0:
        result.warn(
            f"  {entries_unknown_license} entries have license='unknown'"
        )

    if entries_copyleft > 0:
        result.fail(
            f"  {entries_copyleft} entries use copyleft licenses (review required)"
        )

    if license_counts:
        result.ok("  License distribution in corpus entries:")
        for lic, count in sorted(license_counts.items(), key=lambda x: -x[1]):
            marker = ""
            if lic == "unknown":
                marker = " [REVIEW]"
            elif lic in COPYLEFT_LICENSES:
                marker = " [COPYLEFT]"
            result.ok(f"    {lic}: {count}{marker}")

    if origin_counts:
        result.ok("  Source origin distribution:")
        for origin, count in sorted(origin_counts.items(), key=lambda x: -x[1]):
            result.ok(f"    {origin}: {count}")

    for problem in problems:
        result.fail(problem)


def check_ingestor_license_fields(result: CheckResult) -> None:
    """Verify that ingestor source files set a license field in corpus entries."""
    ingest_dir = PROJECT_ROOT / "ingest"
    if not ingest_dir.is_dir():
        result.warn("  ingest/ directory not found, skipping")
        return

    ingestors = {
        "exercism.py": "CC-BY-SA-3.0",
        "rosetta.py": "GFDL-1.2",
        "snippets.py": "CC-BY-4.0",  # primary; also has CC-BY-3.0 for Go
    }

    for filename, expected in ingestors.items():
        path = ingest_dir / filename
        if not path.is_file():
            result.warn(f"  {filename} not found")
            continue

        content = path.read_text(encoding="utf-8")
        if '"license"' in content or "'license'" in content:
            result.ok(f"  {filename} sets license field in corpus entries")
        else:
            result.fail(
                f"  {filename} does NOT set a license field in corpus entries"
            )

        if expected in content:
            result.ok(f"  {filename} references expected license {expected}")
        else:
            result.warn(
                f"  {filename} does not contain expected license string "
                f"'{expected}'"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate license compliance for toke-corpus"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-entry details for issues",
    )
    parser.add_argument(
        "--corpus-dir",
        default=None,
        help="Path to corpus data directory (default: data/)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    corpus_dir = Path(args.corpus_dir) if args.corpus_dir else PROJECT_ROOT / "data"

    result = CheckResult()

    # --- Section 1: Required project files ---
    print("=" * 60)
    print("1. Required license/attribution files")
    print("=" * 60)
    check_required_files(result)

    # --- Section 2: Benchmark licenses ---
    print()
    print("=" * 60)
    print("2. Benchmark dataset licenses")
    print("=" * 60)
    check_benchmark_licenses(result)

    # --- Section 3: Ingestor license fields ---
    print()
    print("=" * 60)
    print("3. Ingestor license field checks")
    print("=" * 60)
    check_ingestor_license_fields(result)

    # --- Section 4: Corpus entry license metadata ---
    print()
    print("=" * 60)
    print("4. Corpus entry license metadata")
    print("=" * 60)
    check_corpus_entries(result, corpus_dir, verbose=args.verbose)

    # --- Summary ---
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for msg in result.passed:
        print(f"  PASS  {msg}")
    for msg in result.warnings:
        print(f"  WARN  {msg}")
    for msg in result.failed:
        print(f"  FAIL  {msg}")

    print()
    total = len(result.passed) + len(result.failed) + len(result.warnings)
    print(
        f"  {len(result.passed)} passed, "
        f"{len(result.warnings)} warnings, "
        f"{len(result.failed)} failed "
        f"({total} checks)"
    )

    if result.success:
        print()
        print("  LICENSE COMPLIANCE: PASS")
        return 0
    else:
        print()
        print("  LICENSE COMPLIANCE: FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
