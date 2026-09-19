#!/usr/bin/env python3
"""prepare_training_data.py — Build instruction-tuning JSONL from deduplicated corpus.

Stories 10.6.1 / 10.6.3: source weighting + multi-epoch training data.

Reads training_config.yaml, loads deduplicated corpus entries, applies quality
gates, performs source-weighted sampling, splits train/eval, and writes
instruction-tuning JSONL files.

Usage:
    python scripts/prepare_training_data.py [--config PATH] [--corpus-dir PATH]
                                            [--output-dir PATH] [--seed INT]
"""
from __future__ import annotations

import argparse
import collections
import fnmatch
import json
import os
import random
import sys
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "infra" / "training_config.yaml"
DEFAULT_CORPUS_DIR = ROOT / "corpus" / "phase2_deduplicated"
DEFAULT_QUALITY_SCORES = ROOT / "data" / "corpus_quality_scores.jsonl"
DEFAULT_COMPLEXITY = ROOT / "data" / "corpus_complexity_dedup.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "data"


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_jsonl_index(path: Path, key: str = "id") -> dict:
    """Load a JSONL file into a dict keyed by *key*."""
    index: dict = {}
    if not path.exists():
        return index
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            index[obj[key]] = obj
    return index


def category_matches(category: str, patterns: list[str]) -> bool:
    """Check whether *category* matches any of the glob-style *patterns*."""
    for pat in patterns:
        if fnmatch.fnmatch(category, pat):
            return True
    return False


def resolve_source_type(category: str, source_types: dict) -> str | None:
    """Return the source type name for a given corpus category, or None."""
    for stype, cfg in source_types.items():
        if category_matches(category, cfg["categories"]):
            return stype
    return None


SYSTEM_PROMPT_PATH = ROOT / "infra" / "system_prompt_phase2.txt"
_SYSTEM_PROMPT: str | None = None


def get_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text().strip()
    return _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Story 10.9.2: category-specific user prompts
# ---------------------------------------------------------------------------

_ERR_KIND = {
    "missing_semicolon": "a missing `;` terminator",
    "undefined_variable": "an undefined variable",
    "immutable_reassignment": "a reassignment of an immutable `let` binding",
    "type_mismatch": "a type mismatch",
    "wrong_argument_count": "a call with the wrong number of arguments",
    "wrong_operator": "a wrong operator",
}

_A_FEATURE = {
    "arr": "array operations",
    "cnd": "conditional branching",
    "err": "error handling",
    "mth": "arithmetic / math operations",
    "srt": "sorting or ordering",
    "str": "string manipulation",
}

_EC2_DOMAIN = {
    "cfg": "configuration parsing or management",
    "cli": "command-line tooling",
    "cry": "cryptography or hashing",
    "dat": "data processing",
    "fio": "file I/O",
    "net": "networking",
    "tst": "testing",
    "web": "web request handling",
}

_STD_TOPIC = {
    "STD-STR": ("std.str", "string manipulation"),
    "STD-FILE": ("std.file", "file I/O"),
    "STD-JSON": ("std.json", "JSON parsing or serialization"),
    "STD-CRYPTO": ("std.crypto", "cryptography / hashing"),
    "STD-TIME": ("std.time", "date/time handling"),
    "STD-ENV": ("std.env", "environment and process info"),
    "STD-LOG": ("std.log", "structured logging"),
    "STD-TEST": ("std.test", "unit testing"),
    "STD-PROC": ("std.proc", "subprocess management"),
    "STD-HTTP": ("std.http", "HTTP client or server work"),
    "STD-DB": ("std.db", "database queries"),
    "STD-I18N": ("std.i18n", "internationalization"),
    "STD-YAML": ("std.yaml", "YAML parsing or emission"),
    "STD-TOON": ("std.toon", "TOON serialization"),
}


def _imports_hint(imports: list[str]) -> str:
    mods = [m for m in imports if isinstance(m, str) and m.startswith("std.")]
    if not mods:
        return ""
    if len(mods) == 1:
        return f" Use `{mods[0]}`."
    if len(mods) == 2:
        return f" Use `{mods[0]}` and `{mods[1]}`."
    return f" Use `{'`, `'.join(mods[:-1])}`, and `{mods[-1]}`."


def build_user_prompt(entry: dict) -> str | None:
    """Return the user-turn content for this record.

    Returns None if the record should be dropped from training entirely
    (e.g. mutation variants — see Story 10.9.2).
    """
    cat = entry.get("category") or ""
    refs = entry.get("references") or {}
    imports = entry.get("imported_modules") or []

    # --- Drop mutation variants: prompts are meaningless -----------------
    if cat.startswith("MUT-"):
        return None

    # --- BIFI: fix-the-broken-program ----------------------------------------
    if cat.startswith("BIFI-"):
        kind = cat[len("BIFI-"):]
        label = _ERR_KIND.get(kind, kind.replace("_", " "))
        broken = refs.get("broken_source")
        diag = (refs.get("diagnostic") or "").strip()
        if isinstance(broken, str) and broken.strip():
            parts = [
                f"The following toke program has {label}. Fix it.",
            ]
            if diag:
                parts.append(f"Compiler error:\n```\n{diag}\n```")
            parts.append(f"Broken program:\n```toke\n{broken.strip()}\n```")
            return "\n\n".join(parts)
        return f"Write a toke program that shows the correct form when avoiding {label}."

    # --- ERR-TRIPLE: either demonstrate or repair ---------------------------
    if cat.startswith("ERR-TRIPLE-"):
        kind = cat[len("ERR-TRIPLE-"):]
        label = _ERR_KIND.get(kind, kind.replace("_", " "))
        broken = refs.get("broken_source")
        if isinstance(broken, str) and broken.strip():
            return (
                f"This toke program has {label}. Rewrite it as a correct, "
                f"compilable program.\n\n```toke\n{broken.strip()}\n```"
            )
        return f"Write a toke program that correctly avoids {label}."

    # --- DOC-EXP: documentation-backed example ------------------------------
    if cat == "DOC-EXP":
        doc = refs.get("doc_source")
        if isinstance(doc, str) and doc.strip():
            return f"Write a toke example that illustrates the concept from `{doc}`."
        return "Write a toke example suitable for language documentation."

    # --- STDLIB-* — explicit module/function --------------------------------
    if cat == "STDLIB":
        module = refs.get("module")
        func = refs.get("function")
        if module and func:
            return f"Write a toke program that demonstrates `{module}.{func}`."
        if module:
            return f"Write a toke program that demonstrates usage of `{module}`."
        return "Write a toke program that demonstrates a standard library function."
    if cat == "STDLIB-MULTI":
        hint = _imports_hint(imports)
        return f"Write a toke program that composes multiple standard library modules.{hint}"

    # --- Seed-generated STD-* — dedicated stdlib topic ----------------------
    if cat in _STD_TOPIC:
        module, topic = _STD_TOPIC[cat]
        return f"Write a toke program that performs {topic} using `{module}`."

    # --- APP-MULTI / COMP-MULTI — multi-function applications ---------------
    if cat == "APP-MULTI":
        hint = _imports_hint(imports)
        return f"Write a multi-function toke application that solves a realistic task.{hint}"
    if cat == "COMP-MULTI":
        hint = _imports_hint(imports)
        return f"Write a multi-function toke program that composes several helper functions.{hint}"

    # --- SIMPLE-ALG — elementary algorithms ---------------------------------
    if cat == "SIMPLE-ALG":
        return "Write a toke program implementing a small, self-contained algorithm."

    # --- ERR-PATTERN / ERR-HANDLE / ERR-GOLD -------------------------------
    if cat in ("ERR-PATTERN", "ERR-HANDLE"):
        return "Write a toke program that demonstrates idiomatic error handling with `|{$ok:v ...; $err:e ...}`."
    if cat == "ERR-GOLD":
        return "Write a hand-crafted gold-standard toke program that demonstrates idiomatic error handling."

    # --- COMPOSE-* — composition patterns -----------------------------------
    if cat == "COMPOSE-C":
        return "Write a toke program that composes functions through call chains."
    if cat == "COMPOSE-D":
        return "Write a toke program that composes functions through data accumulation."
    if cat == "COMPOSE-NEW":
        return "Write a multi-function toke program using a composition pattern (chain, pipeline, accumulator, fanout, or edge-guard)."

    # --- B-CMP — basic compilation ------------------------------------------
    if cat == "B-CMP":
        return "Write a short toke program that compiles cleanly."

    # --- EC2-D-* — domain-focused -------------------------------------------
    if cat.startswith("EC2-D-"):
        domain_key = cat[len("EC2-D-"):].lower()
        domain = _EC2_DOMAIN.get(domain_key, domain_key)
        hint = _imports_hint(imports)
        return f"Write a toke program for {domain}.{hint}"

    # --- A-* — algorithm families -------------------------------------------
    if cat.startswith("A-"):
        key = cat[len("A-"):].lower()
        feature = _A_FEATURE.get(key, key)
        return f"Write a toke program that uses {feature}."

    # --- OSS-INST — OSS-Instruct style --------------------------------------
    if cat == "OSS-INST":
        hint = _imports_hint(imports)
        return f"Write a realistic toke program that solves a practical programming task.{hint}"

    # --- MED-CMPLX / general fallback ---------------------------------------
    if cat == "MED-CMPLX":
        return "Write a medium-complexity toke program with multiple functions and control flow."

    return "Write a toke program."


def build_instruction(entry: dict) -> str:
    """Legacy API — retained so other tools that import this module keep working."""
    return build_user_prompt(entry) or "Write a toke program."


# ---------------------------------------------------------------------------
# Story 10.9.4: tk_source surface guard
#
# Phase 2 uses a 59-char alphabet with no uppercase letters. We enforce a
# hard surface rule: no uppercase letters outside string literals, no `==`,
# `!=`, or square-bracket indexing.
# ---------------------------------------------------------------------------


def _strip_strings(src: str) -> str:
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


import re as _re
_SURFACE_UPPER_RE = _re.compile(r"[A-Z]")


# ---------------------------------------------------------------------------
# Stdlib quota helpers (Story 10.4.5)
# ---------------------------------------------------------------------------

_STDLIB_GRAPH_PATH = ROOT / "data" / "stdlib_graph.json"
_STDLIB_FN_CACHE: list[tuple[str, _re.Pattern]] | None = None


def _load_stdlib_functions() -> list[tuple[str, _re.Pattern]]:
    """Return [(qualified_name, compiled_pattern)] for every stdlib function."""
    global _STDLIB_FN_CACHE
    if _STDLIB_FN_CACHE is not None:
        return _STDLIB_FN_CACHE
    if not _STDLIB_GRAPH_PATH.exists():
        _STDLIB_FN_CACHE = []
        return _STDLIB_FN_CACHE
    with open(_STDLIB_GRAPH_PATH) as f:
        graph = json.load(f)
    fns: list[tuple[str, _re.Pattern]] = []
    for mod, md in (graph.get("modules") or {}).items():
        mod_short = mod.split(".", 1)[1] if "." in mod else mod
        for fn in md.get("functions", []):
            qn = fn.get("qualified_name") or f"{mod}.{fn['name']}"
            pat = _re.compile(rf"\b{_re.escape(mod_short)}\.{_re.escape(fn['name'])}\b")
            fns.append((qn, pat))
    _STDLIB_FN_CACHE = fns
    return fns


def _stdlib_fns_in(src: str) -> set[str]:
    """Return the set of stdlib qualified names referenced in `src`."""
    found: set[str] = set()
    if not src:
        return found
    for qn, pat in _load_stdlib_functions():
        if pat.search(src):
            found.add(qn)
    return found


def _apply_stdlib_quota(
    *,
    sampled: list[dict],
    pool_by_source: dict[str, list[dict]],
    floor: int,
) -> tuple[list[dict], dict]:
    """Top up `sampled` so each stdlib function hits `floor` occurrences.

    Pulls extra records from the full eligible pool across all source types.
    Records already in `sampled` are not duplicated. If a function is under
    floor in the entire pool, it is reported but not filled.
    """
    all_fn_names = [qn for qn, _ in _load_stdlib_functions()]
    if not all_fn_names:
        return sampled, {
            "initial_below": 0, "added": 0, "final_below": 0,
            "corpus_gap_fns": [],
        }

    # Count current coverage in `sampled`
    current_counts: dict[str, int] = {qn: 0 for qn in all_fn_names}
    sampled_ids = set()
    for entry in sampled:
        rid = entry.get("id")
        if rid:
            sampled_ids.add(rid)
        for qn in _stdlib_fns_in(entry.get("tk_source", "")):
            current_counts[qn] = current_counts.get(qn, 0) + 1

    # Index the full eligible pool by stdlib function usage
    eligible_all: list[dict] = []
    for pool in pool_by_source.values():
        eligible_all.extend(pool)

    pool_by_fn: dict[str, list[dict]] = {qn: [] for qn in all_fn_names}
    for entry in eligible_all:
        for qn in _stdlib_fns_in(entry.get("tk_source", "")):
            pool_by_fn[qn].append(entry)

    initial_below = sum(1 for c in current_counts.values() if c < floor)
    added = 0
    corpus_gap: list[str] = []
    extras: list[dict] = []

    # Process under-represented functions in order of most scarce first
    for qn in sorted(all_fn_names, key=lambda q: current_counts[q]):
        cur = current_counts[qn]
        if cur >= floor:
            continue
        need = floor - cur
        candidates = [e for e in pool_by_fn[qn]
                      if e.get("id") and e["id"] not in sampled_ids]
        if len(pool_by_fn[qn]) < floor:
            corpus_gap.append(qn)
        if not candidates:
            continue
        pick = random.sample(candidates, min(need, len(candidates)))
        for entry in pick:
            extras.append(entry)
            sampled_ids.add(entry["id"])
            # Credit every stdlib fn this entry references, not just `qn`
            for q2 in _stdlib_fns_in(entry.get("tk_source", "")):
                current_counts[q2] = current_counts.get(q2, 0) + 1
            added += 1

    sampled = sampled + extras
    final_below = sum(1 for c in current_counts.values() if c < floor)
    return sampled, {
        "initial_below": initial_below,
        "added": added,
        "final_below": final_below,
        "corpus_gap_fns": corpus_gap,
    }


def _apply_stratified_resampling(
    *,
    sampled: list[dict],
    complexity: dict[str, dict],
    pool_by_source: dict[str, list[dict]],
    simple_floor: float,
    application_cap: float,
    bifi_cap: float,
    rng: random.Random,
) -> tuple[list[dict], dict]:
    """Resample to hit complexity tier targets and cap BIFI repair examples.

    1. Cap BIFI-* categories at `bifi_cap` fraction of the sample.
    2. Cap application tier at `application_cap` (drop B-CMP and COMPOSE-NEW first).
    3. Floor simple tier at `simple_floor` by pulling from the eligible pool.

    Returns (resampled_list, report_dict).
    """
    total = len(sampled)
    sampled_ids = {e["id"] for e in sampled if e.get("id")}

    def _tier(entry: dict) -> str:
        cdata = complexity.get(entry.get("id", ""))
        return cdata.get("tier", "unmatched") if cdata else "unmatched"

    def _is_bifi(entry: dict) -> bool:
        return (entry.get("category") or "").startswith("BIFI-")

    # --- Step 1: Cap BIFI ------------------------------------------------
    bifi_max = int(total * bifi_cap)
    bifi_entries = [e for e in sampled if _is_bifi(e)]
    non_bifi = [e for e in sampled if not _is_bifi(e)]
    bifi_dropped = 0
    if len(bifi_entries) > bifi_max:
        rng.shuffle(bifi_entries)
        bifi_dropped = len(bifi_entries) - bifi_max
        bifi_entries = bifi_entries[:bifi_max]
    result = non_bifi + bifi_entries

    # --- Step 2: Cap application tier ------------------------------------
    app_max = int(len(result) * application_cap)
    app_entries = [e for e in result if _tier(e) == "application"]
    non_app = [e for e in result if _tier(e) != "application"]
    app_dropped = 0
    if len(app_entries) > app_max:
        # Prefer to drop B-CMP and COMPOSE-NEW first (bulk contributors)
        deprioritised = [e for e in app_entries
                         if e.get("category") in ("B-CMP", "COMPOSE-NEW")]
        kept_app = [e for e in app_entries
                    if e.get("category") not in ("B-CMP", "COMPOSE-NEW")]
        rng.shuffle(deprioritised)
        need_to_drop = len(app_entries) - app_max
        if need_to_drop <= len(deprioritised):
            deprioritised = deprioritised[need_to_drop:]
        else:
            extra_drop = need_to_drop - len(deprioritised)
            deprioritised = []
            rng.shuffle(kept_app)
            kept_app = kept_app[extra_drop:]
        app_entries = kept_app + deprioritised
        app_dropped = len([e for e in result if _tier(e) == "application"]) - len(app_entries)
    result = non_app + app_entries

    # --- Step 3: Floor simple tier ---------------------------------------
    # Use the ORIGINAL total (before any drops) as the denominator so the
    # floor target isn't deflated by the BIFI and application caps.
    simple_min = int(total * simple_floor)
    current_simple = [e for e in result if _tier(e) == "simple"]
    simple_added = 0
    if len(current_simple) < simple_min:
        need = simple_min - len(current_simple)
        current_ids = {e["id"] for e in result if e.get("id")}
        # Build pool of simple-tier eligible records not already sampled.
        # Exclude BIFI-* entirely (we just capped them — don't add back).
        # Note: MUT-* are not in pool_by_source (excluded by Story 10.9.2).
        simple_pool_gen: list[dict] = []
        for entries in pool_by_source.values():
            for e in entries:
                if e.get("id") in current_ids:
                    continue
                if _tier(e) != "simple":
                    continue
                if _is_bifi(e):
                    continue
                simple_pool_gen.append(e)
        # Draw from pool of unique simple-tier records first, then oversample
        # existing simple records to fill the remaining gap. MUT-* records are
        # excluded from training (Story 10.9.2: no meaningful prompts), so the
        # pool is generation + ERR-TRIPLE only.
        simple_pool: list[dict] = simple_pool_gen
        drawn: list[dict] = []
        if need <= len(simple_pool):
            drawn = rng.sample(simple_pool, need)
        else:
            # Take all unique simple records from pool
            drawn = list(simple_pool)
            remaining = need - len(drawn)
            # Oversample from existing simple records already in result
            # (standard repetition-with-shuffle for curriculum fine-tuning).
            # Exclude BIFI to avoid undoing the BIFI cap.
            non_bifi_simple = [e for e in current_simple if not _is_bifi(e)]
            if remaining > 0 and non_bifi_simple:
                oversample = [dict(e) for e in rng.choices(non_bifi_simple, k=remaining)]
                drawn.extend(oversample)
        result.extend(drawn)
        simple_added = len(drawn)

    simple_unique = len(simple_pool_gen) if len(current_simple) < simple_min else 0
    simple_oversampled = simple_added - simple_unique if simple_added > simple_unique else 0

    rng.shuffle(result)
    report = {
        "bifi_before": len(bifi_entries) + bifi_dropped,
        "bifi_after": len([e for e in result if _is_bifi(e)]),
        "bifi_dropped": bifi_dropped,
        "app_dropped": app_dropped,
        "simple_added": simple_added,
        "simple_unique_added": simple_unique,
        "simple_oversampled": simple_oversampled,
        "total_before": total,
        "total_after": len(result),
    }
    return result, report


def tk_source_surface_ok(src: str) -> bool:
    if not src:
        return False
    stripped = _strip_strings(src)
    if _SURFACE_UPPER_RE.search(stripped):
        return False
    if "==" in stripped or "!=" in stripped:
        return False
    if "[" in stripped or "]" in stripped:
        return False
    return True


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare weighted training data")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--quality-scores", type=Path, default=DEFAULT_QUALITY_SCORES)
    parser.add_argument("--complexity", type=Path, default=DEFAULT_COMPLEXITY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    cfg = load_config(args.config)
    source_types = cfg["source_types"]
    quality_gate = cfg["quality_gate"]

    # ------------------------------------------------------------------
    # 1. Load quality scores and complexity tiers
    # ------------------------------------------------------------------
    print("Loading quality scores...")
    quality_scores = load_jsonl_index(args.quality_scores)
    print(f"  {len(quality_scores)} quality score entries loaded")

    print("Loading complexity tiers...")
    complexity = load_jsonl_index(args.complexity)
    print(f"  {len(complexity)} complexity entries loaded")

    # ------------------------------------------------------------------
    # 2. Walk deduplicated corpus and collect entries
    # ------------------------------------------------------------------
    print(f"\nLoading corpus from {args.corpus_dir}...")
    entries_by_source: dict[str, list[dict]] = collections.defaultdict(list)
    skipped = collections.Counter()
    total_loaded = 0

    for cat_dir in sorted(args.corpus_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        # Story 10.9.2: drop mutation variants entirely — their prompts are meaningless.
        if category.startswith("MUT-"):
            skipped["mutation_dropped"] += 1
            continue
        stype = resolve_source_type(category, source_types)
        if stype is None:
            skipped["unmapped_category"] += 1
            print(f"  WARNING: category '{category}' not mapped to any source type — skipping")
            continue

        for json_file in cat_dir.iterdir():
            if not json_file.suffix == ".json":
                continue
            try:
                with open(json_file) as f:
                    entry = json.load(f)
            except (json.JSONDecodeError, OSError):
                skipped["bad_json"] += 1
                continue

            entry_id = entry.get("id", "")
            entry["category"] = category  # ensure category is set

            # --- Quality gate checks ---

            # Check quality score
            qs = quality_scores.get(entry_id)
            if qs:
                composite = qs.get("composite_score", 0)
                is_fuzzed = qs.get("is_fuzzed", False)

                if composite < quality_gate["min_composite_score"]:
                    skipped["below_min_score"] += 1
                    continue

                if quality_gate["exclude_fuzzed"] and is_fuzzed:
                    skipped["fuzzed"] += 1
                    continue

            # Check compile pass requirement (legacy validation field)
            if quality_gate.get("require_compile_pass"):
                validation = entry.get("validation", {})
                exit_code = validation.get("compiler_exit_code")
                if exit_code is not None and exit_code != 0:
                    skipped["compile_fail"] += 1
                    continue

            # --- Story 10.8.8: v2 enriched quality gate ---

            # Syntax conformance (phase2_syntax_audit output)
            if quality_gate.get("require_syntax_conformant"):
                sa = entry.get("syntax_audit") or {}
                if not sa.get("phase2_conformant"):
                    skipped["not_phase2_conformant"] += 1
                    continue

            # Compile pass on tk_source via tkc --check (compile_check_corpus output)
            if quality_gate.get("require_v2_compile_pass"):
                cc = entry.get("compile_check") or {}
                if not cc.get("passed"):
                    skipped["v2_compile_fail"] += 1
                    continue

            # Runtime pass — permissive: allowed if the record ran, OR the
            # source type is in runtime_skip_sources (e.g. mutations where
            # output is semantically irrelevant), OR the gate is disabled.
            if quality_gate.get("require_runtime_pass"):
                skip_sources = set(quality_gate.get("runtime_skip_sources") or [])
                if stype not in skip_sources:
                    rc = entry.get("runtime_check") or {}
                    if not rc.get("ran"):
                        skipped["runtime_not_run"] += 1
                        continue
                    if not rc.get("passed"):
                        skipped["runtime_fail"] += 1
                        continue

            # Check tk_source is present and non-empty
            tk_source = entry.get("tk_source", "").strip()
            if not tk_source:
                skipped["empty_source"] += 1
                continue

            # Story 10.9.4: hard surface guard — uppercase / == / != / []
            if not tk_source_surface_ok(tk_source):
                skipped["surface_nonconformant"] += 1
                continue

            total_loaded += 1
            entries_by_source[stype].append(entry)

    print(f"\n  Total entries loaded (post quality gate): {total_loaded}")
    for stype in sorted(entries_by_source):
        print(f"    {stype}: {len(entries_by_source[stype])}")
    if skipped:
        print(f"  Skipped: {dict(skipped)}")

    # ------------------------------------------------------------------
    # 3. Apply max_mutations_per_seed limit (MUT-* only)
    #    Note: BIFI-* and ERR-TRIPLE-* live in the "mutation" source type
    #    but each record is an independent repair example, so the per-seed
    #    cap must not apply to them. Since Story 10.9.2 drops MUT-* from
    #    training entirely, this block is effectively a no-op today but
    #    kept correct for future re-introduction of mutations.
    # ------------------------------------------------------------------
    max_mut = quality_gate.get("max_mutations_per_seed", 2)
    if max_mut and "mutation" in entries_by_source:
        seed_counts: dict[str, int] = collections.defaultdict(int)
        filtered = []
        # Shuffle first so we get a random subset of mutations per seed
        random.shuffle(entries_by_source["mutation"])
        for entry in entries_by_source["mutation"]:
            cat = entry.get("category", "")
            if not cat.startswith("MUT-"):
                # BIFI / ERR-TRIPLE — keep all repair examples
                filtered.append(entry)
                continue
            seed = entry.get("task_id", entry.get("id", ""))
            seed_counts[seed] += 1
            if seed_counts[seed] <= max_mut:
                filtered.append(entry)
        before = len(entries_by_source["mutation"])
        entries_by_source["mutation"] = filtered
        after = len(filtered)
        print(f"\n  Mutations after max_mutations_per_seed={max_mut}: {before} -> {after}")

    # ------------------------------------------------------------------
    # 4. Source-weighted sampling
    # ------------------------------------------------------------------
    print("\nApplying source weighting...")

    # Strategy: aim for a target total in the 18-25K range recommended by
    # research.  Redistribute weight from unavailable sources (transpiled=0)
    # to available ones proportionally.  Allow up to 8x oversampling for
    # scarce sources (hand_written) since repetition with shuffling is
    # standard practice for curriculum fine-tuning.
    weights = {st: source_types[st]["weight"] for st in source_types}
    available = {st: len(entries_by_source.get(st, [])) for st in source_types}

    # Redistribute weight from zero-available sources
    active_weights = {st: w for st, w in weights.items() if available[st] > 0 and w > 0}
    dead_weight = sum(w for st, w in weights.items() if available[st] == 0 or w == 0)
    if active_weights and dead_weight > 0:
        redistribution = dead_weight / len(active_weights)
        active_weights = {st: w + redistribution for st, w in active_weights.items()}
        # Normalise
        total_w = sum(active_weights.values())
        active_weights = {st: w / total_w for st, w in active_weights.items()}
        print(f"  Redistributed {dead_weight:.2f} weight from empty sources")
        for st in sorted(active_weights):
            print(f"    {st}: adjusted_weight={active_weights[st]:.3f}")

    if not active_weights:
        print("ERROR: No entries available after filtering. Exiting.")
        sys.exit(1)

    # Target total: aim for ~20K (midpoint of 18-25K research range).
    # Per Phase 2 Corpus.md (2026-04-10): cap hand_written oversampling at 1x
    # to avoid DOC-EXP repetition risk. Shortfall redistributes to llm_generated.
    target_total = 20000
    max_oversample_per_source = {
        "hand_written": 1.0,    # Hard cap — no repetition of DOC-EXP
        "llm_generated": 2.0,   # Allow mild oversampling
        "transpiled": 2.0,
        "mutation": 1.0,        # Mutations already a sampling of a larger pool
    }
    default_max_oversample = 2.0

    targets: dict[str, int] = {}
    for st in source_types:
        if st not in active_weights:
            targets[st] = 0
            continue
        target = int(target_total * active_weights[st])
        cap = max_oversample_per_source.get(st, default_max_oversample)
        targets[st] = min(target, int(available[st] * cap))

    # Redistribute shortfall from capped sources to uncapped sources
    shortfall = sum(int(target_total * active_weights[st]) - targets[st]
                    for st in active_weights)
    if shortfall > 0:
        # Prefer llm_generated (largest, diverse pool)
        redistributable = [st for st in active_weights
                           if available[st] > targets[st]
                           and st in ("llm_generated", "transpiled")]
        if redistributable:
            extra_per = shortfall // len(redistributable)
            for st in redistributable:
                cap = max_oversample_per_source.get(st, default_max_oversample)
                room = int(available[st] * cap) - targets[st]
                targets[st] += min(extra_per, room)
            print(f"  Redistributed {shortfall} shortfall to {redistributable}")

    print(f"  Target corpus size: {sum(targets.values())}")
    for st in sorted(targets):
        print(f"    {st}: target={targets[st]} (available={available[st]}, weight={weights[st]})")

    # Sample (with replacement for oversampling, without for undersampling)
    sampled: list[dict] = []
    for st, target in targets.items():
        pool = entries_by_source.get(st, [])
        if not pool:
            continue
        if target <= len(pool):
            sampled.extend(random.sample(pool, target))
        else:
            # Oversample: take all, then sample additional with replacement
            sampled.extend(pool)
            extra = target - len(pool)
            sampled.extend(random.choices(pool, k=extra))

    random.shuffle(sampled)
    print(f"  Sampled total: {len(sampled)}")

    # ------------------------------------------------------------------
    # 4b. Stdlib-frequency quota top-up (Story 10.4.5)
    #     Ensure each stdlib function has at least `stdlib_floor` occurrences
    #     in the final training sample. Functions that are below floor in the
    #     entire eligible pool are reported but cannot be fixed here (they
    #     need more corpus data).
    # ------------------------------------------------------------------
    stdlib_floor = int(cfg.get("training", {}).get("stdlib_floor_per_fn", 20))
    if stdlib_floor > 0:
        sampled, stdlib_report = _apply_stdlib_quota(
            sampled=sampled,
            pool_by_source=entries_by_source,
            floor=stdlib_floor,
        )
        print(f"\n  Stdlib quota top-up (floor={stdlib_floor}):")
        print(f"    initial below-floor:   {stdlib_report['initial_below']}/74")
        print(f"    records added:         {stdlib_report['added']}")
        print(f"    final below-floor:     {stdlib_report['final_below']}/74")
        if stdlib_report["corpus_gap_fns"]:
            print(f"    corpus gap (unfillable): "
                  f"{len(stdlib_report['corpus_gap_fns'])} fns — "
                  f"{', '.join(stdlib_report['corpus_gap_fns'][:5])}"
                  + ("…" if len(stdlib_report['corpus_gap_fns']) > 5 else ""))
        random.shuffle(sampled)
        print(f"  Sampled total (post-quota): {len(sampled)}")

    # ------------------------------------------------------------------
    # 4c. Stratified complexity resampling + BIFI cap
    #     Floor simple tier, cap application tier, cap BIFI repair examples.
    # ------------------------------------------------------------------
    strat_cfg = cfg.get("stratified_resampling", {})
    simple_floor = strat_cfg.get("simple_floor", 0)
    app_cap = strat_cfg.get("application_cap", 1.0)
    bifi_cap_pct = strat_cfg.get("bifi_cap", 1.0)
    if simple_floor > 0 or app_cap < 1.0 or bifi_cap_pct < 1.0:
        sampled, strat_report = _apply_stratified_resampling(
            sampled=sampled,
            complexity=complexity,
            pool_by_source=entries_by_source,
            simple_floor=simple_floor,
            application_cap=app_cap,
            bifi_cap=bifi_cap_pct,
            rng=random.Random(args.seed + 1),
        )
        print(f"\n  Stratified resampling:")
        print(f"    BIFI:  {strat_report['bifi_before']} → {strat_report['bifi_after']} (dropped {strat_report['bifi_dropped']})")
        print(f"    Application tier: dropped {strat_report['app_dropped']}")
        print(f"    Simple tier: added {strat_report['simple_added']} "
              f"({strat_report['simple_unique_added']} unique, {strat_report['simple_oversampled']} oversampled)")
        print(f"    Total: {strat_report['total_before']} → {strat_report['total_after']}")

    # ------------------------------------------------------------------
    # 5. Split into train / eval
    # ------------------------------------------------------------------
    eval_ratio = cfg["training"]["eval_split"]
    eval_size = max(1, int(len(sampled) * eval_ratio))
    train_size = len(sampled) - eval_size

    eval_set = sampled[:eval_size]
    train_set = sampled[eval_size:]

    print(f"\n  Train: {len(train_set)}  |  Eval: {len(eval_set)}")

    # ------------------------------------------------------------------
    # 6. Convert to chat-format JSONL (Story 10.9.3)
    # ------------------------------------------------------------------
    system_prompt = get_system_prompt()

    def to_chat_format(entry: dict) -> dict | None:
        user = build_user_prompt(entry)
        if user is None:
            return None
        return {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user},
                {"role": "assistant", "content": entry["tk_source"]},
            ]
        }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    train_path = args.output_dir / "train.jsonl"
    eval_path = args.output_dir / "eval.jsonl"

    dropped = 0
    with open(train_path, "w") as f:
        for entry in train_set:
            row = to_chat_format(entry)
            if row is None:
                dropped += 1
                continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with open(eval_path, "w") as f:
        for entry in eval_set:
            row = to_chat_format(entry)
            if row is None:
                dropped += 1
                continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n  Written: {train_path}")
    print(f"  Written: {eval_path}")
    if dropped:
        print(f"  Dropped at chat-format stage: {dropped}")

    # ------------------------------------------------------------------
    # 7. Report per-source-type and per-complexity-tier counts
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("  Training Data Report")
    print("=" * 60)

    # Per-source-type in train set
    train_source_counts: dict[str, int] = collections.Counter()
    train_category_counts: dict[str, int] = collections.Counter()
    train_tier_counts: dict[str, int] = collections.Counter()

    for entry in train_set:
        cat = entry.get("category", "unknown")
        stype = resolve_source_type(cat, source_types) or "unknown"
        train_source_counts[stype] += 1
        train_category_counts[cat] += 1

        # Complexity tier
        cdata = complexity.get(entry.get("id", ""))
        tier = cdata.get("tier", "unmatched") if cdata else "unmatched"
        train_tier_counts[tier] += 1

    print("\n  Per source type (train):")
    for st in sorted(train_source_counts):
        pct = 100 * train_source_counts[st] / len(train_set) if train_set else 0
        print(f"    {st:20s}  {train_source_counts[st]:6d}  ({pct:5.1f}%)")

    print(f"\n  Per category (train, top 15):")
    for cat, count in collections.Counter(train_category_counts).most_common(15):
        print(f"    {cat:35s}  {count:6d}")

    print(f"\n  Per complexity tier (train):")
    for tier in ["simple", "medium", "complex", "application", "unmatched"]:
        count = train_tier_counts.get(tier, 0)
        pct = 100 * count / len(train_set) if train_set else 0
        print(f"    {tier:20s}  {count:6d}  ({pct:5.1f}%)")

    # Eval breakdown
    eval_source_counts: dict[str, int] = collections.Counter()
    for entry in eval_set:
        cat = entry.get("category", "unknown")
        stype = resolve_source_type(cat, source_types) or "unknown"
        eval_source_counts[stype] += 1

    print(f"\n  Per source type (eval):")
    for st in sorted(eval_source_counts):
        pct = 100 * eval_source_counts[st] / len(eval_set) if eval_set else 0
        print(f"    {st:20s}  {eval_source_counts[st]:6d}  ({pct:5.1f}%)")

    print(f"\n  Total train: {len(train_set)}")
    print(f"  Total eval:  {len(eval_set)}")
    print(f"  Grand total: {len(sampled)}")
    print()


if __name__ == "__main__":
    main()
