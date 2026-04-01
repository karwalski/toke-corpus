"""D-APP: Generate multi-function application programs from Phase A+B corpus.

Creates programs with 3+ interacting functions forming mini-applications:
- Pipeline patterns: f(g(h(x)))
- Fan-out: use same input in multiple functions, combine results
- Accumulator: loop calling a function, accumulate results

Zero LLM cost — pure mechanical generation.
Output goes to corpus/phase_d/ with phase="D".
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
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FuncInfo:
    entry_id: str
    task_id: str
    fname: str
    params: list[tuple[str, str]]
    ret_type: str
    source: str
    func_body: str


_FUNC_RE = re.compile(r'(F=(\w+)\(([^)]*)\):(\w+(?:!\w+)?)\s*\{)', re.DOTALL)


def parse_functions(entry: dict) -> list[FuncInfo]:
    src = entry.get("tk_source", "")
    entry_id = entry.get("id", "")
    task_id = entry.get("task_id", "")
    results = []
    for m in _FUNC_RE.finditer(src):
        fname = m.group(2)
        raw_params = m.group(3)
        ret_type = m.group(4)
        params = []
        for p in raw_params.split(";"):
            p = p.strip()
            if ":" in p:
                pn, pt = p.split(":", 1)
                params.append((pn.strip(), pt.strip()))
        start = m.start()
        depth = 0
        i = m.end() - 1
        func_end = len(src)
        for j in range(i, len(src)):
            if src[j] == "{": depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    func_end = j + 1
                    if func_end < len(src) and src[func_end] == ";":
                        func_end += 1
                    break
        results.append(FuncInfo(
            entry_id=entry_id, task_id=task_id, fname=fname,
            params=params, ret_type=ret_type, source=src,
            func_body=src[start:func_end],
        ))
    return results


def _extract_funcs_no_module(source: str) -> str:
    m = re.match(r'M=\w+;', source)
    return source[m.end():].strip() if m else source.strip()


def _get_names(source: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r'(?:F|T)=(\w+)', source)}


def compact(src: str) -> str:
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


def validate_toke(source: str, tkc_path: str) -> bool:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toke", delete=False) as f:
        f.write(source)
        tmp = f.name
    try:
        r = subprocess.run([tkc_path, "--check", tmp], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except:
        return False
    finally:
        os.unlink(tmp)


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
# Application patterns
# ---------------------------------------------------------------------------

def pipeline_3(funcs: list[FuncInfo], rng: random.Random) -> str | None:
    """Chain 3 type-compatible functions: h(g(f(x)))."""
    # Find f->g->h chain where f.ret matches g's single param, etc.
    by_input = {}
    for f in funcs:
        if len(f.params) == 1:
            by_input.setdefault(f.params[0][1], []).append(f)

    by_ret = {}
    for f in funcs:
        r = f.ret_type.split("!")[0]
        by_ret.setdefault(r, []).append(f)

    # Try to find a 3-step chain
    for typ in by_ret:
        consumers = by_input.get(typ, [])
        if not consumers:
            continue
        for f1 in rng.sample(by_ret[typ], min(5, len(by_ret[typ]))):
            for f2 in rng.sample(consumers, min(5, len(consumers))):
                if f2.fname == f1.fname and f2.entry_id == f1.entry_id:
                    continue
                f2_ret = f2.ret_type.split("!")[0]
                final_consumers = by_input.get(f2_ret, [])
                for f3 in rng.sample(final_consumers, min(3, len(final_consumers))):
                    if f3.fname in (f1.fname, f2.fname):
                        continue
                    # Check no name collisions
                    names1 = _get_names(f1.source)
                    names2 = _get_names(f2.source)
                    names3 = _get_names(f3.source)
                    if names1 & names2 or names1 & names3 or names2 & names3:
                        continue

                    funcs1 = _extract_funcs_no_module(f1.source)
                    funcs2 = _extract_funcs_no_module(f2.source)
                    funcs3 = _extract_funcs_no_module(f3.source)

                    mod = f"{f3.fname}{f2.fname[0].upper()}{f1.fname[0].upper()}"[:20]
                    params_str = ";".join(f"{n}:{t}" for n, t in f1.params)
                    args_str = ";".join(n for n, _ in f1.params)
                    glue = f"pipeline"

                    src = (
                        f"M={mod};"
                        f"{funcs1}{funcs2}{funcs3}"
                        f"F={glue}({params_str}):{f3.ret_type}{{"
                        f"< {f3.fname}({f2.fname}({f1.fname}({args_str})))"
                        f"}};"
                    )
                    return src, [f1.entry_id, f2.entry_id, f3.entry_id], "pipeline3"
    return None


def fanout(funcs: list[FuncInfo], rng: random.Random) -> str | None:
    """Apply two functions to same input, combine results."""
    # Find two functions with same input type and same ret type
    by_sig = {}
    for f in funcs:
        if len(f.params) == 1:
            key = (f.params[0][1], f.ret_type)
            by_sig.setdefault(key, []).append(f)

    for (in_type, ret_type), group in by_sig.items():
        if len(group) < 2 or ret_type not in ("i64", "u64", "f64"):
            continue

        pairs = rng.sample(group, min(10, len(group)))
        for i in range(len(pairs)):
            for j in range(i+1, len(pairs)):
                f1, f2 = pairs[i], pairs[j]
                if f1.fname == f2.fname:
                    continue
                names1 = _get_names(f1.source)
                names2 = _get_names(f2.source)
                if names1 & names2:
                    continue

                funcs1 = _extract_funcs_no_module(f1.source)
                funcs2 = _extract_funcs_no_module(f2.source)

                mod = f"{f1.fname}{f2.fname[0].upper()}"[:20]
                param_name = f1.params[0][0]

                src = (
                    f"M={mod};"
                    f"{funcs1}{funcs2}"
                    f"F=combined({param_name}:{in_type}):{ret_type}{{"
                    f"let a={f1.fname}({param_name});"
                    f"let b={f2.fname}({param_name});"
                    f"<a+b"
                    f"}};"
                )
                return src, [f1.entry_id, f2.entry_id], "fanout"
    return None


def accumulator(funcs: list[FuncInfo], rng: random.Random) -> str | None:
    """Loop over array, apply function to each element, accumulate."""
    # Find f(scalar) -> scalar functions
    scalars = [f for f in funcs if len(f.params) == 1
               and f.params[0][1] in ("i64", "u64", "f64")
               and f.ret_type == f.params[0][1]]

    if not scalars:
        return None

    f = rng.choice(scalars)
    in_type = f.params[0][1]
    funcs_body = _extract_funcs_no_module(f.source)
    mod = f"acc{f.fname[0].upper()}{f.fname[1:]}"[:20]
    param_name = f.params[0][0]

    # Cast index to u64 for array access
    idx_cast = "i as u64" if in_type != "u64" else "i"
    len_cast = "arr.len as i64" if in_type != "u64" else "arr.len"

    src = (
        f"M={mod};"
        f"{funcs_body}"
        f"F=accumulate(arr:[{in_type}]):{in_type}{{"
        f"let r=mut.0{' as u64' if in_type == 'u64' else ''}{'' if in_type != 'f64' else '.0'};"
        f"lp(let i=0;i<{len_cast};i=i+1){{"
        f"r=r+{f.fname}(arr[{idx_cast}]);"
        f"}};<r"
        f"}};"
    )
    return src, [f.entry_id], "accumulator"


PATTERNS = [
    ("pipeline3", pipeline_3),
    ("fanout", fanout),
    ("accumulator", accumulator),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_entries(corpus_dir: str) -> list[dict]:
    entries = []
    for phase_dir in ["phase_a", "phase_b"]:
        d = Path(corpus_dir) / phase_dir
        if not d.exists():
            continue
        for p in d.rglob("*.json"):
            try:
                with open(p) as f:
                    e = json.load(f)
                if e.get("judge", {}).get("accepted", False):
                    entries.append(e)
            except:
                pass
    return entries


def run(corpus_dir: str, tkc_path: str, max_entries: int = 5000, seed: int = 42, dry_run: bool = False):
    logger.info("Loading corpus...")
    entries = load_entries(corpus_dir)
    logger.info("Loaded %d accepted entries", len(entries))

    all_funcs = []
    for e in entries:
        all_funcs.extend(parse_functions(e))
    logger.info("Extracted %d functions", len(all_funcs))

    out_dir = Path(corpus_dir) / "phase_d" / "D-APP"
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = len(list(out_dir.glob("*.json")))
    logger.info("Existing D-APP entries: %d", existing)

    rng = random.Random(seed)
    composed = 0
    validated = 0
    failed = 0
    seen = set()

    attempts = 0
    max_attempts = max_entries * 20  # Allow many attempts since random

    while composed < max_entries and attempts < max_attempts:
        attempts += 1
        pattern_name, pattern_fn = rng.choice(PATTERNS)

        result = pattern_fn(all_funcs, rng)
        if result is None:
            continue

        src, source_ids, comp_type = result
        src = compact(src)

        h = hashlib.sha256(src.encode()).hexdigest()[:16]
        if h in seen:
            continue
        seen.add(h)

        if not validate_toke(src, tkc_path):
            failed += 1
            continue

        validated += 1
        task_num = existing + composed + 1
        task_id = f"D-APP-{task_num:05d}"
        entry_id = f"D-{task_id}-{short_hash(src)}"

        entry = {
            "id": entry_id,
            "version": 1,
            "phase": "D",
            "task_id": task_id,
            "tk_source": src,
            "tk_tokens": count_tokens(src),
            "attempts": 0,
            "model": f"mechanical-{comp_type}",
            "validation": {"compiler_exit_code": 0, "error_codes": []},
            "differential": {"languages_agreed": [], "majority_output": ""},
            "judge": {"accepted": True, "score": 1.0},
            "application": {
                "source_entry_ids": source_ids,
                "pattern": comp_type,
            },
            "references": {
                "python_source": "", "python_tokens": 0,
                "c_source": "", "c_tokens": 0,
                "java_source": "", "java_tokens": 0,
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
            logger.info("Progress: %d composed, %d validated, %d failed, %d attempts",
                        composed, validated, failed, attempts)

    logger.info("Done: %d composed, %d validated, %d failed, %d attempts",
                composed, validated, failed, attempts)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", default="corpus")
    parser.add_argument("--tkc", default="bin/tkc")
    parser.add_argument("--max-entries", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.corpus_dir, args.tkc, args.max_entries, args.seed, args.dry_run)


if __name__ == "__main__":
    main()
