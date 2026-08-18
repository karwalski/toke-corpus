#!/usr/bin/env python3
"""Fix Phase 1 uppercase type names leaked into Phase 2 corpus files.

Replaces Str -> $str, Int -> $int, Float -> $float, Bool -> $bool, Void -> $void
in tk_source fields, but only in code context (not inside string literals).

Uses multiprocessing for speed across ~190K files.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

TKC = os.environ.get("TKC", "/Users/matthew.watt/tk/toke/tkc")
CORPUS_DIR = Path("/Users/matthew.watt/tk/toke-corpus/corpus/phase2_combined")

# Map of Phase 1 uppercase types to Phase 2 lowercase with $ prefix
TYPE_MAP = {
    "Str": "$str",
    "Int": "$int",
    "Float": "$float",
    "Bool": "$bool",
    "Void": "$void",
}

# Regex that matches any of the uppercase type names as whole words.
# We use word-boundary-like logic but toke tokens are delimited by
# punctuation (: ; ( ) { } < > ! = , space) not just \b, so we match
# the type name when preceded/followed by non-alpha chars or string edges.
#
# The pattern captures: (before_context)(TypeName)(after_context)
# We need to be careful not to match inside string literals.

# Build alternation for type names
_TYPE_ALT = "|".join(TYPE_MAP.keys())

# This pattern matches an uppercase type name that is NOT part of a larger
# identifier. In toke, identifiers are alphanumeric + underscore, so we
# check that the type name is not preceded or followed by [a-zA-Z0-9_].
TYPE_PATTERN = re.compile(
    r'(?<![a-zA-Z0-9_$])(' + _TYPE_ALT + r')(?![a-zA-Z0-9_])'
)


def fix_source(tk_source: str) -> str:
    """Replace uppercase type names with $-prefixed lowercase versions,
    skipping content inside string literals."""
    # Strategy: split the source into string-literal and code segments,
    # only apply replacements in code segments, then rejoin.
    #
    # Toke string literals are delimited by double quotes. There is no
    # escape for quotes inside strings in toke's compact syntax (strings
    # don't contain quotes). So we can split on "..." segments safely.

    parts = []
    i = 0
    src = tk_source
    while i < len(src):
        if src[i] == '"':
            # Find closing quote
            end = src.find('"', i + 1)
            if end == -1:
                # Unterminated string - take rest as literal
                parts.append(('str', src[i:]))
                i = len(src)
            else:
                parts.append(('str', src[i:end + 1]))
                i = end + 1
        else:
            # Code segment - find next quote or end
            next_quote = src.find('"', i)
            if next_quote == -1:
                parts.append(('code', src[i:]))
                i = len(src)
            else:
                parts.append(('code', src[i:next_quote]))
                i = next_quote

    # Apply replacements only in code segments
    result = []
    for kind, text in parts:
        if kind == 'code':
            text = TYPE_PATTERN.sub(lambda m: TYPE_MAP[m.group(1)], text)
        result.append(text)

    return ''.join(result)


def has_uppercase_types(tk_source: str) -> bool:
    """Quick check if source contains any uppercase type names in code context."""
    # Fast pre-check: if none of the type names appear at all, skip
    for t in TYPE_MAP:
        if t in tk_source:
            # Verify it's not purely inside string literals by doing the
            # full parse. But first, a quick heuristic: if the type name
            # appears outside quotes, we need to fix it.
            # For speed, just check if any match exists via the regex on
            # the code portions. But even faster: just check if the
            # fixed version differs.
            return True
    return False


def validate_with_tkc(tk_source: str) -> bool:
    """Run tkc --check on the given source. Returns True if valid."""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tk', delete=False) as f:
            f.write(tk_source)
            f.flush()
            tmp_path = f.name
        result = subprocess.run(
            [TKC, "--check", tmp_path],
            capture_output=True, timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def process_file(file_path: str) -> tuple:
    """Process a single JSON file. Returns (status, file_path).
    status: 'skipped' | 'fixed' | 'failed' | 'error'
    """
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)

        tk_source = data.get('tk_source', '')
        if not tk_source or not has_uppercase_types(tk_source):
            return ('skipped', file_path)

        fixed_source = fix_source(tk_source)

        # If nothing actually changed (type names were only in strings),
        # skip this file
        if fixed_source == tk_source:
            return ('skipped', file_path)

        # Validate the fixed source with tkc
        if validate_with_tkc(fixed_source):
            data['tk_source'] = fixed_source
            # Write back atomically
            tmp_out = file_path + '.tmp'
            with open(tmp_out, 'w') as f:
                json.dump(data, f, indent=2)
                f.write('\n')
            os.replace(tmp_out, file_path)
            return ('fixed', file_path)
        else:
            return ('failed', file_path)

    except Exception as e:
        return ('error', f"{file_path}: {e}")


def collect_files() -> list:
    """Collect all .json files in the corpus directory."""
    files = []
    for root, dirs, filenames in os.walk(CORPUS_DIR):
        for fn in filenames:
            if fn.endswith('.json'):
                files.append(os.path.join(root, fn))
    return files


def main():
    if not os.path.isfile(TKC):
        print(f"ERROR: tkc not found at {TKC}", file=sys.stderr)
        sys.exit(1)

    print(f"Collecting files from {CORPUS_DIR}...")
    files = collect_files()
    total = len(files)
    print(f"Found {total:,} JSON files")

    print(f"Processing with 8 workers...")
    fixed = 0
    failed = 0
    skipped = 0
    errors = 0
    failed_files = []

    with Pool(processes=8) as pool:
        for i, (status, info) in enumerate(pool.imap_unordered(process_file, files, chunksize=64)):
            if status == 'fixed':
                fixed += 1
            elif status == 'failed':
                failed += 1
                failed_files.append(info)
            elif status == 'error':
                errors += 1
                if errors <= 10:
                    print(f"  ERROR: {info}", file=sys.stderr)
            else:
                skipped += 1

            if (i + 1) % 5000 == 0:
                print(f"  Progress: {i+1:,}/{total:,} "
                      f"(fixed={fixed}, failed={failed}, skipped={skipped})")

    print()
    print("=" * 60)
    print(f"Total scanned:          {total:,}")
    print(f"Total fixed:            {fixed:,}")
    print(f"Total failed after fix: {failed:,}")
    print(f"Total skipped (clean):  {skipped:,}")
    print(f"Total errors:           {errors:,}")
    print("=" * 60)

    if failed_files:
        print(f"\nFailed files (validation failed after fix):")
        for f in failed_files[:50]:
            print(f"  {f}")
        if len(failed_files) > 50:
            print(f"  ... and {len(failed_files) - 50} more")


if __name__ == '__main__':
    main()
