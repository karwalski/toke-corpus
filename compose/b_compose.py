"""B-COMPOSE: Mechanically compose Phase A functions into multi-function programs.

Reads accepted Phase A corpus entries, finds type-compatible function pairs,
and generates composed toke programs at zero LLM cost.

Output goes to corpus/phase_b/ — completely separate from the Phase A corpus.
Entries are tagged with phase="B" and task_ids prefixed with "B-".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FuncSig:
    """Parsed function signature from a corpus entry."""
    entry_id: str
    task_id: str
    category: str
    fname: str
    params: list[tuple[str, str]]  # [(name, type), ...]
    ret_type: str
    source: str  # full toke source of the entry
    func_body: str  # just this function's body including F= line


@dataclass
class ComposedEntry:
    """A composed B-phase corpus entry."""
    id: str
    task_id: str
    phase: str
    tk_source: str
    tk_tokens: int
    source_a_ids: list[str]  # Phase A entry IDs used
    composition_type: str  # e.g. "chain", "nested"
    validation_ok: bool
    compiler_exit_code: int
    error_codes: list[str]
    python_source: str
    c_source: str
    java_source: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Match F=name(params):RetType{ ... };
_FUNC_RE = re.compile(
    r'(F=(\w+)\(([^)]*)\):(\w+(?:!\w+)?)\s*\{)',
    re.DOTALL,
)


def parse_functions(entry: dict) -> list[FuncSig]:
    """Extract function signatures from a corpus entry."""
    src = entry.get("tk_source", "")
    entry_id = entry.get("id", "")
    task_id = entry.get("task_id", "")
    cat = task_id.split("-")[1] if "-" in task_id else "UNK"

    results = []
    for m in _FUNC_RE.finditer(src):
        fname = m.group(2)
        raw_params = m.group(3)
        ret_type = m.group(4)

        # Parse parameters
        params = []
        for p in raw_params.split(";"):
            p = p.strip()
            if ":" in p:
                pname, ptype = p.split(":", 1)
                params.append((pname.strip(), ptype.strip()))

        # Extract this function's full body (from F= to matching };)
        start = m.start()
        brace_depth = 0
        i = m.end() - 1  # position of opening {
        func_end = len(src)
        for j in range(i, len(src)):
            if src[j] == "{":
                brace_depth += 1
            elif src[j] == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    # Include the trailing ;
                    func_end = j + 1
                    if func_end < len(src) and src[func_end] == ";":
                        func_end += 1
                    break
        func_body = src[start:func_end]

        results.append(FuncSig(
            entry_id=entry_id,
            task_id=task_id,
            category=cat,
            fname=fname,
            params=params,
            ret_type=ret_type,
            source=src,
            func_body=func_body,
        ))

    return results


# ---------------------------------------------------------------------------
# Type compatibility
# ---------------------------------------------------------------------------

def is_composable(producer: FuncSig, consumer: FuncSig) -> bool:
    """Check if producer's output can feed consumer's single input."""
    # Consumer must take exactly one parameter
    if len(consumer.params) != 1:
        return False
    # Types must match (strip error union for matching)
    prod_ret = producer.ret_type.split("!")[0]
    cons_input = consumer.params[0][1]
    if prod_ret != cons_input:
        return False
    # Don't compose a function with itself
    if producer.fname == consumer.fname and producer.entry_id == consumer.entry_id:
        return False
    # Skip error-returning functions for now (complex to compose)
    if "!" in producer.ret_type or "!" in consumer.ret_type:
        return False
    return True


# ---------------------------------------------------------------------------
# Composition: generate toke, Python, C, Java
# ---------------------------------------------------------------------------

def _short_hash(text: str, length: int = 8) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def _extract_all_functions(source: str) -> str:
    """Extract all F= and T= declarations from source, stripping M= line."""
    lines = []
    for line in source.split("\n"):
        stripped = line.strip()
        # Skip module declarations — we'll add our own
        if stripped.startswith("M="):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _get_declared_names(source: str) -> set[str]:
    """Get all function/type names declared in a source."""
    names = set()
    for m in re.finditer(r'(?:F|T)=(\w+)', source):
        names.add(m.group(1))
    return names


def compose_toke(producer: FuncSig, consumer: FuncSig, module_name: str) -> str:
    """Generate a composed toke program with both source entries + a glue function."""
    prod_params_str = ";".join(f"{n}:{t}" for n, t in producer.params)
    prod_args_str = ";".join(n for n, _ in producer.params)
    glue_name = f"{consumer.fname}{producer.fname.capitalize()}"

    # Include ALL functions from both source entries (not just the matched ones)
    # This ensures helper functions are available
    prod_funcs = _extract_all_functions(producer.source)
    cons_funcs = _extract_all_functions(consumer.source)

    # Check for name collisions between the two sources — skip if any
    prod_names = _get_declared_names(prod_funcs)
    cons_names = _get_declared_names(cons_funcs)
    if prod_names & cons_names:
        return ""  # signal to caller to skip this pair

    lines = [
        f"M={module_name};",
        prod_funcs,
        cons_funcs,
        f"F={glue_name}({prod_params_str}):{consumer.ret_type}{{",
        f"  <{consumer.fname}({producer.fname}({prod_args_str}))",
        "};",
    ]
    return "\n".join(lines)


def compose_python(producer: FuncSig, consumer: FuncSig) -> str:
    """Generate Python reference for the composed program."""
    # We need the original Python sources — extract from entry references
    # For now, generate a minimal wrapper
    prod_params = ", ".join(n for n, _ in producer.params)
    glue_name = f"{consumer.fname}_{producer.fname}"

    lines = [
        f"# Composed: {consumer.fname}({producer.fname}(...))",
        f"def {glue_name}({prod_params}):",
        f"    return {consumer.fname}({producer.fname}({prod_params}))",
    ]
    return "\n".join(lines)


def compose_c(producer: FuncSig, consumer: FuncSig) -> str:
    """Generate C reference for the composed program."""
    type_map = {
        "i64": "int64_t", "u64": "uint64_t", "f64": "double",
        "bool": "int", "Str": "const char*", "void": "void",
    }
    prod_params = ", ".join(
        f"{type_map.get(t, 'int64_t')} {n}" for n, t in producer.params
    )
    prod_args = ", ".join(n for n, _ in producer.params)
    ret_c = type_map.get(consumer.ret_type, "int64_t")
    glue_name = f"{consumer.fname}_{producer.fname}"

    return (
        f"// Composed: {consumer.fname}({producer.fname}(...))\n"
        f"{ret_c} {glue_name}({prod_params}) {{\n"
        f"    return {consumer.fname}({producer.fname}({prod_args}));\n"
        f"}}\n"
    )


def compose_java(producer: FuncSig, consumer: FuncSig) -> str:
    """Generate Java reference for the composed program."""
    type_map = {
        "i64": "long", "u64": "long", "f64": "double",
        "bool": "boolean", "Str": "String", "void": "void",
    }
    prod_params = ", ".join(
        f"{type_map.get(t, 'long')} {n}" for n, t in producer.params
    )
    prod_args = ", ".join(n for n, _ in producer.params)
    ret_java = type_map.get(consumer.ret_type, "long")
    glue_name = f"{consumer.fname}{producer.fname.capitalize()}"

    return (
        f"// Composed: {consumer.fname}({producer.fname}(...))\n"
        f"static {ret_java} {glue_name}({prod_params}) {{\n"
        f"    return {consumer.fname}({producer.fname}({prod_args}));\n"
        f"}}\n"
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_toke(source: str, tkc_path: str, timeout: int = 10) -> tuple[int, str]:
    """Run tkc --check and return (exit_code, stderr)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toke", delete=False
    ) as f:
        f.write(source)
        f.flush()
        tmp_path = f.name

    try:
        result = subprocess.run(
            [tkc_path, "--check", tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def load_corpus_entries(corpus_dir: str) -> list[dict]:
    """Load all Phase A accepted corpus entries."""
    entries = []
    phase_a_dir = Path(corpus_dir) / "phase_a"
    if not phase_a_dir.exists():
        logger.error("Phase A corpus dir not found: %s", phase_a_dir)
        return entries

    for json_path in phase_a_dir.rglob("*.json"):
        try:
            with open(json_path, encoding="utf-8") as fh:
                entry = json.load(fh)
                if entry.get("judge", {}).get("accepted", False):
                    entries.append(entry)
        except Exception as exc:
            logger.debug("Skipping %s: %s", json_path, exc)
    return entries


def write_composed_entry(
    entry: ComposedEntry,
    corpus_dir: str,
) -> str:
    """Write a Phase B entry to corpus/phase_b/{category}/."""
    cat_code = entry.task_id.split("-")[1] if "-" in entry.task_id else "CMP"
    out_dir = Path(corpus_dir) / "phase_b" / f"B-{cat_code}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{entry.id}.json"

    if out_path.exists():
        return ""  # skip duplicate

    entry_dict = {
        "id": entry.id,
        "version": 1,
        "phase": "B",
        "task_id": entry.task_id,
        "tk_source": entry.tk_source,
        "tk_tokens": entry.tk_tokens,
        "attempts": 0,
        "model": "mechanical-compose",
        "validation": {
            "compiler_exit_code": entry.compiler_exit_code,
            "error_codes": entry.error_codes,
        },
        "differential": {
            "languages_agreed": [],
            "majority_output": "",
        },
        "judge": {
            "accepted": entry.validation_ok,
            "score": 1.0 if entry.validation_ok else 0.0,
        },
        "composition": {
            "source_a_ids": entry.source_a_ids,
            "composition_type": entry.composition_type,
        },
        "references": {
            "python_source": entry.python_source,
            "python_tokens": 0,
            "c_source": entry.c_source,
            "c_tokens": 0,
            "java_source": entry.java_source,
            "java_tokens": 0,
        },
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(entry_dict, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return str(out_path)


def count_tokens_simple(text: str) -> int:
    """Rough token count (whitespace split) — avoids tiktoken dependency."""
    return len(text.split())


def run(
    corpus_dir: str,
    tkc_path: str,
    max_pairs: int = 10000,
    max_per_func: int = 3,
    dry_run: bool = False,
) -> None:
    """Run the B-COMPOSE pipeline."""
    logger.info("Loading Phase A corpus from %s", corpus_dir)
    entries = load_corpus_entries(corpus_dir)
    logger.info("Loaded %d accepted entries", len(entries))

    # Parse all function signatures
    all_funcs: list[FuncSig] = []
    for entry in entries:
        all_funcs.extend(parse_functions(entry))
    logger.info("Extracted %d functions", len(all_funcs))

    # Index by return type (producers) and first-param type (consumers)
    # Only single-param functions can be consumers
    producers_by_ret: dict[str, list[FuncSig]] = {}
    consumers_by_input: dict[str, list[FuncSig]] = {}

    for f in all_funcs:
        ret = f.ret_type.split("!")[0]
        producers_by_ret.setdefault(ret, []).append(f)
        if len(f.params) == 1:
            consumers_by_input.setdefault(f.params[0][1], []).append(f)

    logger.info("Producer types: %s", {k: len(v) for k, v in producers_by_ret.items()})
    logger.info("Consumer types: %s", {k: len(v) for k, v in consumers_by_input.items()})

    # Generate compositions
    composed = 0
    validated = 0
    failed = 0
    skipped = 0
    seen_pairs: set[tuple[str, str]] = set()
    use_count: dict[str, int] = {}  # entry_id -> times used

    # Iterate through type-compatible pairs
    for ret_type, producers in sorted(producers_by_ret.items()):
        consumers = consumers_by_input.get(ret_type, [])
        if not consumers:
            continue

        for prod in producers:
            if use_count.get(prod.entry_id, 0) >= max_per_func:
                continue

            for cons in consumers:
                if composed >= max_pairs:
                    break
                if not is_composable(prod, cons):
                    continue

                # Deduplicate: same function pair (by name) only once
                pair_key = (prod.fname, cons.fname)
                if pair_key in seen_pairs:
                    skipped += 1
                    continue
                seen_pairs.add(pair_key)

                # Generate module name
                mod_name = f"{cons.fname}{prod.fname.capitalize()}"
                if len(mod_name) > 20:
                    mod_name = mod_name[:20]

                # Compose toke source
                toke_src = compose_toke(prod, cons, mod_name)
                if not toke_src:
                    skipped += 1
                    continue  # name collision

                # Validate with tkc
                exit_code, stderr = validate_toke(toke_src, tkc_path)
                error_codes = re.findall(r"E\d{4}", stderr) if stderr else []
                ok = exit_code == 0

                if ok:
                    validated += 1
                else:
                    failed += 1
                    if failed <= 5:
                        logger.debug(
                            "Failed compose %s+%s: exit=%d %s",
                            prod.fname, cons.fname, exit_code,
                            stderr.strip()[:100] if stderr else "",
                        )
                    continue  # only write validated entries

                # Generate references
                py_src = compose_python(prod, cons)
                c_src = compose_c(prod, cons)
                java_src = compose_java(prod, cons)

                # Build entry
                task_num = composed + 1
                cat = f"CMP"
                task_id = f"B-{cat}-{task_num:04d}"
                short = _short_hash(toke_src)
                entry_id = f"B-{task_id}-{short}"

                entry = ComposedEntry(
                    id=entry_id,
                    task_id=task_id,
                    phase="B",
                    tk_source=toke_src,
                    tk_tokens=count_tokens_simple(toke_src),
                    source_a_ids=[prod.entry_id, cons.entry_id],
                    composition_type="chain",
                    validation_ok=True,
                    compiler_exit_code=0,
                    error_codes=[],
                    python_source=py_src,
                    c_source=c_src,
                    java_source=java_src,
                )

                if not dry_run:
                    path = write_composed_entry(entry, corpus_dir)
                    if path:
                        composed += 1
                        use_count[prod.entry_id] = use_count.get(prod.entry_id, 0) + 1
                        use_count[cons.entry_id] = use_count.get(cons.entry_id, 0) + 1
                else:
                    composed += 1

                if composed % 100 == 0:
                    logger.info(
                        "Progress: %d composed, %d validated, %d failed, %d skipped",
                        composed, validated, failed, skipped,
                    )

            if composed >= max_pairs:
                break
        if composed >= max_pairs:
            break

    logger.info(
        "Done: %d composed, %d validated (%.1f%%), %d failed, %d skipped",
        composed, validated,
        validated / (validated + failed) * 100 if (validated + failed) > 0 else 0,
        failed, skipped,
    )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="B-COMPOSE: Compose Phase A functions")
    parser.add_argument("--corpus-dir", default="corpus", help="Corpus root directory")
    parser.add_argument("--tkc", default="bin/tkc", help="Path to tkc compiler")
    parser.add_argument("--max-pairs", type=int, default=10000, help="Max compositions")
    parser.add_argument("--max-per-func", type=int, default=3, help="Max uses per function")
    parser.add_argument("--dry-run", action="store_true", help="Don't write files")
    args = parser.parse_args()

    run(
        corpus_dir=args.corpus_dir,
        tkc_path=args.tkc,
        max_pairs=args.max_pairs,
        max_per_func=args.max_per_func,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
