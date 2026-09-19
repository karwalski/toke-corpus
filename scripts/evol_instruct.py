#!/usr/bin/env python3
"""Evol-Instruct complexity escalation for toke.

Reads seed toke programs from the corpus and generates evolved variants
with increasing complexity across 5 toke-specific dimensions using
template-based AST-level transformations (no LLM needed).

Usage::

    python scripts/evol_instruct.py --max-seeds 5 --dry-run
    python scripts/evol_instruct.py --seed-dir corpus --output data/evol_instruct.jsonl
    python scripts/evol_instruct.py --dimensions type_constraints,mutable_state --max-seeds 10

Story 9.1.3 -- Evol-Instruct complexity escalation for toke.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Toke language context
# ---------------------------------------------------------------------------

TOKE_SYNTAX = """\
Toke syntax reference (Profile 1):
- M= (module), F= (function), T= (struct), I= (import)
- `let x=mut.0` for mutable bindings, `<value` for return
- `lp(init;cond;step){body}` for loops
- `[a;b;c]` for arrays, `arr[i]` for indexing
- `;` as statement separator (not terminator)
- 59-character set, no underscores in identifiers
"""

# ---------------------------------------------------------------------------
# Evolution dimensions
# ---------------------------------------------------------------------------

ALL_DIMENSIONS = [
    "type_constraints",
    "error_propagation",
    "multi_module",
    "algorithmic_complexity",
    "mutable_state",
]

# ---------------------------------------------------------------------------
# Helpers: parse toke source
# ---------------------------------------------------------------------------


def _extract_module_name(source: str) -> str:
    m = re.search(r"M=(\w+)", source)
    return m.group(1) if m else "prog"


def _extract_functions(source: str) -> list[dict[str, str]]:
    """Extract function signatures from source: name, params, return type."""
    funcs = []
    for m in re.finditer(
        r"F=(\w+)\(([^)]*)\)(?::(\w+(?:\[[\w;]+\])?))?", source
    ):
        funcs.append({
            "name": m.group(1),
            "params": m.group(2),
            "ret": m.group(3) or "i64",
        })
    return funcs


def _has_loops(source: str) -> bool:
    return "lp(" in source


def _has_mutability(source: str) -> bool:
    return "mut." in source


def _has_structs(source: str) -> bool:
    return "T=" in source


def _has_imports(source: str) -> bool:
    return "I=" in source


def _count_functions(source: str) -> int:
    return len(re.findall(r"\bF=", source))


def _func_body(source: str, func_name: str) -> str:
    """Try to extract the body of a named function (best effort)."""
    pat = re.compile(rf"F={re.escape(func_name)}\([^)]*\)(?::\w+(?:\[[\w;]+\])?)?\{{")
    m = pat.search(source)
    if not m:
        return ""
    start = m.end()
    depth = 1
    i = start
    while i < len(source) and depth > 0:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1
    return source[start : i - 1] if depth == 0 else source[start:]


# ---------------------------------------------------------------------------
# Evolution templates -- each returns a list of (level, evolved_source,
# description, prompt) tuples.
# ---------------------------------------------------------------------------


def _evolve_type_constraints(
    seed_id: str, source: str
) -> list[tuple[int, str, str, str]]:
    """Add type parameters, nested struct types, typed wrappers."""
    results = []
    mod = _extract_module_name(source)
    funcs = _extract_functions(source)

    # Level 1: Add a type alias struct wrapping the return
    if funcs:
        fn = funcs[0]
        wrapper_name = f"Result{fn['name'].capitalize()}"
        evolved = f"M={mod};T={wrapper_name}{{val:{fn['ret']}}};{source[len(f'M={mod};'):]}"
        results.append((
            1,
            evolved,
            f"Added struct wrapper '{wrapper_name}' around return type {fn['ret']}",
            f"Add a struct type '{wrapper_name}' with a 'val' field of type {fn['ret']} to wrap the return value of {fn['name']}.",
        ))

    # Level 2: Add a typed pair struct
    if funcs:
        fn = funcs[0]
        pair_struct = f"T=Pair{{fst:{fn['ret']};snd:{fn['ret']}}}"
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{pair_struct};{after_mod}"
        results.append((
            2,
            evolved,
            f"Added generic-like Pair struct with fst/snd fields of type {fn['ret']}",
            f"Add a Pair struct with two fields (fst, snd) both of type {fn['ret']}. "
            f"Modify {fn['name']} to return a Pair instead of a bare value.",
        ))

    # Level 3: Nested struct with inner type
    if funcs:
        fn = funcs[0]
        nested = f"T=Inner{{val:{fn['ret']}}};T=Outer{{inner:Inner;tag:i64}}"
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{nested};{after_mod}"
        results.append((
            3,
            evolved,
            "Added nested struct types: Inner containing a value, Outer containing Inner + tag",
            f"Define nested struct types Inner (val: {fn['ret']}) and Outer (inner: Inner, tag: i64). "
            f"Refactor {fn['name']} to work with the Outer struct.",
        ))

    # Level 4: Multiple typed helper functions
    if funcs:
        fn = funcs[0]
        helper = f"F=wrap(v:{fn['ret']}):Inner{{<Inner{{val=v}}}}"
        after_mod = source[len(f"M={mod};"):]
        nested = f"T=Inner{{val:{fn['ret']}}}"
        evolved = f"M={mod};{nested};{helper};{after_mod}"
        results.append((
            4,
            evolved,
            "Added typed helper function 'wrap' that constructs Inner struct",
            f"Add a typed helper function 'wrap' that takes a {fn['ret']} and returns an Inner struct. "
            f"Use it inside {fn['name']}.",
        ))

    # Level 5: Full type hierarchy with validation
    if funcs:
        fn = funcs[0]
        types = (
            f"T=Error{{code:i64;msg:i64}};"
            f"T=Ok{{val:{fn['ret']}}};"
            f"T=Result{{ok:Ok;err:Error;isOk:bool}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{types};{after_mod}"
        results.append((
            5,
            evolved,
            "Added Result/Ok/Error type hierarchy for typed error handling",
            f"Create a Result type hierarchy (Ok with val: {fn['ret']}, Error with code+msg, "
            f"Result with ok/err/isOk flag). Refactor {fn['name']} to return Result.",
        ))

    return results


def _evolve_error_propagation(
    seed_id: str, source: str
) -> list[tuple[int, str, str, str]]:
    """Add error handling paths, validation, edge cases."""
    results = []
    mod = _extract_module_name(source)
    funcs = _extract_functions(source)

    # Level 1: Add input length guard
    if funcs and "arr" in source:
        fn = funcs[0]
        guard = "if(arr.len=0){<0};"
        body = _func_body(source, fn["name"])
        if body:
            evolved = source.replace(body, guard + body, 1)
            results.append((
                1,
                evolved,
                "Added empty-array guard returning 0 for empty input",
                f"Add input validation: if the array is empty, return 0 immediately.",
            ))

    # Level 2: Add bounds checking
    if funcs and "[" in source:
        fn = funcs[0]
        check = "if(arr.len=0){<0};if(arr.len>1000){<0};"
        body = _func_body(source, fn["name"])
        if body:
            evolved = source.replace(body, check + body, 1)
            results.append((
                2,
                evolved,
                "Added bounds checking: empty array and max-length validation",
                f"Add bounds validation: return 0 if array is empty or exceeds 1000 elements.",
            ))

    # Level 3: Add negative value handling
    if funcs:
        fn = funcs[0]
        after_mod = source[len(f"M={mod};"):]
        validator = f"F=isValid(v:i64):bool{{if(v<0){{<false}};if(v>999999){{<false}};<true}}"
        evolved = f"M={mod};{validator};{after_mod}"
        results.append((
            3,
            evolved,
            "Added isValid helper that rejects negative or overly large values",
            f"Add a validation function 'isValid(v: i64): bool' that returns false "
            f"for negative values or values > 999999. Use it to filter inputs in {fn['name']}.",
        ))

    # Level 4: Multiple error paths with early returns
    if funcs:
        fn = funcs[0]
        errmod = (
            f"M={mod};"
            f"F=checkInput(arr:[i64]):i64{{"
            f"if(arr.len=0){{<1}};"
            f"lp(let i=0;i<arr.len;i=i+1){{if(arr[i]<0){{<2}}}};"
            f"<0}};"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = errmod + after_mod
        results.append((
            4,
            evolved,
            "Added checkInput with multiple error codes: 1=empty, 2=negative element",
            f"Add a 'checkInput' function returning error codes (0=ok, 1=empty, 2=has negative). "
            f"Call it at the start of {fn['name']} and return early on error.",
        ))

    # Level 5: Full error handling with error struct
    if funcs:
        fn = funcs[0]
        full = (
            f"M={mod};"
            f"T=Err{{code:i64}};"
            f"F=validate(arr:[i64]):Err{{"
            f"if(arr.len=0){{<Err{{code=1}}}};"
            f"if(arr.len>10000){{<Err{{code=2}}}};"
            f"lp(let i=0;i<arr.len;i=i+1){{"
            f"if(arr[i]<0){{<Err{{code=3}}}}}};"
            f"<Err{{code=0}}}};"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = full + after_mod
        results.append((
            5,
            evolved,
            "Added Err struct + validate() with 4 error paths: empty, overflow, negative, ok",
            f"Create an Err struct with a code field. Add validate(arr) returning Err with "
            f"code 0=ok, 1=empty, 2=too large, 3=negative element. Gate {fn['name']} on validation.",
        ))

    return results


def _evolve_multi_module(
    seed_id: str, source: str
) -> list[tuple[int, str, str, str]]:
    """Split single-file into multi-module with I= imports."""
    results = []
    mod = _extract_module_name(source)
    funcs = _extract_functions(source)

    # Level 1: Extract to a helper module import
    if funcs and len(funcs) >= 1:
        fn = funcs[0]
        helper_mod = f"M=helper;F=identity(x:{fn['ret']}):{fn['ret']}{{<x}}"
        main_mod = f"I=helper;{source}"
        combined = f"// --- helper.tk ---\n{helper_mod}\n// --- {mod}.tk ---\n{main_mod}"
        results.append((
            1,
            combined,
            f"Extracted identity helper to separate module 'helper' with I=helper import",
            f"Split into two modules: a 'helper' module with an identity function, "
            f"and the main module '{mod}' that imports it via I=helper.",
        ))

    # Level 2: Extract utility functions
    if funcs:
        fn = funcs[0]
        util_mod = (
            f"M=util;"
            f"F=max(a:i64;b:i64):i64{{if(a>b){{<a}};if(b>a){{<b}};<a}};"
            f"F=min(a:i64;b:i64):i64{{if(a<b){{<a}};if(b<a){{<b}};<a}}"
        )
        main_mod = f"I=util;{source}"
        combined = f"// --- util.tk ---\n{util_mod}\n// --- {mod}.tk ---\n{main_mod}"
        results.append((
            2,
            combined,
            "Extracted max/min utility functions to 'util' module with I=util import",
            f"Create a 'util' module with max(a,b) and min(a,b) helpers. "
            f"Import it in '{mod}' with I=util and use the helpers.",
        ))

    # Level 3: Three-module split
    if funcs:
        fn = funcs[0]
        types_mod = f"M=types;T=Config{{limit:i64;debug:bool}}"
        util_mod = f"M=util;I=types;F=clamp(v:i64;c:Config):i64{{if(v>c.limit){{<c.limit}};<v}}"
        main_mod = f"I=types;I=util;{source}"
        combined = (
            f"// --- types.tk ---\n{types_mod}\n"
            f"// --- util.tk ---\n{util_mod}\n"
            f"// --- {mod}.tk ---\n{main_mod}"
        )
        results.append((
            3,
            combined,
            "Three-module architecture: types, util (imports types), main (imports both)",
            f"Split into three modules: 'types' (Config struct), 'util' (clamp using Config), "
            f"and '{mod}' (imports both). Demonstrates transitive dependencies.",
        ))

    # Level 4: Four modules with layered imports
    if funcs:
        fn = funcs[0]
        types_mod = f"M=types;T=Pair{{fst:i64;snd:i64}};T=Triple{{a:i64;b:i64;c:i64}}"
        math_mod = f"M=math;I=types;F=addPair(p:Pair):i64{{<p.fst+p.snd}}"
        val_mod = f"M=validate;F=isPos(v:i64):bool{{if(v>0){{<true}};<false}}"
        main_mod = f"I=types;I=math;I=validate;{source}"
        combined = (
            f"// --- types.tk ---\n{types_mod}\n"
            f"// --- math.tk ---\n{math_mod}\n"
            f"// --- validate.tk ---\n{val_mod}\n"
            f"// --- {mod}.tk ---\n{main_mod}"
        )
        results.append((
            4,
            combined,
            "Four-module layered architecture: types, math, validate, main",
            f"Create four modules: 'types' (Pair, Triple structs), 'math' (addPair), "
            f"'validate' (isPos), and '{mod}' importing all three.",
        ))

    # Level 5: Full module DAG with re-exports
    if funcs:
        fn = funcs[0]
        base_mod = f"M=base;T=Error{{code:i64}};F=ok():Error{{<Error{{code=0}}}}"
        types_mod = f"M=types;I=base;T=Pair{{fst:i64;snd:i64}};T=Result{{val:i64;err:Error}}"
        ops_mod = (
            f"M=ops;I=base;I=types;"
            f"F=safeDiv(a:i64;b:i64):Result{{"
            f"if(b=0){{<Result{{val=0;err=Error{{code=1}}}}}};"
            f"<Result{{val=a/b;err=ok()}}}}"
        )
        util_mod = f"M=util;I=types;F=swap(p:Pair):Pair{{<Pair{{fst=p.snd;snd=p.fst}}}}"
        main_mod = f"I=base;I=types;I=ops;I=util;{source}"
        combined = (
            f"// --- base.tk ---\n{base_mod}\n"
            f"// --- types.tk ---\n{types_mod}\n"
            f"// --- ops.tk ---\n{ops_mod}\n"
            f"// --- util.tk ---\n{util_mod}\n"
            f"// --- {mod}.tk ---\n{main_mod}"
        )
        results.append((
            5,
            combined,
            "Five-module DAG: base, types (uses base), ops (uses both), util (uses types), main (all)",
            f"Build a five-module dependency graph: 'base' (Error type), 'types' (Pair, Result), "
            f"'ops' (safeDiv using Result), 'util' (swap Pair), and '{mod}' importing everything.",
        ))

    return results


def _evolve_algorithmic_complexity(
    seed_id: str, source: str
) -> list[tuple[int, str, str, str]]:
    """Increase algorithmic complexity: data structures, nested loops, etc."""
    results = []
    mod = _extract_module_name(source)
    funcs = _extract_functions(source)

    # Level 1: Add a linear scan helper
    if funcs:
        fn = funcs[0]
        helper = (
            f"F=findMax(arr:[i64]):i64{{"
            f"let mx=mut.arr[0];"
            f"lp(let i=1;i<arr.len;i=i+1){{if(arr[i]>mx){{mx=arr[i]}}}};"
            f"<mx}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{helper};{after_mod}"
        results.append((
            1,
            evolved,
            "Added O(n) linear scan findMax helper function",
            f"Add a findMax(arr) function that performs a linear scan O(n) to find the maximum element.",
        ))

    # Level 2: Add binary search
    if funcs:
        fn = funcs[0]
        bsearch = (
            f"F=binSearch(arr:[i64];target:i64):i64{{"
            f"let lo=mut.0;let hi=mut.arr.len;"
            f"lp(;lo<hi;){{"
            f"let mid=(lo+hi)/2;"
            f"if(arr[mid]=target){{<mid}};"
            f"if(arr[mid]<target){{lo=mid+1}}el{{hi=mid}}}};"
            f"<0-1}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{bsearch};{after_mod}"
        results.append((
            2,
            evolved,
            "Added O(log n) binary search function",
            f"Add binSearch(arr, target) implementing binary search O(log n) on a sorted array. "
            f"Returns index or -1 if not found.",
        ))

    # Level 3: Bubble sort (O(n^2))
    if funcs:
        fn = funcs[0]
        bsort = (
            f"F=bubbleSort(arr:[i64];n:i64):i64{{"
            f"lp(let i=0;i<n;i=i+1){{"
            f"lp(let j=0;j<n-i-1;j=j+1){{"
            f"if(arr[j]>arr[j+1]){{"
            f"let tmp=arr[j];arr[j]=arr[j+1];arr[j+1]=tmp"
            f"}}}}}};<0}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{bsort};{after_mod}"
        results.append((
            3,
            evolved,
            "Added O(n^2) bubble sort with nested loops and in-place swaps",
            f"Add bubbleSort(arr, n) with nested loops implementing O(n^2) sorting. "
            f"Demonstrates nested iteration and array mutation.",
        ))

    # Level 4: Hash-map-like bucketed lookup
    if funcs:
        fn = funcs[0]
        hashmap = (
            f"F=hashIdx(key:i64;sz:i64):i64{{"
            f"let h=mut.key;if(h<0){{h=0-h}};<h-(h/sz)*sz}};"
            f"F=insertBucket(buckets:[i64];key:i64;sz:i64):i64{{"
            f"let idx=hashIdx(key;sz);"
            f"lp(let probe=0;probe<sz;probe=probe+1){{"
            f"let pos=idx+probe;if(pos>sz-1){{pos=pos-sz}};"
            f"if(buckets[pos]=0){{buckets[pos]=key;<pos}}}};<0-1}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{hashmap};{after_mod}"
        results.append((
            4,
            evolved,
            "Added hash-bucketed insertion with linear probing (amortised O(1), worst O(n))",
            f"Add hash-based bucket insertion: hashIdx computes a bucket index, "
            f"insertBucket uses linear probing. Amortised O(1) lookup.",
        ))

    # Level 5: Merge sort (O(n log n)) with helper functions
    if funcs:
        fn = funcs[0]
        msort = (
            f"F=merge(arr:[i64];lo:i64;mid:i64;hi:i64;tmp:[i64]):i64{{"
            f"let i=mut.lo;let j=mut.mid;"
            f"lp(let k=lo;k<hi;k=k+1){{"
            f"if(i<mid){{if(j<hi){{if(arr[i]<arr[j]){{tmp[k]=arr[i];i=i+1}}"
            f"el{{tmp[k]=arr[j];j=j+1}}}}"
            f"el{{tmp[k]=arr[i];i=i+1}}}}"
            f"el{{tmp[k]=arr[j];j=j+1}}}};"
            f"lp(let k=lo;k<hi;k=k+1){{arr[k]=tmp[k]}};<0}};"
            f"F=msort(arr:[i64];lo:i64;hi:i64;tmp:[i64]):i64{{"
            f"if(hi-lo<2){{<0}};"
            f"let mid=(lo+hi)/2;"
            f"msort(arr;lo;mid;tmp);"
            f"msort(arr;mid;hi;tmp);"
            f"merge(arr;lo;mid;hi;tmp);<0}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{msort};{after_mod}"
        results.append((
            5,
            evolved,
            "Added O(n log n) merge sort with recursive splitting and merge helper",
            f"Add a full merge sort: merge() combines two halves, msort() recursively splits. "
            f"O(n log n) with temporary array. Demonstrates recursion + array mutation.",
        ))

    return results


def _evolve_mutable_state(
    seed_id: str, source: str
) -> list[tuple[int, str, str, str]]:
    """Add mutable bindings, in-place updates, stateful computations."""
    results = []
    mod = _extract_module_name(source)
    funcs = _extract_functions(source)

    # Level 1: Convert a pure value to mutable accumulator
    if funcs:
        fn = funcs[0]
        accum = (
            f"F=accumulate(arr:[i64]):i64{{"
            f"let acc=mut.0;"
            f"lp(let i=0;i<arr.len;i=i+1){{acc=acc+arr[i]}};"
            f"<acc}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{accum};{after_mod}"
        results.append((
            1,
            evolved,
            "Added mutable accumulator pattern: let acc=mut.0 with loop update",
            f"Add an accumulate(arr) function using a mutable accumulator (let acc=mut.0) "
            f"that sums elements in a loop.",
        ))

    # Level 2: Multiple mutable counters
    if funcs:
        fn = funcs[0]
        counters = (
            f"F=countPosNeg(arr:[i64]):i64{{"
            f"let pos=mut.0;let neg=mut.0;"
            f"lp(let i=0;i<arr.len;i=i+1){{"
            f"if(arr[i]>0){{pos=pos+1}};"
            f"if(arr[i]<0){{neg=neg+1}}}};"
            f"<pos-neg}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{counters};{after_mod}"
        results.append((
            2,
            evolved,
            "Added multiple mutable counters: pos and neg tracking in a single pass",
            f"Add countPosNeg(arr) with two mutable counters (pos, neg) updated conditionally "
            f"in a single pass. Returns the difference.",
        ))

    # Level 3: Stateful running average
    if funcs:
        fn = funcs[0]
        running = (
            f"F=runningAvg(arr:[i64];out:[i64]):i64{{"
            f"let sum=mut.0;let count=mut.0;"
            f"lp(let i=0;i<arr.len;i=i+1){{"
            f"sum=sum+arr[i];count=count+1;"
            f"out[i]=sum/count}};"
            f"<count}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{running};{after_mod}"
        results.append((
            3,
            evolved,
            "Added running average with mutable sum/count and in-place output array writes",
            f"Add runningAvg(arr, out) that computes a running average, writing each "
            f"intermediate result to an output array. Uses mutable sum and count.",
        ))

    # Level 4: State machine with mutable state variable
    if funcs:
        fn = funcs[0]
        fsm = (
            f"F=stateMachine(arr:[i64]):i64{{"
            f"let state=mut.0;let result=mut.0;"
            f"lp(let i=0;i<arr.len;i=i+1){{"
            f"if(state=0){{if(arr[i]>0){{state=1;result=result+arr[i]}}}}"
            f"el{{if(state=1){{if(arr[i]<0){{state=2}}el{{result=result+arr[i]}}}}"
            f"el{{if(arr[i]=0){{state=0}}}}}}}};"
            f"<result}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{fsm};{after_mod}"
        results.append((
            4,
            evolved,
            "Added 3-state finite state machine with mutable state and result variables",
            f"Add stateMachine(arr) implementing a 3-state FSM (0->1->2->0) with mutable "
            f"state tracking. Accumulates result only in state 1.",
        ))

    # Level 5: In-place array reversal + stateful partition
    if funcs:
        fn = funcs[0]
        inplace = (
            f"F=reverse(arr:[i64];n:i64):i64{{"
            f"let lo=mut.0;let hi=mut.n-1;"
            f"lp(;lo<hi;){{"
            f"let tmp=arr[lo];arr[lo]=arr[hi];arr[hi]=tmp;"
            f"lo=lo+1;hi=hi-1}};<0}};"
            f"F=partition(arr:[i64];n:i64;pivot:i64):i64{{"
            f"let lo=mut.0;let hi=mut.n-1;"
            f"lp(;lo<hi;){{"
            f"lp(;arr[lo]<pivot;){{lo=lo+1}};"
            f"lp(;arr[hi]>pivot;){{hi=hi-1}};"
            f"if(lo<hi){{"
            f"let tmp=arr[lo];arr[lo]=arr[hi];arr[hi]=tmp;"
            f"lo=lo+1;hi=hi-1}}}};<lo}}"
        )
        after_mod = source[len(f"M={mod};"):]
        evolved = f"M={mod};{inplace};{after_mod}"
        results.append((
            5,
            evolved,
            "Added in-place array reversal and Hoare partition with multiple mutable pointers",
            f"Add reverse(arr, n) for in-place reversal and partition(arr, n, pivot) using "
            f"Hoare's scheme. Both use multiple mutable index variables with convergent loops.",
        ))

    return results


# Map dimension names to evolution functions
EVOLUTION_FNS: dict[str, Any] = {
    "type_constraints": _evolve_type_constraints,
    "error_propagation": _evolve_error_propagation,
    "multi_module": _evolve_multi_module,
    "algorithmic_complexity": _evolve_algorithmic_complexity,
    "mutable_state": _evolve_mutable_state,
}


# ---------------------------------------------------------------------------
# Corpus reading (shared pattern with reverse_oss_instruct.py)
# ---------------------------------------------------------------------------


def iter_corpus_entries(
    corpus_dir: Path, max_seeds: int | None = None
) -> list[dict[str, Any]]:
    """Read corpus JSON files. Dedup by task_id, keeping first variant."""
    seen_tasks: set[str] = set()
    entries: list[dict[str, Any]] = []

    json_files = sorted(corpus_dir.rglob("*.json"))
    json_files = [
        f for f in json_files
        if f.name not in ("manifest.json", "schema.json")
    ]

    for path in json_files:
        if max_seeds is not None and len(entries) >= max_seeds:
            break
        try:
            with open(path) as fh:
                data = json.load(fh)
            if not data.get("tk_source"):
                continue
            task_id = data.get("task_id", data.get("id", "unknown"))
            # Keep only one variant per task_id for seeds
            if task_id in seen_tasks:
                continue
            seen_tasks.add(task_id)
            entries.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping {path}: {exc}", file=sys.stderr)

    return entries


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def _make_variant_id(seed_id: str, dimension: str, level: int) -> str:
    """Deterministic variant ID from seed + dimension + level."""
    raw = f"{seed_id}:{dimension}:{level}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"evol-{seed_id}-{dimension[:4]}-L{level}-{h}"


def evolve_seed(
    entry: dict[str, Any],
    dimensions: list[str],
) -> list[dict[str, Any]]:
    """Generate evolved variants for a single seed entry."""
    seed_id = entry.get("task_id", entry.get("id", "unknown"))
    source = entry["tk_source"]
    variants: list[dict[str, Any]] = []

    for dim in dimensions:
        fn = EVOLUTION_FNS.get(dim)
        if fn is None:
            continue
        evolutions = fn(seed_id, source)
        for level, evolved_source, description, prompt in evolutions:
            vid = _make_variant_id(seed_id, dim, level)
            variants.append({
                "variant_id": vid,
                "seed_id": seed_id,
                "dimension": dim,
                "level": level,
                "evolved_source": evolved_source,
                "evolution_description": description,
                "prompt": prompt,
                "seed_source": source,
            })

    return variants


def run_pipeline(args: argparse.Namespace) -> int:
    """Run the Evol-Instruct pipeline. Returns exit code."""
    corpus_dir = Path(args.seed_dir).resolve()
    if not corpus_dir.is_dir():
        print(f"ERROR: seed directory not found: {corpus_dir}", file=sys.stderr)
        return 1

    output_path = Path(args.output)

    # Parse dimensions
    if args.dimensions == "all":
        dimensions = list(ALL_DIMENSIONS)
    else:
        dimensions = [d.strip() for d in args.dimensions.split(",")]
        invalid = [d for d in dimensions if d not in ALL_DIMENSIONS]
        if invalid:
            print(
                f"ERROR: unknown dimensions: {', '.join(invalid)}. "
                f"Valid: {', '.join(ALL_DIMENSIONS)}",
                file=sys.stderr,
            )
            return 1

    print(f"Reading seeds from {corpus_dir} ...", file=sys.stderr)
    entries = iter_corpus_entries(corpus_dir, max_seeds=args.max_seeds)
    print(f"Found {len(entries)} seed programs.", file=sys.stderr)
    print(f"Dimensions: {', '.join(dimensions)}", file=sys.stderr)

    if not entries:
        print("WARNING: no seed programs found.", file=sys.stderr)
        return 0

    all_variants: list[dict[str, Any]] = []

    for entry in entries:
        variants = evolve_seed(entry, dimensions)
        all_variants.extend(variants)

    # Stats
    dim_counts: dict[str, int] = {}
    level_counts: dict[int, int] = {}
    for v in all_variants:
        dim_counts[v["dimension"]] = dim_counts.get(v["dimension"], 0) + 1
        level_counts[v["level"]] = level_counts.get(v["level"], 0) + 1

    print(f"\nGenerated {len(all_variants)} evolved variants:", file=sys.stderr)
    for dim in dimensions:
        print(f"  {dim}: {dim_counts.get(dim, 0)}", file=sys.stderr)
    for lvl in sorted(level_counts):
        print(f"  Level {lvl}: {level_counts[lvl]}", file=sys.stderr)

    if args.dry_run:
        print("\n--- DRY RUN: sample output ---", file=sys.stderr)
        for v in all_variants[:3]:
            print(json.dumps(v, indent=2, ensure_ascii=False), file=sys.stderr)
        print(
            f"\n[dry-run] Would write {len(all_variants)} records to {output_path}",
            file=sys.stderr,
        )
        # Still validate structure
        errors = 0
        for v in all_variants:
            for field in (
                "variant_id", "seed_id", "dimension", "level",
                "evolved_source", "evolution_description", "prompt",
            ):
                if field not in v:
                    print(f"VALIDATION FAIL: missing {field} in {v.get('variant_id', '?')}", file=sys.stderr)
                    errors += 1
            if v.get("level", 0) not in range(1, 6):
                print(f"VALIDATION FAIL: level {v.get('level')} not in 1-5", file=sys.stderr)
                errors += 1
            if v.get("dimension") not in ALL_DIMENSIONS:
                print(f"VALIDATION FAIL: dimension {v.get('dimension')}", file=sys.stderr)
                errors += 1
        if errors:
            print(f"\n{errors} validation errors found.", file=sys.stderr)
            return 1
        print(f"All {len(all_variants)} records pass structural validation.", file=sys.stderr)
        return 0

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        for v in all_variants:
            fh.write(json.dumps(v, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(all_variants)} records to {output_path}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evol-Instruct complexity escalation for toke (Story 9.1.3)",
    )
    parser.add_argument(
        "--seed-dir",
        default="corpus",
        help="Directory containing seed toke programs (default: corpus/)",
    )
    parser.add_argument(
        "--output",
        default="data/evol_instruct.jsonl",
        help="Output JSONL file (default: data/evol_instruct.jsonl)",
    )
    parser.add_argument(
        "--dimensions",
        default="all",
        help=(
            "Comma-separated dimensions to evolve (default: all). "
            f"Options: {', '.join(ALL_DIMENSIONS)}"
        ),
    )
    parser.add_argument(
        "--max-seeds",
        type=int,
        default=None,
        help="Maximum number of seed programs to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print sample output and validate structure without writing",
    )
    args = parser.parse_args()
    return run_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
