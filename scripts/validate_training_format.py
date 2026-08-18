#!/usr/bin/env python3
"""validate_training_format.py — Story 10.9.4.

Spot-check the generated train.jsonl / eval.jsonl:
- every row has exactly `messages` with roles system/user/assistant
- system prompt byte-identical to infra/system_prompt_phase2.txt
- user prompt non-empty and does NOT contain raw template placeholders
- assistant content begins with `m=` (module decl)
- assistant content passes a cheap Phase 2 surface check
  (no uppercase letters outside strings, no `==`, no `!=`, no `[...]` indexing)
- prints a summary and exits non-zero if any issues

Also samples 10 rows and dumps them for human review.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT = (ROOT / "infra" / "system_prompt_phase2.txt").read_text().strip()


# Rough but useful Phase 2 surface checks. The proper gate is tkc --check
# (already run in compile_check_corpus.py); this is just a sanity pass on the
# rendered training rows, not a full compile.

def _strip_strings(src: str) -> str:
    """Remove double-quoted string literals so we can scan structural text only."""
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == '"':
            i += 1
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
            out.append('""')
            continue
        out.append(c)
        i += 1
    return "".join(out)


_RE_UPPER = re.compile(r"[A-Z]")
_RE_EQEQ = re.compile(r"==")
_RE_NEQ = re.compile(r"!=")
_RE_BRACKET_INDEX = re.compile(r"[a-z0-9_][\[]")  # identifier followed by [


def surface_check(src: str) -> list[str]:
    issues: list[str] = []
    stripped = _strip_strings(src)
    if not src.lstrip().startswith("m="):
        issues.append("missing_module_decl")
    if _RE_UPPER.search(stripped):
        issues.append("uppercase_letter")
    if _RE_EQEQ.search(masked):
        issues.append("double_equals")
    if _RE_NEQ.search(masked):
        issues.append("not_equals")
    if _RE_BRACKET_INDEX.search(masked) or "[" in masked or "]" in masked:
        issues.append("square_bracket")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate chat-format training JSONL")
    parser.add_argument("--train", type=Path, default=ROOT / "data" / "train.jsonl")
    parser.add_argument("--eval", type=Path, default=ROOT / "data" / "eval.jsonl")
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--dump", type=int, default=10, help="dump N rows for review")
    args = parser.parse_args()

    rng = random.Random(42)
    files = [args.train, args.eval]

    counts = collections.Counter()
    issue_counts: collections.Counter = collections.Counter()
    bad_rows: list[tuple[int, str, list[str]]] = []

    rows_all: list[dict] = []

    for path in files:
        if not path.exists():
            print(f"ERROR: {path} missing", file=sys.stderr)
            return 2
        with open(path) as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    counts["json_error"] += 1
                    continue
                counts["total"] += 1
                rows_all.append(row)

                # Shape check
                msgs = row.get("messages")
                if not isinstance(msgs, list) or len(msgs) != 3:
                    counts["bad_shape"] += 1
                    continue
                roles = [m.get("role") for m in msgs]
                if roles != ["system", "user", "assistant"]:
                    counts["bad_roles"] += 1
                    continue
                sys_c, user_c, asst_c = [m.get("content", "") for m in msgs]

                if sys_c != SYSTEM_PROMPT:
                    counts["bad_system"] += 1
                if not user_c.strip():
                    counts["empty_user"] += 1
                if "{" + "task_id" + "}" in user_c or "{category}" in user_c:
                    counts["unfilled_template"] += 1
                if not asst_c.strip():
                    counts["empty_assistant"] += 1

    # Surface-check 100 random rows
    sample = rng.sample(rows_all, min(args.sample, len(rows_all)))
    for i, row in enumerate(sample):
        msgs = row.get("messages") or []
        if len(msgs) != 3:
            continue
        asst = msgs[2].get("content", "")
        issues = surface_check(asst)
        for issue in issues:
            issue_counts[issue] += 1
        if issues:
            bad_rows.append((i, msgs[1].get("content", "")[:80], issues))

    # Dump N rows (deterministic) for human review
    dump_rows = rng.sample(rows_all, min(args.dump, len(rows_all)))
    print("=" * 70)
    print(f"Sample rows ({len(dump_rows)})")
    print("=" * 70)
    for i, row in enumerate(dump_rows):
        msgs = row["messages"]
        print(f"\n--- row {i+1} ---")
        print(f"USER: {msgs[1]['content'][:200]}")
        body = msgs[2]["content"]
        print(f"ASSISTANT ({len(body)} chars): {body[:200]}")

    print("\n" + "=" * 70)
    print(f"Validation summary over {counts['total']} rows")
    print("=" * 70)
    for k in sorted(counts):
        print(f"  {k:20s}  {counts[k]}")

    print(f"\nSurface check on {len(sample)} sampled rows:")
    if not issue_counts:
        print("  (no surface issues)")
    for k, n in issue_counts.most_common():
        print(f"  {k:20s}  {n}")

    # Show up to 5 examples of surface issues
    if bad_rows:
        print(f"\nExamples ({min(5, len(bad_rows))}):")
        for _, user, issues in bad_rows[:5]:
            print(f"  issues={issues}  user={user!r}")

    # Exit code reflects hard failures only
    hard_fail = (
        counts["json_error"] + counts["bad_shape"] + counts["bad_roles"]
        + counts["bad_system"] + counts["empty_user"] + counts["empty_assistant"]
        + counts["unfilled_template"]
    )
    if hard_fail:
        print(f"\nFAIL: {hard_fail} hard failures", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
