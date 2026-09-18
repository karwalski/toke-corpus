#!/usr/bin/env python3
"""Differential checker for Epic 131 rewrites (story 131.14).

Given the ORIGINAL source of a corpus record and a CANDIDATE (the source after
`tkc --lint --fix` or an agent rewrite), build both with `tkc -O2 --allow-all`
and require byte-identical stdout + exit status on:

  (a) the spec's test cases
        full_program / migrate_fix : the binary as-is (its main() holds the cases)
        single_function            : driver.append_main() synthesised main
        stdin_program              : one run per case, input on stdin
  (b) single_function only: N_GENERATED (20) extra typed inputs per signature,
      derived from driver.effective_input_types with a seed fixed per task_id
      (ints incl. 0/negatives/large, empty/1-element/duplicate arrays,
      empty/whitespace/unicode strings, bools). The generated main prints a
      `#k` marker line before call k so outputs attribute to inputs even when a
      call returns an array (one line per element) or aborts the run.
  (c) where audit/a_tests/<base>.json carries a `python_ref` whose signature
      matches the spec, the reference runs on the same generated inputs and
      the candidate must match it too (skipped with a logged reason when the
      signature cannot be mapped). A mismatch the ORIGINAL shares (e.g. i64
      wrap-around vs Python bigints) is recorded as `mismatch_shared`; only a
      candidate-only mismatch fails the check.

Verdict: `identical` | `diverged` (first differing input + both outputs) |
`unverifiable` (reason). Every binary and temp file is removed on exit; each
run has a timeout. Wall time + peak RSS of every run come from os.wait4
rusage (the bench/patterns/run_patterns.py method), so a caller can reuse the
prepared programs for the Tier-1 perf gate (pattern_autofix.py does).

Library: prepare_program() / compare() / diff_check() / differential(). CLI:
  diff_check.py --spec spec.json --orig a.tk --cand b.tk [--a-test A-XXX-0001.json]
"""
import argparse, hashlib, json, os, random, re, select, shutil, signal, subprocess, sys
import tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import driver as drv                                    # noqa: E402
import validate                                         # noqa: E402  (131.39: rebind its TKC)
import tkc_pin                                          # noqa: E402  (131.39)
from validate import materialise_fixtures               # noqa: E402

# 131.39: $TOKE_TKC_PIN (a harness's pinned copy), else $TKC, else ~/tk/toke/tkc;
# main() pins its own copy. Every compare() result carries `tkc_bin_sha`.
TKC = tkc_pin.default_tkc()
BUILD_FLAGS = ["-O2", "--allow-all"]
BUILD_TIMEOUT = 90
RUN_TIMEOUT = 15
REF_TIMEOUT = 20
N_GENERATED = 20
N_PLAIN = 12            # generated inputs 0..N_PLAIN-1 are "plain" values; the rest edge cases
FLOAT_TOL = 1e-6
STDOUT_KEEP = 2000      # bytes of each side kept in a divergence report

_PY_TYPES = {"i64": int, "u64": int, "i32": int, "u32": int, "str": str, "bool": bool,
             "f64": float, "f32": float}
_WORDS = ["a", "b", "z", "ab", "abc", "hello", "world", "Hello World", "foo bar", "x1",
          "42", "toke", "banana", "apple", "Cat", "dog dog", "aA", "the quick brown fox",
          "12 34", "level", "racecar", "a-b_c", "key=value", "1,2,3", "MiXeD", "end."]
_EDGE_STR = ["", " ", "   ", "\t", "  padded  ", "héllo wörld", "日本語", "emoji 🎉 ok",
             "x" * 40, "a,b,,c", "0", "-42", "tab\there", "line1\nline2", "ÀÉÎõü", "\"q\""]
_EDGE_INT = [0, -1, 1, -7, 2, 10, 255, 1000, 10 ** 9, -(10 ** 9), 2 ** 40, -(2 ** 40), 65535]
_EDGE_UINT = [0, 1, 2, 7, 255, 1000, 10 ** 9, 2 ** 40, 65535]
_EDGE_FLT = [0.0, -1.5, 1.0, 0.1, 1e6, -1e-3, 2.5, 100.25]


# --------------------------------------------------------------- running ---
def run_measured(binary, stdin_text="", timeout=RUN_TIMEOUT, cwd=None, env=None):
    """Run `binary` once. Returns {stdout (bytes), stderr (bytes), exit, wall_ms,
    rss_kb, timed_out}. RSS = ru_maxrss from os.wait4 (bytes on macOS, KB on
    Linux, normalised to KB). A timeout SIGKILLs the child (exit -9)."""
    t0 = time.perf_counter()
    p = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, cwd=cwd, env=env)

    def feed():
        try:
            if stdin_text:
                p.stdin.write(stdin_text.encode("utf-8", "replace"))
            p.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    threading.Thread(target=feed, daemon=True).start()
    bufs = {p.stdout.fileno(): [], p.stderr.fileno(): []}
    open_fds = set(bufs)
    timed_out = False
    deadline = t0 + timeout
    while open_fds:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            try:
                os.kill(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            timed_out = True
            break
        ready, _, _ = select.select(list(open_fds), [], [], remaining)
        for fd in ready:
            data = os.read(fd, 65536)
            if data:
                bufs[fd].append(data)
            else:
                open_fds.discard(fd)
    try:
        _, status, ru = os.wait4(p.pid, 0)
        rss = ru.ru_maxrss
        exit_code = os.waitstatus_to_exitcode(status) if not timed_out else -9
    except ChildProcessError:
        p.wait()
        exit_code, rss = p.returncode, 0
    p.returncode = exit_code
    wall_ms = (time.perf_counter() - t0) * 1000.0
    if sys.platform == "darwin":
        rss_kb = rss / 1024.0
    else:
        rss_kb = float(rss)
    out_fd, err_fd = p.stdout.fileno(), p.stderr.fileno()
    out, err = b"".join(bufs[out_fd]), b"".join(bufs[err_fd])
    for f in (p.stdout, p.stderr):
        try:
            f.close()
        except OSError:
            pass
    return {"stdout": out, "stderr": err,
            "exit": exit_code, "wall_ms": wall_ms, "rss_kb": rss_kb, "timed_out": timed_out}


def build(src, bin_path, workdir, tag, flags=None):
    """Write src to <workdir>/<tag>.tk, build it to bin_path. Returns (ok, detail).
    The .tk is removed afterwards; bin_path is left for the caller to delete."""
    tk = os.path.join(workdir, tag + ".tk")
    with open(tk, "w") as f:
        f.write(src)
    try:
        r = subprocess.run([TKC, tk, "-o", bin_path] + list(flags if flags is not None else BUILD_FLAGS),
                           capture_output=True, text=True, errors="replace", timeout=BUILD_TIMEOUT)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout)[:800]
        return True, None
    except subprocess.TimeoutExpired:
        return False, "build timeout"
    finally:
        if os.path.exists(tk):
            os.unlink(tk)


# ------------------------------------------------------ generated inputs ---
def seed_for(task_id):
    return int(hashlib.sha256(f"131.14:{task_id}".encode()).hexdigest()[:8], 16)


def _gen_scalar(rng, t, edge):
    if t in ("i64", "i32"):
        return rng.choice(_EDGE_INT) if edge else rng.randint(-5, 20)
    if t in ("u64", "u32"):
        return rng.choice(_EDGE_UINT) if edge else rng.randint(0, 20)
    if t in ("f64", "f32"):
        return rng.choice(_EDGE_FLT) if edge else round(rng.uniform(-10, 10), 2)
    if t == "str":
        return rng.choice(_EDGE_STR) if edge else rng.choice(_WORDS)
    if t == "bool":
        return rng.random() < 0.5
    raise ValueError(t)


def _gen_array(rng, elem, edge):
    if edge:
        shape = rng.choice(["empty", "one", "dup", "long", "neg", "desc"])
        if shape == "empty":
            return []
        if shape == "one":
            return [_gen_scalar(rng, elem, False)]
        if shape == "dup":
            v = _gen_scalar(rng, elem, False)
            return [v, v, _gen_scalar(rng, elem, False), v]
        if shape == "long":
            return [_gen_scalar(rng, elem, False) for _ in range(25)]
        if shape == "neg" and elem in ("i64", "i32", "f64", "f32"):
            return [-abs(_gen_scalar(rng, elem, False)) for _ in range(rng.randint(2, 6))]
        vals = [_gen_scalar(rng, elem, False) for _ in range(rng.randint(2, 6))]
        try:
            return sorted(vals, reverse=True)
        except TypeError:
            return vals
    return [_gen_scalar(rng, elem, False) for _ in range(rng.randint(3, 8))]


def supported_type(t):
    return t in _PY_TYPES or (t.startswith("@") and t[1:] in _PY_TYPES)


def generate_inputs(spec, task_id, n=N_GENERATED):
    """(inputs, None) — n input tuples for the spec's signature, or (None, reason)
    when a parameter type cannot be generated. Deterministic per task_id.
    Tuples 0..N_PLAIN-1 are plain values, the rest draw from the edge lists,
    so a crash on an edge case truncates as little as possible."""
    types = drv.effective_input_types(spec)
    if not types:
        return None, "no input types"
    bad = [t for t in types if not supported_type(t)]
    if bad:
        return None, f"unsupported input type {bad[0]}"
    rng = random.Random(seed_for(task_id))
    out = []
    for k in range(n):
        edge = k >= N_PLAIN
        tup = []
        for t in types:
            if t.startswith("@"):
                tup.append(_gen_array(rng, t[1:], edge))
            else:
                tup.append(_gen_scalar(rng, t, edge))
        out.append(tup)
    return out, None


def lit(v, t):
    """driver.lit plus a cast for unsigned scalars: an integer literal types as
    i64, so `@(6;9)` does not pass for `@u64` (E4031); `(6 as u64)` does."""
    if t.startswith("@"):
        if not isinstance(v, list):
            raise ValueError(f"array type {t} but non-list input {v!r}")
        return "@(" + ";".join(lit(x, t[1:]) for x in v) + ")"
    if t in ("u64", "u32"):
        return f"({int(v)} as {t})"
    return drv.lit(v, t)


def gen_main(spec, src, inputs):
    """single_function module + real-bodied stubs + a main() that prints `#k`
    then calls the target on inputs[k], rendering results exactly as
    driver.append_main does. Returns (source, error)."""
    if "f=main(" in src:
        return None, "module already has main"
    types = drv.effective_input_types(spec)
    ret = spec.get("output_type_v03") or spec.get("output_type") or ""
    src2, err = drv._swap_real_stubs(spec, src)
    if err:
        return None, err
    target = drv.find_target(spec, src2)
    if not target:
        return None, "no target function found"
    calls = []
    for k, ins in enumerate(inputs):
        try:
            args = ";".join(lit(v, t) for v, t in zip(ins, types))
        except (ValueError, TypeError) as e:
            return None, f"input {k}: {e}"
        call = f"{target}({args})"
        calls.append(f'io.println("#{k}")')
        if ret == "@str":
            calls.append(f"let r{k}={call};"
                         f"lp(let x{k}=0;x{k}<r{k}.len;x{k}=x{k}+1){{io.println(r{k}.get(x{k}))}}")
        elif ret.startswith("@"):
            calls.append(f"let r{k}={call};"
                         f'lp(let x{k}=0;x{k}<r{k}.len;x{k}=x{k}+1){{io.println("\\(r{k}.get(x{k}))")}}')
        elif ret == "str":
            calls.append(f"let r{k}={call};io.println(r{k})")
        else:
            calls.append(f'io.println("\\({call})")')
    return ensure_imports(src2.rstrip() + "\nf=main():i64{" + ";".join(calls) + ";<0};\n"), None


def split_marked(stdout_bytes, n):
    """stdout of a gen_main binary -> list of n output-line lists (None for
    inputs the run never reached, e.g. after an abort)."""
    text = stdout_bytes.decode("utf-8", "replace")
    outs = [None] * n
    cur = None
    for line in text.split("\n"):
        if line.startswith("#") and line[1:].isdigit() and int(line[1:]) < n:
            cur = int(line[1:])
            outs[cur] = []
        elif cur is not None:
            outs[cur].append(line)
    for k in range(n):
        if outs[k] and outs[k][-1] == "":        # trailing split artefact
            outs[k] = outs[k][:-1]
    return outs


# ------------------------------------------------------------ python ref ---
_REF_RUNNER = r'''
import json, sys, copy, math
payload = json.load(sys.stdin)
ns = {}
try:
    exec(payload["ref"], {"__builtins__": __builtins__}, ns)
except Exception as e:
    print(json.dumps({"error": f"ref does not execute: {type(e).__name__}: {e}"})); sys.exit(0)
fns = [v for v in ns.values() if callable(v)]
if not fns:
    print(json.dumps({"error": "ref defines no function"})); sys.exit(0)
fn = fns[-1]
def conv(v):
    if isinstance(v, (bool, int, str)) or v is None:
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else repr(v)
    if isinstance(v, (list, tuple)):
        return [conv(x) for x in v]
    raise TypeError(type(v).__name__)
res = []
for ins in payload["inputs"]:
    try:
        v = fn(*copy.deepcopy(ins))
        res.append({"ok": True, "value": conv(v)})
    except Exception as e:
        res.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
print(json.dumps({"results": res}))
'''


def run_python_ref(ref_src, inputs, timeout=REF_TIMEOUT):
    """Run the a_tests python_ref on inputs in a subprocess. Returns
    ({"results": [{ok, value|error}]} | {"error": reason})."""
    try:
        r = subprocess.run([sys.executable, "-c", _REF_RUNNER],
                           input=json.dumps({"ref": ref_src, "inputs": inputs}),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "ref timeout"}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "ref runner failed: " + (r.stderr or r.stdout)[:300]}


def render_ref_lines(value):
    """Lines the driver would print for a value the ref returned, or None when
    the value has no driver rendering (dict/None/nested list)."""
    if value is None or isinstance(value, dict):
        return None
    if isinstance(value, list):
        out = []
        for x in value:
            if isinstance(x, (list, dict)) or x is None:
                return None
            out.extend(render_ref_lines(x))
        return out
    if isinstance(value, str):
        return value.split("\n")
    return [drv.render_out(value)]


def lines_equal(got, want):
    if len(got) != len(want):
        return False
    for g, w in zip(got, want):
        if g == w or g.strip() == w.strip():
            continue
        try:
            if abs(float(g) - float(w)) < FLOAT_TOL:
                continue
        except ValueError:
            pass
        return False
    return True


def ref_signature_ok(spec, a_test):
    """None when the python_ref can be applied to this spec, else the reason."""
    if not a_test or not a_test.get("python_ref"):
        return "no python_ref"
    types = [drv._norm_type(t) for t in drv.effective_input_types(spec)]
    a_types = [drv._norm_type(t) for t in (a_test.get("input_types") or [])]
    # a_tests carry the sampler-mangled spelling (`@(u64` for `@u64`, 129.7)
    a_types = [t[:-1] if t.endswith("(") else t.replace("@(", "@") for t in a_types]
    if a_types and a_types != types:
        return f"a_tests signature {a_test['input_types']} != spec {types}"
    ret = spec.get("output_type_v03") or spec.get("output_type") or ""
    if "!" in ret or ":" in ret or ret.startswith("@@") or ret == "":
        return f"unmapped return type {ret!r}"
    return None


# ------------------------------------------------------------ imports ---
_IMPORT_NEED = (("io", "i=io:std.io;"), ("s", "i=s:std.str;"))


def ensure_imports(src):
    """Driver-built sources need `io` (the generated main prints) and `s` (the
    real-bodied context stubs call std.str). A record whose harness prefix
    lost those imports (unused-import --fix, 129 repairs) still has to run:
    re-insert any missing import the source references, after the `m=` line.
    The record's own bytes are never banked from here — this only shapes the
    throwaway differential/perf binaries."""
    missing = [decl for alias, decl in _IMPORT_NEED
               if decl not in src and re.search(rf"(?<![\w.]){alias}\.\w", src)]
    if not missing:
        return src
    lines = src.split("\n", 1)
    if lines[0].startswith("m=") and len(lines) == 2:
        return lines[0] + "\n" + "\n".join(missing) + "\n" + lines[1]
    return "\n".join(missing) + "\n" + src


# --------------------------------------------------------------- program ---
class Program:
    """Built executables for one source: `test_bin` runs the spec's cases,
    `gen_bin` (single_function only) runs the generated inputs. `mode` is
    main | stdin | driver. `reason` explains a missing test_bin."""

    def __init__(self, tag):
        self.tag = tag
        self.mode = None
        self.test_bin = None
        self.gen_bin = None
        self.reason = None
        self.gen_reason = None
        self.stdin_cases = []
        self.n_gen = 0
        self.paths = []

    def cleanup(self):
        for p in self.paths:
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        self.paths = []


def prepare_program(src, spec, task_id, workdir, tag, gen_inputs=None):
    """Build the executables for src (never raises; see Program.reason)."""
    prog = Program(tag)
    ttype = spec.get("task_type", "full_program")
    if ttype == "single_function":
        prog.mode = "driver"
        if spec.get("test_cases"):
            dsrc, err = drv.append_main(spec, src)
            if err:
                prog.reason = "driver: " + err
            else:
                b = os.path.join(workdir, tag + ".test.bin")
                ok, detail = build(ensure_imports(dsrc), b, workdir, tag + ".test")
                prog.paths.append(b)
                if ok:
                    prog.test_bin = b
                else:
                    prog.reason = "driver build failed: " + (detail or "")[:200]
        else:
            prog.reason = "no spec test cases"
        if gen_inputs:
            gsrc, err = gen_main(spec, src, gen_inputs)
            if err:
                prog.gen_reason = "gen driver: " + err
            else:
                b = os.path.join(workdir, tag + ".gen.bin")
                ok, detail = build(gsrc, b, workdir, tag + ".gen")
                prog.paths.append(b)
                if ok:
                    prog.gen_bin = b
                    prog.n_gen = len(gen_inputs)
                else:
                    prog.gen_reason = "gen build failed: " + (detail or "")[:200]
        else:
            prog.gen_reason = "no generated inputs"
        return prog
    if ttype == "stdin_program":
        prog.mode = "stdin"
        prog.stdin_cases = drv.stdin_cases(spec) or [{"input": "", "expected_output": "", "fixtures": None}]
    else:
        prog.mode = "main"
        if "f=main(" not in src:
            prog.reason = "no main() — not executable"
            return prog
    b = os.path.join(workdir, tag + ".test.bin")
    ok, detail = build(src, b, workdir, tag + ".test",
                       flags=BUILD_FLAGS + list(spec.get("build_flags") or []))
    prog.paths.append(b)
    if ok:
        prog.test_bin = b
    else:
        prog.reason = "build failed: " + (detail or "")[:200]
    return prog


def run_spec_cases(prog, workdir, timeout=RUN_TIMEOUT):
    """[(label, run_measured result)] for the spec cases of a prepared program."""
    if not prog.test_bin:
        return []
    if prog.mode == "stdin":
        out = []
        with tempfile.TemporaryDirectory(dir=workdir) as td:
            for i, tc in enumerate(prog.stdin_cases):
                cwd = os.path.join(td, f"c{i}")
                os.makedirs(cwd, exist_ok=True)
                try:
                    materialise_fixtures(tc.get("fixtures"), cwd)
                except (ValueError, OSError) as e:
                    out.append((f"stdin case {i}", {"stdout": b"", "stderr": b"", "exit": None,
                                                    "wall_ms": 0.0, "rss_kb": 0.0, "timed_out": False,
                                                    "fixture_error": str(e)}))
                    continue
                out.append((f"stdin case {i}", run_measured(prog.test_bin, tc.get("input") or "",
                                                            timeout=timeout, cwd=cwd)))
        return out
    return [("spec cases", run_measured(prog.test_bin, "", timeout=timeout, cwd=workdir))]


def _trunc(b):
    return b.decode("utf-8", "replace")[:STDOUT_KEEP]


def _same_run(a, b):
    return (a["stdout"] == b["stdout"] and a["exit"] == b["exit"]
            and a["timed_out"] == b["timed_out"] and a.get("fixture_error") == b.get("fixture_error"))


def compare(porig, pcand, spec, task_id, workdir, gen_inputs=None, a_test=None,
            timeout=RUN_TIMEOUT):
    """Differential verdict for two prepared programs. Returns the result dict
    (verdict, reason, checks, first_diff, timings)."""
    res = {"task_id": task_id, "verdict": None, "reason": None,
           "checks": {"spec_cases": None, "generated": None, "ref": None},
           "first_diff": None,
           "tkc_bin_sha": tkc_pin.bin_sha(TKC),          # 131.39: the binary both sides were built with
           "timings": {"orig": [], "cand": []}}
    checked_any = False
    # (a) spec cases
    if porig.test_bin and pcand.test_bin:
        ro = run_spec_cases(porig, workdir, timeout)
        rc = run_spec_cases(pcand, workdir, timeout)
        res["timings"]["orig"] += [{"wall_ms": r["wall_ms"], "rss_kb": r["rss_kb"]} for _, r in ro]
        res["timings"]["cand"] += [{"wall_ms": r["wall_ms"], "rss_kb": r["rss_kb"]} for _, r in rc]
        both_timeout = [lab for (lab, a), (_, b) in zip(ro, rc) if a["timed_out"] and b["timed_out"]]
        diffs = [(lab, a, b) for (lab, a), (_, b) in zip(ro, rc) if not _same_run(a, b)]
        res["checks"]["spec_cases"] = {"mode": porig.mode, "runs": len(ro), "identical": not diffs,
                                       "both_timeout": both_timeout}
        if diffs:
            lab, a, b = diffs[0]
            res["first_diff"] = {"stage": "spec_cases", "input": lab,
                                 "orig_stdout": _trunc(a["stdout"]), "cand_stdout": _trunc(b["stdout"]),
                                 "orig_exit": a["exit"], "cand_exit": b["exit"]}
            res["verdict"], res["reason"] = "diverged", f"spec cases differ ({lab})"
            return res
        if both_timeout:
            res["verdict"], res["reason"] = "unverifiable", f"timeout on both sides ({both_timeout[0]})"
            return res
        checked_any = True
    elif porig.test_bin and not pcand.test_bin:
        res["verdict"], res["reason"] = "diverged", "candidate not executable: " + str(pcand.reason)
        res["first_diff"] = {"stage": "build", "input": "spec cases", "orig_stdout": "", "cand_stdout": "",
                             "orig_exit": None, "cand_exit": None, "detail": pcand.reason}
        return res
    else:
        res["checks"]["spec_cases"] = {"mode": porig.mode, "runs": 0, "identical": None,
                                       "skipped": porig.reason}
    # (b) generated inputs (single_function)
    if porig.mode == "driver":
        if porig.gen_bin and pcand.gen_bin:
            a = run_measured(porig.gen_bin, "", timeout=timeout, cwd=workdir)
            b = run_measured(pcand.gen_bin, "", timeout=timeout, cwd=workdir)
            oa, ob = split_marked(a["stdout"], porig.n_gen), split_marked(b["stdout"], pcand.n_gen)
            reached = sum(1 for x in oa if x is not None)
            gen = {"n": porig.n_gen, "reached": reached, "orig_exit": a["exit"], "cand_exit": b["exit"],
                   "identical": _same_run(a, b)}
            res["checks"]["generated"] = gen
            if not gen["identical"]:
                k = next((i for i in range(porig.n_gen) if oa[i] != ob[i]), None)
                res["first_diff"] = {"stage": "generated",
                                     "input": gen_inputs[k] if (gen_inputs and k is not None) else None,
                                     "input_index": k,
                                     "orig_stdout": "\n".join(oa[k] or []) if k is not None else _trunc(a["stdout"]),
                                     "cand_stdout": "\n".join(ob[k] or []) if k is not None else _trunc(b["stdout"]),
                                     "orig_exit": a["exit"], "cand_exit": b["exit"]}
                res["verdict"] = "diverged"
                res["reason"] = f"generated input {k} differs" if k is not None else "generated run differs (exit/tail)"
                return res
            if a["timed_out"] and b["timed_out"]:
                res["verdict"], res["reason"] = "unverifiable", "timeout on both sides (generated)"
                return res
            checked_any = True
            # (c) python_ref cross-check
            why = ref_signature_ok(spec, a_test)
            if why:
                res["checks"]["ref"] = {"status": "skipped", "reason": why}
            else:
                res["checks"]["ref"] = _ref_check(a_test["python_ref"], gen_inputs, oa, ob)
                if res["checks"]["ref"]["status"] == "mismatch":
                    k = res["checks"]["ref"]["first_mismatch"]
                    res["first_diff"] = {"stage": "python_ref", "input": gen_inputs[k], "input_index": k,
                                         "orig_stdout": "\n".join(oa[k] or []),
                                         "cand_stdout": "\n".join(ob[k] or []),
                                         "ref": res["checks"]["ref"]["first_mismatch_ref"]}
                    res["verdict"], res["reason"] = "diverged", f"candidate != python_ref on generated input {k}"
                    return res
        elif porig.gen_bin and not pcand.gen_bin:
            res["verdict"], res["reason"] = "diverged", "candidate generated-input driver failed: " + str(pcand.gen_reason)
            return res
        else:
            res["checks"]["generated"] = {"n": 0, "identical": None, "skipped": porig.gen_reason}
    if not checked_any:
        why = porig.reason or "nothing executable"
        if porig.mode == "driver" and porig.gen_reason:
            why = f"{why}; {porig.gen_reason}"
        res["verdict"], res["reason"] = "unverifiable", why
        return res
    res["verdict"] = "identical"
    return res


def _ref_check(ref_src, gen_inputs, orig_outs, cand_outs):
    r = run_python_ref(ref_src, gen_inputs)
    if "error" in r:
        return {"status": "skipped", "reason": r["error"]}
    matched = mismatched = shared = errors = unreached = unrendered = 0
    first = first_ref = None
    for k, rr in enumerate(r["results"]):
        if not rr.get("ok"):
            errors += 1
            continue
        want = render_ref_lines(rr.get("value"))
        if want is None:
            unrendered += 1
            continue
        got = cand_outs[k]
        if got is None:
            unreached += 1
            continue
        if lines_equal(got, want):
            matched += 1
        elif orig_outs[k] is not None and lines_equal(orig_outs[k], got):
            shared += 1          # the original prints the same wrong thing: pre-existing
        else:
            mismatched += 1
            if first is None:
                first, first_ref = k, "\n".join(want)
    if mismatched:
        status = "mismatch"
    elif shared:
        status = "mismatch_shared"
    elif matched:
        status = "match"
    else:
        status = "skipped"
    out = {"status": status, "inputs": len(gen_inputs), "matched": matched, "mismatched": mismatched,
           "mismatch_shared": shared, "ref_errors": errors, "unreached": unreached,
           "unrendered": unrendered}
    if status == "skipped":
        out["reason"] = "ref produced no comparable value"
    if first is not None:
        out["first_mismatch"], out["first_mismatch_ref"] = first, first_ref
    return out


def diff_check(orig_src, cand_src, spec, task_id, workdir=None, a_test=None,
               n_generated=N_GENERATED, timeout=RUN_TIMEOUT):
    """Build both sources, run every check, remove all artefacts. Returns the
    compare() result dict (plus `generated_inputs` reason when skipped)."""
    base = workdir or tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    td = tempfile.mkdtemp(prefix="dc_", dir=base)
    gen_inputs, gen_why = (None, None)
    if spec.get("task_type") == "single_function":
        gen_inputs, gen_why = generate_inputs(spec, task_id, n_generated)
    porig = pcand = None
    try:
        porig = prepare_program(orig_src, spec, task_id, td, "orig", gen_inputs)
        pcand = prepare_program(cand_src, spec, task_id, td, "cand", gen_inputs)
        res = compare(porig, pcand, spec, task_id, td, gen_inputs, a_test, timeout)
        if gen_why:
            res["checks"]["generated"] = {"n": 0, "identical": None, "skipped": gen_why}
        return res
    finally:
        for p in (porig, pcand):
            if p:
                p.cleanup()
        shutil.rmtree(td, ignore_errors=True)


def differential(spec, orig_src, cand_src, workdir=None, a_test=None, task_id=None):
    """Gate-shaped entrypoint (the 131.15 pattern_common.diff_gate contract):
    diff_check() plus `identical` = True | False (diverged) | None
    (unverifiable). Keyword order matches diff_gate's positional call
    (spec, orig, cand, tmpdir)."""
    tid = task_id or spec.get("task_id", "task")
    res = diff_check(orig_src, cand_src, spec, tid, workdir, a_test)
    res["identical"] = {"identical": True, "diverged": False}.get(res["verdict"])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", required=True, help="task spec JSON")
    ap.add_argument("--orig", required=True)
    ap.add_argument("--cand", required=True)
    ap.add_argument("--a-test", help="audit/a_tests/<base>.json (python_ref cross-check)")
    ap.add_argument("--task-id")
    ap.add_argument("--workdir", default="/tmp")
    ap.add_argument("--n", type=int, default=N_GENERATED)
    args = ap.parse_args(argv)
    spec = json.load(open(args.spec))
    a_test = json.load(open(args.a_test)) if args.a_test else None
    with tkc_pin.pin().install(sys.modules[__name__], validate):   # 131.39
        res = diff_check(open(args.orig).read(), open(args.cand).read(), spec,
                         args.task_id or spec.get("task_id", "task"), args.workdir, a_test, args.n)
    print(json.dumps(res, indent=1))
    return 0 if res["verdict"] == "identical" else 1


if __name__ == "__main__":
    sys.exit(main())
