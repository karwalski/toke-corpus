#!/usr/bin/env python3
"""judge_runtime_output.py — Story 10.8.7.

For each corpus record where `runtime_check.ran == true` AND the record is
not a mutation, send `(tk_source, captured_stdout, captured_exit)` to
Sonnet 4.6 as a *judge* and record its verdict in `judge_output_check`:

    judge_output_check = {
      "verified": true,
      "correct": bool,              # does the output look right for this program?
      "reasoning": str,             # short (<=300 chars) justification
      "confidence": "high" | "medium" | "low",
      "judged_at": ISO-8601,
      "judge_model": "claude-sonnet-4-6"
    }

Strategy:
- The judge is given the program, its captured stdout/stderr/exit code, and a
  rubric. It does NOT predict the output — it checks post-hoc whether the
  program's observed behavior is *plausibly correct*. This is the weakest
  signal per story 10.8.7 but gives us a check beyond "compile_check passed".
- Budget: only records with `runtime_check.ran == true` are judged, which
  (post 10.8.6) is ~a few thousand records, keeping cost well under $50.
- Records already marked with `judge_output_check.verified == true` are
  skipped, so the script is resumable.

Usage:
    python scripts/judge_runtime_output.py [--corpus-dir PATH] [--sample N]
                                           [--dry-run] [--concurrency N]
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as _cf
import datetime as _dt
import json
import os
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "corpus" / "phase2_deduplicated"
JUDGE_MODEL = "claude-sonnet-4-6"
MAX_TK_SOURCE = 4000  # chars
MAX_STDOUT = 2000
MAX_STDERR = 1000

JUDGE_PROMPT_HEAD = """You are a code-review judge for the toke programming language. I will give you:

1. A toke program (Phase 2 default syntax: lowercase identifiers, ';' separators, '$str' for string type, '@type' for arrays, '|{Variant:bind body}' for postfix match).
2. The stdout/stderr/exit-code that the program actually produced when compiled with tkc and executed.

Your job is NOT to predict the output. Your job is to judge whether the observed output is *plausibly correct* for the program as written. Examples:

- Program adds 2 + 3, stdout is "5", exit 0 -> correct
- Program prints "hello", stdout is "hello", exit 0 -> correct
- Program reads a file that doesn't exist, logs an error, exits 1 -> correct (error-handling path is exercised)
- Program should print an array sum but stdout is empty and exit code is 139 -> incorrect (crash)
- Program should print "even" for 4 but stdout is "odd" -> incorrect

Do not penalize programs that exit non-zero when non-zero is a sensible outcome for the code path taken.

Respond with a single JSON object, no surrounding prose, with keys: correct (boolean), confidence (one of "high"/"medium"/"low"), reasoning (one sentence, <=300 chars).

---
"""


def _truncate(s: str | None, n: int) -> str:
    if s is None:
        return ""
    if len(s) <= n:
        return s
    return s[:n] + f"\n...<truncated, total {len(s)} bytes>"


def build_prompt(source: str, rc: dict) -> str:
    stdout = rc.get("stdout") or ""
    stderr = rc.get("stderr") or ""
    body = (
        f"Program:\n```toke\n{_truncate(source, MAX_TK_SOURCE)}\n```\n\n"
        f"Exit code: {rc.get('exit_code')}\n"
        f"Stdout ({len(stdout)} bytes):\n```\n{_truncate(stdout, MAX_STDOUT)}\n```\n"
        f"Stderr ({len(stderr)} bytes):\n```\n{_truncate(stderr, MAX_STDERR)}\n```\n\n"
        f"Your JSON verdict:"
    )
    return JUDGE_PROMPT_HEAD + body


_RE_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_verdict(text: str) -> dict | None:
    m = _RE_JSON.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "correct" not in obj:
        return None
    return {
        "correct": bool(obj.get("correct")),
        "confidence": obj.get("confidence") or "low",
        "reasoning": str(obj.get("reasoning") or "")[:400],
    }


def judge_one(client, source: str, rc: dict) -> dict | None:
    import anthropic
    prompt = build_prompt(source, rc)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=JUDGE_MODEL,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text
            verdict = parse_verdict(text)
            if verdict:
                return verdict
            return None
        except anthropic.RateLimitError:
            time.sleep(5 * (attempt + 1))
        except Exception as exc:
            print(f"  judge error: {exc}", file=sys.stderr)
            time.sleep(2)
    return None


def iter_candidates(corpus_dir: Path, sample: int | None):
    """Yield paths to records with runtime_check.ran == true and no prior judgment."""
    cats = sorted(p for p in corpus_dir.iterdir() if p.is_dir())
    candidates: list[Path] = []
    for cat_dir in cats:
        if cat_dir.name.startswith("MUT-"):
            continue
        for p in cat_dir.iterdir():
            if p.suffix != ".json":
                continue
            try:
                rec = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            rc = rec.get("runtime_check") or {}
            if not rc.get("ran"):
                continue
            j = rec.get("judge_output_check") or {}
            if j.get("verified"):
                continue
            candidates.append(p)
    if sample is not None and len(candidates) > sample:
        random.shuffle(candidates)
        candidates = candidates[:sample]
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM runtime-output judge")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    random.seed(42)

    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("ERROR: anthropic SDK not installed", file=sys.stderr)
        return 2

    import anthropic
    client = anthropic.Anthropic()

    candidates = iter_candidates(args.corpus_dir, args.sample)
    total = len(candidates)
    print(f"  candidates: {total}", file=sys.stderr)
    if args.dry_run:
        print(f"  DRY RUN — no files modified", file=sys.stderr)
        for p in candidates[:10]:
            print(f"    {p.parent.name}/{p.name}", file=sys.stderr)
        return 0

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

    counter = collections.Counter()
    cat_stats: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )

    def _process(path: Path) -> tuple[str, str]:
        try:
            rec = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return (path.parent.name, "skip_error")
        src = rec.get("tk_source", "")
        rc = rec.get("runtime_check") or {}
        verdict = judge_one(client, src, rc)
        if not verdict:
            return (path.parent.name, "judge_error")
        rec["judge_output_check"] = {
            "verified": True,
            "correct": verdict["correct"],
            "reasoning": verdict["reasoning"],
            "confidence": verdict["confidence"],
            "judged_at": now_iso,
            "judge_model": JUDGE_MODEL,
        }
        try:
            with open(path, "w") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            return (path.parent.name, "write_error")
        return (path.parent.name, "correct" if verdict["correct"] else "incorrect")

    done = 0
    with _cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for cat, code in pool.map(_process, candidates):
            counter[code] += 1
            cat_stats[cat][code] += 1
            cat_stats[cat]["scanned"] += 1
            done += 1
            if done % 100 == 0 or done == total:
                print(
                    f"    progress: {done}/{total}  "
                    f"correct={counter['correct']} "
                    f"incorrect={counter['incorrect']} "
                    f"errors={counter['judge_error']+counter['skip_error']+counter['write_error']}",
                    file=sys.stderr,
                )

    print("\n" + "=" * 70, file=sys.stderr)
    print("  Runtime-output judge summary", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    for k in ("correct", "incorrect", "judge_error", "skip_error", "write_error"):
        print(f"  {k:15s}  {counter[k]:6d}", file=sys.stderr)
    total_judged = counter["correct"] + counter["incorrect"]
    if total_judged:
        print(
            f"\n  Correct rate: {100*counter['correct']/total_judged:.1f}% "
            f"({counter['correct']}/{total_judged})",
            file=sys.stderr,
        )

    print(f"\n  Per category (top 15 by scanned):", file=sys.stderr)
    ranked = sorted(cat_stats.items(), key=lambda kv: -kv[1].get("scanned", 0))
    for cat, cnts in ranked[:15]:
        print(
            f"    {cat:35s} scanned={cnts.get('scanned',0):4d} "
            f"correct={cnts.get('correct',0):4d} "
            f"incorrect={cnts.get('incorrect',0):4d}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
