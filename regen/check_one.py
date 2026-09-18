#!/usr/bin/env python3
"""Worker-side check of one generated task: assemble, tkc --check, and (for
full_program specs with test cases) build + run + output compare.

Prints PASS, or FAIL <reason> followed by compiler/runtime diagnostics the
worker can repair from. Exit code 0 on pass, 1 on fail. Writes nothing to the
corpus, MANIFEST, or ledger — validation for acceptance happens separately in
run_shard.py validate.

Usage: check_one.py --workdir <shard workdir> --task-id <id>
"""
import argparse, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from assemble import assemble                      # noqa: E402
from validate import tkc_check, signature_conforms  # noqa: E402
from run_shard import run_test_cases               # noqa: E402
import validate                                    # noqa: E402  (131.39)
import tkc_pin                                     # noqa: E402  (131.39)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--task-id", required=True)
    args = ap.parse_args()

    spec = json.load(open(os.path.join(args.workdir, "specs", args.task_id + ".json")))
    raw = open(os.path.join(args.workdir, "gen", "out_" + args.task_id + ".tk")).read()
    src = assemble(spec, raw)

    # 131.39: exec a private copy of tkc so a `make` mid-check cannot swap the compiler
    pinned = tkc_pin.pin().install(validate)
    TKC = pinned.argv0
    with tempfile.NamedTemporaryFile("w", suffix=".tk", delete=False) as f:
        f.write(src)
        tkpath = f.name
    try:
        rc, codes, diag = tkc_check(tkpath)
        if rc != 0:
            print("FAIL compile:", ",".join(codes[:5]))
            print(diag[:1500])
            sys.exit(1)
        sig_ok, sig_detail = signature_conforms(spec, src)
        if not sig_ok:
            print("FAIL signature:", sig_detail)
            sys.exit(1)
        if spec.get("task_type") == "full_program" and spec.get("test_cases"):
            binpath = tkpath + ".bin"
            b = subprocess.run([TKC, tkpath, "-o", binpath], capture_output=True, text=True, timeout=90)
            if b.returncode != 0:
                print("FAIL build:", (b.stderr or b.stdout)[:800])
                sys.exit(1)
            rt = run_test_cases(binpath, spec)
            if os.path.exists(binpath):
                os.unlink(binpath)
            if rt and not rt["match"]:
                print("FAIL runtime:", rt.get("reason"))
                if rt.get("stdout") is not None:
                    print("--- your stdout:")
                    print(rt["stdout"])
                print("--- expected (one line per test case, in order):")
                from run_shard import render_expected
                for tc in spec["test_cases"]:
                    print(render_expected(tc.get("expected")))
                sys.exit(1)
        print("PASS")
    finally:
        if os.path.exists(tkpath):
            os.unlink(tkpath)
        pinned.close()


if __name__ == "__main__":
    main()
