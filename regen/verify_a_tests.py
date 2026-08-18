#!/usr/bin/env python3
"""Shared verifier for 129.7 A-category test files (worker self-check AND
main-thread banking both call verify() — banking re-executes the Python
reference, so expected values are never taken on faith).

File shape (gen/tests_<base>.json):
  {"base": "A-XXX-0000", "function": "name",
   "python_ref": "def f(...):\\n    ...",
   "test_cases": [{"inputs": [...], "expected": <value>}]}

Checks: schema, 3-5 cases, inputs arity/type vs the spec, ref executes, and
ref(inputs) == expected for every case (this is the ground truth).
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from driver import effective_input_types

CORPUS = "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04"
WD = os.path.join(CORPUS, "work", "a_tests_129")

_PY = {"str": str, "i64": int, "f64": float, "bool": bool,
       "@str": list, "@i64": list, "@f64": list, "@bool": list}


def verify(base, doc, spec):
    errs = []
    tcs = doc.get("test_cases") or []
    if not (3 <= len(tcs) <= 5):
        errs.append(f"need 3-5 test cases, got {len(tcs)}")
    ref = doc.get("python_ref") or ""
    if "def " not in ref:
        errs.append("python_ref must define a function")
    if errs:
        return errs
    in_types = effective_input_types(spec)
    ns = {}
    try:
        exec(ref, {"__builtins__": __builtins__}, ns)  # trusted-local audit tooling
    except Exception as e:
        return [f"python_ref does not execute: {e}"]
    fns = [v for v in ns.values() if callable(v)]
    if not fns:
        return ["python_ref defines no function"]
    fn = fns[-1]
    for k, tc in enumerate(tcs):
        ins = tc.get("inputs")
        if not isinstance(ins, list) or len(ins) != len(in_types):
            errs.append(f"case {k}: {len(ins or [])} inputs vs {len(in_types)} types")
            continue
        for v, t in zip(ins, in_types):
            want = _PY.get(t)
            if want is int and isinstance(v, bool):
                errs.append(f"case {k}: bool passed for {t}")
            elif want is float and isinstance(v, (int, bool)):
                pass  # ints acceptable for f64
            elif want and not isinstance(v, want):
                errs.append(f"case {k}: input {v!r} not {t}")
        try:
            got = fn(*ins)
        except Exception as e:
            errs.append(f"case {k}: ref raised {type(e).__name__}: {e}")
            continue
        exp = tc.get("expected")
        ok = (abs(got - exp) < 1e-6 if isinstance(exp, float) and isinstance(got, (int, float))
              and not isinstance(exp, bool) else got == exp)
        if not ok:
            errs.append(f"case {k}: ref({ins}) = {got!r} but expected {exp!r}")
    return errs


def main():
    base = sys.argv[1]
    path = os.path.join(WD, "gen", f"tests_{base}.json")
    if not os.path.exists(path):
        print(f"FAIL no file at {path}")
        sys.exit(1)
    try:
        doc = json.load(open(path))
    except json.JSONDecodeError as e:
        print(f"FAIL invalid JSON: {e}")
        sys.exit(1)
    spec = json.load(open(os.path.join(WD, "specs", base + ".json")))
    errs = verify(base, doc, spec)
    if errs:
        print("FAIL " + "; ".join(errs))
        sys.exit(1)
    print(f"PASS {len(doc['test_cases'])} cases verified against the reference")


if __name__ == "__main__":
    main()
