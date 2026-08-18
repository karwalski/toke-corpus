"""Expand multi-function programs using existing composition patterns.

Reads single-function programs from phase2_deduplicated/ (A-ARR, A-CND, A-MTH,
A-SRT, A-STR), finds type-compatible pairs/triples, and generates new composed
programs using chain, edge-case, and application patterns.

Validates each with tkc --check, writes passing entries to COMPOSE-NEW/.
Uses multiprocessing with 8 workers.

Target: 2,000-3,000 new multi-function programs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from multiprocessing import Pool, cpu_count
from pathlib import Path

logger = logging.getLogger(__name__)

TKC_PATH = ""  # set from args
CORPUS_DIR = ""

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FuncSig:
    entry_id: str
    task_id: str
    category: str
    fname: str
    params: list[tuple[str, str]]  # [(name, type), ...]
    ret_type: str
    source: str       # full toke source of the entry
    func_body: str    # just this function's body including f= line
    module: str       # module name from m=...;


# Regex for toke function: f=name(params):rettype{
_FUNC_RE = re.compile(
    r'(f=(\w+)\(([^)]*)\):(\w+(?:!\w+)?)\s*\{)',
    re.DOTALL,
)

_MODULE_RE = re.compile(r'm=(\w+);')


def parse_functions(entry: dict) -> list[FuncSig]:
    """Extract function signatures from a corpus entry."""
    src = entry.get("tk_source", "")
    entry_id = entry.get("id", "")
    task_id = entry.get("task_id", "")
    cat = task_id.split("-")[1] if "-" in task_id else "UNK"

    mod_m = _MODULE_RE.search(src)
    module = mod_m.group(1) if mod_m else "mod"

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

        # Extract function body (from f= to matching };)
        start = m.start()
        depth = 0
        i = m.end() - 1  # position of opening {
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

        results.append(FuncSig(
            entry_id=entry_id, task_id=task_id, category=cat,
            fname=fname, params=params, ret_type=ret_type,
            source=src, func_body=func_body, module=module,
        ))
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def structural_hash(src: str) -> str:
    """Hash for structural deduplication - normalise whitespace + module name."""
    # Strip module declaration, normalise whitespace
    normalised = re.sub(r'm=\w+;', '', src)
    normalised = re.sub(r'\s+', '', normalised)
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def count_tokens(text: str) -> int:
    return len(text.split())


def extract_funcs_no_module(source: str) -> str:
    """Get everything except m= line."""
    m = re.match(r'm=\w+;', source)
    return source[m.end():].strip() if m else source.strip()


def get_declared_names(source: str) -> set[str]:
    """Get all function/type names declared in a source."""
    return {m.group(1) for m in re.finditer(r'(?:f|t)=(\w+)', source)}


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
            if result and i + 1 < len(src):
                prev = result[-1]
                nxt = src[i + 1]
                if (prev.isalnum() or prev == '_') and (nxt.isalnum() or nxt == '_'):
                    result.append(' ')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def validate_toke(source: str) -> bool:
    """Run tkc --check and return whether it passes."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toke", delete=False) as f:
        f.write(source)
        tmp = f.name
    try:
        r = subprocess.run(
            [TKC_PATH, "--check", tmp],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Load corpus
# ---------------------------------------------------------------------------

def load_fundamental_entries(corpus_dir: str) -> list[dict]:
    """Load single-function programs from A-ARR, A-CND, A-MTH, A-SRT, A-STR."""
    categories = ["A-ARR", "A-CND", "A-MTH", "A-SRT", "A-STR"]
    entries = []
    base = Path(corpus_dir) / "phase2_deduplicated"

    for cat in categories:
        cat_dir = base / cat
        if not cat_dir.exists():
            logger.warning("Category dir not found: %s", cat_dir)
            continue
        for json_path in cat_dir.glob("*.json"):
            try:
                with open(json_path, encoding="utf-8") as fh:
                    entry = json.load(fh)
                if entry.get("judge", {}).get("accepted", False):
                    entries.append(entry)
            except Exception:
                pass

    logger.info("Loaded %d fundamental entries", len(entries))
    return entries


def load_existing_hashes(corpus_dir: str) -> set[str]:
    """Load structural hashes of existing COMPOSE-C and COMPOSE-D entries."""
    hashes = set()
    base = Path(corpus_dir) / "phase2_deduplicated"
    for subdir in ["COMPOSE-C", "COMPOSE-D", "COMPOSE-NEW"]:
        d = base / subdir
        if not d.exists():
            continue
        for json_path in d.glob("*.json"):
            try:
                with open(json_path, encoding="utf-8") as fh:
                    entry = json.load(fh)
                src = entry.get("tk_source", "")
                if src:
                    hashes.add(structural_hash(src))
            except Exception:
                pass
    logger.info("Loaded %d existing composition hashes", len(hashes))
    return hashes


# ---------------------------------------------------------------------------
# Composition pattern 1: Chain (f(g(x)))
# ---------------------------------------------------------------------------

def generate_chains(funcs: list[FuncSig], existing_hashes: set[str],
                    max_count: int = 1500) -> list[tuple[str, list[str], str]]:
    """Find type-compatible pairs and generate chain compositions."""
    # Index by return type (producers) and single-param input type (consumers)
    producers_by_ret: dict[str, list[FuncSig]] = {}
    consumers_by_input: dict[str, list[FuncSig]] = {}

    for f in funcs:
        ret = f.ret_type.split("!")[0]
        if "!" not in f.ret_type:  # skip error-returning for chains
            producers_by_ret.setdefault(ret, []).append(f)
        if len(f.params) == 1 and "!" not in f.ret_type:
            consumers_by_input.setdefault(f.params[0][1], []).append(f)

    results = []
    seen_pairs: set[tuple[str, str]] = set()

    for ret_type, producers in sorted(producers_by_ret.items()):
        consumers = consumers_by_input.get(ret_type, [])
        if not consumers:
            continue

        for prod in producers:
            for cons in consumers:
                if len(results) >= max_count:
                    return results

                # Don't compose function with itself
                if prod.fname == cons.fname and prod.entry_id == cons.entry_id:
                    continue

                # Deduplicate by function name pair
                pair_key = (prod.fname, cons.fname)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                # Check name collisions between sources
                prod_names = get_declared_names(prod.source)
                cons_names = get_declared_names(cons.source)
                if prod_names & cons_names:
                    continue

                # Build composed source
                prod_funcs = extract_funcs_no_module(prod.source)
                cons_funcs = extract_funcs_no_module(cons.source)

                params_str = ";".join(f"{n}:{t}" for n, t in prod.params)
                args_str = ";".join(n for n, _ in prod.params)
                glue_name = f"{cons.fname}{prod.fname[0].upper()}{prod.fname[1:]}"
                if len(glue_name) > 30:
                    glue_name = glue_name[:30]
                mod_name = glue_name[:20]

                src = (
                    f"m={mod_name};"
                    f"{prod_funcs}"
                    f"{cons_funcs}"
                    f"f={glue_name}({params_str}):{cons.ret_type}{{"
                    f"<{cons.fname}({prod.fname}({args_str}))"
                    f"}};"
                )
                src = compact(src)

                # Check structural uniqueness
                h = structural_hash(src)
                if h in existing_hashes:
                    continue

                results.append((src, [prod.entry_id, cons.entry_id], "chain"))
                existing_hashes.add(h)

    logger.info("Generated %d chain candidates", len(results))
    return results


# ---------------------------------------------------------------------------
# Composition pattern 2: Edge-case wrappers
# ---------------------------------------------------------------------------

def _default_value(typ: str) -> str | None:
    defaults = {
        "i64": "0", "u64": "0 as u64", "f64": "0.0",
        "bool": "false", "$str": '""', "void": "",
    }
    return defaults.get(typ)


def generate_edge_cases(funcs: list[FuncSig], existing_hashes: set[str],
                        max_count: int = 1000) -> list[tuple[str, list[str], str]]:
    """Generate edge-case wrapper programs."""
    results = []
    seen: set[str] = set()

    transforms = [
        _guard_empty,
        _guard_negative,
        _single_element,
        _clamp_input,
        _invert_bool,
    ]

    for func in funcs:
        if len(results) >= max_count:
            break
        for transform in transforms:
            if len(results) >= max_count:
                break
            out = transform(func)
            if out is None:
                continue

            src, transform_name = out
            src = compact(src)

            h = structural_hash(src)
            if h in existing_hashes or h in seen:
                continue
            seen.add(h)

            results.append((src, [func.entry_id], f"edge_{transform_name}"))
            existing_hashes.add(h)

    logger.info("Generated %d edge-case candidates", len(results))
    return results


def _guard_empty(func: FuncSig) -> tuple[str, str] | None:
    """Wrap with empty-input guard for array/string params."""
    guard_param = None
    for pname, ptype in func.params:
        if ptype == "$str" or ptype.startswith("@"):
            guard_param = (pname, ptype)
            break
    if not guard_param:
        return None

    pname, ptype = guard_param
    default = _default_value(func.ret_type)
    if default is None:
        return None

    inner = extract_funcs_no_module(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)
    args_str = ";".join(n for n, _ in func.params)
    wrap_name = f"safe{func.fname[0].upper()}{func.fname[1:]}"
    mod = f"{wrap_name}"[:20]

    src = (
        f"m={mod};"
        f"{inner}"
        f"f={wrap_name}({params_str}):{func.ret_type}{{"
        f"if({pname}.len=0 as u64){{<{default}}};"
        f"<{func.fname}({args_str})"
        f"}};"
    )
    return src, "guard_empty"


def _guard_negative(func: FuncSig) -> tuple[str, str] | None:
    """Guard against negative i64 inputs."""
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

    inner = extract_funcs_no_module(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)
    args_str = ";".join(n for n, _ in func.params)
    wrap_name = f"{func.fname}Safe"
    mod = wrap_name[:20]

    src = (
        f"m={mod};"
        f"{inner}"
        f"f={wrap_name}({params_str}):{func.ret_type}{{"
        f"if({pname}<0){{<{default}}};"
        f"<{func.fname}({args_str})"
        f"}};"
    )
    return src, "guard_neg"


def _single_element(func: FuncSig) -> tuple[str, str] | None:
    """Single-element shortcut for array->scalar functions."""
    arr_param = None
    for pname, ptype in func.params:
        if ptype.startswith("@"):
            arr_param = (pname, ptype)
            break
    if not arr_param:
        return None

    pname, ptype = arr_param
    elem_type = ptype[1:]  # @i64 -> i64

    if func.ret_type != elem_type:
        return None

    inner = extract_funcs_no_module(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)
    args_str = ";".join(n for n, _ in func.params)
    wrap_name = f"{func.fname}Single"
    mod = wrap_name[:20]

    src = (
        f"m={mod};"
        f"{inner}"
        f"f={wrap_name}({params_str}):{func.ret_type}{{"
        f"if({pname}.len=1 as u64){{<{pname}.get(0 as u64)}};"
        f"<{func.fname}({args_str})"
        f"}};"
    )
    return src, "single_elem"


def _clamp_input(func: FuncSig) -> tuple[str, str] | None:
    """Clamp i64 input to [0, 1000] range."""
    i64_param = None
    for pname, ptype in func.params:
        if ptype == "i64":
            i64_param = (pname, ptype)
            break
    if not i64_param:
        return None

    pname, _ = i64_param
    inner = extract_funcs_no_module(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)
    # Build args substituting clamped value
    args = []
    for n, _ in func.params:
        if n == pname:
            args.append("clamped")
        else:
            args.append(n)
    args_str = ";".join(args)
    wrap_name = f"{func.fname}Clamped"
    mod = wrap_name[:20]

    src = (
        f"m={mod};"
        f"{inner}"
        f"f={wrap_name}({params_str}):{func.ret_type}{{"
        f"let clamped=mut.{pname};"
        f"if({pname}<0){{clamped=0}};"
        f"if({pname}>1000){{clamped=1000}};"
        f"<{func.fname}({args_str})"
        f"}};"
    )
    return src, "clamp"


def _invert_bool(func: FuncSig) -> tuple[str, str] | None:
    """NOT wrapper for bool-returning functions."""
    if func.ret_type != "bool":
        return None

    inner = extract_funcs_no_module(func.source)
    params_str = ";".join(f"{n}:{t}" for n, t in func.params)
    args_str = ";".join(n for n, _ in func.params)
    wrap_name = f"not{func.fname[0].upper()}{func.fname[1:]}"
    mod = wrap_name[:20]

    src = (
        f"m={mod};"
        f"{inner}"
        f"f={wrap_name}({params_str}):bool{{"
        f"<!({func.fname}({args_str}))"
        f"}};"
    )
    return src, "invert"


# ---------------------------------------------------------------------------
# Composition pattern 3: Application programs (3+ functions)
# ---------------------------------------------------------------------------

def generate_applications(funcs: list[FuncSig], existing_hashes: set[str],
                          max_count: int = 1000, seed: int = 42
                          ) -> list[tuple[str, list[str], str]]:
    """Generate 3+ function application programs."""
    rng = random.Random(seed)
    results = []
    seen: set[str] = set()

    # Index functions
    by_input: dict[str, list[FuncSig]] = {}
    by_ret: dict[str, list[FuncSig]] = {}
    scalars: list[FuncSig] = []

    for f in funcs:
        if "!" in f.ret_type:
            continue
        ret = f.ret_type.split("!")[0]
        by_ret.setdefault(ret, []).append(f)
        if len(f.params) == 1:
            by_input.setdefault(f.params[0][1], []).append(f)
        if (len(f.params) == 1 and f.params[0][1] in ("i64", "u64", "f64")
                and f.ret_type == f.params[0][1]):
            scalars.append(f)

    # Pattern generators and their target counts
    patterns = [
        ("pipeline3", lambda: _pipeline3(funcs, by_ret, by_input, rng)),
        ("fanout", lambda: _fanout(funcs, by_input, rng)),
        ("accumulator", lambda: _accumulator(scalars, rng)),
    ]

    max_attempts = max_count * 30
    attempts = 0

    while len(results) < max_count and attempts < max_attempts:
        attempts += 1
        pat_name, gen_fn = rng.choice(patterns)
        out = gen_fn()
        if out is None:
            continue

        src, source_ids, comp_type = out
        src = compact(src)

        h = structural_hash(src)
        if h in existing_hashes or h in seen:
            continue
        seen.add(h)

        results.append((src, source_ids, comp_type))
        existing_hashes.add(h)

    logger.info("Generated %d application candidates", len(results))
    return results


def _pipeline3(funcs: list[FuncSig],
               by_ret: dict[str, list[FuncSig]],
               by_input: dict[str, list[FuncSig]],
               rng: random.Random) -> tuple[str, list[str], str] | None:
    """Chain 3 type-compatible functions: h(g(f(x)))."""
    # Pick a random starting type
    types_with_both = [t for t in by_ret if t in by_input]
    if not types_with_both:
        return None

    mid_type = rng.choice(types_with_both)
    producers = by_ret.get(mid_type, [])
    consumers = by_input.get(mid_type, [])

    if not producers or not consumers:
        return None

    f1 = rng.choice(producers)
    f2 = rng.choice(consumers)

    if f2.fname == f1.fname and f2.entry_id == f1.entry_id:
        return None

    # Find f3 that consumes f2's return type
    f2_ret = f2.ret_type.split("!")[0]
    f3_candidates = by_input.get(f2_ret, [])
    if not f3_candidates:
        return None

    f3 = rng.choice(f3_candidates)
    if f3.fname in (f1.fname, f2.fname):
        return None

    # Check name collisions
    names1 = get_declared_names(f1.source)
    names2 = get_declared_names(f2.source)
    names3 = get_declared_names(f3.source)
    if names1 & names2 or names1 & names3 or names2 & names3:
        return None

    funcs1 = extract_funcs_no_module(f1.source)
    funcs2 = extract_funcs_no_module(f2.source)
    funcs3 = extract_funcs_no_module(f3.source)

    mod = f"{f3.fname}{f2.fname[0].upper()}{f1.fname[0].upper()}"[:20]
    params_str = ";".join(f"{n}:{t}" for n, t in f1.params)
    args_str = ";".join(n for n, _ in f1.params)

    src = (
        f"m={mod};"
        f"{funcs1}{funcs2}{funcs3}"
        f"f=pipeline({params_str}):{f3.ret_type}{{"
        f"<{f3.fname}({f2.fname}({f1.fname}({args_str})))"
        f"}};"
    )
    return src, [f1.entry_id, f2.entry_id, f3.entry_id], "pipeline3"


def _fanout(funcs: list[FuncSig],
            by_input: dict[str, list[FuncSig]],
            rng: random.Random) -> tuple[str, list[str], str] | None:
    """Apply two functions to same input, combine with a third."""
    # Find two single-param functions with same input type and numeric return
    by_sig: dict[tuple[str, str], list[FuncSig]] = {}
    for f in funcs:
        if len(f.params) == 1 and f.ret_type in ("i64", "u64", "f64"):
            key = (f.params[0][1], f.ret_type)
            by_sig.setdefault(key, []).append(f)

    # Pick a signature group with at least 2 functions
    valid_groups = [(k, v) for k, v in by_sig.items() if len(v) >= 2]
    if not valid_groups:
        return None

    (in_type, ret_type), group = rng.choice(valid_groups)
    pair = rng.sample(group, 2)
    f1, f2 = pair[0], pair[1]

    if f1.fname == f2.fname:
        return None

    names1 = get_declared_names(f1.source)
    names2 = get_declared_names(f2.source)
    if names1 & names2:
        return None

    funcs1 = extract_funcs_no_module(f1.source)
    funcs2 = extract_funcs_no_module(f2.source)

    mod = f"{f1.fname}{f2.fname[0].upper()}"[:20]
    param_name = f1.params[0][0]

    src = (
        f"m={mod};"
        f"{funcs1}{funcs2}"
        f"f=combined({param_name}:{in_type}):{ret_type}{{"
        f"let a={f1.fname}({param_name});"
        f"let b={f2.fname}({param_name});"
        f"<a+b"
        f"}};"
    )
    return src, [f1.entry_id, f2.entry_id], "fanout"


def _accumulator(scalars: list[FuncSig],
                 rng: random.Random) -> tuple[str, list[str], str] | None:
    """Loop over array, apply scalar function, accumulate."""
    if not scalars:
        return None

    f = rng.choice(scalars)
    in_type = f.params[0][1]
    funcs_body = extract_funcs_no_module(f.source)
    mod = f"acc{f.fname[0].upper()}{f.fname[1:]}"[:20]

    # Build proper init and casts based on type
    if in_type == "u64":
        init = "0 as u64"
        len_cast = "arr.len"
        idx_expr = "i"
    elif in_type == "f64":
        init = "0.0"
        len_cast = "arr.len as i64"
        idx_expr = "i as u64"
    else:  # i64
        init = "0"
        len_cast = "arr.len as i64"
        idx_expr = "i as u64"

    src = (
        f"m={mod};"
        f"{funcs_body}"
        f"f=accumulate(arr:@{in_type}):{in_type}{{"
        f"let r=mut.{init};"
        f"lp(let i=0;i<{len_cast};i=i+1){{"
        f"r=r+{f.fname}(arr.get({idx_expr}));"
        f"}};<r"
        f"}};"
    )
    return src, [f.entry_id], "accumulator"


# ---------------------------------------------------------------------------
# Validation worker (for multiprocessing)
# ---------------------------------------------------------------------------

def _init_worker(tkc_path: str):
    """Initializer for pool workers — sets the global TKC_PATH."""
    global TKC_PATH
    TKC_PATH = tkc_path


def _validate_candidate(args: tuple[int, str, list[str], str]
                        ) -> tuple[int, str, list[str], str, bool] | None:
    """Validate a single candidate. Returns (idx, src, source_ids, comp_type, ok)."""
    idx, src, source_ids, comp_type = args
    ok = validate_toke(src)
    return (idx, src, source_ids, comp_type, ok)


# ---------------------------------------------------------------------------
# Write entries
# ---------------------------------------------------------------------------

def write_entry(out_dir: Path, src: str, source_ids: list[str],
                comp_type: str, seq_num: int) -> str:
    """Write a single COMPOSE-NEW entry. Returns path or empty string."""
    task_id = f"COMPOSE-NEW-{seq_num:05d}"
    entry_id = f"P2-N-{task_id}-{short_hash(src)}"

    entry = {
        "id": entry_id,
        "version": 1,
        "phase": "B",
        "task_id": task_id,
        "tk_source": src,
        "tk_tokens": count_tokens(src),
        "attempts": 1,
        "model": f"mechanical-{comp_type}",
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
        "composition": {
            "source_entry_ids": source_ids,
            "pattern": comp_type,
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

    out_path = out_dir / f"{entry_id}.json"
    if out_path.exists():
        return ""

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return str(out_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Expand multi-function compositions for the toke corpus"
    )
    parser.add_argument(
        "--corpus-dir",
        default="/Users/matthew.watt/tk/toke-corpus/corpus",
        help="Corpus root directory",
    )
    parser.add_argument(
        "--tkc",
        default="/Users/matthew.watt/tk/toke/tkc",
        help="Path to tkc compiler",
    )
    parser.add_argument(
        "--workers", type=int, default=8,
        help="Number of parallel workers",
    )
    parser.add_argument(
        "--target", type=int, default=3000,
        help="Target number of new compositions",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for application patterns",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't write files, just report counts",
    )
    args = parser.parse_args()

    global TKC_PATH
    TKC_PATH = args.tkc

    # Verify tkc exists
    if not Path(TKC_PATH).exists():
        logger.error("tkc not found at %s", TKC_PATH)
        sys.exit(1)

    # Load corpus
    entries = load_fundamental_entries(args.corpus_dir)
    if not entries:
        logger.error("No entries loaded")
        sys.exit(1)

    # Parse all function signatures
    all_funcs: list[FuncSig] = []
    for entry in entries:
        all_funcs.extend(parse_functions(entry))
    logger.info("Extracted %d function signatures", len(all_funcs))

    # Log type distribution
    type_counts: dict[str, int] = {}
    for f in all_funcs:
        type_counts[f.ret_type] = type_counts.get(f.ret_type, 0) + 1
    logger.info("Return type distribution: %s",
                dict(sorted(type_counts.items(), key=lambda x: -x[1])[:10]))

    single_param = [f for f in all_funcs if len(f.params) == 1]
    logger.info("Single-param functions (can be consumers): %d", len(single_param))

    # Load existing composition hashes for dedup
    existing_hashes = load_existing_hashes(args.corpus_dir)

    # Generate candidates from all three patterns
    # Allocate budget: ~50% chains, ~25% edge-cases, ~25% applications
    chain_target = int(args.target * 0.50)
    edge_target = int(args.target * 0.25)
    app_target = int(args.target * 0.25)

    logger.info("Generating chain candidates (target %d)...", chain_target)
    chains = generate_chains(all_funcs, existing_hashes, max_count=chain_target)

    logger.info("Generating edge-case candidates (target %d)...", edge_target)
    edges = generate_edge_cases(all_funcs, existing_hashes, max_count=edge_target)

    logger.info("Generating application candidates (target %d)...", app_target)
    apps = generate_applications(all_funcs, existing_hashes, max_count=app_target,
                                 seed=args.seed)

    all_candidates = chains + edges + apps
    logger.info("Total candidates to validate: %d", len(all_candidates))

    if not all_candidates:
        logger.error("No candidates generated")
        sys.exit(1)

    # Validate with multiprocessing
    logger.info("Validating with %d workers...", args.workers)
    work_items = [
        (i, src, ids, ctype)
        for i, (src, ids, ctype) in enumerate(all_candidates)
    ]

    passed = []
    failed = 0

    with Pool(processes=args.workers,
               initializer=_init_worker,
               initargs=(args.tkc,)) as pool:
        for result in pool.imap_unordered(_validate_candidate, work_items, chunksize=20):
            if result is None:
                failed += 1
                continue
            idx, src, source_ids, comp_type, ok = result
            if ok:
                passed.append((src, source_ids, comp_type))
            else:
                failed += 1

            total = len(passed) + failed
            if total % 500 == 0:
                logger.info("Validated %d/%d (passed: %d, failed: %d)",
                            total, len(all_candidates), len(passed), failed)

    logger.info("Validation complete: %d passed, %d failed out of %d",
                len(passed), failed, len(all_candidates))

    if args.dry_run:
        logger.info("DRY RUN — would write %d entries", len(passed))
        # Show breakdown
        by_type: dict[str, int] = {}
        for _, _, ctype in passed:
            by_type[ctype] = by_type.get(ctype, 0) + 1
        logger.info("Breakdown: %s", by_type)
        return

    # Write passing entries
    out_dir = Path(args.corpus_dir) / "phase2_deduplicated" / "COMPOSE-NEW"
    out_dir.mkdir(parents=True, exist_ok=True)

    existing_count = len(list(out_dir.glob("*.json")))
    written = 0

    for src, source_ids, comp_type in passed:
        seq = existing_count + written + 1
        path = write_entry(out_dir, src, source_ids, comp_type, seq)
        if path:
            written += 1

    # Summary
    by_type: dict[str, int] = {}
    for _, _, ctype in passed:
        by_type[ctype] = by_type.get(ctype, 0) + 1

    logger.info("=== SUMMARY ===")
    logger.info("Written: %d new compositions to %s", written, out_dir)
    logger.info("Breakdown by pattern: %s", by_type)
    logger.info("Pass rate: %.1f%%",
                len(passed) / len(all_candidates) * 100 if all_candidates else 0)


if __name__ == "__main__":
    main()
