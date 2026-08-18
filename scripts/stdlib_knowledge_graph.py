#!/usr/bin/env python3
"""
Story 10.3.1 — Build a stdlib API knowledge graph from toke's stdlib documentation.

Parses stdlib-signatures.md, scans the entire corpus, and produces:
  - docs/stdlib_coverage.md   (human-readable gap analysis)
  - data/stdlib_graph.json    (structured function inventory for downstream scripts)
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
SIGNATURES_FILE = Path("/Users/matthew.watt/tk/toke-spec/spec/stdlib-signatures.md")
CORPUS_DIRS = [
    REPO / "corpus" / "phase2_combined",
]
# Add deduplicated if it exists
_dedup = REPO / "corpus" / "phase2_deduplicated"
if _dedup.exists():
    CORPUS_DIRS.append(_dedup)

OUTPUT_MD = REPO / "docs" / "stdlib_coverage.md"
OUTPUT_JSON = REPO / "data" / "stdlib_graph.json"
MIN_EXAMPLES = 20


# ── 1. Parse stdlib-signatures.md ─────────────────────────────────────────────
def parse_signatures(path: Path) -> dict:
    """
    Returns: {
        "std.str": [
            {"name": "len", "params": [{"name": "s", "type": "$str"}], "return_type": "i64"},
            ...
        ], ...
    }
    """
    text = path.read_text()
    modules = {}
    current_module = None
    in_code_block = False

    for line in text.splitlines():
        # Detect module heading: ### std.xyz
        m = re.match(r"^###\s+(std\.\w+)", line)
        if m:
            current_module = m.group(1)
            modules[current_module] = []
            in_code_block = False
            continue

        if line.strip() == "```" and current_module is not None:
            in_code_block = not in_code_block
            continue

        if in_code_block and current_module and line.strip().startswith("f="):
            fn = parse_function_sig(line.strip())
            if fn:
                modules[current_module].append(fn)

    return modules


def parse_function_sig(sig: str) -> dict | None:
    """Parse a single function signature like f=len(s:$str):i64"""
    m = re.match(r"f=(\w+)\(([^)]*)\):(\S+)", sig)
    if not m:
        return None
    name = m.group(1)
    raw_params = m.group(2)
    return_type = m.group(3)

    params = []
    if raw_params.strip():
        for p in raw_params.split(";"):
            p = p.strip()
            if ":" in p:
                pname, ptype = p.split(":", 1)
                params.append({"name": pname.strip(), "type": ptype.strip()})
            else:
                params.append({"name": p, "type": "unknown"})

    return {"name": name, "params": params, "return_type": return_type}


# ── 2. Scan corpus for stdlib usage ───────────────────────────────────────────
def build_search_patterns(modules: dict) -> dict:
    """
    Build regex patterns for each module.function.
    We look for two patterns per function:
      1. Import alias usage: alias.funcname(   e.g. str.split(
      2. Full qualified usage: std.mod.funcname( e.g. std.str.split(
    Also track which aliases map to which modules via import lines.
    """
    # We'll search per-file: first find imports to learn aliases, then count calls
    return modules  # We do the matching inline for accuracy


def scan_corpus(modules: dict) -> dict:
    """
    Returns: {"std.str.len": count, "std.str.concat": count, ...}
    """
    # Build the set of (module_short, func_name) pairs
    # module_short is the part after "std." e.g. "str", "json"
    func_counts = {}
    for mod, funcs in modules.items():
        for fn in funcs:
            key = f"{mod}.{fn['name']}"
            func_counts[key] = 0

    total_files = 0
    files_with_stdlib = 0

    for corpus_dir in CORPUS_DIRS:
        for subdir in sorted(corpus_dir.iterdir()):
            if not subdir.is_dir():
                continue
            for fpath in subdir.iterdir():
                if not fpath.suffix == ".json":
                    continue
                total_files += 1
                try:
                    data = json.loads(fpath.read_text())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                source = data.get("tk_source", "")
                if not source:
                    continue

                # Find import aliases: i=alias:std.mod;
                alias_map = {}  # alias -> module e.g. {"str": "std.str"}
                for im in re.finditer(r"i=(\w+):(std\.\w+)", source):
                    alias_map[im.group(1)] = im.group(2)

                if not alias_map:
                    continue

                files_with_stdlib += 1

                # For each alias, find calls: alias.funcname(
                for alias, mod in alias_map.items():
                    if mod not in modules:
                        continue
                    for fn in modules[mod]:
                        fname = fn["name"]
                        # Pattern: alias.funcname( or alias.funcname;  or alias.funcname)
                        # Most reliable: alias.funcname(
                        pattern = re.escape(alias) + r"\." + re.escape(fname) + r"\b"
                        matches = re.findall(pattern, source)
                        key = f"{mod}.{fname}"
                        if key in func_counts:
                            func_counts[key] += len(matches)

    return func_counts, total_files, files_with_stdlib


# ── 3. Generate outputs ───────────────────────────────────────────────────────
def generate_markdown(modules: dict, counts: dict, total_files: int, files_with_stdlib: int) -> str:
    lines = []
    lines.append("# Stdlib Coverage Analysis")
    lines.append("")
    lines.append(f"Generated by `scripts/stdlib_knowledge_graph.py` — Story 10.3.1")
    lines.append("")
    lines.append("## Corpus Summary")
    lines.append("")
    lines.append(f"- **Total corpus files scanned:** {total_files:,}")
    lines.append(f"- **Files containing stdlib imports:** {files_with_stdlib:,} ({files_with_stdlib*100/max(total_files,1):.1f}%)")
    lines.append(f"- **Minimum examples per function target:** {MIN_EXAMPLES}")
    lines.append("")

    total_funcs = sum(len(fns) for fns in modules.values())
    zero_funcs = sum(1 for c in counts.values() if c == 0)
    below_funcs = sum(1 for c in counts.values() if 0 < c < MIN_EXAMPLES)
    adequate_funcs = sum(1 for c in counts.values() if c >= MIN_EXAMPLES)
    total_gap = sum(max(0, MIN_EXAMPLES - c) for c in counts.values())

    lines.append("## High-Level Summary")
    lines.append("")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total stdlib functions | {total_funcs} |")
    lines.append(f"| Functions with 0 occurrences | {zero_funcs} |")
    lines.append(f"| Functions with 1-{MIN_EXAMPLES-1} occurrences (below minimum) | {below_funcs} |")
    lines.append(f"| Functions with >= {MIN_EXAMPLES} occurrences (adequate) | {adequate_funcs} |")
    lines.append(f"| Total new examples needed to close gap | {total_gap} |")
    lines.append("")

    # Per-module breakdown
    lines.append("## Per-Module Breakdown")
    lines.append("")

    for mod in sorted(modules.keys()):
        funcs = modules[mod]
        lines.append(f"### {mod}")
        lines.append("")
        lines.append(f"| Function | Params | Return | Occurrences | Status | Gap |")
        lines.append(f"|----------|--------|--------|-------------|--------|-----|")

        for fn in funcs:
            key = f"{mod}.{fn['name']}"
            count = counts.get(key, 0)
            gap = max(0, MIN_EXAMPLES - count)
            if count == 0:
                status = "ZERO"
            elif count < MIN_EXAMPLES:
                status = "LOW"
            else:
                status = "OK"
            params_str = ", ".join(f"{p['name']}:{p['type']}" for p in fn["params"])
            lines.append(f"| `{fn['name']}` | `{params_str}` | `{fn['return_type']}` | {count} | {status} | {gap} |")

        mod_total = sum(counts.get(f"{mod}.{fn['name']}", 0) for fn in funcs)
        mod_gap = sum(max(0, MIN_EXAMPLES - counts.get(f"{mod}.{fn['name']}", 0)) for fn in funcs)
        lines.append(f"| **Total** | | | **{mod_total}** | | **{mod_gap}** |")
        lines.append("")

    # Zero-occurrence list
    lines.append("## Functions with Zero Occurrences")
    lines.append("")
    zero_list = sorted(k for k, v in counts.items() if v == 0)
    if zero_list:
        for k in zero_list:
            lines.append(f"- `{k}`")
    else:
        lines.append("All functions have at least one occurrence.")
    lines.append("")

    # Below-minimum list
    lines.append("## Functions Below Minimum (1-19 occurrences)")
    lines.append("")
    below_list = sorted(((k, v) for k, v in counts.items() if 0 < v < MIN_EXAMPLES), key=lambda x: x[1])
    if below_list:
        for k, v in below_list:
            lines.append(f"- `{k}` — {v} occurrences (need {MIN_EXAMPLES - v} more)")
    else:
        lines.append("No functions in this range.")
    lines.append("")

    # Gap analysis by module
    lines.append("## Gap Analysis — New Examples Needed per Module")
    lines.append("")
    lines.append("| Module | Functions | Adequate | Below Min | Zero | Examples Needed |")
    lines.append("|--------|-----------|----------|-----------|------|-----------------|")
    for mod in sorted(modules.keys()):
        funcs = modules[mod]
        n_funcs = len(funcs)
        n_adequate = sum(1 for fn in funcs if counts.get(f"{mod}.{fn['name']}", 0) >= MIN_EXAMPLES)
        n_below = sum(1 for fn in funcs if 0 < counts.get(f"{mod}.{fn['name']}", 0) < MIN_EXAMPLES)
        n_zero = sum(1 for fn in funcs if counts.get(f"{mod}.{fn['name']}", 0) == 0)
        needed = sum(max(0, MIN_EXAMPLES - counts.get(f"{mod}.{fn['name']}", 0)) for fn in funcs)
        lines.append(f"| `{mod}` | {n_funcs} | {n_adequate} | {n_below} | {n_zero} | {needed} |")

    lines.append(f"| **TOTAL** | **{total_funcs}** | **{adequate_funcs}** | **{below_funcs}** | **{zero_funcs}** | **{total_gap}** |")
    lines.append("")

    return "\n".join(lines)


def generate_json(modules: dict, counts: dict, total_files: int, files_with_stdlib: int) -> dict:
    graph = {
        "meta": {
            "total_corpus_files": total_files,
            "files_with_stdlib": files_with_stdlib,
            "min_examples_target": MIN_EXAMPLES,
        },
        "modules": {},
    }

    for mod, funcs in sorted(modules.items()):
        mod_entry = {"functions": []}
        for fn in funcs:
            key = f"{mod}.{fn['name']}"
            count = counts.get(key, 0)
            gap = max(0, MIN_EXAMPLES - count)
            mod_entry["functions"].append({
                "name": fn["name"],
                "qualified_name": key,
                "params": fn["params"],
                "return_type": fn["return_type"],
                "corpus_occurrences": count,
                "gap_to_minimum": gap,
                "status": "adequate" if count >= MIN_EXAMPLES else ("zero" if count == 0 else "below_minimum"),
            })
        graph["modules"][mod] = mod_entry

    return graph


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Parsing stdlib signatures...")
    modules = parse_signatures(SIGNATURES_FILE)

    total_funcs = sum(len(fns) for fns in modules.values())
    print(f"  Found {len(modules)} modules, {total_funcs} functions")

    print("Scanning corpus...")
    counts, total_files, files_with_stdlib = scan_corpus(modules)
    print(f"  Scanned {total_files:,} files, {files_with_stdlib:,} contain stdlib imports")

    print("Generating outputs...")
    md = generate_markdown(modules, counts, total_files, files_with_stdlib)
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md)
    print(f"  Written: {OUTPUT_MD}")

    graph = generate_json(modules, counts, total_files, files_with_stdlib)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(graph, indent=2) + "\n")
    print(f"  Written: {OUTPUT_JSON}")

    # Print summary
    zero = sum(1 for c in counts.values() if c == 0)
    below = sum(1 for c in counts.values() if 0 < c < MIN_EXAMPLES)
    adequate = sum(1 for c in counts.values() if c >= MIN_EXAMPLES)
    gap = sum(max(0, MIN_EXAMPLES - c) for c in counts.values())
    print()
    print("=== SUMMARY ===")
    print(f"  Total functions:       {total_funcs}")
    print(f"  Adequate (>={MIN_EXAMPLES}):       {adequate}")
    print(f"  Below minimum (1-19):  {below}")
    print(f"  Zero occurrences:      {zero}")
    print(f"  Total gap to close:    {gap} new examples needed")


if __name__ == "__main__":
    main()
