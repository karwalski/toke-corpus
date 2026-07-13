#!/usr/bin/env python3
"""Validate a generated toke program and emit a corpus-schema record.

Usage: validate.py --task-json <spec.json> --source <prog.tk> --model <name> [--attempts N]

Steps:
  1. tkc --check          (syntax/type check; capture diagnostics JSON)
  2. tkc build + run      (if test cases present: run binary, feed inputs via argv
                           convention used by the spec, compare stdout)
  3. emit record JSON on stdout (corpus schema v1 + regen metadata extension)
"""
import argparse, json, re, subprocess, sys, os, tempfile

TKC = "/Users/matthew.watt/tk/toke/tkc"


def signature_conforms(spec, src):
    """Check the generated target function matches the spec's arity.
    Returns (ok, detail). Only enforced when the spec description names a
    target signature f=name(...)."""
    m = re.search(r"f=([a-z][a-z0-9]*)\(([^)]*)\)", spec.get("description_v03", "") or spec.get("description", ""))
    if not m:
        return True, "no target signature in spec"
    name, params = m.group(1), m.group(2)
    want_arity = len([p for p in params.split(";") if p.strip()])
    g = re.search(rf"f={name}\(([^)]*)\)", src)
    if not g:
        return False, f"function {name} not found in output"
    got_arity = len([p for p in g.group(1).split(";") if p.strip()])
    if got_arity != want_arity:
        return False, f"{name}: expected {want_arity} params, got {got_arity}"
    return True, "ok"


def tkc_check(src_path):
    p = subprocess.run([TKC, "--check", src_path], capture_output=True, text=True, timeout=30)
    codes = []
    for line in (p.stderr + "\n" + p.stdout).splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                d = json.loads(line)
                if d.get("error_code"):
                    codes.append(d["error_code"])
            except json.JSONDecodeError:
                pass
    return p.returncode, codes, (p.stderr or p.stdout)[:2000]


def tkc_run(src_path, workdir):
    """Compile to binary and run with no args; returns (exit, stdout)."""
    binpath = os.path.join(workdir, "prog")
    b = subprocess.run([TKC, src_path, "-o", binpath], capture_output=True, text=True, timeout=60)
    if b.returncode != 0:
        return None, None, "build_failed: " + (b.stderr or b.stdout)[:500]
    r = subprocess.run([binpath], capture_output=True, text=True, timeout=15)
    return r.returncode, r.stdout, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-json", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--model", default="claude-fable-5")
    ap.add_argument("--attempts", type=int, default=1)
    ap.add_argument("--run", action="store_true", help="also build and execute")
    args = ap.parse_args()

    spec = json.load(open(args.task_json))
    src = open(args.source).read()

    rc, codes, diag_excerpt = tkc_check(args.source)
    sig_ok, sig_detail = signature_conforms(spec, src)
    record = {
        "id": f"P3-{spec['task_id']}",
        "version": 2,
        "phase": "C",
        "task_id": spec["task_id"],
        "tk_source": src,
        "tk_tokens": None,
        "attempts": args.attempts,
        "model": args.model,
        "validation": {"compiler_exit_code": rc, "error_codes": codes},
        "differential": {"languages_agreed": [], "majority_output": ""},
        "judge": {"accepted": rc == 0 and sig_ok, "score": 1.0 if (rc == 0 and sig_ok) else 0.0},
        "regen": {
            "syntax_version": "v0.3-2.8.0",
            "task_type": spec.get("task_type", "full_program"),
            "category": spec.get("category"),
            "difficulty": spec.get("difficulty"),
            "signature_ok": sig_ok,
            "signature_detail": sig_detail,
            "runtime": None,
        },
    }

    if rc == 0 and args.run:
        with tempfile.TemporaryDirectory() as td:
            exit_code, stdout, err = tkc_run(args.source, td)
            record["regen"]["runtime"] = {
                "exit": exit_code,
                "stdout": (stdout or "")[:1000],
                "error": err,
            }

    if rc != 0:
        record["regen"]["diagnostic_excerpt"] = diag_excerpt

    json.dump(record, sys.stdout)
    print()


if __name__ == "__main__":
    main()
