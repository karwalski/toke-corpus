#!/usr/bin/env python3
"""validate_schema.py — Validate / migrate corpus records against schema v2.

Story 10.8.1. Canonical schema documented in toke-corpus/docs/corpus_schema.md.

Usage:
    python scripts/validate_schema.py [--corpus-dir PATH] [--migrate]
                                      [--strict] [--sample N]

Modes:
    default:   validate every record, report non-conformant records
    --migrate: also rewrite v1 records with v2 placeholder fields
    --strict:  exit non-zero if ANY record fails validation (default lenient)
    --sample:  only check N random records per category (smoke test)
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"

# ---------------------------------------------------------------------------
# Schema v2 defaults
# ---------------------------------------------------------------------------

REQUIRED_TOP_LEVEL = {
    "id": str,
    "version": int,
    "phase": str,
    "task_id": str,
    "category": str,
    "tk_source": str,
    "tk_tokens": int,
    "references": dict,
}

# Fields required only post-enrichment stories — validator accepts records
# missing them, but --migrate fills them with placeholder values.
ENRICHMENT_FIELDS = [
    "syntax_audit",
    "compile_check",
    "imported_modules",
    "imports_unresolved",
    "expected_input",
    "expected_output",
    "runtime_check",
    "judge_output_check",
]


def default_syntax_audit() -> dict:
    return {
        "violations": [],
        "autofix_applied": False,
        "phase2_conformant": False,
        "collisions": [],
        "checked_at": None,
    }


def default_compile_check() -> dict:
    return {
        "passed": False,
        "exit_code": None,
        "error_codes": [],
        "stage": None,
        "diagnostic": None,
        "inverted": False,
        "expected_error_code": None,
        "ran_at": None,
        "tkc_version": None,
    }


def default_runtime_check() -> dict:
    return {
        "linked": False,
        "ran": False,
        "exit_code": None,
        "stdout": None,
        "stderr": None,
        "captured_at": None,
        "timeout_s": None,
        "link_errors": [],
    }


def default_judge_output_check() -> dict:
    return {
        "verified": False,
        "correct": None,
        "reasoning": None,
        "confidence": None,
        "judge_model": None,
        "judged_at": None,
    }


def default_placeholder(field: str) -> Any:
    match field:
        case "syntax_audit":
            return default_syntax_audit()
        case "compile_check":
            return default_compile_check()
        case "imported_modules":
            return []
        case "imports_unresolved":
            return []
        case "expected_input":
            return None
        case "expected_output":
            return None
        case "runtime_check":
            return default_runtime_check()
        case "judge_output_check":
            return default_judge_output_check()
        case _:
            raise ValueError(f"unknown enrichment field: {field}")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_record(record: dict, category_dir: str) -> list[str]:
    """Return a list of validation error strings (empty list = valid)."""
    errors: list[str] = []

    # Required top-level fields + types
    for name, ty in REQUIRED_TOP_LEVEL.items():
        if name not in record:
            errors.append(f"missing required field '{name}'")
            continue
        if not isinstance(record[name], ty):
            errors.append(
                f"field '{name}' wrong type: expected {ty.__name__}, "
                f"got {type(record[name]).__name__}"
            )

    # Version must be 2
    if record.get("version") != 2:
        errors.append(f"version={record.get('version')} (expected 2)")

    # Category must match directory
    if record.get("category") != category_dir:
        errors.append(
            f"category={record.get('category')!r} does not match "
            f"directory {category_dir!r}"
        )

    # Enrichment fields must all be present (may be defaults)
    for field in ENRICHMENT_FIELDS:
        if field not in record:
            errors.append(f"missing enrichment field '{field}'")

    # Cross-field consistency
    sa = record.get("syntax_audit")
    if isinstance(sa, dict):
        if sa.get("autofix_applied") and "original_source" not in sa:
            errors.append(
                "syntax_audit.autofix_applied=true but original_source missing"
            )

    rc = record.get("runtime_check")
    if isinstance(rc, dict):
        if rc.get("ran") and rc.get("captured_at") is None:
            errors.append("runtime_check.ran=true but captured_at is null")

    return errors


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate_record(record: dict, category_dir: str) -> bool:
    """Fill missing v2 fields with defaults. Return True if record was modified."""
    modified = False

    if record.get("version") != 2:
        record["version"] = 2
        modified = True

    if "category" not in record:
        record["category"] = category_dir
        modified = True

    for field in ENRICHMENT_FIELDS:
        if field not in record:
            record[field] = default_placeholder(field)
            modified = True

    return modified


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def iter_records(corpus_dir: Path, sample_per_cat: int | None = None):
    """Yield (path, record, category_dir, parse_error)."""
    for cat_dir in sorted(corpus_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        files = [p for p in cat_dir.iterdir() if p.suffix == ".json"]
        if sample_per_cat is not None and len(files) > sample_per_cat:
            files = random.sample(files, sample_per_cat)
        for path in files:
            try:
                with open(path) as f:
                    record = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                yield (path, None, cat_dir.name, str(exc))
                continue
            yield (path, record, cat_dir.name, None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate/migrate corpus records to schema v2")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--migrate", action="store_true",
                        help="Also rewrite v1 records with v2 defaults")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 if any record fails validation")
    parser.add_argument("--sample", type=int, default=None,
                        help="Only check N random records per category")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every error (default: first 20 per category)")
    args = parser.parse_args()

    random.seed(42)

    cat_seen = collections.Counter()
    cat_errors = collections.Counter()
    cat_migrated = collections.Counter()
    parse_errors = 0
    total_errors: list[str] = []
    printed_per_cat: dict[str, int] = collections.defaultdict(int)

    for path, record, cat, parse_err in iter_records(args.corpus_dir, args.sample):
        cat_seen[cat] += 1

        if parse_err:
            parse_errors += 1
            cat_errors[cat] += 1
            if args.verbose or printed_per_cat[cat] < 20:
                print(f"  PARSE FAIL {cat}/{path.name}: {parse_err}", file=sys.stderr)
                printed_per_cat[cat] += 1
            continue

        if args.migrate:
            if migrate_record(record, cat):
                cat_migrated[cat] += 1
                with open(path, "w") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

        errors = validate_record(record, cat)
        if errors:
            cat_errors[cat] += 1
            total_errors.append(f"{cat}/{path.name}: {'; '.join(errors)}")
            if args.verbose or printed_per_cat[cat] < 20:
                print(f"  FAIL {cat}/{path.name}: {'; '.join(errors)}", file=sys.stderr)
                printed_per_cat[cat] += 1

    # Summary
    print("\n" + "=" * 70, file=sys.stderr)
    print("  Schema validation summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    total_seen = sum(cat_seen.values())
    total_err = sum(cat_errors.values())
    total_mig = sum(cat_migrated.values())
    print(f"  Records scanned: {total_seen}", file=sys.stderr)
    print(f"  Parse errors:    {parse_errors}", file=sys.stderr)
    print(f"  Validation fails: {total_err}", file=sys.stderr)
    if args.migrate:
        print(f"  Migrated to v2:  {total_mig}", file=sys.stderr)

    print(f"\n  Per-category (seen / fails / migrated):", file=sys.stderr)
    for cat in sorted(cat_seen):
        seen = cat_seen[cat]
        fails = cat_errors[cat]
        mig = cat_migrated[cat]
        flag = " " if fails == 0 else "!"
        print(f"    {flag} {cat:35s}  {seen:6d}  fails={fails:6d}  mig={mig:6d}", file=sys.stderr)

    if args.strict and total_err > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
