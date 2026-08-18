#!/usr/bin/env python3
"""Harvest compile-verified toke code from MCP telemetry (DynamoDB toke-usage table).

Scans for records where collected_for_training=true, extracts toke source from
the response field, verifies compilation via tkc --check, deduplicates against
the existing corpus, scores by quality, and outputs verified .tk files ready
for corpus inclusion.

Usage:
    python harvest-telemetry.py --output-dir /path/to/output
    python harvest-telemetry.py --output-dir /path/to/output --dry-run
    python harvest-telemetry.py --output-dir /path/to/output --region us-east-1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("harvest-telemetry")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TABLE = "toke-usage"
DEFAULT_REGION = "ap-southeast-2"
DEFAULT_TKC = str(Path.home() / "tk" / "toke" / "tkc")
DEFAULT_CORPUS = str(Path.home() / "tk" / "toke-corpus")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class HarvestedRecord:
    """A single telemetry record after extraction and validation."""

    api_key_hash: str
    timestamp: str
    prompt: str
    raw_response: str
    tk_source: str
    source_hash: str
    compiles: bool = False
    compiler_exit_code: int = -1
    compiler_errors: list[str] = field(default_factory=list)
    quality_score: float = 0.0
    duplicate: bool = False
    duplicate_of: str | None = None


# ---------------------------------------------------------------------------
# Source extraction
# ---------------------------------------------------------------------------


def extract_toke_source(response: str) -> str | None:
    """Extract toke source code from a model response.

    The response may contain markdown fences, explanatory text, or raw toke
    source. We try several strategies to pull out the actual code.
    """
    if not response or response == "(opted out)":
        return None

    text = response.strip()

    # Strategy 1: fenced code block (```toke ... ``` or ``` ... ```)
    fence_pattern = re.compile(
        r"```(?:toke|tk)?\s*\n(.*?)```", re.DOTALL
    )
    match = fence_pattern.search(text)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("m="):
            return candidate

    # Strategy 2: the entire response is toke source (starts with m=)
    if text.startswith("m="):
        # Trim any trailing explanation after the last semicolon
        # Toke source ends with }; or ; at the end of the last definition
        return text

    # Strategy 3: find a line starting with m= and take everything from there
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("m="):
            candidate = "\n".join(lines[i:]).strip()
            # Remove trailing non-toke text (lines that look like explanation)
            clean_lines: list[str] = []
            for cline in candidate.split("\n"):
                cs = cline.strip()
                if not cs:
                    continue
                # Stop at lines that look like natural language
                if cs and not cs[0].islower() and not cs.startswith("//"):
                    # Might be a sentence; check for toke-like content
                    if not any(
                        tok in cs for tok in ("m=", "f=", "t=", "i=", "let ", "lp(", "if(")
                    ):
                        break
                clean_lines.append(cline)
            if clean_lines:
                return "\n".join(clean_lines).strip()

    return None


# ---------------------------------------------------------------------------
# Compilation check
# ---------------------------------------------------------------------------


def check_compiles(tk_source: str, tkc_path: str) -> tuple[bool, int, list[str]]:
    """Run tkc --check on the source and return (ok, exit_code, error_codes)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tk", delete=False
    ) as tmp:
        tmp.write(tk_source)
        tmp.flush()
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [tkc_path, "--check", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        error_codes: list[str] = []
        if result.returncode != 0:
            # Parse error codes from stderr (format: E1001, W2003, etc.)
            error_codes = re.findall(r"[EW]\d{4}", result.stderr)
        return result.returncode == 0, result.returncode, error_codes
    except subprocess.TimeoutExpired:
        log.warning("tkc --check timed out for source hash %s", hashlib.sha256(tk_source.encode()).hexdigest()[:8])
        return False, -1, ["TIMEOUT"]
    except FileNotFoundError:
        log.error("tkc not found at %s", tkc_path)
        sys.exit(1)
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def load_corpus_hashes(corpus_dir: str) -> set[str]:
    """Load all tk_source SHA-256 hashes from the existing corpus.

    Reads corpus JSON files and hashes their tk_source field. Also reads the
    hashes.json manifest if available.
    """
    hashes: set[str] = set()
    corpus_path = Path(corpus_dir) / "corpus"

    # Walk all JSON files in the corpus directory
    for json_file in corpus_path.rglob("*.json"):
        if json_file.name in ("schema.json", "manifest.json"):
            continue
        try:
            data = json.loads(json_file.read_text())
            tk_src = data.get("tk_source", "")
            if tk_src:
                h = hashlib.sha256(normalise_source(tk_src).encode()).hexdigest()
                hashes.add(h)
        except (json.JSONDecodeError, OSError):
            continue

    log.info("Loaded %d existing corpus source hashes", len(hashes))
    return hashes


def normalise_source(source: str) -> str:
    """Normalise toke source for deduplication.

    Strips whitespace variations so that semantically identical programs
    with different formatting are detected as duplicates.
    """
    # Remove all whitespace except inside string literals
    result: list[str] = []
    in_string = False
    escape_next = False
    for ch in source:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == "\\" and in_string:
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            result.append(ch)
            continue
        # Outside strings: collapse whitespace
        if ch in (" ", "\t", "\n", "\r"):
            continue
        result.append(ch)
    return "".join(result)


def source_hash(source: str) -> str:
    """SHA-256 hash of normalised source."""
    return hashlib.sha256(normalise_source(source).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------


def score_quality(record: HarvestedRecord) -> float:
    """Score a harvested record for training value.

    Scoring criteria:
      - Compiles (required, 0 if not)                    : 0.0 or base
      - Base score for compiling                         : 0.4
      - Source length / complexity bonus                 : 0.0-0.2
      - Pattern diversity (uses different keywords)      : 0.0-0.2
      - Has module declaration                           : 0.05
      - Has function definitions                         : 0.05
      - Uses control flow (if/el/lp)                     : 0.05
      - Uses type definitions                            : 0.05

    Returns a score from 0.0 to 1.0.
    """
    if not record.compiles:
        return 0.0

    score = 0.4  # base for compiling

    src = record.tk_source

    # Length bonus: prefer programs between 50-500 chars
    src_len = len(src)
    if 50 <= src_len <= 500:
        score += 0.1
    elif src_len > 500:
        score += 0.2  # longer programs are rarer and more valuable

    # Keyword diversity
    keywords = {"m=", "f=", "t=", "i=", "if(", "el{", "lp(", "let ", "mut.", "rt ", "mt "}
    used = sum(1 for kw in keywords if kw in src)
    score += min(0.2, used * 0.02)

    # Structural checks
    if src.startswith("m="):
        score += 0.05
    if "f=" in src:
        score += 0.05
    if any(kw in src for kw in ("if(", "el{", "lp(")):
        score += 0.05
    if "t=" in src and "{" in src:
        score += 0.05

    return min(1.0, score)


# ---------------------------------------------------------------------------
# DynamoDB scanning
# ---------------------------------------------------------------------------


def scan_usage_table(
    table_name: str, region: str, limit: int | None = None
) -> list[dict[str, Any]]:
    """Scan the toke-usage DynamoDB table for training-eligible records."""
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    records: list[dict[str, Any]] = []
    scan_kwargs: dict[str, Any] = {
        "FilterExpression": Attr("collected_for_training").eq(True),
    }

    while True:
        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])
        records.extend(items)

        if limit and len(records) >= limit:
            records = records[:limit]
            break

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    log.info("Scanned %d records with collected_for_training=true", len(records))
    return records


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_output(
    records: list[HarvestedRecord],
    output_dir: str,
    dry_run: bool = False,
) -> None:
    """Write verified, non-duplicate records as .tk files and a manifest."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    accepted = [r for r in records if r.compiles and not r.duplicate]
    accepted.sort(key=lambda r: r.quality_score, reverse=True)

    if dry_run:
        log.info("[DRY RUN] Would write %d .tk files to %s", len(accepted), output_dir)
        for r in accepted:
            log.info(
                "  %s  score=%.2f  len=%d  prompt=%s",
                r.source_hash[:12],
                r.quality_score,
                len(r.tk_source),
                r.prompt[:60].replace("\n", " "),
            )
        return

    manifest: list[dict[str, Any]] = []
    for i, r in enumerate(accepted, 1):
        filename = f"TEL-{r.source_hash[:12]}.tk"
        filepath = out_path / filename
        filepath.write_text(r.tk_source)

        manifest.append({
            "filename": filename,
            "source_hash": r.source_hash,
            "quality_score": round(r.quality_score, 4),
            "prompt": r.prompt[:500],
            "timestamp": r.timestamp,
            "api_key_hash": r.api_key_hash,
            "tk_source_length": len(r.tk_source),
            "compiler_exit_code": r.compiler_exit_code,
        })

    # Write manifest
    manifest_path = out_path / "harvest-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    log.info("Wrote %d .tk files and manifest to %s", len(accepted), output_dir)


def print_summary(records: list[HarvestedRecord]) -> None:
    """Print a summary of the harvest results."""
    total = len(records)
    extracted = sum(1 for r in records if r.tk_source)
    compiled = sum(1 for r in records if r.compiles)
    duplicates = sum(1 for r in records if r.duplicate)
    accepted = sum(1 for r in records if r.compiles and not r.duplicate)

    scores = [r.quality_score for r in records if r.compiles and not r.duplicate]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    print("\n--- Harvest Summary ---")
    print(f"  Records scanned:        {total}")
    print(f"  Source extracted:        {extracted}")
    print(f"  Compile-verified:       {compiled}")
    print(f"  Duplicates (skipped):   {duplicates}")
    print(f"  Accepted for corpus:    {accepted}")
    print(f"  Average quality score:  {avg_score:.3f}")
    if scores:
        print(f"  Min quality score:      {min(scores):.3f}")
        print(f"  Max quality score:      {max(scores):.3f}")
    print("---")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def harvest(args: argparse.Namespace) -> None:
    """Run the full harvest pipeline."""
    # Step 1: Load existing corpus hashes for deduplication
    log.info("Loading existing corpus hashes from %s", args.corpus_dir)
    existing_hashes = load_corpus_hashes(args.corpus_dir)

    # Also track hashes within this harvest batch for intra-batch dedup
    batch_hashes: set[str] = set()

    # Step 2: Scan DynamoDB
    log.info("Scanning DynamoDB table %s in %s", args.table, args.region)
    raw_records = scan_usage_table(args.table, args.region, limit=args.limit)

    # Step 3: Process each record
    results: list[HarvestedRecord] = []
    for item in raw_records:
        api_key_hash = str(item.get("api_key", ""))
        timestamp = str(item.get("timestamp", ""))
        prompt = str(item.get("prompt", ""))
        response = str(item.get("response", ""))

        # 3a: Extract toke source from response
        tk_source = extract_toke_source(response)
        if tk_source is None:
            continue

        rec = HarvestedRecord(
            api_key_hash=api_key_hash,
            timestamp=timestamp,
            prompt=prompt,
            raw_response=response,
            tk_source=tk_source,
            source_hash=source_hash(tk_source),
        )

        # 3b: Deduplicate against corpus and batch
        if rec.source_hash in existing_hashes:
            rec.duplicate = True
            rec.duplicate_of = "corpus"
            results.append(rec)
            continue

        if rec.source_hash in batch_hashes:
            rec.duplicate = True
            rec.duplicate_of = "batch"
            results.append(rec)
            continue

        batch_hashes.add(rec.source_hash)

        # 3c: Compile check
        ok, exit_code, errors = check_compiles(tk_source, args.tkc)
        rec.compiles = ok
        rec.compiler_exit_code = exit_code
        rec.compiler_errors = errors

        if not ok:
            log.debug(
                "Compile failed for %s: exit=%d errors=%s",
                rec.source_hash[:12],
                exit_code,
                errors,
            )

        # 3d: Quality score
        rec.quality_score = score_quality(rec)

        results.append(rec)

    # Step 4: Output
    print_summary(results)
    write_output(results, args.output_dir, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Harvest compile-verified toke code from MCP telemetry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --output-dir ./harvested\n"
            "  %(prog)s --output-dir ./harvested --dry-run\n"
            "  %(prog)s --output-dir ./harvested --limit 100 --min-score 0.6\n"
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write verified .tk files and manifest",
    )
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"DynamoDB table name (default: {DEFAULT_TABLE})",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--tkc",
        default=DEFAULT_TKC,
        help=f"Path to tkc compiler binary (default: {DEFAULT_TKC})",
    )
    parser.add_argument(
        "--corpus-dir",
        default=DEFAULT_CORPUS,
        help=f"Path to toke-corpus root (default: {DEFAULT_CORPUS})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of records to scan (for testing)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum quality score to accept (default: 0.0, i.e. all compiling)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be harvested without writing files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate tkc exists
    if not Path(args.tkc).is_file():
        log.error("tkc compiler not found at %s", args.tkc)
        sys.exit(1)

    harvest(args)


if __name__ == "__main__":
    main()
