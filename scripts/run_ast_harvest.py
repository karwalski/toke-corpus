"""Batch runner for AST harvesting from open-source repos.

Clones curated permissively-licensed repos, scans for harvestable
functions, transpiles to toke, validates with tkc, and outputs
a JSONL corpus file.
"""

import argparse
import json
import logging
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from ingest.ast_harvest import ASTHarvester, HarvestedFunction
from ingest.ast_transpile import ASTTranspiler
from ingest.repo_scanner import RepoScanner

logger = logging.getLogger(__name__)

TKC_BINARY = Path("/Users/matthew.watt/tk/toke/tkc")

# Curated list of permissively-licensed repos
REPOS = [
    # Python repos
    {
        "url": "https://github.com/keon/algorithms.git",
        "language": "python",
        "name": "algorithms",
        "license": "MIT",
    },
    {
        "url": "https://github.com/grantjenks/python-sortedcontainers.git",
        "language": "python",
        "name": "python-sortedcontainers",
        "license": "Apache-2.0",
    },
    # C repos
    {
        "url": "https://github.com/kokke/tiny-regex-c.git",
        "language": "c",
        "name": "tiny-regex-c",
        "license": "Unlicense",
    },
    {
        "url": "https://github.com/silentbicycle/greatest.git",
        "language": "c",
        "name": "greatest",
        "license": "ISC",
    },
    {
        "url": "https://github.com/swenson/sort.git",
        "language": "c",
        "name": "sort",
        "license": "MIT",
    },
]

OUTPUT_PATH = Path("data/corpus_ast_harvested.jsonl")


def validate_toke(tk_source: str, tkc_path: Path) -> tuple[bool, str]:
    """Validate toke source using tkc --check."""
    with tempfile.NamedTemporaryFile(
        suffix=".tk", mode="w", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(tk_source)
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [str(tkc_path), "--check", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    finally:
        tmp_path.unlink(missing_ok=True)


def make_entry(
    func: HarvestedFunction,
    tk_source: str,
    repo_info: dict,
    idx: int,
) -> dict:
    """Build a corpus entry dict."""
    func_name = func.name.lower().replace("_", "")
    entry_id = f"ast-{func.language}-{repo_info['name']}-{func_name}-{idx:03d}"

    return {
        "id": entry_id,
        "tk_source": tk_source,
        "generation_method": "ast_harvest",
        "source": {
            "origin": repo_info["url"],
            "language": func.language,
            "function": func.name,
            "license": func.license or repo_info.get("license", "unknown"),
            "retrieval_date": str(date.today()),
        },
        "validation": {"tkc_check": "pass"},
    }


def run_harvest(
    dry_run: bool = False,
    output_path: Path = OUTPUT_PATH,
    tkc_path: Path = TKC_BINARY,
    repos: list[dict] | None = None,
) -> dict:
    """Run the full harvest pipeline.

    Args:
        dry_run: If True, scan and report stats without transpiling.
        output_path: Path for output JSONL file.
        tkc_path: Path to tkc binary.
        repos: List of repo configs. Uses REPOS if None.

    Returns:
        Stats dict with counts.
    """
    if repos is None:
        repos = REPOS

    scanner = RepoScanner()
    transpiler = ASTTranspiler()

    stats = {
        "repos_scanned": 0,
        "functions_found": 0,
        "transpile_success": 0,
        "transpile_fail": 0,
        "tkc_pass": 0,
        "tkc_fail": 0,
        "entries_written": 0,
    }

    all_functions: list[tuple[HarvestedFunction, dict]] = []

    for repo_info in repos:
        logger.info(
            "Scanning %s (%s)...", repo_info["name"], repo_info["language"]
        )
        try:
            functions = scanner.clone_and_scan(
                repo_info["url"], repo_info["language"]
            )
        except Exception as exc:
            logger.error("Failed to clone/scan %s: %s", repo_info["name"], exc)
            continue

        stats["repos_scanned"] += 1
        stats["functions_found"] += len(functions)

        for func in functions:
            all_functions.append((func, repo_info))

        logger.info(
            "  Found %d harvestable functions in %s",
            len(functions), repo_info["name"],
        )

    if dry_run:
        logger.info("Dry run complete. Stats: %s", stats)
        return stats

    # Transpile and validate
    output_path.parent.mkdir(parents=True, exist_ok=True)

    idx = 0
    with open(output_path, "w", encoding="utf-8") as out:
        for func, repo_info in all_functions:
            idx += 1
            tk_source = transpiler.transpile(func)
            if tk_source is None:
                stats["transpile_fail"] += 1
                continue
            stats["transpile_success"] += 1

            # Validate
            passed, diag = validate_toke(tk_source, tkc_path)
            if not passed:
                stats["tkc_fail"] += 1
                logger.debug(
                    "tkc fail for %s: %s", func.name, diag[:200]
                )
                continue
            stats["tkc_pass"] += 1

            entry = make_entry(func, tk_source, repo_info, idx)
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            stats["entries_written"] += 1

    logger.info("Harvest complete. Stats: %s", stats)
    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Harvest functions from open-source repos and transpile to toke"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report stats without transpiling",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help=f"Output JSONL path (default: {OUTPUT_PATH})",
    )
    parser.add_argument(
        "--tkc",
        default=str(TKC_BINARY),
        help=f"Path to tkc binary (default: {TKC_BINARY})",
    )
    args = parser.parse_args()

    stats = run_harvest(
        dry_run=args.dry_run,
        output_path=Path(args.output),
        tkc_path=Path(args.tkc),
    )

    print("\n--- AST Harvest Results ---")
    for key, val in stats.items():
        label = key.replace("_", " ").title()
        print(f"  {label:25s}: {val}")


if __name__ == "__main__":
    main()
