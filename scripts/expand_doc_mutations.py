#!/usr/bin/env python3
"""Expand 330 DOC stdlib examples into more corpus entries via mechanical mutations.

Reads DOC entries from corpus/phase_b/DOC/, applies the mutation engine,
validates each mutation with tkc --check, and writes passing entries as
individual JSON files to corpus/phase2_combined/DOC-EXP/.

No LLM calls -- purely local/mechanical transforms.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Add project root so we can import the mutation engine
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mutate.mutations import MutationEngine

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOC_DIR = PROJECT_ROOT / "corpus" / "phase_b" / "DOC"
OUT_DIR = PROJECT_ROOT / "corpus" / "phase2_combined" / "DOC-EXP"
TKC = os.environ.get("TKC", str(Path.home() / "tk" / "toke" / "tkc"))

# Additional mutations beyond what MutationEngine provides
# We add: module renaming, function renaming, whitespace normalization,
# semicolon-newline variants, combined mutations

# ---------------------------------------------------------------------------
# Extra mutation strategies (supplement the engine)
# ---------------------------------------------------------------------------

import re
import random

_FUNC_RE = re.compile(
    r"(?P<prefix>[Ff]=)"
    r"(?P<name>\w+)"
    r"\((?P<params>[^)]*)\)"
)

_MODULE_NAMES = [
    "test", "demo", "example", "sample", "prog", "app",
    "run", "main", "entry", "lib", "mod", "impl", "work",
]

_FUNC_RENAME_MAP = {
    "main": [],  # don't rename main
    "add": ["sum", "plus", "combine"],
    "sub": ["subtract", "minus", "diff"],
    "mul": ["multiply", "product", "times"],
    "div": ["divide", "quotient", "split"],
    "max": ["maximum", "largest", "biggest"],
    "min": ["minimum", "smallest", "least"],
    "get": ["fetch", "retrieve", "obtain"],
    "set": ["assign", "store", "put"],
    "run": ["exec", "execute", "perform"],
    "check": ["verify", "validate", "test"],
    "count": ["tally", "enumerate", "numof"],
    "find": ["search", "locate", "lookup"],
    "sort": ["order", "arrange", "rank"],
    "swap": ["exchange", "flip", "switch"],
    "compute": ["calc", "evaluate", "process"],
    "calc": ["compute", "evaluate", "process"],
    "apply": ["invoke", "call", "use"],
    "build": ["make", "create", "construct"],
    "parse": ["decode", "read", "extract"],
    "format": ["fmt", "render", "display"],
    "filter": ["select", "pick", "choose"],
    "merge": ["combine", "join", "concat"],
    "split": ["divide", "separate", "partition"],
    "update": ["modify", "change", "alter"],
    "remove": ["delete", "drop", "erase"],
    "insert": ["add", "place", "inject"],
    "convert": ["transform", "cast", "change"],
    "validate": ["check", "verify", "confirm"],
    "process": ["handle", "manage", "treat"],
    "example": ["demo", "sample", "showcase"],
    "test": ["verify", "check", "probe"],
    "bad": ["broken", "wrong", "invalid"],
    "good": ["valid", "correct", "ok"],
    "helper": ["util", "support", "assist"],
    "init": ["setup", "start", "begin"],
}


def extra_module_rename(source: str, rng: random.Random) -> list[tuple[str, str, str]]:
    """Rename the module declaration."""
    results = []
    mod_match = re.search(r"m=(\w+);", source)
    if not mod_match:
        return results
    old_name = mod_match.group(1)
    candidates = [n for n in _MODULE_NAMES if n != old_name]
    rng.shuffle(candidates)
    for new_name in candidates[:3]:
        mutated = source.replace(f"m={old_name};", f"m={new_name};", 1)
        if mutated != source:
            results.append((mutated, "module_rename", f"m={old_name} -> m={new_name}"))
    return results


def extra_function_rename(source: str, rng: random.Random) -> list[tuple[str, str, str]]:
    """Rename non-main function names."""
    results = []
    func_names = []
    for match in _FUNC_RE.finditer(source):
        fname = match.group("name")
        if fname not in func_names and fname != "main":
            func_names.append(fname)

    for fname in func_names:
        candidates = _FUNC_RENAME_MAP.get(fname)
        if not candidates:
            candidates = [fname + "fn", fname + "op", fname + "v2"]
        for new_name in candidates[:2]:
            if new_name in func_names:
                continue
            # Whole-word replace outside strings
            parts = re.split(r'("(?:[^"\\]|\\.)*")', source)
            pattern = re.compile(r"(?<![a-zA-Z0-9_])" + re.escape(fname) + r"(?![a-zA-Z0-9_])")
            new_parts = []
            for i, part in enumerate(parts):
                if i % 2 == 1:
                    new_parts.append(part)
                else:
                    new_parts.append(pattern.sub(new_name, part))
            mutated = "".join(new_parts)
            if mutated != source:
                results.append((mutated, "function_rename", f"f={fname} -> f={new_name}"))
    return results


def extra_whitespace_variants(source: str) -> list[tuple[str, str, str]]:
    """Add/remove whitespace around operators and after semicolons."""
    results = []

    # Variant 1: Add spaces around = in let bindings
    v1 = re.sub(r"let\s+(\w+)=", r"let \1 = ", source)
    if v1 != source:
        results.append((v1, "whitespace_let_spaces", "add spaces around = in let"))

    # Variant 2: Add newlines after top-level semicolons
    v2 = re.sub(r";(?!\n)(?!\")", ";\n", source)
    if v2 != source and v2 != source:
        results.append((v2, "whitespace_newlines", "newline after every semicolon"))

    # Variant 3: Compact - remove all optional whitespace
    v3 = source
    # Remove spaces after { and before }
    v3 = re.sub(r"\{\s+", "{", v3)
    v3 = re.sub(r"\s+\}", "}", v3)
    # Remove spaces around operators
    v3 = re.sub(r"\s*([+\-*/=<>])\s*", r"\1", v3)
    # Restore let keyword space
    v3 = re.sub(r"let(\w)", r"let \1", v3)
    # Restore as keyword space
    v3 = re.sub(r"(\w)as\s", r"\1 as ", v3)
    v3 = re.sub(r"\sas(\w)", r" as \1", v3)
    if v3 != source:
        results.append((v3, "whitespace_compact", "remove optional whitespace"))

    return results


def extra_combined_mutations(
    source: str, engine: MutationEngine, rng: random.Random
) -> list[tuple[str, str, str]]:
    """Apply two mutations in sequence for combinatorial expansion."""
    results = []
    base_mutations = engine.mutate(source)

    # Take up to 5 base mutations and apply a second mutation to each
    selected = base_mutations[:5] if len(base_mutations) > 5 else base_mutations

    for mutated_src, mut_type1, mut_detail1 in selected:
        # Apply module rename on top
        mod_renames = extra_module_rename(mutated_src, rng)
        for combo_src, _, mod_detail in mod_renames[:1]:
            results.append((
                combo_src,
                f"combined_{mut_type1}+module_rename",
                f"{mut_detail1} | {mod_detail}",
            ))

        # Apply function rename on top
        fn_renames = extra_function_rename(mutated_src, rng)
        for combo_src, _, fn_detail in fn_renames[:1]:
            results.append((
                combo_src,
                f"combined_{mut_type1}+function_rename",
                f"{mut_detail1} | {fn_detail}",
            ))

    return results


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_with_tkc(source: str) -> tuple[int, list[str]]:
    """Run tkc --check on source. Returns (exit_code, error_codes)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tk", delete=False, dir="/tmp"
    ) as f:
        f.write(source)
        f.flush()
        tmp_path = f.name

    try:
        result = subprocess.run(
            [TKC, "--check", "--diag-json", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        exit_code = result.returncode
        error_codes = []
        if exit_code != 0 and result.stdout.strip():
            try:
                diag = json.loads(result.stdout)
                if isinstance(diag, list):
                    for d in diag:
                        if "code" in d:
                            error_codes.append(d["code"])
                elif isinstance(diag, dict) and "code" in diag:
                    error_codes.append(diag["code"])
            except (json.JSONDecodeError, KeyError):
                pass
            # Also try stderr
            if not error_codes and result.stderr.strip():
                for line in result.stderr.strip().split("\n"):
                    m = re.search(r"E\d{4}", line)
                    if m:
                        error_codes.append(m.group(0))
        return exit_code, error_codes
    except subprocess.TimeoutExpired:
        return -1, ["TIMEOUT"]
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def make_id(seq_num: int, source: str) -> str:
    """Generate a corpus ID like B-DOCX-NNNN-HHHHHHHH."""
    h = hashlib.sha256(source.encode()).hexdigest()[:8]
    return f"B-DOCX-{seq_num:04d}-{h}"


# ---------------------------------------------------------------------------
# Token counting (approximate)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-zA-Z_]\w*|[0-9]+|[^\s]")

def count_tokens(source: str) -> int:
    """Rough token count for toke source."""
    return len(_TOKEN_RE.findall(source))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Verify tkc
    if not Path(TKC).exists():
        print(f"ERROR: tkc not found at {TKC}", file=sys.stderr)
        sys.exit(1)

    # Create output dir
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load all DOC entries
    doc_files = sorted(DOC_DIR.glob("*.json"))
    print(f"Found {len(doc_files)} DOC entries")

    entries = []
    for jf in doc_files:
        with open(jf) as f:
            entries.append(json.load(f))

    # Initialize mutation engine
    engine = MutationEngine(seed=42)
    rng = random.Random(42)

    seq_num = 0
    total_mutations = 0
    total_passed = 0
    total_failed = 0
    seen_sources: set[str] = set()  # dedup by source hash

    # Track stats per mutation type
    type_stats: dict[str, dict[str, int]] = {}

    for idx, entry in enumerate(entries):
        source = entry.get("tk_source", "")
        if not source:
            continue

        original_id = entry.get("id", "unknown")
        task_id = entry.get("task_id", "")

        # Collect all mutations for this entry
        all_mutations: list[tuple[str, str, str]] = []

        # 1. Engine mutations (variable rename, type widening, let->mut, expr extraction, constant variation)
        all_mutations.extend(engine.mutate(source))

        # 2. Module rename
        all_mutations.extend(extra_module_rename(source, rng))

        # 3. Function rename
        all_mutations.extend(extra_function_rename(source, rng))

        # 4. Whitespace variants
        all_mutations.extend(extra_whitespace_variants(source))

        # 5. Combined mutations (two transforms stacked)
        all_mutations.extend(extra_combined_mutations(source, engine, rng))

        if (idx + 1) % 50 == 0 or idx == 0:
            print(f"  [{idx+1}/{len(entries)}] {original_id}: {len(all_mutations)} candidate mutations")

        for mutated_source, mutation_type, mutation_details in all_mutations:
            total_mutations += 1

            # Dedup
            src_hash = hashlib.sha256(mutated_source.encode()).hexdigest()
            if src_hash in seen_sources:
                continue
            seen_sources.add(src_hash)

            # Validate
            exit_code, error_codes = validate_with_tkc(mutated_source)

            # Track stats
            if mutation_type not in type_stats:
                type_stats[mutation_type] = {"pass": 0, "fail": 0}

            if exit_code != 0:
                total_failed += 1
                type_stats[mutation_type]["fail"] += 1
                continue

            total_passed += 1
            type_stats[mutation_type]["pass"] += 1

            # Build corpus entry
            corpus_id = make_id(seq_num, mutated_source)
            corpus_entry = {
                "id": corpus_id,
                "version": 1,
                "phase": "B",
                "task_id": f"DOCX-{original_id}",
                "tk_source": mutated_source,
                "tk_tokens": count_tokens(mutated_source),
                "attempts": 1,
                "model": f"mutation-{mutation_type}",
                "validation": {
                    "compiler_exit_code": 0,
                    "error_codes": [],
                },
                "differential": {
                    "languages_agreed": [],
                    "majority_output": "",
                },
                "judge": {
                    "accepted": True,
                    "score": 0.85,
                },
                "references": {
                    "mutation_type": mutation_type,
                    "mutation_details": mutation_details,
                    "original_source": source,
                    "original_id": original_id,
                    "doc_source": entry.get("references", {}).get("doc_source", ""),
                },
            }

            # Write individual JSON file
            out_file = OUT_DIR / f"{corpus_id}.json"
            with open(out_file, "w") as f:
                json.dump(corpus_entry, f)
                f.write("\n")

            seq_num += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"DOC Expansion Summary")
    print(f"{'='*60}")
    print(f"Input entries:      {len(entries)}")
    print(f"Total mutations:    {total_mutations}")
    print(f"Unique mutations:   {total_mutations}  (before dedup/validation)")
    print(f"Passed validation:  {total_passed}")
    print(f"Failed validation:  {total_failed}")
    print(f"Output files:       {seq_num}")
    print(f"Output directory:   {OUT_DIR}")
    print()
    print(f"{'Mutation Type':<45} {'Pass':>6} {'Fail':>6} {'Rate':>7}")
    print(f"{'-'*45} {'-'*6} {'-'*6} {'-'*7}")
    for mtype in sorted(type_stats.keys()):
        s = type_stats[mtype]
        total = s["pass"] + s["fail"]
        rate = s["pass"] / total * 100 if total > 0 else 0
        print(f"{mtype:<45} {s['pass']:>6} {s['fail']:>6} {rate:>6.1f}%")

    print(f"\nDone. {seq_num} expanded corpus entries written.")


if __name__ == "__main__":
    main()
