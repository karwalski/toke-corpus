#!/usr/bin/env python3
"""Deterministic auto-rewrite pass over the 131.13 AUTO bucket (story 131.14).

Per record (parallel, resumable, never raises past the worker):
  1. copy tk_source -> `tkc --lint --fix` (fixpoint + overlap rules are the
     compiler's; TKC_LINT_CONCAT_FIX is inherited from the environment, unset =
     the concat rewrite stays dormant)               -> `noop` when nothing changed
     The single_function harness stub prefix (`m=harness;` + stub imports /
     context stubs, idiom_judge.stub_prefix_len) is NOT the record's: --fix
     deletes its unused `i=io:std.io;`/`i=s:std.str;` on nearly every
     single_function record, and audit_one's driver main needs them. The
     original prefix is re-attached after the fix; a fix that touched only the
     prefix is `noop:harness_prefix_only`.
  2. re-lint (stub-stripped, linter FPs suppressed): 0 pattern-rule
     errors/warnings (strict — no style-mandate exemption, the EXEMPT bucket
     precedes AUTO in 131.13) AND 0 other errors/warnings (hints OK). A
     remaining warning the ORIGINAL already carries is reject:preexisting:lint.
  3. run_shard.validate_one_gates(): compile, signature, build+tests
     (full_program / stdin_program), idiom >= floor, depth <= 4, fn <= 600 B,
     pattern gate. single_function records go through it as a verbatim module
     (task_type switched to full_program for the call, test_cases emptied) so
     the harness stub prefix is NOT re-assembled — 129.4/5 repairs dropped
     unused stubs and re-adding them would inflate min_bytes; their spec tests
     run on the driver.append_main binary that diff_check builds anyway.
     A gate the ORIGINAL also fails is reported as reject:preexisting:<gate>.
  4. min_bytes (tkc --min byte length) non-increasing
  5. diff_check: spec cases + 20 generated inputs + python_ref -> `identical`
  6. Tier-1 perf on the spec-case binaries: median of --perf-runs (3) passes,
     wall + peak RSS from os.wait4 rusage; flag iff ratio > 1.5 AND absolute
     delta > 5 ms / 1 MB, confirmed by a second pass of max(runs, 5) (a flag
     that does not reproduce is recorded under perf.confirm and passes)
  -> decision bank | reject:<reason> | noop

Writes <workdir>/results/<task_id>.json + <workdir>/cand/<task_id>.tk (when
the fix changed the source) + <workdir>/summary.json. Nothing under the corpus
category dirs, MANIFEST.jsonl, ledger/ or audit/replaced/ is touched unless
--bank is given: then every `bank` result is applied the bank_repairs.py way
(original archived to audit/replaced/131/<task_id>.tk, regen.rewrite131
provenance, ledger/rewrite_131.jsonl line, manifest_tool.stamp).

Default workdir: corpus/regen_v04/work/autofix_131/
"""
import argparse, hashlib, json, multiprocessing, os, random, re, shutil, statistics
import subprocess, sys, tempfile, time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diff_check as dc                                   # noqa: E402
import audit                                              # noqa: E402  (131.39: rebind its TKC)
import run_shard                                          # noqa: E402  (131.39: rebind its TKC)
import validate                                           # noqa: E402  (131.39: rebind its TKC)
import tkc_pin                                            # noqa: E402  (131.39)
import driver as drv                                      # noqa: E402
import idiom_judge                                        # noqa: E402
import manifest_tool                                      # noqa: E402  (131.35)
import metrics                                            # noqa: E402
from audit import load_specs, _BASE, _compare             # noqa: E402
from run_shard import validate_one_gates, CARD_SHA        # noqa: E402

CORPUS = os.environ.get("TOKE_CORPUS", "/Users/matthew.watt/tk/toke-corpus/corpus/regen_v04")
# 131.39: main() pins a private copy of the compiler once (before the pool) and
# rebinds TKC here + in every sibling module; workers see it via $TOKE_TKC_PIN.
TKC = tkc_pin.default_tkc()
_TKC_SOURCE = os.environ.get("TKC", str(tkc_pin.DEFAULT_TKC))      # the symlink, never the pinned copy
TOKE_REPO = (os.path.dirname(os.path.realpath(_TKC_SOURCE)) if os.path.exists(_TKC_SOURCE)
             else str(tkc_pin.DEFAULT_TOKE))
CATALOGUE = os.path.join(TOKE_REPO, "patterns", "catalogue.json")
STORY = "131.14"
WAVE = "auto"
FIX_TIMEOUT = 60
PERF_RUNS = 3
PERF_RATIO = 1.5
PERF_WALL_MS = 5.0
PERF_RSS_KB = 1024.0
IDIOM_FLOOR = idiom_judge.IDIOM_FLOOR
_SKIPPED = re.compile(r"(\d+) fix\(es\) skipped")
_FIXED = re.compile(r"fixed (\d+) violation")
_SWEEP_LINE = re.compile(r"/([A-Za-z0-9._-]+)\.tk:\d+")


def sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def tool_shas():
    """(tkc_sha, catalogue_sha, toke_git, tkc_bin_sha): tkc_sha = short sha256
    of the tkc binary (the rescore_131 / pattern_common.tkc_stamp convention —
    the provenance block's "tkc build sha used to verify"); toke_git = toke
    HEAD; tkc_bin_sha (131.39) = the full sha256 of the binary actually
    exec'd (the pinned copy once main() pinned)."""
    tkc_bin_sha = tkc_pin.bin_sha(TKC)
    tkc_sha = tkc_bin_sha[:12] if tkc_bin_sha else None
    try:
        toke_git = subprocess.run(["git", "-C", TOKE_REPO, "rev-parse", "--short", "HEAD"],
                                  capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        toke_git = None
    cat_sha = sha256_file(CATALOGUE)[:12] if os.path.exists(CATALOGUE) else None
    return tkc_sha or "unknown", cat_sha or "none", toke_git, tkc_bin_sha


def _bin_sha_of(shas):
    """131.39: the pinned-binary sha from a tool_shas() tuple (older 3-tuples
    fall back to the binary TKC names right now)."""
    return shas[3] if len(shas) > 3 and shas[3] else tkc_pin.bin_sha(TKC)


_catalogue_by_rule = None


def rules_to_patterns(rules, catalogue=CATALOGUE):
    """Lint rule ids -> catalogue pattern ids (freeze/README `patterns_fixed`
    is pattern ids); a rule no catalogue entry carries maps to itself."""
    global _catalogue_by_rule
    if _catalogue_by_rule is None:
        by_rule = {}
        try:
            cat = json.load(open(catalogue))
            for e in (cat["entries"] if isinstance(cat, dict) else cat):
                r = (e.get("lint") or {}).get("rule")
                if r:
                    by_rule.setdefault(r, []).append(e["id"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        _catalogue_by_rule = by_rule
    out = []
    for r in rules:
        for pid in _catalogue_by_rule.get(r) or [r]:
            if pid not in out:
                out.append(pid)
    return out


def a_test_for(task_id, a_dir):
    m = _BASE.match(task_id)
    p = os.path.join(a_dir, (m.group(1) if m else task_id) + ".json")
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- fixing ---
def run_fix(path, env=None):
    """`tkc --lint --fix path` in place. Returns {rc, diags (first round),
    fixed (count from stderr), skipped (count), stderr}."""
    try:
        r = subprocess.run([TKC, "--lint", "--fix", path], capture_output=True, text=True,
                           errors="replace", timeout=FIX_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return {"rc": None, "diags": [], "fixed": 0, "skipped": 0, "stderr": "fix timeout"}
    diags = idiom_judge.parse_diag_lines(r.stdout)
    m_fixed = _FIXED.search(r.stderr or "")
    m_skip = _SKIPPED.search(r.stderr or "")
    return {"rc": r.returncode, "diags": diags,
            "fixed": int(m_fixed.group(1)) if m_fixed else 0,
            "skipped": int(m_skip.group(1)) if m_skip else 0,
            "stderr": (r.stderr or "")[:500]}


_STUB_IMPORT = re.compile(r"^i=\w+:[\w.]+;$")
_STUB_FN = re.compile(r"^f=([a-z0-9]+)\([^)]*\):\S+\{<[^{}]*\};$")


def harness_prefix_len(src, spec=None):
    """Character length of the single_function harness prefix: `m=harness;`,
    the stub imports, and the context stubs. Unlike idiom_judge.stub_prefix_len
    a one-liner stub-shaped function counts only when the spec's
    domain_context declares it — a one-liner TARGET function is the record's."""
    if not src.startswith("m=harness;"):
        return 0
    stubs = drv._stub_names(spec) if spec is not None else None
    end = 0
    for line in src.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        m = _STUB_FN.match(bare)
        if bare == "m=harness;" or _STUB_IMPORT.match(bare) or \
                (m and (stubs is None or m.group(1) in stubs)):
            end += len(line)
            continue
        break
    return end


def restore_prefix(orig, fixed, spec=None):
    """Re-attach the original harness stub prefix to a --fix result. Returns
    (candidate, prefix_len_orig chars). No-op for sources without the prefix."""
    n = harness_prefix_len(orig, spec)
    if not n:
        return fixed, 0
    m = harness_prefix_len(fixed, spec)
    return orig[:n] + fixed[m:], n


def _lint_remaining(diags):
    """Error/warning diagnostics (stub-stripped, FPs suppressed by metrics.lint)
    split into (pattern-rule hits, other)."""
    live = [d for d in diags if d.get("severity") in idiom_judge.GATE_SEVERITIES
            and not d.get("suppressed")]
    pat = idiom_judge.hard_gate(live, ())
    return pat, [d for d in live if d.get("rule") not in idiom_judge.PATTERN_RULES]


def _rule_list(diags):
    return ",".join(sorted({d.get("rule") or "?" for d in diags}))


def gate_spec(spec):
    """Spec used for validate_one_gates: single_function is checked as a verbatim
    module (no re-assembly of the harness prefix; tests run on the driver)."""
    if spec.get("task_type") != "single_function":
        return spec
    g = dict(spec)
    g["task_type"] = "full_program"
    g["test_cases"] = []
    return g


def _gate_key(reason):
    return (reason or "").split(":", 1)[0] or "unknown"


def driver_tests(prog, spec, workdir):
    """tests_new verdict of a prepared single_function program on its spec cases:
    line match AND exit 0 AND no extra lines. (None, why) when not runnable."""
    if not prog.test_bin:
        return None, prog.reason
    runs = dc.run_spec_cases(prog, workdir)
    if not runs:
        return None, "no run"
    r = runs[0][1]
    got = r["stdout"].decode("utf-8", "replace").splitlines()
    want = drv.expected_lines(spec)
    ok, extra = _compare(got, want)
    if r["timed_out"]:
        return False, "timeout"
    if not ok:
        return False, "output mismatch"
    if r["exit"] != 0:
        return False, f"exit={r['exit']}"
    if extra:
        return False, f"extra_lines={extra}"
    return True, None


def perf_compare(porig, pcand, workdir, runs=PERF_RUNS):
    """Tier-1 perf: median over `runs` passes of the spec-case binaries (a pass =
    every spec case once; wall summed, RSS max). None when not runnable."""
    if not (porig.test_bin and pcand.test_bin):
        return None

    samples = {porig.tag: ([], []), pcand.tag: ([], [])}
    for _ in range(runs):                        # interleaved: both sides see the same cache state
        for prog in (porig, pcand):
            rs = dc.run_spec_cases(prog, workdir)
            if not rs or any(r["timed_out"] for _, r in rs):
                return None
            samples[prog.tag][0].append(sum(r["wall_ms"] for _, r in rs))
            samples[prog.tag][1].append(max(r["rss_kb"] for _, r in rs))
    ow, orss = (statistics.median(x) for x in samples[porig.tag])
    cw, crss = (statistics.median(x) for x in samples[pcand.tag])
    ratio = (cw / ow) if ow > 0 else (1.0 if cw == 0 else float("inf"))
    rss_ratio = (crss / orss) if orss > 0 else (1.0 if crss == 0 else float("inf"))
    wall_flag = ratio > PERF_RATIO and (cw - ow) > PERF_WALL_MS
    rss_flag = rss_ratio > PERF_RATIO and (crss - orss) > PERF_RSS_KB
    return {"tier": "1", "runs": runs,
            "orig_wall_ms": round(ow, 3), "cand_wall_ms": round(cw, 3), "ratio": round(ratio, 3),
            "orig_rss_kb": round(orss, 1), "cand_rss_kb": round(crss, 1), "rss_ratio": round(rss_ratio, 3),
            "flag": bool(wall_flag or rss_flag),
            "verdict": "fail" if (wall_flag or rss_flag) else "pass"}


def tier1_perf(spec, orig_src, cand_src, tmpdir, runs=PERF_RUNS):
    """Shared Tier-1 perf gate (pattern_common.tier1_perf prefers this when
    importable): build both sides' spec-case binaries, perf_compare, clean up.
    Returns {tier, ratio, verdict pass|fail|n/a, ...}; never raises."""
    tid = spec.get("task_id", "task")
    td = tempfile.mkdtemp(prefix="perf_", dir=tmpdir)
    porig = pcand = None
    try:
        porig = dc.prepare_program(orig_src, spec, tid, td, "orig")
        pcand = dc.prepare_program(cand_src, spec, tid, td, "cand")
        out = perf_compare(porig, pcand, td, runs)
        if out is None:
            return {"tier": "1", "ratio": None, "verdict": "n/a",
                    "detail": pcand.reason or porig.reason or "not runnable"}
        return out
    except Exception as e:  # noqa: BLE001
        return {"tier": "1", "ratio": None, "verdict": "n/a", "detail": f"{type(e).__name__}: {e}"}
    finally:
        for p in (porig, pcand):
            if p:
                p.cleanup()
        shutil.rmtree(td, ignore_errors=True)


# ---------------------------------------------------------------- worker ---
def autofix_one(job):
    """One record end-to-end. Returns the result dict (never raises)."""
    tid, rec_path, spec, a_test, opts = job
    t0 = time.time()
    res = {"task_id": tid, "category": spec.get("category"), "task_type": spec.get("task_type"),
           "story": STORY, "wave": WAVE, "decision": None, "reason": None, "ts": int(t0),
           "prev_sha256": None, "changed": False, "fix": None, "lint_after": None, "gates": None,
           "min_bytes": None, "proxy_tokens": None, "tests": None, "diff": None, "perf": None,
           "candidate_path": None}
    tmp = tempfile.mkdtemp(prefix=tid + "_", dir=opts["tmpdir"])
    porig = pcand = None
    try:
        rec = json.load(open(rec_path))
        orig = rec["tk_source"]
        res["prev_sha256"] = sha256_file(rec_path)
        res["style_mandate"] = idiom_judge.style_mandate(spec)
        path = os.path.join(tmp, tid + ".tk")
        with open(path, "w") as f:
            f.write(orig)
        # 1. fix (whole module — the linter needs the prefix for context)
        fx = run_fix(path)
        prefix_bytes = len(orig[:harness_prefix_len(orig, spec)].encode("utf-8"))
        in_body = [d for d in fx["diags"] if ((d.get("span") or {}).get("start") or 0) >= prefix_bytes]
        fix_rules = sorted({d.get("rule") or d.get("code") for d in in_body if d.get("fix")})
        res["fix"] = {"rc": fx["rc"], "fixed": fx["fixed"], "skipped": fx["skipped"],
                      "rules_with_fix": fix_rules,
                      "harness_fixes": sum(1 for d in fx["diags"] if d.get("fix")) - len([d for d in in_body if d.get("fix")]),
                      "diag_rules": sorted(Counter(d.get("rule") or d.get("code") for d in in_body).items()),
                      "stderr": fx["stderr"] if fx["rc"] != 0 else None}
        if fx["rc"] != 0:
            res["decision"], res["reason"] = "reject", "fix_failed:rc=" + str(fx["rc"])
            return res
        fixed_raw = open(path).read()
        fixed, _ = restore_prefix(orig, fixed_raw, spec)
        res["fix"]["prefix_restored"] = fixed != fixed_raw
        if fixed == orig:
            res["decision"] = "noop"
            res["reason"] = ("harness_prefix_only" if fixed_raw != orig
                             else "fixes_skipped" if fx["skipped"] else "no_fixable_diagnostics")
            return res
        res["changed"] = True
        with open(path, "w") as f:
            f.write(fixed)
        # 2. re-lint (strict: no mandate exemption; 0 pattern hits AND 0 other warnings)
        after = metrics.lint(path, fixed)
        remaining, other = _lint_remaining(after)
        exempt = idiom_judge.mandate_exempt_rules(res["style_mandate"])
        res["lint_after"] = {"pattern_remaining": idiom_judge.violation_summary(remaining) or None,
                             "pattern_remaining_exempt_by_mandate":
                                 sum(1 for d in remaining if d.get("rule") in exempt),
                             "other_remaining": _rule_list(other) or None,
                             "hints": sum(1 for d in after if d.get("severity") == "hint"),
                             "warnings": sum(1 for d in after if d.get("severity") == "warning")}
        if remaining or other:
            o_pat, o_other = _lint_remaining(metrics.lint(_write(tmp, tid + ".pre.tk", orig), orig))
            if remaining:
                pre = _rule_list(remaining) and set(d["rule"] for d in remaining) <= set(d["rule"] for d in o_pat)
                res["decision"] = "reject"
                res["reason"] = ("preexisting:pattern:" if pre else "pattern_remaining:") + \
                    idiom_judge.violation_summary(remaining)
            else:
                pre = set(d.get("rule") for d in other) <= set(d.get("rule") for d in o_other)
                res["decision"] = "reject"
                res["reason"] = ("preexisting:lint:" if pre else "lint_remaining:") + _rule_list(other)
            return res
        # 3. gates
        gspec = gate_spec(spec)
        record, ok, reason, gates = validate_one_gates(gspec, fixed, tmp)
        res["gates"] = gates
        cand = record["tk_source"] if record else fixed
        cand_path = os.path.join(opts["cand_dir"], tid + ".tk")
        with open(cand_path, "w") as f:
            f.write(cand)
        res["candidate_path"] = cand_path
        if not ok:
            _o, ook, oreason, _g = validate_one_gates(gspec, orig, tmp)
            if not ook and _gate_key(oreason) == _gate_key(reason):
                res["decision"], res["reason"] = "reject", "preexisting:" + (reason or "gate")
            else:
                res["decision"], res["reason"] = "reject", "gate:" + (reason or "unknown")
            return res
        # 4. min_bytes / proxy tokens (candidate from the gate record; original recomputed)
        omin = metrics.min_form(_write(tmp, tid + ".orig.tk", orig))
        orig_min = len(omin.encode()) if omin is not None else rec.get("regen", {}).get("min_bytes")
        cand_min = record["regen"].get("min_bytes")
        res["min_bytes"] = {"before": orig_min, "after": cand_min}
        res["proxy_tokens"] = {"before": metrics.proxy_tokens(omin) if omin is not None
                               else rec.get("regen", {}).get("proxy_tokens"),
                               "after": record["regen"].get("proxy_tokens")}
        res["max_depth"] = {"before": rec.get("regen", {}).get("max_depth"), "after": record["regen"].get("max_depth")}
        res["idiom"] = {"before": rec.get("judge", {}).get("score"), "after": record["judge"]["score"]}
        res["lint_exempt"] = record["regen"].get("lint_exempt") or []
        res["over_budget"] = metrics.over_budget(spec.get("category"), spec.get("task_type"),
                                                 record["regen"].get("proxy_tokens"))
        if orig_min is not None and cand_min is not None and cand_min > orig_min:
            res["decision"], res["reason"] = "reject", f"min_bytes_increased:{orig_min}->{cand_min}"
            return res
        # 5. programs (built once: driver tests, diff, perf)
        gen_inputs, gen_why = (None, None)
        if spec.get("task_type") == "single_function":
            gen_inputs, gen_why = dc.generate_inputs(spec, tid, opts.get("n_generated", dc.N_GENERATED))
        porig = dc.prepare_program(orig, spec, tid, tmp, "orig", gen_inputs)
        pcand = dc.prepare_program(cand, spec, tid, tmp, "cand", gen_inputs)
        if spec.get("task_type") == "single_function" and spec.get("test_cases"):
            t_ok, t_why = driver_tests(pcand, spec, tmp)
            res["tests"] = {"via": "driver", "ok": t_ok, "reason": t_why,
                            "from": spec.get("_tests_from", "spec")}
            res["gates"]["tests"] = t_ok
            if t_ok is False:
                o_ok, o_why = driver_tests(porig, spec, tmp)
                if o_ok is False and o_why == t_why:
                    res["decision"], res["reason"] = "reject", "preexisting:tests:" + str(t_why)
                else:
                    res["decision"], res["reason"] = "reject", "tests:" + str(t_why)
                return res
        elif gates.get("tests") is not None:
            res["tests"] = {"via": "main" if spec.get("task_type") != "stdin_program" else "stdin",
                            "ok": gates["tests"], "reason": None, "from": "spec"}
        # 6. differential
        diff = dc.compare(porig, pcand, spec, tid, tmp, gen_inputs, a_test)
        if gen_why:
            diff["checks"]["generated"] = {"n": 0, "identical": None, "skipped": gen_why}
        diff.pop("timings", None)
        res["diff"] = diff
        if diff["verdict"] == "diverged":
            res["decision"], res["reason"] = "reject", "diverged:" + str(diff["reason"])
            return res
        # 7. Tier-1 perf (a flag is confirmed by a second, longer pass: the
        # median-of-3 on a ~10 ms binary jitters past x1.5 under parallel load)
        runs = opts.get("perf_runs", PERF_RUNS)
        res["perf"] = perf_compare(porig, pcand, tmp, runs)
        if res["perf"] and res["perf"]["flag"]:
            confirm = perf_compare(porig, pcand, tmp, max(runs, 5))
            res["perf"]["confirm"] = confirm
            if confirm and not confirm["flag"]:
                res["perf"].update({"flag": False, "verdict": "pass",
                                    "note": "first pass flagged; confirmation pass clean"})
        if res["perf"] and res["perf"]["flag"]:
            res["decision"] = "reject"
            res["reason"] = f"perf:wall x{res['perf']['ratio']} rss x{res['perf']['rss_ratio']}"
            return res
        if diff["verdict"] == "unverifiable":
            if opts.get("allow_unverifiable"):
                res["decision"], res["reason"] = "bank", "unverifiable_allowed:" + str(diff["reason"])
            else:
                res["decision"], res["reason"] = "reject", "unverifiable:" + str(diff["reason"])
            return res
        if opts.get("ref_strict") and (diff["checks"].get("ref") or {}).get("status") == "mismatch_shared":
            res["decision"], res["reason"] = "reject", "ref_mismatch_shared"
            return res
        res["decision"] = "bank"
        res["reason"] = "fixed:" + ",".join(fix_rules)
        return res
    except Exception as e:  # defensive: one bad record must not kill the wave
        res["decision"], res["reason"] = "reject", f"error:{type(e).__name__}: {e}"
        return res
    finally:
        for p in (porig, pcand):
            if p:
                p.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)
        res["elapsed_s"] = round(time.time() - t0, 3)


def _write(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(text)
    return p


# ------------------------------------------------------------------ bank ---
def bank_one(res, corpus, manifest, ledger_path, shas, now=None):
    """Apply one `bank` result: archive original, rewrite the record, stamp the
    manifest, append the rewrite_131 ledger line. Returns the ledger entry."""
    tid = res["task_id"]
    tkc_sha, cat_sha = shas[0], shas[1]
    toke_git = shas[2] if len(shas) > 2 else None
    tkc_bin_sha = _bin_sha_of(shas)
    ts = now or manifest_tool.now_iso()
    rec_path = os.path.join(corpus, res["category"], tid + ".json")
    attempts = 1
    if os.path.exists(ledger_path):
        for l in open(ledger_path):
            l = l.strip()
            if l:
                e = json.loads(l)
                if e.get("task_id") == tid and e.get("wave") == WAVE:
                    attempts = max(attempts, int(e.get("attempts") or 0) + 1)
    entry = {"task_id": tid, "wave": WAVE, "status": None, "reason": None, "attempts": attempts,
             "prev_sha256": res.get("prev_sha256"), "new_sha256": None, "ts": ts}
    cur_sha = sha256_file(rec_path) if os.path.exists(rec_path) else None
    if cur_sha != res.get("prev_sha256"):
        entry["status"], entry["reason"] = "skipped", "record changed since the dry run (stale prev_sha256)"
    elif not res.get("candidate_path") or not os.path.exists(res["candidate_path"]):
        entry["status"], entry["reason"] = "failed", "candidate source missing"
    else:
        cand = open(res["candidate_path"]).read()
        rec = json.load(open(rec_path))
        replaced_dir = os.path.join(corpus, "audit", "replaced", "131")
        os.makedirs(replaced_dir, exist_ok=True)
        archive = os.path.join(replaced_dir, tid + ".tk")
        if not os.path.exists(archive):          # first touch keeps the freeze-129 original
            with open(archive, "w") as f:
                f.write(rec["tk_source"])
        rec["tk_source"] = cand
        rg = rec.setdefault("regen", {})
        rg["source_sha256"] = hashlib.sha256(cand.encode()).hexdigest()
        for k in ("min_bytes", "proxy_tokens", "max_depth"):
            v = (res.get(k) or {}).get("after")
            if v is not None:
                rg[k] = v
        rec["tk_tokens"] = (res.get("proxy_tokens") or {}).get("after", rec.get("tk_tokens"))
        rg["lint_pattern_violations"] = []
        rg["lint_exempt"] = res.get("lint_exempt") or []
        if res.get("over_budget") is not None:
            rg["over_budget"] = res["over_budget"]
        if (res.get("idiom") or {}).get("after") is not None:
            rec.setdefault("judge", {})["score"] = res["idiom"]["after"]
        perf = res.get("perf") or {}
        rules_fixed = (res.get("fix") or {}).get("rules_with_fix") or []
        rg["rewrite131"] = {
            "wave": WAVE, "story": STORY, "ts": ts, "prev_sha256": res.get("prev_sha256"),
            "card_sha": CARD_SHA, "catalogue_sha": cat_sha, "tkc_sha": tkc_sha, "toke_git": toke_git,
            "tkc_bin_sha": tkc_bin_sha,               # 131.39: the binary that verified this rewrite
            "patterns_fixed": rules_to_patterns(rules_fixed), "rules_fixed": rules_fixed,
            "lint_violations": 0, "lint_exempt": res.get("lint_exempt") or [],
            "proxy_tokens_before": (res.get("proxy_tokens") or {}).get("before"),
            "proxy_tokens_after": (res.get("proxy_tokens") or {}).get("after"),
            "perf": {"tier": "1", "ratio": perf.get("ratio", 1.0),
                     "verdict": perf.get("verdict", "n/a")},
            "diff_check": (res.get("diff") or {}).get("verdict"),
        }
        with open(rec_path, "w") as f:
            json.dump(rec, f)
        manifest.stamp(tid, rec_path)            # 131.35: never replace without re-stamping
        entry["status"] = "rewritten"
        entry["reason"] = res.get("reason")
        entry["new_sha256"] = sha256_file(rec_path)
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    with open(ledger_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


# --------------------------------------------------------------- driving ---
def sweep_example_ids(path):
    ids = []
    for m in _SWEEP_LINE.finditer(open(path, errors="replace").read()):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def bucket_ids(path, bucket=None):
    """task_ids from a 131.13 bucket file: JSONL rows with task_id (+ optional
    `bucket` to filter on), or one task_id per line."""
    ids = []
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            row = json.loads(line)
            if bucket and (row.get("bucket") or "").upper() != bucket.upper():
                continue
            ids.append(row["task_id"])
        else:
            ids.append(line.split()[0])
    return ids


def select_task_ids(args, manifest_ids):
    ids = []
    if args.bucket:
        ids += bucket_ids(args.bucket, args.bucket_name)
    if args.examples and os.path.exists(args.examples):
        ids += sweep_example_ids(args.examples)
    ids += args.task_id or []
    if args.sample:
        pool = [t for t in manifest_ids]
        ids += random.Random(args.seed).sample(pool, min(args.sample, len(pool)))
    seen, out = set(), []
    for t in ids:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(p / 100 * len(vals)))]


def summarise(results, opts):
    decisions = Counter(r["decision"] for r in results)
    reasons = Counter(f"{r['decision']}:{(r.get('reason') or '').split(':')[0]}" for r in results)
    changed = [r for r in results if r.get("changed")]
    verdicts = Counter((r.get("diff") or {}).get("verdict") or "not_run" for r in changed)
    unver = Counter((r["diff"].get("reason") or "").split(";")[0] for r in changed
                    if (r.get("diff") or {}).get("verdict") == "unverifiable")
    ref = Counter((((r.get("diff") or {}).get("checks") or {}).get("ref") or {}).get("status") or "n/a"
                  for r in changed if r.get("diff"))
    fix_rules = Counter(x for r in changed for x in (r.get("fix") or {}).get("rules_with_fix") or [])
    perf_flags = [r["task_id"] for r in changed if (r.get("perf") or {}).get("flag")]
    el = [r.get("elapsed_s", 0) for r in results]
    bank = decisions.get("bank", 0)
    return {"story": STORY, "wave": WAVE, "ts": manifest_tool.now_iso(),
            "tkc_sha": opts["shas"][0], "catalogue_sha": opts["shas"][1],
            "toke_git": opts["shas"][2] if len(opts["shas"]) > 2 else None,
            "tkc_bin_sha": _bin_sha_of(opts["shas"]),     # 131.39
            "unknown_task_ids": opts.get("unknown") or [],
            "concat_fix_armed": bool(os.environ.get("TKC_LINT_CONCAT_FIX") not in (None, "", "0")),
            "records": len(results), "changed": len(changed),
            "decisions": dict(decisions), "reasons": dict(sorted(reasons.items())),
            "reject_reasons_detail": dict(Counter(r.get("reason") for r in results
                                                  if r["decision"] == "reject").most_common(40)),
            "bank_rate_of_changed": round(bank / len(changed), 4) if changed else None,
            "harness_prefix_only": sum(1 for r in results if r.get("reason") == "harness_prefix_only"),
            "preexisting_rejects": sum(1 for r in results if (r.get("reason") or "").startswith("preexisting:")),
            "bank_rate_of_all": round(bank / len(results), 4) if results else None,
            "diff_verdicts": dict(verdicts), "unverifiable_reasons": dict(unver),
            "ref_status": dict(ref), "fix_rules": dict(fix_rules),
            "perf_flags": perf_flags,
            "elapsed_s": {"total": round(sum(el), 1), "median": _pct(el, 50), "p90": _pct(el, 90),
                          "max": max(el) if el else None},
            "by_task_type": {tt: dict(Counter(r["decision"] for r in results if r.get("task_type") == tt))
                             for tt in sorted({r.get("task_type") for r in results}, key=str)},
            "thresholds": {"perf_ratio": PERF_RATIO, "perf_wall_ms": PERF_WALL_MS, "perf_rss_kb": PERF_RSS_KB,
                           "perf_runs": opts.get("perf_runs"), "n_generated": opts.get("n_generated"),
                           "idiom_floor": IDIOM_FLOOR, "max_depth": 4, "max_func_bytes": 600},
            "mode": "bank" if opts.get("bank") else "dry-run"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-dir", default=CORPUS)
    ap.add_argument("--workdir", default=os.path.join(CORPUS, "work", "autofix_131"))
    ap.add_argument("--bucket", help="131.13 bucket file (jsonl with task_id[, bucket] or one id per line)")
    ap.add_argument("--bucket-name", default=None, help="keep only rows whose bucket == NAME (e.g. AUTO)")
    ap.add_argument("--task-id", action="append")
    ap.add_argument("--examples", help="131.9 sweep corpus_examples.txt (task ids parsed from file paths)")
    ap.add_argument("--sample", type=int, help="add N random manifest records")
    ap.add_argument("--seed", type=int, default=131)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    ap.add_argument("--perf-runs", type=int, default=PERF_RUNS)
    ap.add_argument("--n-generated", type=int, default=dc.N_GENERATED)
    ap.add_argument("--redo", action="store_true", help="re-process task ids that already have a result")
    ap.add_argument("--allow-unverifiable", action="store_true",
                    help="bank on static gates alone when no differential check is possible")
    ap.add_argument("--ref-strict", action="store_true",
                    help="reject when the candidate disagrees with python_ref even where the original does too")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) evaluate only; write nothing to the corpus")
    mode.add_argument("--bank", action="store_true", help="apply every `bank` result to the corpus")
    args = ap.parse_args(argv)

    corpus = args.corpus_dir
    wd = args.workdir
    for sub in ("results", "cand", "tmp"):
        os.makedirs(os.path.join(wd, sub), exist_ok=True)
    manifest_rows = [json.loads(l) for l in open(os.path.join(corpus, "MANIFEST.jsonl"))]
    by_id = {}
    for e in manifest_rows:
        by_id[e["task_id"]] = e
    specs = load_specs(corpus)
    ids = select_task_ids(args, [t for t in by_id if t in specs])
    if args.limit:
        ids = ids[:args.limit]
    a_dir = os.path.join(corpus, "audit", "a_tests")
    # 131.39: pin the compiler before tool_shas() and before the pool; every
    # result and the summary carry its sha256 (`tkc_bin_sha`)
    pinned = tkc_pin.pin().install(sys.modules[__name__], dc, validate, metrics, idiom_judge, run_shard, audit)
    shas = tool_shas()
    assert shas[3] == pinned.sha256
    jobs, results, skipped, unknown = [], [], 0, []
    opts = {"tmpdir": os.path.join(wd, "tmp"), "cand_dir": os.path.join(wd, "cand"),
            "perf_runs": args.perf_runs, "n_generated": args.n_generated,
            "allow_unverifiable": args.allow_unverifiable, "ref_strict": args.ref_strict,
            "bank": args.bank, "shas": shas, "unknown": unknown}
    for tid in ids:
        rp = os.path.join(wd, "results", tid + ".json")
        if os.path.exists(rp) and not args.redo:
            results.append(json.load(open(rp)))
            skipped += 1
            continue
        e = by_id.get(tid)
        if not e or tid not in specs:
            unknown.append(tid)               # not a corpus record (e.g. a library program): not scored
            continue
        rec_path = os.path.join(corpus, e["path"])
        jobs.append((tid, rec_path, specs[tid], a_test_for(tid, a_dir), opts))
    print(f"autofix {len(jobs)} records ({skipped} already done, {len(unknown)} unknown ids), "
          f"{args.workers} workers, tkc {pinned.version} {shas[0]} (toke {shas[2]}, pinned copy {pinned.path}) "
          f"catalogue {shas[1]} mode={'bank' if args.bank else 'dry-run'}", file=sys.stderr)
    t0 = time.time()
    n = 0
    if jobs:
        with pinned, multiprocessing.Pool(args.workers) as pool:
            for r in pool.imap_unordered(autofix_one, jobs, chunksize=1):
                r["tkc_bin_sha"] = pinned.sha256      # 131.39 (results reloaded from disk keep their own)
                with open(os.path.join(wd, "results", r["task_id"] + ".json"), "w") as f:
                    json.dump(r, f, indent=1)
                results.append(r)
                n += 1
                if n % 25 == 0 or n == len(jobs):
                    c = Counter(x["decision"] for x in results)
                    print(f"  {n}/{len(jobs)}  {dict(c)}  {time.time() - t0:.0f}s", file=sys.stderr)
    summary = summarise(results, opts)
    if args.bank:
        manifest = manifest_tool.Manifest(os.path.join(corpus, "MANIFEST.jsonl"))
        ledger_path = os.path.join(corpus, "ledger", "rewrite_131.jsonl")
        banked = Counter()
        for r in results:
            if r.get("decision") == "bank":
                entry = bank_one(r, corpus, manifest, ledger_path, shas)
                banked[entry["status"]] += 1
        summary["banked"] = dict(banked)
    with open(os.path.join(wd, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))
    pinned.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
