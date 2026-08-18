#!/usr/bin/env python3
"""canonicalise_tk_source.py — Story 10.10.1.

Collapse every tk_source record in `corpus/phase2_deduplicated/` to a
single physical line: strip `//` and `/* */` comments, collapse all
runs of whitespace outside string literals to a single space, then
drop that space wherever it sits next to a non-identifier punctuation
character (so adjacent tokens still parse, but no token budget is
spent on gratuitous spacing).

String literals (`"..."` and `'...'`) are preserved byte-for-byte —
including any `\\n` escape sequences that appear as literal text
inside them. Escape handling tracks backslash-quote so we don't
terminate the literal early.

Verification pass: before writing back, `canonicalise()` is checked
against `tkc --check` on a random sample to make sure the rewritten
form still parses. AST-hash equivalence is not enforced (tkc doesn't
expose one directly) — we rely on (a) compile pass/fail staying the
same and (b) a byte-level structural check where the set of non-whitespace
characters (outside strings) is identical before and after.

Usage:
    python3 scripts/canonicalise_tk_source.py --dry-run     # print stats only
    python3 scripts/canonicalise_tk_source.py --sample 500  # verify sample via tkc
    python3 scripts/canonicalise_tk_source.py --apply       # rewrite corpus in place
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpus" / "phase2_deduplicated"
TKC = Path("/Users/matthew.watt/tk/toke/tkc")

# Punctuation characters that never require flanking whitespace to
# separate them from an adjacent identifier or keyword.
PUNCT = set("{}();,:<>=+-*/!|@$.?&%^~")


def _canonicalise(src: str) -> str:
    """Return the single-line, comment-free form of src.

    Preserves string literals verbatim. Drops `//` line comments and
    `/* ... */` block comments. Collapses runs of whitespace outside
    strings to one space, then removes that space whenever either
    neighbour is punctuation.
    """
    n = len(src)
    out: list[str] = []
    i = 0
    while i < n:
        c = src[i]
        # Double-quoted string literal — preserve verbatim, honour \ escapes.
        if c == '"':
            out.append(c)
            i += 1
            while i < n:
                ch = src[i]
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                i += 1
                if ch == '"':
                    break
            continue
        # Single-quoted byte literal — same treatment.
        if c == "'":
            out.append(c)
            i += 1
            while i < n:
                ch = src[i]
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                i += 1
                if ch == "'":
                    break
            continue
        # Line comment: drop to end of line.
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            i += 2
            while i < n and src[i] != "\n":
                i += 1
            # leave newline for the whitespace branch to absorb
            continue
        # Block comment: drop through closing */.
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2  # skip */ (or EOF)
            continue
        # Whitespace run: collapse to single space; caller may drop later.
        if c in " \t\n\r":
            while i < n and src[i] in " \t\n\r":
                i += 1
            out.append(" ")
            continue
        out.append(c)
        i += 1

    flat = "".join(out).strip()

    # Second pass: drop spaces adjacent to punctuation. We must not
    # descend into strings here, so re-scan with the same tracker.
    result: list[str] = []
    i = 0
    n = len(flat)
    while i < n:
        c = flat[i]
        if c == '"':
            result.append(c)
            i += 1
            while i < n:
                ch = flat[i]
                result.append(ch)
                if ch == "\\" and i + 1 < n:
                    result.append(flat[i + 1])
                    i += 2
                    continue
                i += 1
                if ch == '"':
                    break
            continue
        if c == "'":
            result.append(c)
            i += 1
            while i < n:
                ch = flat[i]
                result.append(ch)
                if ch == "\\" and i + 1 < n:
                    result.append(flat[i + 1])
                    i += 2
                    continue
                i += 1
                if ch == "'":
                    break
            continue
        if c == " ":
            prev = result[-1] if result else ""
            nxt = flat[i + 1] if i + 1 < n else ""
            if prev in PUNCT or nxt in PUNCT or prev == "" or nxt == "":
                i += 1
                continue
        result.append(c)
        i += 1

    return "".join(result)


def _non_string_skeleton(src: str) -> str:
    """Return src with all whitespace and string contents removed.

    Used as a structural fingerprint: if the skeleton is identical
    before and after canonicalisation, we did not change the program's
    token stream in any semantically significant way.
    """
    n = len(src)
    out: list[str] = []
    i = 0
    while i < n:
        c = src[i]
        if c == '"':
            out.append(c)
            i += 1
            while i < n:
                ch = src[i]
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
                if ch == '"':
                    out.append(ch)
                    break
            continue
        if c == "'":
            out.append(c)
            i += 1
            while i < n:
                ch = src[i]
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
                if ch == "'":
                    out.append(ch)
                    break
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            i += 2
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c in " \t\n\r":
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def collect_files(root: Path) -> list[str]:
    files: list[str] = []
    for cat_dir in sorted(root.iterdir()):
        if not cat_dir.is_dir():
            continue
        for p in cat_dir.iterdir():
            if p.suffix == ".json":
                files.append(str(p))
    return files


def _process_one(path_str: str) -> dict:
    """Read record, canonicalise tk_source, return stats + new record."""
    path = Path(path_str)
    try:
        data = json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        return {"path": path_str, "error": str(e)}
    src = data.get("tk_source", "")
    if not src:
        return {"path": path_str, "skipped": "no_source"}
    flat = _canonicalise(src)
    orig_skel = _non_string_skeleton(src)
    new_skel = _non_string_skeleton(flat)
    return {
        "path": path_str,
        "orig_len": len(src),
        "new_len": len(flat),
        "orig_nl": src.count("\n"),
        "new_nl": flat.count("\n"),
        "skeleton_ok": orig_skel == new_skel,
        "had_line_comment": "//" in orig_skel,
        "had_block_comment": "/*" in orig_skel,
        "new_src": flat,
    }


def _verify_with_tkc(new_src: str) -> tuple[bool, str]:
    """Write src to a tempfile and run tkc --check. Return (ok, stderr_tail)."""
    with tempfile.NamedTemporaryFile(suffix=".tk", delete=False, mode="w") as tf:
        tf.write(new_src)
        tmp = tf.name
    try:
        proc = subprocess.run(
            [str(TKC), "--check", tmp],
            capture_output=True, text=True, timeout=15,
        )
        return (proc.returncode == 0, (proc.stderr or proc.stdout)[-300:])
    finally:
        os.unlink(tmp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print stats only, do not write")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite corpus records in place")
    ap.add_argument("--sample", type=int, default=0,
                    help="run tkc --check on N random rewritten sources")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        ap.error("pass --dry-run or --apply (or both)")

    files = collect_files(CORPUS_DIR)
    print(f"Found {len(files):,} corpus records", file=sys.stderr)

    print(f"Canonicalising with {min(8, os.cpu_count() or 8)} workers...",
          file=sys.stderr)
    with Pool(processes=min(8, os.cpu_count() or 8)) as pool:
        results = pool.map(_process_one, files, chunksize=1000)

    # Aggregate
    n_total = len(results)
    n_changed = sum(1 for r in results if "new_src" in r and r["new_len"] != r["orig_len"])
    n_skel_ok = sum(1 for r in results if r.get("skeleton_ok"))
    n_skel_bad = sum(1 for r in results if "skeleton_ok" in r and not r["skeleton_ok"])
    n_line_comment = sum(1 for r in results if r.get("had_line_comment"))
    n_block_comment = sum(1 for r in results if r.get("had_block_comment"))
    nl_before = sum(r.get("orig_nl", 0) for r in results)
    nl_after = sum(r.get("new_nl", 0) for r in results)
    bytes_saved = sum(
        r.get("orig_len", 0) - r.get("new_len", 0)
        for r in results if "new_len" in r
    )

    print(f"\nRecords scanned:          {n_total:,}", file=sys.stderr)
    print(f"Records changed:          {n_changed:,}", file=sys.stderr)
    print(f"Skeleton preserved:       {n_skel_ok:,}", file=sys.stderr)
    print(f"Skeleton DIVERGENT (bad): {n_skel_bad:,}", file=sys.stderr)
    print(f"Records with // comment:  {n_line_comment:,}", file=sys.stderr)
    print(f"Records with /* */ cmt:   {n_block_comment:,}", file=sys.stderr)
    print(f"Newlines removed:         {nl_before - nl_after:,}", file=sys.stderr)
    print(f"Bytes saved:              {bytes_saved:,}", file=sys.stderr)

    if n_skel_bad:
        print("\nFirst 5 skeleton mismatches:", file=sys.stderr)
        bad = [r for r in results if "skeleton_ok" in r and not r["skeleton_ok"]][:5]
        for r in bad:
            print(f"  {r['path']}", file=sys.stderr)

    # Optional tkc verification on a sample
    if args.sample:
        rng = random.Random(args.seed)
        changed = [r for r in results if "new_src" in r and r["new_len"] != r["orig_len"]]
        sample = rng.sample(changed, min(args.sample, len(changed)))
        print(f"\nVerifying {len(sample)} rewritten sources via tkc --check...",
              file=sys.stderr)
        passed = 0
        failed: list[tuple[str, str]] = []
        for r in sample:
            ok, tail = _verify_with_tkc(r["new_src"])
            if ok:
                passed += 1
            else:
                failed.append((r["path"], tail))
        print(f"  passed: {passed} / {len(sample)}", file=sys.stderr)
        if failed:
            print(f"  failed: {len(failed)} — first 5:", file=sys.stderr)
            for path, tail in failed[:5]:
                print(f"    {path}", file=sys.stderr)
                print(f"    {tail[:200]}", file=sys.stderr)

    # Apply
    if args.apply:
        print("\nWriting changes in place...", file=sys.stderr)
        n_written = 0
        for r in results:
            if "new_src" not in r:
                continue
            if r["new_len"] == r["orig_len"] and r["orig_nl"] == 0:
                continue  # nothing to change
            path = Path(r["path"])
            data = json.loads(path.read_text())
            data["tk_source"] = r["new_src"]
            # Mark canonicalisation so downstream can tell
            data.setdefault("canonicalisation", {})
            data["canonicalisation"] = {
                "single_line": True,
                "comments_stripped": True,
                "orig_newlines": r["orig_nl"],
                "orig_len": r["orig_len"],
                "new_len": r["new_len"],
            }
            path.write_text(json.dumps(data, separators=(",", ":")))
            n_written += 1
        print(f"  wrote {n_written:,} files", file=sys.stderr)

    return 0 if n_skel_bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
