#!/usr/bin/env python3
"""Generate a parallel training dataset with compact and expanded toke code.

Reads existing corpus JSONL files, runs each toke source through ``tkc --fmt``
(compact) and ``tkc --pretty`` (expanded), and writes JSONL where each entry
has both forms side by side.  This enables reproducible expander training --
teaching models to go from expanded -> compact and compact -> expanded.

In ``--dry-run`` mode the script produces synthetic pairs that demonstrate the
output format without requiring a working ``tkc`` binary.

Usage::

    python scripts/parallel_expand.py \\
        --corpus-dir data \\
        --output data/parallel_expand.jsonl

    # Preview format without tkc:
    python scripts/parallel_expand.py --dry-run --output /dev/stdout

Story 10.8.5 -- Publish parallel training dataset (compact + expanded).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_tkc(tkc_path: str, source: str, flag: str) -> str | None:
    """Run ``tkc <flag>`` on *source* and return stdout, or None on error."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toke", delete=True
    ) as tmp:
        tmp.write(source)
        tmp.flush()
        try:
            result = subprocess.run(
                [tkc_path, flag, tmp.name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.rstrip("\n")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return None


def count_tokens(text: str, encoder) -> int | None:
    """Count tokens with a tiktoken encoder, or return None."""
    if encoder is None:
        return None
    return len(encoder.encode(text))


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def load_corpus_entries(corpus_dir: Path) -> list[dict]:
    """Load toke source entries from all JSONL files in *corpus_dir*.

    Looks for a ``toke_source`` field in each record.  Falls back to
    ``compact_source`` or ``source`` if present.
    """
    entries: list[dict] = []
    for jsonl_path in sorted(corpus_dir.glob("*.jsonl")):
        with open(jsonl_path) as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    print(
                        f"Warning: skipping malformed JSON at "
                        f"{jsonl_path.name}:{line_no}",
                        file=sys.stderr,
                    )
                    continue
                # Extract toke source from whichever key is available.
                src = (
                    record.get("toke_source")
                    or record.get("compact_source")
                    or record.get("source")
                )
                if not src:
                    continue
                task_id = record.get("task_id", f"{jsonl_path.stem}:{line_no}")
                entries.append({"task_id": task_id, "source": src})
    return entries


# ---------------------------------------------------------------------------
# Pair generation
# ---------------------------------------------------------------------------

def build_pairs_live(
    entries: list[dict],
    tkc_path: str,
    encoder,
    max_entries: int | None,
) -> list[dict]:
    """Build compact/expanded pairs by running ``tkc``."""
    records: list[dict] = []
    limit = max_entries if max_entries else len(entries)

    for entry in entries[:limit]:
        task_id = entry["task_id"]
        source = entry["source"]

        compact = _run_tkc(tkc_path, source, "--fmt")
        expanded = _run_tkc(tkc_path, source, "--pretty")

        if compact is None or expanded is None:
            print(
                f"Warning: tkc failed for {task_id}, skipping.",
                file=sys.stderr,
            )
            continue

        tok_c = count_tokens(compact, encoder)
        tok_e = count_tokens(expanded, encoder)
        ratio = round(tok_c / tok_e, 4) if tok_c and tok_e else None

        record: dict = {
            "task_id": task_id,
            "compact_source": compact,
            "expanded_source": expanded,
            "token_count_compact": tok_c,
            "token_count_expanded": tok_e,
            "compression_ratio": ratio,
        }
        records.append(record)

    return records


def build_pairs_dry_run(max_entries: int | None) -> list[dict]:
    """Generate synthetic pairs showing the output format."""
    samples = [
        {
            "task_id": "demo-001",
            "compact": (
                'M=hello;I=s:std.str;'
                'F=main():void{s.print("Hello, world!");};'
            ),
            "expanded": (
                'Module = hello\n'
                'Import s : std.str\n'
                '\n'
                'Function main() : void {\n'
                '    s.print("Hello, world!")\n'
                '}\n'
            ),
        },
        {
            "task_id": "demo-002",
            "compact": (
                'M=sum;I=j:std.json;I=s:std.str;'
                'F=main():void{let input=j.parse(s.argv(1));'
                'let arr=input as [i64];let sum=mut.0;'
                'lp(let i=0;i<arr.len;i=i+1){sum=sum+arr[i];};'
                'j.print(sum);};'
            ),
            "expanded": (
                'Module = sum\n'
                'Import j : std.json\n'
                'Import s : std.str\n'
                '\n'
                'Function main() : void {\n'
                '    let input = j.parse(s.argv(1))\n'
                '    let arr = input as [i64]\n'
                '    let sum = mut 0\n'
                '    loop (let i = 0; i < arr.len; i = i + 1) {\n'
                '        sum = sum + arr[i]\n'
                '    }\n'
                '    j.print(sum)\n'
                '}\n'
            ),
        },
        {
            "task_id": "demo-003",
            "compact": (
                'M=fib;F=fib(n:i64):i64{'
                'if(n<=1){rt n;};rt fib(n-1)+fib(n-2);};'
                'F=main():void{std.io.print(fib(10));};'
            ),
            "expanded": (
                'Module = fib\n'
                '\n'
                'Function fib(n : i64) : i64 {\n'
                '    if (n <= 1) {\n'
                '        return n\n'
                '    }\n'
                '    return fib(n - 1) + fib(n - 2)\n'
                '}\n'
                '\n'
                'Function main() : void {\n'
                '    std.io.print(fib(10))\n'
                '}\n'
            ),
        },
    ]

    limit = max_entries if max_entries else len(samples)
    records: list[dict] = []

    for sample in samples[:limit]:
        compact = sample["compact"]
        expanded = sample["expanded"]
        tok_c = len(compact)  # character-based stand-in
        tok_e = len(expanded)
        records.append({
            "task_id": sample["task_id"],
            "compact_source": compact,
            "expanded_source": expanded,
            "token_count_compact": tok_c,
            "token_count_expanded": tok_e,
            "compression_ratio": round(tok_c / tok_e, 4) if tok_e else None,
        })

    return records


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(records: list[dict], file=sys.stderr) -> None:
    """Print summary statistics to *file*."""
    total = len(records)
    print(f"Total pairs: {total}", file=file)

    ratios = [r["compression_ratio"] for r in records if r.get("compression_ratio")]
    if not ratios:
        print("No compression ratios available.", file=file)
        return

    import statistics
    mean_r = round(statistics.mean(ratios), 4)
    median_r = round(statistics.median(ratios), 4)
    print(f"Mean compression ratio (compact/expanded): {mean_r}", file=file)
    print(f"Median compression ratio:                  {median_r}", file=file)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a parallel training dataset with compact and expanded "
            "toke code side by side."
        ),
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("data"),
        help="Directory containing corpus JSONL files (default: data)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/parallel_expand.jsonl"),
        help="Output JSONL path (default: data/parallel_expand.jsonl)",
    )
    parser.add_argument(
        "--tkc-path",
        default="tkc",
        help="Path to the tkc binary (default: tkc on PATH)",
    )
    parser.add_argument(
        "--max-entries",
        type=int,
        default=None,
        help="Limit the number of entries processed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Produce synthetic pairs without requiring tkc",
    )
    parser.add_argument(
        "--tokenizer",
        default="cl100k_base",
        help="tiktoken encoding name (default: cl100k_base)",
    )
    args = parser.parse_args()

    # ---- dry-run shortcut ----
    if args.dry_run:
        print("Dry-run mode: generating synthetic pairs.", file=sys.stderr)
        records = build_pairs_dry_run(args.max_entries)
    else:
        # Load corpus
        corpus_dir = args.corpus_dir
        if not corpus_dir.is_dir():
            print(
                f"Error: corpus directory not found: {corpus_dir}",
                file=sys.stderr,
            )
            sys.exit(1)

        entries = load_corpus_entries(corpus_dir)
        if not entries:
            print("Error: no toke source entries found in corpus.", file=sys.stderr)
            sys.exit(1)
        print(f"Loaded {len(entries)} source entries.", file=sys.stderr)

        # Load tiktoken (graceful degradation)
        encoder = None
        try:
            import tiktoken
            encoder = tiktoken.get_encoding(args.tokenizer)
            print(f"Using tokenizer: {args.tokenizer}", file=sys.stderr)
        except ImportError:
            print(
                "tiktoken not installed -- using character counts.",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"Could not load tokenizer {args.tokenizer}: {exc}",
                file=sys.stderr,
            )

        records = build_pairs_live(entries, args.tkc_path, encoder, args.max_entries)

    if not records:
        print("Warning: no pairs generated.", file=sys.stderr)
        sys.exit(1)

    # ---- write output ----
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(records)} pairs to {args.output}", file=sys.stderr)
    print("---", file=sys.stderr)
    print_summary(records)


if __name__ == "__main__":
    main()
