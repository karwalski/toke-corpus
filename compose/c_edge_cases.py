"""C-EDGE: Generate edge-case variant programs from Phase A corpus.

Takes accepted Phase A entries and creates edge-case wrapper programs
that add boundary checking, guard clauses, and defensive patterns.
Zero LLM cost — pure mechanical transformation.

Output goes to corpus/phase_c/ with phase="C" and task_ids prefixed "C-".
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
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FuncInfo:
    entry_id: str
    task_id: str
    category: str
    fname: str
    params: list[tuple[str, str]]  # [(name, type), ...]
    ret_type: str
    source: str  # full toke source
    func_body: str  # just this function


# Regex to match F=name(params):RetType{
_FUNC_RE = re.compile(r'(F=(\w+)\(([^)]*)\):(\w+(?:!\w+)?)\s*\{)', re.DOTALL)


def parse_functions(entry: dict) -> list[FuncInfo]:
    src = entry.get("tk_source", "")
    entry_id = entry.get("id", "")
    task_id = entry.get("task_id", "")
    cat = task_id.split("-")[1] if "-" in task_id else "UNK"

    results = []
    for m in _FUNC_RE.finditer(src):
        fname = m.group(2)
        raw_params = m.group(3)
        ret_type = m.group(4)

        params = []
        for p in raw_params.split(";"):
            p = p.strip()
            if ":" in p:
                pname, ptype = p.split(":", 1)
                params.append((pname.strip(), ptype.strip()))

        # Extract function body
        start = m.start()
        depth = 0
        i = m.end() - 1
        func_end = len(src)
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    func_end = j + 1
                    if func_end < len(src) and src[func_end] == ";":
                        func_end += 1
                    break
        func_body = src[start:func_end]

        results.append(FuncInfo(
            entry_id=entry_id, task_id=task_id, category=cat,
            fname=fname, params=params, ret_type=ret_type,
            source=src, func_body=func_body,
        ))
    return results


# ---------------------------------------------------------------------------
# Edge-case transformations
# ---------------------------------------------------------------------------

def _default_value(typ: str) -> str | None:
    """Return a sensible default/edge-case return value for a type."""
    defaults = {
        "i64": "0", "u64": "0 as u64", "f64": "0.0",
        "bool": "false", "Str": '""', "void": "",
    }
    return defaults.get(typ)


def _extract_module(source: str) -> str:
    """Extract module declaration from source."""
    m = re.match(r'M=(\w+);', source)
    return m.group(1) if m else "edge"


def _extract_all_funcs(source: str) -> str:
    """Get everything except M= line."""
    lines = []
    for part in source.split(";"):
        stripped = part.strip()
        if not stripped.startswith("M="):
            lines.append(part)
    # Rejoin — but need to be careful with semicolons
    result = source
    m = re.match(r'M=\w+;', source)
    if m:
        result = source[m.end():]
    return result.strip()


# ── Transform 1: Empty-input guard ──────────────────────────────────────
def guard_empty_input(func: FuncInfo) -> str | None:
    """Wrap function with empty-input guard for array/string params."""
    # Find first array or Str param
    guard_param = None
    for pname, ptype in func.params:
        if ptype == "Str" or ptype.startswith("["):
            guard_param = (pname, ptype)
            break

    if not guard_param:
        return None

    pname, ptype = guard_param
    default = _default_value(func.ret_type)
    if default is None:
        return None

    mod = _extract_module(func.source) + "Guard"
    inner_funcs = _extract_all_funcs(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)
    args_str = ";".join(n for n, _ in func.params)
    guard_name = f"safe{func.fname[0].upper()}{func.fname[1:]}"

    src = f"M={mod};{inner_funcs}F={guard_name}({params_str}):{func.ret_type}{{if({pname}.len=0){{<{default}}};< {func.fname}({args_str})}};"
    return src


# ── Transform 2: Single-element specialization ──────────────────────────
def single_element(func: FuncInfo) -> str | None:
    """Wrap function with single-element shortcut for array params."""
    arr_param = None
    for pname, ptype in func.params:
        if ptype.startswith("["):
            arr_param = (pname, ptype)
            break

    if not arr_param:
        return None

    pname, ptype = arr_param
    elem_type = ptype[1:-1]  # [i64] -> i64

    # For array->scalar functions, single element = that element
    if func.ret_type == elem_type:
        mod = _extract_module(func.source) + "Single"
        inner_funcs = _extract_all_funcs(func.source)
        params_str = ";".join(f"{n}:{t}" for n, t in func.params)
        args_str = ";".join(n for n, _ in func.params)
        wrap_name = f"{func.fname}Single"

        src = f"M={mod};{inner_funcs}F={wrap_name}({params_str}):{func.ret_type}{{if({pname}.len=1){{<{pname}[0]}};< {func.fname}({args_str})}};"
        return src

    return None


# ── Transform 3: Negative-input guard ───────────────────────────────────
def guard_negative(func: FuncInfo) -> str | None:
    """Add guard for negative i64 inputs — return default for negative."""
    i64_param = None
    for pname, ptype in func.params:
        if ptype == "i64":
            i64_param = (pname, ptype)
            break

    if not i64_param:
        return None

    pname, _ = i64_param
    default = _default_value(func.ret_type)
    if default is None:
        return None

    mod = _extract_module(func.source) + "Neg"
    inner_funcs = _extract_all_funcs(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)
    args_str = ";".join(n for n, _ in func.params)
    wrap_name = f"{func.fname}Safe"

    src = f"M={mod};{inner_funcs}F={wrap_name}({params_str}):{func.ret_type}{{if({pname}<0){{<{default}}};< {func.fname}({args_str})}};"
    return src


# ── Transform 4: Clamp-and-call ─────────────────────────────────────────
def clamp_input(func: FuncInfo) -> str | None:
    """Clamp i64 input to [0, 1000] range before calling."""
    i64_param = None
    for pname, ptype in func.params:
        if ptype == "i64":
            i64_param = (pname, ptype)
            break

    if not i64_param:
        return None

    pname, _ = i64_param
    mod = _extract_module(func.source) + "Clamp"
    inner_funcs = _extract_all_funcs(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)

    # Build args with clamped value
    args = []
    for n, _ in func.params:
        if n == pname:
            args.append("clamped")
        else:
            args.append(n)
    args_str = ";".join(args)
    wrap_name = f"{func.fname}Clamped"

    src = (
        f"M={mod};{inner_funcs}"
        f"F={wrap_name}({params_str}):{func.ret_type}{{"
        f"let clamped=mut.{pname};"
        f"if({pname}<0){{clamped=0}};"
        f"if({pname}>1000){{clamped=1000}};"
        f"< {func.fname}({args_str})}};"
    )
    return src


# ── Transform 5: Boolean inversion wrapper ──────────────────────────────
def invert_bool(func: FuncInfo) -> str | None:
    """For bool-returning functions, create a NOT wrapper."""
    if func.ret_type != "bool":
        return None

    mod = _extract_module(func.source) + "Not"
    inner_funcs = _extract_all_funcs(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)
    args_str = ";".join(n for n, _ in func.params)
    wrap_name = f"not{func.fname[0].upper()}{func.fname[1:]}"

    src = f"M={mod};{inner_funcs}F={wrap_name}({params_str}):bool{{<!({func.fname}({args_str}))}};"
    return src


TRANSFORMS = [
    ("guard_empty", guard_empty_input),
    ("single_elem", single_element),
    ("guard_neg", guard_negative),
    ("clamp", clamp_input),
    ("invert", invert_bool),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_toke(source: str, tkc_path: str, timeout: int = 10) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toke", delete=False) as f:
        f.write(source)
        tmp = f.name
    try:
        r = subprocess.run(
            [tkc_path, "--check", tmp],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    finally:
        os.unlink(tmp)


def compact(src: str) -> str:
    """Remove non-essential whitespace."""
    result = []
    in_str = False
    i = 0
    while i < len(src):
        c = src[i]
        if c == '"' and (i == 0 or src[i-1] != '\\'):
            in_str = not in_str
            result.append(c)
        elif in_str:
            result.append(c)
        elif c in ' \t\n\r':
            if result and i+1 < len(src):
                prev = result[-1]
                nxt = src[i+1]
                if (prev.isalnum() or prev == '_') and (nxt.isalnum() or nxt == '_'):
                    result.append(' ')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        return len(text.split())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_phase_a(corpus_dir: str) -> list[dict]:
    entries = []
    phase_a = Path(corpus_dir) / "phase_a"
    if not phase_a.exists():
        return entries
    for p in phase_a.rglob("*.json"):
        try:
            with open(p) as f:
                e = json.load(f)
            if e.get("judge", {}).get("accepted", False):
                entries.append(e)
        except Exception:
            pass
    return entries


def run(corpus_dir: str, tkc_path: str, max_entries: int = 10000, dry_run: bool = False):
    logger.info("Loading Phase A corpus...")
    entries = load_phase_a(corpus_dir)
    logger.info("Loaded %d accepted entries", len(entries))

    all_funcs = []
    for e in entries:
        all_funcs.extend(parse_functions(e))
    logger.info("Extracted %d functions", len(all_funcs))

    out_dir = Path(corpus_dir) / "phase_c" / "C-EDG"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Check existing
    existing = len(list(out_dir.glob("*.json")))
    logger.info("Existing C-EDG entries: %d", existing)

    composed = 0
    validated = 0
    failed = 0
    seen = set()

    for func in all_funcs:
        if composed >= max_entries:
            break

        for transform_name, transform_fn in TRANSFORMS:
            if composed >= max_entries:
                break

            result = transform_fn(func)
            if result is None:
                continue

            # Compact and deduplicate
            result = compact(result)
            h = hashlib.sha256(result.encode()).hexdigest()[:16]
            if h in seen:
                continue
            seen.add(h)

            # Validate
            exit_code, stderr = validate_toke(result, tkc_path)
            if exit_code != 0:
                failed += 1
                if failed <= 5:
                    logger.debug("Failed %s/%s: %s", func.fname, transform_name, stderr[:100])
                continue

            validated += 1
            task_num = existing + composed + 1
            task_id = f"C-EDG-{task_num:05d}"
            entry_id = f"C-{task_id}-{short_hash(result)}"

            entry = {
                "id": entry_id,
                "version": 1,
                "phase": "C",
                "task_id": task_id,
                "tk_source": result,
                "tk_tokens": count_tokens(result),
                "attempts": 0,
                "model": f"mechanical-{transform_name}",
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
                    "score": 1.0,
                },
                "edge_case": {
                    "source_entry_id": func.entry_id,
                    "transform": transform_name,
                    "original_function": func.fname,
                },
                "references": {
                    "python_source": "",
                    "python_tokens": 0,
                    "c_source": "",
                    "c_tokens": 0,
                    "java_source": "",
                    "java_tokens": 0,
                },
            }

            if not dry_run:
                out_path = out_dir / f"{entry_id}.json"
                if not out_path.exists():
                    with open(out_path, "w") as f:
                        json.dump(entry, f, indent=2, ensure_ascii=False)
                        f.write("\n")

            composed += 1
            if composed % 500 == 0:
                logger.info("Progress: %d composed, %d validated, %d failed", composed, validated, failed)

    logger.info("Done: %d composed, %d validated, %d failed", composed, validated, failed)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", default="corpus")
    parser.add_argument("--tkc", default="bin/tkc")
    parser.add_argument("--max-entries", type=int, default=10000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.corpus_dir, args.tkc, args.max_entries, args.dry_run)


if __name__ == "__main__":
    main()
