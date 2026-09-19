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

import tkc_pin  # noqa: E402  (131.39)
import driver as drv  # noqa: E402  (131.44: err rendering + paren-aware decls)

# 131.39: the pinned private copy when TOKE_TKC_PIN is set (a parent harness
# pinned once), else $TKC, else the ~/tk/toke/tkc symlink. Entry points pin.
TKC = tkc_pin.default_tkc()


def render_expected(val):
    """One expected stdout line for a full_program / run_test_cases test case
    (bool -> true/false, integral float -> int). 131.44 (d): an a_tests err
    marker (`{'err': 'NotFound'}` / `{'error': ...}`) renders as
    `err:<name>` (driver.render_err — `err:notfound`), the line the driver's
    `$err` arm prints; multi-line strs are split by the callers.
    run_shard.run_test_cases keeps a local copy of the pre-131.44 rule — the
    one-line hook is `from validate import render_expected` there."""
    name = drv.err_name(val)
    if name is not None:
        return drv.render_err(name)
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val)


_TARGET = re.compile(r"f=([a-z][a-z0-9]*)\(")


def target_name(spec):
    """Function name the spec's description mandates (`f=name(`), or None."""
    m = _TARGET.search(spec.get("description_v03", "") or spec.get("description", "") or "")
    return m.group(1) if m else None


def has_target_function(src, spec):
    """131.44 (b): a single_function record must DECLARE the target function.
    Returns (ok, detail). Paren-aware (driver.function_decls), so
    `f=flatten(p:@(@u64)):@u64` counts — the `[^)]*` regexes cannot see it.
    When the description names `f=name(`, that name must be declared; else at
    least one declaration that is neither main nor a domain-context stub.
    Not applicable (True) for other task types. Hook for run_shard.
    validate_one_gates (131.42 owns that file): after signature_conforms,
        if sig_ok and ttype == "single_function": sig_ok, sig_detail = has_target_function(src, spec)
    """
    if spec.get("task_type", "full_program") != "single_function":
        return True, "n/a: " + spec.get("task_type", "full_program")
    decls = drv.function_decls(src)
    names = [d["name"] for d in decls]
    want = target_name(spec)
    if want:
        if want in names:
            return True, "ok"
        return False, f"target function {want} not declared" + \
            (f" (declared: {','.join(n for n in names if n != 'main')})" if names else " (no functions)")
    stubs = drv._stub_names(spec)
    real = [n for n in names if n != "main" and n not in stubs]
    if real:
        return True, "ok (unnamed target: " + real[-1] + ")"
    return False, "no target function declared (no f= besides main/stubs)"


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


# ------------------------------------------------ 131.42: return-type gate ---
# 131.40 found 254 A-ERR records that "pass" by returning the a_tests err
# marker as a hard-coded `str` (`<"{'err': 'EmptyCollection'}"`) instead of
# the spec's `T!Err` union: the arity-only signature gate above never looked
# at the return type. `return_type_conforms` is the gate: an error-union spec
# return MUST be declared as an error union (hard); any other spec/declared
# difference is a SOFT flag (`return_type_mismatch`) until 131.43 decides the
# type-aware signature gate + its widening allowlist.

# 131.43 fills this: normalised spec return -> set of normalised declared
# returns accepted as equal (e.g. widening). Empty = exact match only.
RETURN_TYPE_ALLOWLIST = {}

def _scan_params(text, start):
    """Index just past the `)` that closes the parameter list opened at
    text[start] == '(' (depth-aware: `@(i64)` params), or -1."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _scan_type(text, start, stop_at_space):
    """Type text from text[start] up to the first depth-0 `{` (or whitespace
    when stop_at_space — the spec description continues in prose)."""
    depth, out = 0, []
    for ch in text[start:]:
        if depth == 0 and (ch == "{" or (stop_at_space and ch.isspace())):
            break
        depth += (ch == "(") - (ch == ")")
        out.append(ch)
    return "".join(out).strip().rstrip(".,;")


def _sig_return(text, name, stop_at_space):
    m = re.search(rf"f={re.escape(name)}\(", text)
    if not m:
        return None
    end = _scan_params(text, m.end() - 1)
    if end < 0 or end >= len(text) or text[end] != ":":
        return None
    t = _scan_type(text, end + 1, stop_at_space)
    return t or None


def spec_return_type(spec):
    """The spec's return type: the f=sig in the description (v03 spelling
    first — authoritative like driver.sig_input_types), else the
    output_type_v03 / output_type field. None when the spec has neither.
    (`target_name` above is 131.44's — one definition, shared.)"""
    for key in ("description_v03", "description"):
        name = target_name({key: spec.get(key, "")})
        if name:
            t = _sig_return(spec.get(key, ""), name, stop_at_space=True)
            if t:
                return t
    return spec.get("output_type_v03") or spec.get("output_type") or None


def declared_return_type(src, name):
    """Return type the function `name` DECLARES in src (raw text, e.g.
    `i64!$parseerr`, `@(i64)!$lookuperr`), or None when not found/unparsed."""
    return _sig_return(src, name, stop_at_space=False)


def _norm_arr(t):
    if t.startswith("@(") and t.endswith(")") and _scan_params(t, 1) == len(t):
        return "@" + _norm_arr(t[2:-1])
    return t


def norm_return_type(t):
    """Comparison form: whitespace/`$` dropped, lower-cased, `@(T)` -> `@T` at
    every level on both sides of `!` (spec `@(i64)!LookupErr` and declared
    `@(i64)!$lookuperr` both -> `@i64!lookuperr`)."""
    if t is None:
        return None
    t = re.sub(r"\s+", "", t).replace("$", "").lower()
    parts = t.split("!", 1)
    parts = [_norm_arr(p) for p in parts]
    return "!".join(parts)


def is_error_union(t):
    return bool(t) and "!" in t


def return_type_conforms(spec, src):
    """131.42 gate. Returns (hard_ok, detail, soft_mismatch, declared).
    hard_ok False only when the spec return is an error union and the
    declared one is not. soft_mismatch is a string when declared != spec
    (normalised, modulo RETURN_TYPE_ALLOWLIST) and the hard case does not
    apply — recorded, not enforced (131.43). declared is None when the spec
    names no target / the function is absent (gate not applicable)."""
    name = target_name(spec)
    if not name:
        return True, "no target signature in spec", None, None
    want = spec_return_type(spec)
    if not want:
        return True, "no spec return type", None, None
    got = declared_return_type(src, name)
    if got is None:
        return True, f"function {name} not found / return type unparsed", None, None
    if is_error_union(want) and not is_error_union(got):
        return False, f"{name} declared `{got}`, spec `{want}` requires an error union", None, got
    # second gaming shape (131.42): the named target is a correct union but a
    # trailing same-arity wrapper (`showvalidate`) returning the marker `str`
    # is what driver.find_target (last matching decl) actually executes
    callee = drv.find_target(spec, src)
    if is_error_union(want) and callee and callee != name:
        cgot = declared_return_type(src, callee)
        if cgot is not None and not is_error_union(cgot):
            return (False, f"driver callee `{callee}` declared `{cgot}` (target {name} is `{got}`), "
                           f"spec `{want}` requires an error union", None, got)
    nw, ng = norm_return_type(want), norm_return_type(got)
    if nw != ng and ng not in RETURN_TYPE_ALLOWLIST.get(nw, ()):
        return True, "ok (soft mismatch)", f"{name} declared `{got}`, spec `{want}`", got
    return True, "ok", None, got


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


# ---------------------------------------------------------------------------
# 131.18: stdin_program execution core (shared by run_shard.validate_one,
# audit.audit_one and ingest_library). Library manifests (toke-test-programs
# results/library/*.json) drive a program by stdin and compare the WHOLE stdout
# against expected_output, one execution per test case. Comparison is exact
# modulo trailing whitespace per line and leading/trailing blank lines (the
# verify-one.py / audit_library.py `norm` rule, so the 129.3 1,583/1,583 result
# stays comparable). Tightened 129.6 gates apply per case: exit 0, no extra
# lines (implied by whole-output comparison), multi-line expecteds whole.
# ---------------------------------------------------------------------------
STDIN_CASE_TIMEOUT = 10
FIXTURE_ROOT = "/tmp"


def norm_stdout(s):
    return "\n".join(l.rstrip() for l in (s or "").strip().splitlines())


def materialise_fixtures(fixtures, cwd):
    """Create the dirs/files a library test case needs (126.8 fixtures).
    Paths are absolute in the manifests (all under /tmp); relative paths are
    resolved against cwd. Refuses any absolute path outside FIXTURE_ROOT."""
    fx = fixtures or {}
    for d in fx.get("dirs") or []:
        os.makedirs(_fixture_path(d, cwd), exist_ok=True)
    for fpath, content in (fx.get("files") or {}).items():
        ap = _fixture_path(fpath, cwd)
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "wb") as f:
            f.write(content.encode("latin-1"))


def _fixture_path(p, cwd):
    if os.path.isabs(p):
        real = os.path.realpath(p)
        if not real.startswith(os.path.realpath(FIXTURE_ROOT) + os.sep):
            raise ValueError(f"fixture path outside {FIXTURE_ROOT}: {p}")
        return p
    return os.path.join(cwd, p)


def run_stdin_case(binpath, tc, cwd, timeout=STDIN_CASE_TIMEOUT):
    """Run the binary once with tc['input'] on stdin. Returns a per-case
    result: {match, exit, reason, stdout} — match requires exact normalised
    stdout equality AND exit 0; a crash (signal) or timeout is a mismatch."""
    try:
        materialise_fixtures(tc.get("fixtures"), cwd)
    except (ValueError, OSError) as e:
        return {"match": False, "exit": None, "reason": f"fixture: {e}"}
    try:
        r = subprocess.run([binpath], input=tc.get("input", "") or "",
                           capture_output=True, text=True, errors="replace",
                           timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"match": False, "exit": None, "reason": "timeout"}
    got, want = norm_stdout(r.stdout), norm_stdout(tc.get("expected_output", ""))
    if r.returncode < 0 or r.returncode >= 128:
        return {"match": False, "exit": r.returncode,
                "reason": f"crash sig {abs(r.returncode) % 128}", "stdout": r.stdout[:500]}
    if got != want:
        gl, wl = got.split("\n"), want.split("\n")
        why = ("output mismatch" if len(gl) == len(wl)
               else f"expected {len(wl)} lines, got {len(gl)}")
        return {"match": False, "exit": r.returncode, "reason": why, "stdout": r.stdout[:500]}
    if r.returncode != 0:
        return {"match": False, "exit": r.returncode, "reason": f"exit code {r.returncode}"}
    return {"match": True, "exit": 0, "reason": None}


def run_stdin_cases(binpath, spec, workdir, timeout=STDIN_CASE_TIMEOUT):
    """All test cases of a stdin_program spec, one execution each, fresh cwd per
    case. Returns the runtime dict recorded under regen.runtime (same top-level
    keys as run_shard.run_test_cases: ran/exit/match/reason, plus cases[])."""
    tcs = spec.get("test_cases") or []
    if not tcs:
        return None
    cases = []
    with tempfile.TemporaryDirectory(dir=workdir) as td:
        for i, tc in enumerate(tcs):
            cwd = os.path.join(td, f"t{i}")
            os.makedirs(cwd, exist_ok=True)
            res = run_stdin_case(binpath, tc, cwd, timeout)
            res["case"] = i
            cases.append(res)
    failed = [c for c in cases if not c["match"]]
    first = failed[0] if failed else None
    return {"ran": True, "mode": "stdin", "cases_total": len(cases),
            "cases_passed": len(cases) - len(failed),
            "exit": first["exit"] if first else 0,
            "match": not failed,
            "reason": f"{first['reason']} (case {first['case']})" if first else None,
            "cases": cases}


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

    pinned = tkc_pin.pin().install(sys.modules[__name__])   # 131.39
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
            "tkc_bin_sha": pinned.sha256,   # 131.39
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
    pinned.close()


if __name__ == "__main__":
    main()
