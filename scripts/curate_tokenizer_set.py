#!/usr/bin/env python3
"""curate_tokenizer_set.py -- tokenizer Phase 1 curation + frozen manifest.

Story 131.22 (Epic 131); scope = docs/architecture/tokenizer-v04-plan.md
section 3 D2/D5/D6/D7 and section 4 Phase 1 items 1-5.  Masking rule =
docs/spec/patterns-protocol-v0.4.md section 4 (toke/scripts/patterns/mask_strings.py).

Sources (D5)
  * corpus/regen_v04/{A-*,D-*,L-*}/*.json accepted records
    (validation.compiler_exit_code == 0 AND judge.accepted), audit bucket per
    regen/audit.py (last write wins in audit/audit_corpus.jsonl): `build_fail`,
    `compile_fail`, `audit_error` are EXCLUDED; `test_fail` / `new_gate_only` /
    `not_executable` / `driver_fail` / `pass` are INCLUDED (they compile; a
    tokenizer cares about surface statistics).
  * --library-dir: toke-test-programs results/solutions/<cat>/<id>/solution.tk,
    restricted to the `pass` bucket of audit/audit_library.jsonl (the 1,583
    idiom-pass library programs) until they are ingested as L-* records.
  * a synthetic stdlib public-surface doc generated from toke/stdlib/*.tki
    (module + function/route/type names, params, returns), rendered in the v0.4
    charset and repeated --stdlib-repeat times (default 5: clears every
    `min_frequency` value the Phase 2 sweep considers, 2..5, with one
    occurrence per repetition).  Never in the holdout.

Pipeline per program (D6/D2)
  tkc --min (one process per program, parallel; a `min_fail` -- timeout, non-zero
  exit, exec error, empty output -- is retried up to --min-attempts times and a
  retried-then-ok program is logged under `min_retry`) -> drop+log failures; >1 output
  line == raw newline inside a literal (131.34) -> logged `multiline_literal`
  (0 since 131.34 fixed `--min`);
  mask string bodies to "_" (escape-aware, keeps \\(...) interiors);
  v0.4 charset validation outside string literals (no uppercase, `_`, `,`);
  --max-line-bytes guard; SHA-256 exact dedup post-min (first by task_id order).

Holdout (D7)
  Carved by base task family (A-CAT-NNNNvMM -> A-CAT-NNNN, same rule as
  regen/a_tests_prep.py; library programs are their own family), stratified by
  category x difficulty, whole eligible families drawn per stratum in seeded
  order until ~--holdout-frac of the stratum's records is covered.  Families with any member in data/holdout_task_ids.txt
  (benchmark ids) or in the 131.20 baseline sample are ineligible; disjointness
  is asserted after the carve.

Outputs (labelled by --label)
  data/tokenizer_training_<label>.txt      one masked --min program per line
  data/tokenizer_holdout_<label>.txt       same, holdout families
  docs/tokenizer_data_report_<label>.md    counts / exclusions / charset / dedup / holdout
  data/tokenizer_manifest_<label>.json     frozen manifest (record + line SHAs, tkc pin,
                                           corpus HEAD, counts, holdout spec, script sha)
  data/tokenizer_curate_<label>.log.jsonl  per-exclusion detail (gitignored)

Reproducibility: inputs are sorted, the RNG is seeded, no timestamps are
written; --repro-check runs the whole pipeline twice and asserts identical
output bytes + per-record --min SHAs (recorded in the manifest; per-program
SHA log in data/tokenizer_repro_<label>.log.jsonl).  --pin-tkc copies the
compiler binary to a private temp dir first: ~/tk/toke/tkc is a symlink that a
concurrent `make` relinks, and an exec during that swap (ENOENT) or against a
different build was the one-program divergence seen on the first pre131 run.

Usage
  python3 scripts/curate_tokenizer_set.py --label pre131 --pin-tkc \\
      --library-dir ~/tk/toke-test-programs/results/solutions --repro-check
  # after 131.19: same command with --label v04
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TOKE = Path(os.environ.get("TOKE_REPO", Path.home() / "tk" / "toke"))
DEFAULT_TOKENIZER_REPO = Path.home() / "tk" / "toke-tokenizer"

# --- mask_strings.py is imported from the compiler repo (never copied) --------
sys.path.insert(0, str(DEFAULT_TOKE / "scripts" / "patterns"))
try:
    from mask_strings import mask_strings, skip_string  # noqa: E402
except ImportError as e:  # pragma: no cover
    sys.exit(f"cannot import toke/scripts/patterns/mask_strings.py ({e}); set $TOKE_REPO")

STUB_PREFIX = "m=harness;i=io:std.io;"           # single_function harness stub (131.4 note)
RECORD_DIR_GLOBS = ("A-*", "D-*", "L-*")          # same as regen/manifest_tool.py
_BASE = re.compile(r"^([A-Z]-[A-Z]+-\d+)v\d+$")    # A-CAT-NNNNvMM -> A-CAT-NNNN (a_tests_prep.py)
# v0.4 Profile-1 charset outside string literals (lowercase, digits, punctuation, space).
V04_OUTSIDE = set("abcdefghijklmnopqrstuvwxyz0123456789 =;{}()<>!.:@$\"+-*/%&|^~#?\\'")
FORBIDDEN_OUTSIDE = {"upper": lambda c: "A" <= c <= "Z", "underscore": lambda c: c == "_",
                     "comma": lambda c: c == ","}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              check=False).stdout.strip()
    except OSError:
        return ""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --- sources -----------------------------------------------------------------

def base_of(task_id: str) -> str:
    m = _BASE.match(task_id)
    return m.group(1) if m else task_id


def audit_bucket(r: dict) -> str:
    """regen/audit.py cmd_report bucket rule, verbatim."""
    if r.get("audit_error"):
        return "audit_error"
    if not r.get("compile"):
        return "compile_fail"
    if r.get("driver_fail"):
        return "driver_fail"
    if not r.get("executed"):
        return "not_executable"
    if not r.get("build"):
        return "build_fail"
    if not r.get("tests_old"):
        return "test_fail"
    if not r.get("tests_new"):
        return "new_gate_only"
    return "pass"


def library_bucket(r: dict) -> str:
    """regen/audit_library.py cmd_report bucket rule, verbatim."""
    if r.get("audit_error"):
        return "audit_error"
    if not r.get("compile"):
        return "compile_fail"
    if not r.get("executed"):
        return "static_only_ok"
    if not r.get("tests_old"):
        return "test_fail"
    if not r.get("tests_new"):
        return "new_gate_only"
    return "pass"


def load_audit(path: Path) -> dict[str, str]:
    """task_id -> bucket (last write wins, as audit.py does)."""
    rows: dict[str, dict] = {}
    if not path.exists():
        return {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            rows[r["task_id"]] = r
    return {t: audit_bucket(r) for t, r in rows.items()}


def collect_regen(corpus: Path, audit: dict[str, str], excl: list[dict]) -> list[dict]:
    items = []
    paths: list[Path] = []
    for g in RECORD_DIR_GLOBS:
        for d in sorted(corpus.glob(g)):
            if d.is_dir():
                paths.extend(sorted(d.glob("*.json")))
    for p in paths:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            excl.append({"task_id": p.stem, "source": "regen", "reason": "unreadable", "detail": str(e)[:200]})
            continue
        tid = r.get("task_id") or p.stem
        regen = r.get("regen") or {}
        item = {
            "task_id": tid, "source": "regen", "path": str(p.relative_to(ROOT)),
            "category": regen.get("category") or p.parent.name,
            "difficulty": regen.get("difficulty"),
            "task_type": regen.get("task_type"),
            "record_sha256": sha256_file(p),
            "src": r.get("tk_source") or "",
        }
        accepted = ((r.get("validation") or {}).get("compiler_exit_code") == 0
                    and bool((r.get("judge") or {}).get("accepted")))
        if not accepted:
            excl.append({**_pub(item), "reason": "not_accepted",
                         "detail": f"exit={((r.get('validation') or {}).get('compiler_exit_code'))} "
                                   f"accepted={(r.get('judge') or {}).get('accepted')}"})
            continue
        bucket = audit.get(tid, "unaudited")
        item["audit_bucket"] = bucket
        if bucket in ("build_fail", "compile_fail", "audit_error"):
            excl.append({**_pub(item), "reason": bucket, "detail": "audit_corpus.jsonl bucket"})
            continue
        if not item["src"].strip():
            excl.append({**_pub(item), "reason": "empty_source", "detail": ""})
            continue
        items.append(item)
    items.sort(key=lambda it: it["task_id"])
    return items


def collect_library(lib_dir: Path | None, lib_audit: Path, excl: list[dict]) -> list[dict]:
    if lib_dir is None:
        return []
    items = []
    if lib_audit.exists():
        rows: dict[str, dict] = {}
        for line in open(lib_audit, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                if r.get("asset") == "library":
                    rows[r["id"]] = r
        for pid in sorted(rows):
            r = rows[pid]
            b = library_bucket(r)
            p = lib_dir / r["category"] / pid / "solution.tk"
            item = {"task_id": pid, "source": "library", "path": str(p.relative_to(lib_dir)), "category": r["category"],
                    "difficulty": r.get("difficulty"), "task_type": "library", "audit_bucket": b}
            if b != "pass":
                excl.append({**_pub(item), "reason": f"library_{b}", "detail": "audit_library.jsonl bucket"})
                continue
            if not p.exists():
                excl.append({**_pub(item), "reason": "library_missing", "detail": str(p)})
                continue
            item["record_sha256"] = sha256_file(p)
            item["src"] = p.read_text(encoding="utf-8")
            items.append(item)
    else:  # no audit: take every solution.tk
        for p in sorted(lib_dir.glob("*/*/solution.tk")):
            items.append({"task_id": p.parent.name, "source": "library", "path": str(p.relative_to(lib_dir)),
                          "category": p.parent.parent.name, "difficulty": None, "task_type": "library",
                          "audit_bucket": "unaudited", "record_sha256": sha256_file(p),
                          "src": p.read_text(encoding="utf-8")})
    items.sort(key=lambda it: it["task_id"])
    return items


def _pub(item: dict) -> dict:
    return {k: item.get(k) for k in ("task_id", "source", "path", "category", "difficulty", "task_type")}


# --- stdlib public-surface doc ------------------------------------------------

def _v04_type(t: str | None) -> str:
    """Render a .tki type string in the v0.4 charset: [T] -> @T, lowercase, no `_`."""
    if not t or t == "void":
        return ""
    t = t.strip()
    while True:
        m = re.search(r"\[([^\[\]]*)\]", t)
        if not m:
            break
        t = t[:m.start()] + "@" + m.group(1) + t[m.end():]
    t = t.replace(",", ";").replace("_", "")
    # error types are written `!$name` in v0.4 corpus text (`i64!$lookuperr`)
    t = re.sub(r"!(?!\$)([a-zA-Z])", r"!$\1", t)
    return t.lower()


def build_stdlib_doc(stdlib_dir: Path, excl: list[dict]) -> tuple[list[dict], dict]:
    """One line per module: `i=<short>:<module>;<name>(<params>):<ret>;...`."""
    lines = []
    stats = {"modules": 0, "entries": 0, "skipped_entries": 0, "files": []}
    for p in sorted(stdlib_dir.glob("*.tki")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            excl.append({"task_id": f"STDLIB-{p.stem}", "source": "stdlib", "path": str(p),
                         "reason": "stdlib_unreadable", "detail": str(e)[:200]})
            continue
        module = d.get("module") or f"std.{p.stem}"
        short = module.split(".")[-1]
        parts = [f"i={short}:{module};"]
        seen_parts: set[str] = set()
        n = 0
        for e in d.get("exports") or []:
            kind, name = e.get("kind"), e.get("name") or ""
            if kind in ("func", "route"):
                if "_" in name or re.search(r"[A-Z,]", name):
                    stats["skipped_entries"] += 1
                    continue
                params = ";".join(_v04_type(x) for x in (e.get("params") or []))
                ret = _v04_type(e.get("return"))
                part = f"{name}({params})" + (f":{ret}" if ret else "") + ";"
                if part in seen_parts:
                    stats["skipped_entries"] += 1
                    continue
                seen_parts.add(part)
                parts.append(part)
                n += 1
            elif kind in ("type", "sum_type", "sum", "record", "opaque"):
                tname = name.replace("_", "").lower()
                if not tname or re.search(r"[,]", tname):
                    stats["skipped_entries"] += 1
                    continue
                fields = ";".join(f"{f.get('name', '').replace('_', '').lower()}:{_v04_type(f.get('type'))}"
                                  for f in (e.get("fields") or []) if isinstance(f, dict))
                part = f"t={short}.{tname}{{{fields}}};" if fields else f"t={short}.{tname};"
                if part in seen_parts:
                    stats["skipped_entries"] += 1
                    continue
                seen_parts.add(part)
                parts.append(part)
                n += 1
            else:
                stats["skipped_entries"] += 1
        if n == 0:
            continue
        lines.append({"task_id": f"STDLIB-{module}", "source": "stdlib", "path": f"stdlib/{p.name}", "category": "STDLIB",
                      "difficulty": None, "task_type": "stdlib_doc", "record_sha256": sha256_file(p),
                      "min": "".join(parts)})
        stats["modules"] += 1
        stats["entries"] += n
        stats["files"].append(p.name)
    return lines, stats


# --- tkc --min ---------------------------------------------------------------

def _tkc_min_once(path: str, tkc: Path, timeout: float) -> tuple[str | None, str, str]:
    """One `tkc --min` attempt. Return (single line, reason, detail); reason '' on success."""
    try:
        proc = subprocess.run([str(tkc), "--min", path], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "min_fail", f"timeout {timeout}s"
    except OSError as e:
        # ENOENT / ETXTBSY: the binary was swapped under us (a concurrent `make` in the toke repo
        # relinks `tkc`); the 131.22 pre131 repro divergence was exactly this. Transient -> retried.
        return None, "min_fail", f"exec error: {e.strerror or e}"
    if proc.returncode != 0:
        return None, "min_fail", f"exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}"
    out = proc.stdout.rstrip("\n")
    if not out.strip():
        return None, "min_fail", "empty output"
    lines = out.split("\n")
    if len(lines) > 1:
        return None, "multiline_literal", f"{len(lines)} lines (131.34)"
    return lines[0], "", ""


def tkc_min(src: str, tkc: Path, timeout: float, attempts: int = 3, backoff: float = 0.5) -> dict:
    """`tkc --min` with up to `attempts` tries for `min_fail` outcomes (timeout, non-zero exit,
    exec error, empty output).  `multiline_literal` and success are deterministic -> no retry.
    Returns {line, reason, detail, attempts, failures}; a retried-then-succeeded program is
    reported under `min_retry` (not excluded)."""
    with tempfile.NamedTemporaryFile("w", suffix=".tk", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    failures: list[str] = []
    try:
        for a in range(1, max(1, attempts) + 1):
            line, reason, detail = _tkc_min_once(path, tkc, timeout)
            if reason != "min_fail":
                return {"line": line, "reason": reason, "detail": detail, "attempts": a, "failures": failures}
            failures.append(f"#{a}: {detail}")
            if a < attempts:
                time.sleep(backoff * a)
    finally:
        os.unlink(path)
    return {"line": None, "reason": "min_fail", "detail": f"{attempts} attempts: " + " | ".join(failures),
            "attempts": attempts, "failures": failures}


def _violation_tokens(masked: str) -> Counter:
    """Identifier-ish tokens outside literals that contain a forbidden char (for the report)."""
    out: list[str] = []
    i, n = 0, len(masked)
    while i < n:
        if masked[i] == '"':
            i = skip_string(masked, i)
            continue
        out.append(masked[i])
        i += 1
    return Counter(re.findall(r"[A-Za-z0-9$_,]*[A-Z_,][A-Za-z0-9_]*", "".join(out)))


def charset_violations(masked: str) -> tuple[Counter, Counter]:
    """(forbidden-class counts, all chars outside literals) on masked --min text."""
    bad: Counter = Counter()
    outside: Counter = Counter()
    i, n = 0, len(masked)
    while i < n:
        c = masked[i]
        if c == '"':
            i = skip_string(masked, i)
            continue
        outside[c] += 1
        for cls, pred in FORBIDDEN_OUTSIDE.items():
            if pred(c):
                bad[cls] += 1
        i += 1
    return bad, outside


# --- holdout -----------------------------------------------------------------

def read_id_file(p: Path | None) -> set[str]:
    ids: set[str] = set()
    if p is None or not p.exists():
        return ids
    for line in open(p, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        ids.add(s.split("\t")[0].split()[0])
    return ids


def carve_holdout(kept: list[dict], frac: float, seed: int, protected: set[str], target: str = "records") -> dict:
    """Family-level stratified carve.  Returns spec dict; sets item['split'].

    target="records": per stratum, eligible families are drawn in seeded-shuffled
    order until the holdout covers ~frac of the stratum's kept records (whole
    families only).  target="families": exactly round(frac * eligible) families.
    The records target is the default because the protected (benchmark/baseline)
    ids sit in most multi-variant families, leaving mostly singleton families
    eligible -- 5% of families would be <1% of records."""
    fams: dict[str, list[dict]] = defaultdict(list)
    for it in kept:
        fams[it["base"]].append(it)
    strata: dict[str, list[str]] = defaultdict(list)
    ineligible: dict[str, int] = Counter()
    fam_stratum: dict[str, str] = {}
    for base in sorted(fams):
        members = sorted(fams[base], key=lambda it: it["task_id"])
        head = members[0]
        stratum = f"{head['category']}|{head['difficulty']}"
        fam_stratum[base] = stratum
        if any(m["task_id"] in protected for m in members) or base in protected:
            ineligible[stratum] += 1
            continue
        strata[stratum].append(base)
    chosen: set[str] = set()
    per_stratum = OrderedDict()
    stratum_records: Counter = Counter()
    for base, st in fam_stratum.items():
        stratum_records[st] += len(fams[base])
    for stratum in sorted(set(fam_stratum.values())):
        elig = strata.get(stratum, [])
        rng = random.Random(f"{seed}:{stratum}")
        order = list(elig)
        rng.shuffle(order)
        if target == "records":
            goal = int(round(frac * stratum_records[stratum]))
            pick, got = [], 0
            for b in order:
                if got >= goal:
                    break
                pick.append(b)
                got += len(fams[b])
        else:
            goal = int(round(frac * len(elig)))
            pick = order[:goal]
        pick = sorted(pick)
        chosen.update(pick)
        per_stratum[stratum] = {"families": len(elig) + ineligible[stratum], "eligible": len(elig),
                                "ineligible_protected": ineligible[stratum], "records": stratum_records[stratum],
                                "goal": goal, "holdout_families": len(pick),
                                "holdout_records": sum(len(fams[b]) for b in pick)}
    for it in kept:
        it["split"] = "holdout" if it["base"] in chosen else "train"
    return {"rule": "base task family (A-CAT-NNNNvMM -> A-CAT-NNNN; other ids are their own family), "
                    "stratified category x difficulty (stratum of the family's lowest task_id); per stratum the "
                    "eligible families are shuffled with random.Random(f'{seed}:{stratum}') and taken "
                    + ("until ~fraction of the stratum's kept records is covered (whole families)"
                       if target == "records" else "up to round(fraction * eligible families)"),
            "target": target, "fraction": frac, "seed": seed, "families_total": len(fams),
            "families_holdout": len(chosen), "per_stratum": per_stratum}


# --- main --------------------------------------------------------------------

def run(args: argparse.Namespace) -> dict:
    t0 = time.time()
    corpus = ROOT / args.corpus if not Path(args.corpus).is_absolute() else Path(args.corpus)
    excl: list[dict] = []

    audit = load_audit(corpus / "audit" / "audit_corpus.jsonl")
    regen = collect_regen(corpus, audit, excl)
    lib = collect_library(Path(args.library_dir).expanduser() if args.library_dir else None,
                          corpus / "audit" / "audit_library.jsonl", excl)
    if args.limit:
        regen, lib = regen[:args.limit], lib[:args.limit]
    items = regen + lib
    log(f"[{args.label}] candidates: regen={len(regen)} library={len(lib)} "
        f"(pre-filter exclusions {len(excl)}) in {time.time() - t0:.1f}s")

    # tkc --min, parallel, one process per program
    t1 = time.time()
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(lambda it: tkc_min(it["src"], args.tkc, args.timeout, args.min_attempts), items))
    log(f"[{args.label}] tkc --min x{len(items)} in {time.time() - t1:.1f}s ({args.jobs} jobs)")

    kept: list[dict] = []
    retries: list[dict] = []        # succeeded after >=1 failed attempt (environmental, not excluded)
    violation_tokens: Counter = Counter()
    for it, r in zip(items, results):
        line, reason, detail = r["line"], r["reason"], r["detail"]
        if r["failures"] and not reason:
            retries.append({**_pub(it), "reason": "min_retry", "attempts": r["attempts"],
                            "detail": " | ".join(r["failures"])})
        if reason:
            excl.append({**_pub(it), "reason": reason, "detail": detail})
            continue
        masked = mask_strings(line)
        bad, outside = charset_violations(masked)
        if bad:
            excl.append({**_pub(it), "reason": "charset", "detail": ",".join(f"{k}={v}" for k, v in sorted(bad.items()))})
            violation_tokens.update(_violation_tokens(masked))
            continue
        nbytes = len(masked.encode("utf-8"))
        if nbytes > args.max_line_bytes:
            excl.append({**_pub(it), "reason": "oversize", "detail": f"{nbytes} bytes > {args.max_line_bytes}"})
            continue
        it["min"] = masked
        it["min_bytes_unmasked"] = len(line.encode("utf-8"))
        it["line_bytes"] = nbytes
        it["min_sha256"] = sha256_bytes(masked.encode("utf-8"))
        it["base"] = base_of(it["task_id"]) if it["source"] == "regen" else it["task_id"]
        kept.append(it)

    # SHA-256 exact dedup post-min, first by task_id order (regen sorted, then library sorted)
    seen: dict[str, dict] = {}
    deduped: list[dict] = []
    dup_pairs = Counter()
    for it in kept:
        first = seen.get(it["min_sha256"])
        if first is not None:
            excl.append({**_pub(it), "reason": "duplicate", "detail": f"dup_of={first['task_id']}"})
            dup_pairs[(it["source"], first["source"])] += 1
            continue
        seen[it["min_sha256"]] = it
        deduped.append(it)
    kept = deduped

    # holdout carve (D7)
    bench_ids = read_id_file(args.benchmark_ids)
    baseline_ids = read_id_file(args.baseline_ids)
    protected = bench_ids | baseline_ids
    holdout_spec = carve_holdout(kept, args.holdout_frac, args.holdout_seed, protected, args.holdout_target)
    hold_ids = {it["task_id"] for it in kept if it["split"] == "holdout"}
    hold_bases = {it["base"] for it in kept if it["split"] == "holdout"}
    train_bases = {it["base"] for it in kept if it["split"] == "train"}
    assert not (hold_ids & bench_ids), f"holdout overlaps benchmark ids: {sorted(hold_ids & bench_ids)[:5]}"
    assert not (hold_bases & bench_ids), "holdout family overlaps benchmark ids"
    assert not (hold_ids & baseline_ids), f"holdout overlaps 131.20 baseline ids: {sorted(hold_ids & baseline_ids)[:5]}"
    assert not (hold_bases & train_bases), "a family is split across train and holdout"
    holdout_spec["benchmark_ids_file"] = str(args.benchmark_ids) if args.benchmark_ids else None
    holdout_spec["benchmark_ids_n"] = len(bench_ids)
    holdout_spec["baseline_ids_file"] = str(args.baseline_ids) if args.baseline_ids else None
    holdout_spec["baseline_ids_n"] = len(baseline_ids)
    holdout_spec["disjoint_benchmark"] = True
    holdout_spec["disjoint_baseline"] = True
    holdout_spec["family_disjoint_train"] = True

    # stdlib public-surface doc (training only, repeated)
    stdlib_lines, stdlib_stats = build_stdlib_doc(args.stdlib_dir, excl)
    ok_stdlib = []
    for sl in stdlib_lines:
        bad, _ = charset_violations(sl["min"])
        if bad:
            excl.append({**_pub(sl), "reason": "charset", "detail": "stdlib doc line: " + ",".join(f"{k}={v}" for k, v in sorted(bad.items()))})
            continue
        sl["min_sha256"] = sha256_bytes(sl["min"].encode("utf-8"))
        sl["line_bytes"] = len(sl["min"].encode("utf-8"))
        sl["base"] = sl["task_id"]
        sl["split"] = "train"
        ok_stdlib.append(sl)
    stdlib_stats["repeat"] = args.stdlib_repeat
    stdlib_stats["lines_emitted"] = len(ok_stdlib) * args.stdlib_repeat
    stdlib_stats["doc_sha256"] = sha256_bytes("".join(s["min"] + "\n" for s in ok_stdlib).encode("utf-8"))

    # stub-prefix report + optional strip (131.4 note)
    stub = Counter()
    for it in kept:
        if it["min"].startswith(STUB_PREFIX):
            stub[it["task_type"]] += 1
    stripped = 0
    for it in kept:
        emit = it["min"]
        if args.dedup_stub_prefix and it["task_type"] == "single_function" and emit.startswith(STUB_PREFIX):
            emit = emit[len(STUB_PREFIX):]
            stripped += 1
        it["emit"] = emit
        it["stub_stripped"] = emit != it["min"]
        it["line_sha256"] = sha256_bytes(emit.encode("utf-8"))
    stub_report = {"prefix": STUB_PREFIX, "lines_with_prefix": sum(stub.values()),
                   "by_task_type": dict(sorted(stub.items(), key=lambda kv: str(kv[0]))),
                   "dedup_stub_prefix": bool(args.dedup_stub_prefix), "stripped": stripped,
                   "why": "131.4: the proxy8k tokenizer spent 5.9% of its vocab (479 merges >32 bytes) on the "
                          "repeated single_function harness stub; --dedup-stub-prefix removes exactly this "
                          "prefix from single_function lines before emission so BPE cannot learn it as one "
                          "long merge (alternative: max_token_length in the Phase 2 trainer)."}

    # emission
    train_lines = [it["emit"] for it in kept if it["split"] == "train"]
    for _ in range(args.stdlib_repeat):
        train_lines.extend(s["min"] for s in ok_stdlib)
    hold_lines = [it["emit"] for it in kept if it["split"] == "holdout"]
    train_txt = "".join(l + "\n" for l in train_lines)
    hold_txt = "".join(l + "\n" for l in hold_lines)

    # charset stats over the emitted training text
    all_chars = Counter(train_txt)
    outside_chars: Counter = Counter()
    forbidden_out = 0
    for l in train_lines:
        bad, oc = charset_violations(l)
        forbidden_out += sum(bad.values())
        outside_chars.update(oc)
    unexpected = {c: n for c, n in outside_chars.items() if c not in V04_OUTSIDE}

    # counts
    excl_by_reason = Counter(e["reason"] for e in excl)
    excl_by_source_reason = Counter((e.get("source"), e["reason"]) for e in excl)
    counts = {
        "candidates": {"regen": len(regen), "library": len(lib)},
        "candidates_by_category": dict(sorted(Counter(it["category"] for it in items).items())),
        "audit_buckets_included": dict(sorted(Counter(it.get("audit_bucket") for it in regen).items(), key=lambda kv: str(kv[0]))),
        "kept": len(kept),
        "kept_by_source": dict(sorted(Counter(it["source"] for it in kept).items())),
        "kept_by_category": dict(sorted(Counter(it["category"] for it in kept).items())),
        "kept_by_task_type": dict(sorted(Counter(str(it["task_type"]) for it in kept).items())),
        "train_records": sum(1 for it in kept if it["split"] == "train"),
        "holdout_records": len(hold_lines),
        "train_lines_total": len(train_lines),
        "excluded_total": len(excl),
        "min_retry": len(retries),
        "min_retry_ids": [r["task_id"] for r in retries],
        "excluded_by_reason": dict(sorted(excl_by_reason.items())),
        "excluded_by_source_reason": {f"{s}:{r}": n for (s, r), n in sorted(excl_by_source_reason.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))},
        "duplicates_by_source": {f"{a}->{b}": n for (a, b), n in sorted(dup_pairs.items())},
        "line_bytes": _dist([it["line_bytes"] for it in kept]),
        "mask_savings_bytes": sum(it["min_bytes_unmasked"] - it["line_bytes"] for it in kept),
    }
    charset = {"distinct_chars_total": len(all_chars),
               "violation_tokens_top": dict(violation_tokens.most_common(30)),
               "chars_outside_literals": {c: n for c, n in sorted(outside_chars.items())},
               "unexpected_outside_literals": unexpected,
               "forbidden_outside_literals_in_output": forbidden_out}

    return {"label": args.label, "kept": kept, "stdlib": ok_stdlib, "stdlib_stats": stdlib_stats, "excl": excl,
            "retries": retries, "counts": counts, "charset": charset, "holdout": holdout_spec, "stub": stub_report,
            "train_txt": train_txt, "hold_txt": hold_txt, "elapsed_s": round(time.time() - t0, 1)}


def _dist(v: list[int]) -> dict:
    if not v:
        return {}
    s = sorted(v)
    q = lambda p: s[min(int(p * len(s)), len(s) - 1)]
    return {"n": len(s), "min": s[0], "p50": q(0.5), "p90": q(0.9), "p99": q(0.99), "max": s[-1],
            "total": sum(s)}


def build_manifest(res: dict, args: argparse.Namespace, out_shas: dict) -> dict:
    script = Path(__file__).resolve()
    toke = Path(args.toke_repo).expanduser().resolve()
    ver = subprocess.run([str(args.tkc), "--version"], capture_output=True, text=True, check=False)
    records = []
    for it in res["kept"]:
        records.append(OrderedDict([
            ("task_id", it["task_id"]), ("source", it["source"]), ("path", it["path"]),
            ("category", it["category"]), ("difficulty", it["difficulty"]), ("task_type", it["task_type"]),
            ("base", it["base"]), ("split", it["split"]), ("audit_bucket", it.get("audit_bucket")),
            ("record_sha256", it["record_sha256"]), ("min_sha256", it["min_sha256"]),
            ("line_sha256", it["line_sha256"]), ("line_bytes", it["line_bytes"]),
            ("stub_stripped", bool(it["stub_stripped"])),
        ]))
    for s in res["stdlib"]:
        records.append(OrderedDict([
            ("task_id", s["task_id"]), ("source", "stdlib"), ("path", s["path"]), ("category", "STDLIB"),
            ("difficulty", None), ("task_type", "stdlib_doc"), ("base", s["task_id"]), ("split", "train"),
            ("audit_bucket", None), ("record_sha256", s["record_sha256"]), ("min_sha256", s["min_sha256"]),
            ("line_sha256", s["min_sha256"]), ("line_bytes", s["line_bytes"]), ("stub_stripped", False),
            ("repeat", args.stdlib_repeat),
        ]))
    return OrderedDict([
        ("schema", "toke-corpus tokenizer manifest v1 (story 131.22)"),
        ("label", args.label),
        ("script", OrderedDict([
            ("path", str(script.relative_to(ROOT))),
            ("git_blob_sha", git(ROOT, "hash-object", str(script))),
            ("file_sha256", sha256_file(script)),
            ("args", OrderedDict([
                ("corpus", args.corpus), ("library_dir", args.library_dir), ("stdlib_dir", str(args.stdlib_dir)),
                ("stdlib_repeat", args.stdlib_repeat), ("holdout_frac", args.holdout_frac),
                ("holdout_seed", args.holdout_seed), ("holdout_target", args.holdout_target),
                ("max_line_bytes", args.max_line_bytes),
                ("min_attempts", args.min_attempts), ("timeout", args.timeout), ("jobs", args.jobs),
                ("dedup_stub_prefix", bool(args.dedup_stub_prefix)),
                ("benchmark_ids", str(args.benchmark_ids) if args.benchmark_ids else None),
                ("baseline_ids", str(args.baseline_ids) if args.baseline_ids else None),
            ])),
        ])),
        ("toke_corpus", OrderedDict([("head", git(ROOT, "rev-parse", "HEAD"))])),
        ("tkc", OrderedDict([
            ("path", str(args.tkc_requested)), ("pinned_copy", str(args.tkc) if args.pin_tkc else None),
            ("version", (ver.stdout or ver.stderr).strip().splitlines()[0]),
            ("binary_sha256", sha256_file(args.tkc)),
            ("toke_repo", str(toke)),
            ("toke_head", git(toke, "rev-parse", "HEAD")),
            ("src_dirty", bool(git(toke, "status", "--porcelain", "--", "src"))),
            ("last_src_commit", git(toke, "log", "-1", "--format=%H", "--", "src")),
            ("last_src_commit_date", git(toke, "log", "-1", "--format=%cs", "--", "src")),
            ("note", "the binary sha256 is the pin; toke_head/src_dirty say what tree it was built from "
                     "(src_dirty=true means uncommitted compiler edits were present -- other workers share the repo)"),
        ])),
        ("mask", "toke/scripts/patterns/mask_strings.py (patterns-protocol-v0.4 section 4): string bodies -> _, "
                 "escape-aware, \\(...) interiors kept"),
        ("charset_rule", "outside string literals: no A-Z, no _, no , (v0.4 Profile-1)"),
        ("counts", res["counts"]),
        ("charset", res["charset"]),
        ("holdout", res["holdout"]),
        ("stub_prefix_report", res["stub"]),
        ("stdlib_doc", res["stdlib_stats"]),
        ("outputs", out_shas),
        ("repro_check", res.get("repro_check")),
        ("min_retry", [OrderedDict(sorted(e.items())) for e in res["retries"]]),
        ("excluded", [OrderedDict(sorted(e.items())) for e in res["excl"]]),
        ("records", records),
    ])


def build_report(res: dict, args: argparse.Namespace, out_shas: dict, manifest: dict) -> str:
    c, h, s = res["counts"], res["holdout"], res["stub"]
    L = [f"# Tokenizer training data report -- `{args.label}`", "",
         f"Story 131.22 (plan D2/D5/D6/D7, Phase 1 items 1-5). Generated by `scripts/curate_tokenizer_set.py` "
         f"(blob `{manifest['script']['git_blob_sha'][:12]}`) on toke-corpus `{manifest['toke_corpus']['head'][:12]}` "
         f"with `{manifest['tkc']['version']}` (toke `{manifest['tkc']['toke_head'][:12]}`, last src commit "
         f"`{manifest['tkc']['last_src_commit'][:12]}` {manifest['tkc']['last_src_commit_date']}).", "",
         "## Summary", "",
         f"- Candidates: regen {c['candidates']['regen']:,} + library {c['candidates']['library']:,}",
         f"- Kept after `--min` / mask / charset / size / dedup: **{c['kept']:,}** "
         f"({', '.join(f'{k} {v:,}' for k, v in c['kept_by_source'].items())})",
         f"- Training records {c['train_records']:,} + stdlib doc lines {res['stdlib_stats']['lines_emitted']} "
         f"= **{c['train_lines_total']:,} lines**; holdout **{c['holdout_records']:,}** records "
         f"({h['families_holdout']} of {h['families_total']} families, fraction {h['fraction']})",
         f"- Excluded {c['excluded_total']:,} (by reason below); duplicates removed {c['excluded_by_reason'].get('duplicate', 0):,}",
         f"- `multiline_literal` (131.34): {c['excluded_by_reason'].get('multiline_literal', 0)}; "
         f"`min_fail`: {c['excluded_by_reason'].get('min_fail', 0)}; `min_retry` (ok after a transient failure, "
         f"up to {args.min_attempts} attempts): {c['min_retry']}; charset violators: {c['excluded_by_reason'].get('charset', 0)}; "
         f"oversize (> {args.max_line_bytes} B): {c['excluded_by_reason'].get('oversize', 0)}",
         f"- Line bytes (masked --min): p50 {c['line_bytes'].get('p50')} p90 {c['line_bytes'].get('p90')} "
         f"p99 {c['line_bytes'].get('p99')} max {c['line_bytes'].get('max')}; masking saved {c['mask_savings_bytes']:,} bytes",
         "", "## Outputs", "", "| file | lines | bytes | sha256 |", "|---|--:|--:|---|"]
    for name, o in out_shas.items():
        L.append(f"| `{name}` | {o['lines']:,} | {o['bytes']:,} | `{o['sha256']}` |")
    L += ["", "## Sources and audit buckets", "",
          "Regen records require `validation.compiler_exit_code == 0` and `judge.accepted`; the audit bucket "
          "(regen/audit.py rule, last write wins in `audit/audit_corpus.jsonl`) excludes `build_fail` / `compile_fail` / "
          "`audit_error` and includes `test_fail` and friends (D5).", "",
          "| audit bucket (included regen candidates) | n |", "|---|--:|"]
    L += [f"| {k} | {v:,} |" for k, v in c["audit_buckets_included"].items()]
    L += ["", "| category | candidates | kept |", "|---|--:|--:|"]
    for cat, n in c["candidates_by_category"].items():
        L.append(f"| {cat} | {n:,} | {c['kept_by_category'].get(cat, 0):,} |")
    L += ["", "| task type (kept) | n |", "|---|--:|"]
    L += [f"| {k} | {v:,} |" for k, v in c["kept_by_task_type"].items()]
    L += ["", "## Exclusions by reason", "", "| reason | n |", "|---|--:|"]
    L += [f"| {k} | {v:,} |" for k, v in c["excluded_by_reason"].items()]
    L += ["", "| source:reason | n |", "|---|--:|"]
    L += [f"| {k} | {v:,} |" for k, v in c["excluded_by_source_reason"].items()]
    ml = [e for e in res["excl"] if e["reason"] == "multiline_literal"]
    if ml:
        L += ["", f"`multiline_literal` ids (131.34; first {min(len(ml), 40)}): " + ", ".join(f"`{e['task_id']}`" for e in ml[:40])]
    cs = [e for e in res["excl"] if e["reason"] == "charset"]
    if cs:
        L += ["", f"charset violators (first {min(len(cs), 40)}): " + ", ".join(f"`{e['task_id']}` ({e['detail']})" for e in cs[:40]),
              "", "most frequent offending tokens (outside literals) across violators: " +
              ", ".join(f"`{t}` x{n}" for t, n in list(res["charset"]["violation_tokens_top"].items())[:20])]
    ov = [e for e in res["excl"] if e["reason"] == "oversize"]
    if ov:
        L += ["", f"oversize (first {min(len(ov), 40)}): " + ", ".join(f"`{e['task_id']}` ({e['detail']})" for e in ov[:40])]
    L += ["", "## Dedup (SHA-256 of masked `--min` line, first by task_id order: regen, then library)", "",
          f"- duplicates removed: {c['excluded_by_reason'].get('duplicate', 0):,}",
          f"- by source pair (dup -> kept): {c['duplicates_by_source'] or '{}'}",
          "", "## Charset (outside string literals, emitted training text)", "",
          f"- distinct characters in the training text (incl. literal bodies): {res['charset']['distinct_chars_total']}",
          f"- forbidden (A-Z / `_` / `,`) outside literals in the output: {res['charset']['forbidden_outside_literals_in_output']} "
          "(violators are excluded upstream)",
          f"- unexpected chars outside literals (not in the v0.4 Profile-1 set): {res['charset']['unexpected_outside_literals'] or 'none'}",
          "", "| char | count |", "|---|--:|"]
    L += [f"| `{ch if ch != '`' else 'backtick'}`{'' if ch != ' ' else ' (space)'} | {n:,} |"
          for ch, n in sorted(res["charset"]["chars_outside_literals"].items(), key=lambda kv: -kv[1])[:60]]
    L += ["", "## Harness stub prefix report (131.4 note)", "",
          f"- prefix: `{s['prefix']}`", f"- lines starting with the prefix: {s['lines_with_prefix']:,} "
          f"(by task type: {s['by_task_type']})",
          f"- `--dedup-stub-prefix`: {'ON' if s['dedup_stub_prefix'] else 'off'}; stripped {s['stripped']:,}",
          f"- why: {s['why']}",
          "", "## Stdlib public-surface doc", "",
          f"- generated from `{args.stdlib_dir}/*.tki`: {res['stdlib_stats']['modules']} modules, "
          f"{res['stdlib_stats']['entries']} entries ({res['stdlib_stats']['skipped_entries']} skipped: `_`/uppercase/comma names or unknown kinds)",
          f"- one line per module `i=<short>:<module>;name(params):ret;t=<short>.<type>{{fields}};...`, types rendered "
          "`[T]`->`@T`, lowercased, `!Err`->`!$err` (v0.4 Profile-1 charset)",
          f"- repetition factor **{res['stdlib_stats']['repeat']}** (each name appears once per line; 5 clears every "
          f"`min_frequency` in the Phase 2 sweep range 2..5) -> {res['stdlib_stats']['lines_emitted']} training lines; "
          f"doc sha256 `{res['stdlib_stats']['doc_sha256']}`",
          "- never in the holdout",
          "", "## Holdout spec (D7)", "",
          f"- rule: {h['rule']}", f"- fraction {h['fraction']} of {h['target']}, seed {h['seed']}",
          f"- families {h['families_total']:,}, holdout families {h['families_holdout']:,}, holdout records {c['holdout_records']:,}",
          f"- protected (ineligible) ids: benchmark `{h['benchmark_ids_file']}` ({h['benchmark_ids_n']:,} ids), "
          f"131.20 baseline `{h['baseline_ids_file']}` ({h['baseline_ids_n']:,} ids)",
          f"- asserted: disjoint from benchmark ids = {h['disjoint_benchmark']}, disjoint from baseline ids = "
          f"{h['disjoint_baseline']}, no family split across train/holdout = {h['family_disjoint_train']}",
          "", "| stratum (category|difficulty) | families | eligible | protected | records | goal | holdout fam | holdout rec |",
          "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for st, v in h["per_stratum"].items():
        L.append(f"| {st} | {v['families']} | {v['eligible']} | {v['ineligible_protected']} | {v['records']} | {v['goal']} | "
                 f"{v['holdout_families']} | {v['holdout_records']} |")
    rc = res.get("repro_check")
    tk = manifest["tkc"]
    L += ["", "## Reproducibility", "",
          f"- inputs sorted, seeded RNG, no timestamps; `--repro-check`: "
          f"{'run twice, outputs byte-identical' if rc and rc.get('identical') else ('NOT identical' if rc else 'not run')}"
          + (f" (run1 train `{rc['train_sha256_run1'][:12]}` = run2 `{rc['train_sha256_run2'][:12]}`; "
             f"holdout `{rc['holdout_sha256_run1'][:12]}` = `{rc['holdout_sha256_run2'][:12]}`; per-record `--min` SHA "
             f"mismatches {len(rc.get('min_sha_mismatch_ids', []))}; `min_retry` run1 {rc.get('min_retry_run1')} / run2 "
             f"{rc.get('min_retry_run2')}; run1 {rc.get('elapsed_run1_s')}s, run2 {rc.get('elapsed_run2_s')}s)" if rc else ""),
          f"- `--min` is retried up to {args.min_attempts}x on `min_fail` (timeout / non-zero exit / exec error / empty output) "
          f"before a record is excluded; programs that needed a retry are listed under `min_retry` in the manifest "
          f"({c['min_retry']} this run{': ' + ', '.join(f'`{t}`' for t in c['min_retry_ids'][:20]) if c['min_retry'] else ''}). "
          "Root cause of the original pre131 divergence: `~/tk/toke/tkc` is a symlink that a concurrent `make` (other "
          "workers share the compiler repo) relinks, so an exec during the swap fails or runs a different build -- "
          "hence `--pin-tkc` (copy the binary before the run) and the retry.",
          f"- manifest: `data/tokenizer_manifest_{args.label}.json` (record + line SHAs, tkc pin, corpus HEAD, holdout spec)",
          f"- tkc pin: version `{tk['version']}`, binary sha256 `{tk['binary_sha256']}`"
          + (f" (pinned copy of `{tk['path']}`)" if tk.get("pinned_copy") else "")
          + f"; toke `{(tk['toke_head'] or '?')[:12]}`, src {'DIRTY (uncommitted compiler edits present)' if tk['src_dirty'] else 'clean'}, "
          f"last src commit `{(tk['last_src_commit'] or '?')[:12]}` {tk['last_src_commit_date']} -- the vocab is "
          "coupled to the minifier (D6): a rebuilt tkc can change `--min` output, so re-run with --repro-check against the "
          "pinned binary before trusting a manifest",
          ""]
    return "\n".join(L)


def _log_repro_diff(r1: dict, r2: dict) -> None:
    """Explain a failed --repro-check: which component differs and the first offenders."""
    k1 = {it["task_id"]: it for it in r1["kept"]}
    k2 = {it["task_id"]: it for it in r2["kept"]}
    only1, only2 = sorted(set(k1) - set(k2)), sorted(set(k2) - set(k1))
    log(f"repro diff: kept run1={len(k1)} run2={len(k2)}; only-run1={only1[:10]} only-run2={only2[:10]}")
    diff = [t for t in k1 if t in k2 and k1[t]["line_sha256"] != k2[t]["line_sha256"]]
    log(f"repro diff: {len(diff)} records with different line sha: {diff[:10]}")
    for t in diff[:3]:
        a, b = k1[t]["emit"], k2[t]["emit"]
        i = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
        log(f"  {t}: first diff at {i}: run1={a[max(0, i - 30):i + 30]!r} run2={b[max(0, i - 30):i + 30]!r}")
    e1 = {(e["task_id"], e["reason"]): e for e in r1["excl"]}
    e2 = {(e["task_id"], e["reason"]): e for e in r2["excl"]}
    log(f"repro diff: exclusions only-run1={sorted(set(e1) - set(e2))[:10]} only-run2={sorted(set(e2) - set(e1))[:10]}")
    for k in list(set(e1) - set(e2))[:3] + list(set(e2) - set(e1))[:3]:
        log(f"  {k}: {(e1.get(k) or e2.get(k))['detail'][:200]}")
    log(f"repro diff: holdout equal={r1['holdout'] == r2['holdout']} train_txt equal={r1['train_txt'] == r2['train_txt']}")


def produce(args: argparse.Namespace) -> dict:
    res = run(args)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True, help="output label, e.g. pre131 or v04")
    ap.add_argument("--corpus", default="corpus/regen_v04")
    ap.add_argument("--library-dir", default=None, help="toke-test-programs results/solutions")
    ap.add_argument("--stdlib-dir", type=Path, default=DEFAULT_TOKE / "stdlib")
    ap.add_argument("--stdlib-repeat", type=int, default=5)
    ap.add_argument("--tkc", type=Path, default=Path(os.environ.get("TKC", DEFAULT_TOKE / "tkc")))
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--min-attempts", type=int, default=3,
                    help="retries for a transient `tkc --min` failure before excluding the record (logged as min_retry)")
    ap.add_argument("--toke-repo", type=Path, default=DEFAULT_TOKE, help="compiler repo (git pin in the manifest)")
    ap.add_argument("--pin-tkc", action="store_true",
                    help="copy the tkc binary to a private temp dir before the run so a concurrent `make` cannot swap it")
    ap.add_argument("--max-line-bytes", type=int, default=4096)
    ap.add_argument("--holdout-frac", type=float, default=0.05)
    ap.add_argument("--holdout-seed", type=int, default=131)
    ap.add_argument("--holdout-target", choices=("records", "families"), default="records",
                    help="what --holdout-frac applies to per stratum (whole families either way)")
    ap.add_argument("--benchmark-ids", type=Path, default=ROOT / "data" / "holdout_task_ids.txt")
    ap.add_argument("--baseline-ids", type=Path, default=DEFAULT_TOKENIZER_REPO / "data" / "baseline_sample_ids_v04.txt")
    ap.add_argument("--dedup-stub-prefix", action="store_true")
    ap.add_argument("--repro-check", action="store_true", help="run twice; assert byte-identical outputs")
    ap.add_argument("--limit", type=int, default=0, help="smoke test: first N regen + N library candidates")
    ap.add_argument("--out-data", type=Path, default=ROOT / "data")
    ap.add_argument("--out-docs", type=Path, default=ROOT / "docs")
    args = ap.parse_args(argv)
    args.tkc = args.tkc.expanduser().resolve()
    args.tkc_requested = args.tkc
    if not args.tkc.exists():
        sys.exit(f"tkc not found at {args.tkc}")
    if args.pin_tkc:
        pin_dir = Path(tempfile.mkdtemp(prefix=f"tkc_pin_{args.label}_"))
        pinned = pin_dir / "tkc"
        shutil.copy2(args.tkc, pinned)
        pinned.chmod(0o755)
        log(f"[{args.label}] pinned {args.tkc} -> {pinned} (sha256 {sha256_file(pinned)[:12]})")
        args.tkc = pinned
    for k in ("benchmark_ids", "baseline_ids"):
        p = getattr(args, k)
        if p is not None and not p.exists():
            log(f"warning: {k} file missing: {p} (treated as empty)")
            setattr(args, k, None)

    args.out_data.mkdir(parents=True, exist_ok=True)
    args.out_docs.mkdir(parents=True, exist_ok=True)
    res = produce(args)
    if args.repro_check:
        res2 = produce(args)
        k1 = {it["task_id"]: it["min_sha256"] for it in res["kept"]}
        k2 = {it["task_id"]: it["min_sha256"] for it in res2["kept"]}
        mism = sorted(t for t in k1 if t in k2 and k1[t] != k2[t]) + sorted(set(k1) ^ set(k2))
        same = (res["train_txt"] == res2["train_txt"] and res["hold_txt"] == res2["hold_txt"]
                and [it["line_sha256"] for it in res["kept"]] == [it["line_sha256"] for it in res2["kept"]]
                and res["holdout"] == res2["holdout"] and res["excl"] == res2["excl"])
        res["repro_check"] = {"performed": True, "identical": bool(same),
                              "train_sha256_run1": sha256_bytes(res["train_txt"].encode()),
                              "train_sha256_run2": sha256_bytes(res2["train_txt"].encode()),
                              "holdout_sha256_run1": sha256_bytes(res["hold_txt"].encode()),
                              "holdout_sha256_run2": sha256_bytes(res2["hold_txt"].encode()),
                              "records_compared": len(k1), "min_sha_mismatch_ids": mism[:100],
                              "min_retry_run1": len(res["retries"]), "min_retry_run2": len(res2["retries"]),
                              "elapsed_run1_s": res["elapsed_s"], "elapsed_run2_s": res2["elapsed_s"]}
        # per-program --min SHA log for both runs (gitignored data/*.jsonl): diff-able offline
        with open(args.out_data / f"tokenizer_repro_{args.label}.log.jsonl", "w", encoding="utf-8") as f:
            for t in sorted(set(k1) | set(k2)):
                f.write(json.dumps({"task_id": t, "min_sha256_run1": k1.get(t), "min_sha256_run2": k2.get(t),
                                    "same": k1.get(t) == k2.get(t)}) + "\n")
            for tag, r in (("run1", res), ("run2", res2)):
                for e in r["retries"]:
                    f.write(json.dumps({"run": tag, **e}, ensure_ascii=False) + "\n")
        if not same:
            _log_repro_diff(res, res2)
            raise AssertionError("repro check FAILED: second run differs (see log above)")
        log(f"[{args.label}] repro check: identical (run2 {res2['elapsed_s']}s; min_retry run1 {len(res['retries'])} "
            f"run2 {len(res2['retries'])})")

    train_p = args.out_data / f"tokenizer_training_{args.label}.txt"
    hold_p = args.out_data / f"tokenizer_holdout_{args.label}.txt"
    man_p = args.out_data / f"tokenizer_manifest_{args.label}.json"
    rep_p = args.out_docs / f"tokenizer_data_report_{args.label}.md"
    logp = args.out_data / f"tokenizer_curate_{args.label}.log.jsonl"
    train_p.write_bytes(res["train_txt"].encode("utf-8"))
    hold_p.write_bytes(res["hold_txt"].encode("utf-8"))
    out_shas = OrderedDict()
    for p, txt in ((train_p, res["train_txt"]), (hold_p, res["hold_txt"])):
        out_shas[p.name] = {"sha256": sha256_bytes(txt.encode("utf-8")), "bytes": len(txt.encode("utf-8")),
                            "lines": txt.count("\n")}
    manifest = build_manifest(res, args, out_shas)
    man_p.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    rep_p.write_text(build_report(res, args, out_shas, manifest), encoding="utf-8")
    with open(logp, "w", encoding="utf-8") as f:
        for e in res["excl"] + res["retries"]:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    c = res["counts"]
    log(f"[{args.label}] kept {c['kept']:,} (train {c['train_records']:,} + holdout {c['holdout_records']:,}); "
        f"excluded {c['excluded_total']:,} {c['excluded_by_reason']}; min_retry {c['min_retry']}; "
        f"stub-prefix lines {res['stub']['lines_with_prefix']:,}; elapsed {res['elapsed_s']}s")
    for p in (train_p, hold_p, man_p, rep_p, logp):
        log(f"  wrote {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p} ({p.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
