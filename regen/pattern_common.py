"""Epic 131.15 — shared plumbing for the agent pattern-rewrite wave.

Used by pattern_prep.py (prompt + batch prep), check_pattern.py (worker
self-check) and bank_pattern.py (main-thread banking). Nothing here writes to
the corpus; the only writers are bank_pattern.py --bank paths.

Candidate contract (same as the 129 repair wave): a worker writes
`gen/pat_<task_id>.tk` with the SAME module shape as the record's current
tk_source — single_function modules keep their `m=harness;` stub prefix and
have no main(); full_program / stdin_program modules keep their main().

Gate order (bank_pattern applies these in sequence and stops at the first
failure; check_pattern reports every static gate it can evaluate):
  shape -> compile -> signature -> build -> tests -> idiom -> structure ->
  pattern (lint net 0, hints pass, exemptions honoured) -> min_bytes ->
  proxy_tokens -> diff (131.14 diff_check, guarded) -> perf (Tier-1)

Guarded imports (in-flight sibling stories):
  metrics.proxy_tokens          131.10  (present on disk; guarded anyway)
  diff_check                    131.14  (absent -> gate reports "n/a", TODO)
  pattern_autofix.tier1_perf    131.14  (absent -> local median-of-3 rusage)
"""
import hashlib, json, os, re, resource, statistics, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import idiom_judge                                    # noqa: E402
import metrics                                        # noqa: E402
import driver as drv                                  # noqa: E402
from assemble import sanitize                         # noqa: E402
from audit import _exec_tests, style_mandate          # noqa: E402
from run_shard import validate_one_gates, TKC         # noqa: E402
from validate import run_stdin_cases, STDIN_CASE_TIMEOUT  # noqa: E402

# ---------------------------------------------------------------- guarded ---
try:                                                  # 131.14 differential check
    import diff_check                                 # noqa: E402
    HAVE_DIFF_CHECK = True
except ImportError:                                   # TODO(131.14): drop the guard once diff_check.py lands
    diff_check = None
    HAVE_DIFF_CHECK = False

try:                                                  # 131.14 Tier-1 perf helper (gate-shaped)
    from pattern_autofix import tier1_perf as _autofix_tier1  # noqa: E402
except Exception:                                     # noqa: BLE001 — absent name or a mid-edit sibling
    _autofix_tier1 = None

try:                                                  # 131.14 Tier-1 perf over diff_check-prepared programs
    from pattern_autofix import perf_compare as _autofix_perf_compare  # noqa: E402
except Exception:                                     # noqa: BLE001
    _autofix_perf_compare = None
HAVE_AUTOFIX_PERF = _autofix_perf_compare is not None

_proxy_tokens = getattr(metrics, "proxy_tokens", None)   # 131.10 (guarded)

# ------------------------------------------------------------------ paths ---
CORPUS = os.environ.get("TOKE_CORPUS_DIR",
                        os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04"))
TOKE_ROOT = os.environ.get("TOKE_ROOT", os.path.expanduser("~/tk/toke"))
CATALOGUE_PATH = os.path.join(TOKE_ROOT, "patterns", "catalogue.json")
LINT_RULES_DOC = os.path.join(TOKE_ROOT, "docs", "lint-rules-v1.md")
CARD_PATH = os.path.join(HERE, "syntax_card.md")
WAVE = "agent"
STORY = "131.15"
BATCH_SIZE = 20
MAX_BATCHES = 100            # REBUILD_STATUS wave rule: <=100 batches per wave
MAX_ATTEMPTS = 2             # one retry (bank_pattern rejects -> retry_queue once)
DONE_STATUSES = ("rewritten", "unchanged")
GEN_PREFIX = "pat_"
# Tier-1 perf flag: ratio > 1.5 AND absolute delta > 5 ms / 1 MB (131.15 row)
PERF_RATIO = 1.5
PERF_WALL_MS = 5.0
PERF_RSS_KB = 1024
PERF_RUNS = 3


class Paths:
    """All on-disk locations for one wave, overridable for tests."""

    def __init__(self, corpus=CORPUS, workdir=None, ledger=None, replaced=None,
                 manifest=None, catalogue=CATALOGUE_PATH, card=CARD_PATH,
                 toke_root=TOKE_ROOT, tkc=TKC):
        self.corpus = corpus
        self.workdir = workdir or os.path.join(corpus, "work", "pattern_131")
        self.ledger = ledger or os.path.join(corpus, "ledger", "rewrite_131.jsonl")
        self.replaced = replaced or os.path.join(corpus, "audit", "replaced", "131")
        self.manifest = manifest or os.path.join(corpus, "MANIFEST.jsonl")
        self.catalogue = catalogue
        self.card = card
        self.toke_root = toke_root
        self.tkc = tkc

    def sub(self, *parts):
        return os.path.join(self.workdir, *parts)

    def record(self, category, tid):
        return os.path.join(self.corpus, category, tid + ".json")


# ------------------------------------------------------------------- shas ---
def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_text(s):
    return sha256_bytes(s.encode("utf-8"))


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def short(sha):
    return sha[:12] if sha else None


def tkc_stamp(tkc=TKC, toke_root=TOKE_ROOT):
    """{tkc_sha (short sha256 of the binary — the rescore_131 convention),
    tkc_sha256, tkc_version, toke_git}. Never raises."""
    out = {"tkc_sha": None, "tkc_sha256": None, "tkc_version": None, "toke_git": None}
    try:
        out["tkc_sha256"] = sha256_file(tkc)
        out["tkc_sha"] = short(out["tkc_sha256"])
    except OSError:
        pass
    try:
        r = subprocess.run([tkc, "--version"], capture_output=True, text=True, timeout=10)
        out["tkc_version"] = (r.stdout or r.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        pass
    try:
        r = subprocess.run(["git", "-C", toke_root, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            out["toke_git"] = r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- ledger ---
def read_ledger(path):
    """{task_id: {"attempts": n, "done": bool, "last": entry}} over the
    append-only rewrite ledger, wave == agent lines only. `attempts` is the
    max cumulative attempts seen; `done` when any line is rewritten/unchanged."""
    state = {}
    if not path or not os.path.exists(path):
        return state
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("wave") != WAVE:
            continue
        st = state.setdefault(e["task_id"], {"attempts": 0, "done": False, "last": None})
        st["attempts"] = max(st["attempts"], int(e.get("attempts") or 0))
        st["done"] = st["done"] or e.get("status") in DONE_STATUSES
        st["last"] = e
    return state


def append_ledger(path, entry):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------- bucket ---
def load_bucket(path, bucket="AGENT"):
    """Rows of audit/pattern_sweep.jsonl (131.13 schema: task_id, category,
    task_type, violations[{rule,severity,span}], exemptions, proxy_tokens,
    est_saving, perf_sensitive, bucket) whose bucket matches. Keyed by task_id;
    a later line for the same id wins (resumable sweeps append)."""
    rows = {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if bucket and r.get("bucket") != bucket:
            continue
        rows[r["task_id"]] = r
    return rows


def bucket_rules(row):
    """Ordered unique rule ids the sweep flagged on this row."""
    out = []
    for v in row.get("violations") or []:
        rule = v.get("rule") if isinstance(v, dict) else v
        if rule and rule not in out:
            out.append(rule)
    return out


def bucket_exemptions(row):
    """Rule ids the sweep recorded as exempt (list of ids, list of {rule,..}
    dicts, or {rule: reason} map — all accepted)."""
    ex = row.get("exemptions") or []
    if isinstance(ex, dict):
        return [k for k in ex]
    out = []
    for v in ex:
        rule = v.get("rule") if isinstance(v, dict) else v
        if rule and rule not in out:
            out.append(rule)
    return out


# ------------------------------------------------------------- catalogue ---
_RULE_ROW = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|\s*$")


def load_rule_texts(doc_path=LINT_RULES_DOC):
    """{rule: (severity, one-line description)} from the lint-rules-v1.md
    reference table; {} when the doc is absent."""
    out = {}
    try:
        for line in open(doc_path, encoding="utf-8"):
            m = _RULE_ROW.match(line.rstrip("\n"))
            if m:
                out[m.group(1)] = (m.group(2), m.group(3).replace("\\|", "|"))
    except OSError:
        pass
    return out


def load_catalogue(path=CATALOGUE_PATH):
    """{sha, sha256, entries, by_id, by_rule} — by_rule maps a lint rule id to
    the catalogue entries that carry it (several entries may share a rule)."""
    raw = open(path, "rb").read()
    cat = json.loads(raw)
    entries = cat["entries"] if isinstance(cat, dict) else cat
    by_id, by_rule = {}, {}
    for e in entries:
        by_id[e["id"]] = e
        rule = (e.get("lint") or {}).get("rule")
        if rule:
            by_rule.setdefault(rule, []).append(e)
    return {"sha256": sha256_bytes(raw), "sha": short(sha256_bytes(raw)),
            "entries": entries, "by_id": by_id, "by_rule": by_rule,
            "protocol": cat.get("protocol") if isinstance(cat, dict) else None}


def canonical_candidate(entry):
    form = (entry.get("verdict") or {}).get("canonical")
    for c in entry.get("candidates") or []:
        if c.get("form") == form:
            return c
    return (entry.get("candidates") or [None])[0]


def fixture_source(entry, toke_root=TOKE_ROOT, strip_main=True):
    """The canonical form's fixture source (patterns/<id>/<form>.tk), with the
    benchmark main() dropped so only the pattern function(s) are shown."""
    c = canonical_candidate(entry)
    if not c or not c.get("fixture"):
        return None
    path = os.path.join(toke_root, c["fixture"])
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return None
    if strip_main:
        lines = src.splitlines()
        cut = next((i for i, l in enumerate(lines) if l.startswith("f=main(")), None)
        if cut is not None:
            lines = lines[:cut]
        lines = [l for l in lines if not l.startswith(("m=", "i="))]
        src = "\n".join(lines).strip("\n")
    return src


def render_entry(entry, toke_root=TOKE_ROOT):
    """One catalogue entry as prompt text: id, card rule, intent, applicability,
    canonical form + its fixture source."""
    c = canonical_candidate(entry) or {}
    lint = entry.get("lint") or {}
    lines = [f"### pattern `{entry['id']}`  (lint rule: {lint.get('rule') or 'none'}"
             f"{', ' + lint['severity'] if lint.get('severity') else ''})",
             f"card rule: {entry.get('card_rule', '')}",
             f"intent: {entry.get('intent', '')}",
             f"applicability: {entry.get('applicability', '')}"]
    hot = (entry.get("verdict") or {}).get("choose_hot_path_when")
    if hot:
        lines.append(f"choose hot-path form when: {hot}")
    lines.append(f"canonical form ({c.get('form', '?')}): {c.get('label', '')}")
    src = fixture_source(entry, toke_root)
    if src:
        lines += ["```", src, "```"]
    return "\n".join(lines)


def entries_for_rules(cat, rules, pattern_ids=()):
    """Catalogue entries to inline for the flagged rule ids (+ explicit
    pattern ids), in first-seen order, no duplicates."""
    out, seen = [], set()
    for rule in rules:
        for e in cat["by_rule"].get(rule, []):
            if e["id"] not in seen:
                seen.add(e["id"])
                out.append(e)
    for pid in pattern_ids:
        e = cat["by_id"].get(pid)
        if e and e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out


def entries_for_row(cat, rules, row):
    """Entries to inline for a sweep row: for each flagged rule, ONLY the
    entries the sweep named via `violations[].pattern_id` when it did, else
    every catalogue entry carrying that rule (several may). First-seen order,
    no duplicates."""
    pid_by_rule = {}
    for v in row.get("violations") or []:
        if isinstance(v, dict) and v.get("rule") and v.get("pattern_id"):
            pid_by_rule.setdefault(v["rule"], []).append(v["pattern_id"])
    out, seen = [], set()
    for rule in rules:
        named = [cat["by_id"][p] for p in pid_by_rule.get(rule, []) if p in cat["by_id"]]
        for e in named or cat["by_rule"].get(rule, []):
            if e["id"] not in seen:
                seen.add(e["id"])
                out.append(e)
    for pid in row.get("patterns") or []:
        e = cat["by_id"].get(pid)
        if e and e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out


# -------------------------------------------------------------- analysis ---
def analyse_source(src, tmpdir, tag="cur"):
    """metrics.analyse on a source string (writes a temp .tk). None when the
    AST is unavailable (does not compile)."""
    path = os.path.join(tmpdir, tag + ".tk")
    with open(path, "w") as f:
        f.write(src)
    try:
        return metrics.analyse(path, src)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def live_rules(struct, exempt=()):
    """(must_fix, hints, exempt_hit) rule-id lists from a metrics.analyse
    result: error/warning pattern hits net of exemptions; hints; exempt hits."""
    hits = idiom_judge.pattern_hits(struct.get("lint") or []) if struct else []
    must, hints, exh = [], [], []
    for d in hits:
        r = d.get("rule")
        if r in exempt:
            if r not in exh:
                exh.append(r)
        elif d.get("severity") in idiom_judge.GATE_SEVERITIES:
            if r not in must:
                must.append(r)
        elif r not in hints:
            hints.append(r)
    return must, hints, exh


def exempt_rules(spec, row=None):
    """Union of the spec's style-mandate exemptions and the sweep row's."""
    out = list(idiom_judge.mandate_exempt_rules(style_mandate(spec)))
    for r in bucket_exemptions(row or {}):
        if r not in out:
            out.append(r)
    return out


def expected_output_lines(spec):
    """Prompt text describing the test lock for the task type, or the
    not-executable note."""
    ttype = spec.get("task_type", "full_program")
    tcs = spec.get("test_cases") or []
    if not tcs:
        return ["NO TEST LOCK for this record (not executable): the gates are compile + "
                "build + --min only. Behaviour must still be identical — change only "
                "form, never semantics."]
    lines = []
    if ttype == "single_function":
        lines.append("The harness appends a main() that calls the target function on each "
                     "test input and prints one line per result (bool prints as 1/0; array "
                     "results print one line per element). Expected, in order:")
        for tc in tcs:
            lines.append(f"  inputs={json.dumps(tc.get('inputs'))} -> expected "
                         f"{json.dumps(tc.get('expected'))}")
        lines.append(f"  (as printed lines: {json.dumps(drv.expected_lines(spec))})")
    elif ttype == "stdin_program":
        lines.append("main() is run once per test case with the input on stdin; the whole "
                     "stdout must match exactly (exit 0):")
        for i, tc in enumerate(drv.stdin_cases(spec)):
            lines.append(f"  case {i}: stdin={json.dumps(tc['input'])} -> stdout="
                         f"{json.dumps(tc['expected_output'])}")
    else:
        lines.append("main() must print exactly one line per test case, in order (exit 0, "
                     "no extra lines):")
        for tc in tcs:
            lines.append(f"  inputs={json.dumps(tc.get('inputs'))} -> expected "
                         f"{json.dumps(tc.get('expected'))}")
    return lines


# ----------------------------------------------------------------- gates ---
def strip_harness_prefix(src):
    """Remove the single_function stub prefix (assemble() re-adds the canonical
    one). Tolerates a non-canonical prefix: drops the `m=harness;` line and any
    following import / context-stub one-liners."""
    n = idiom_judge.stub_prefix_len(src)
    if n:
        return src[n:]
    if not src.startswith("m=harness;"):
        return src
    lines = src.splitlines(keepends=True)
    i = 1
    while i < len(lines) and (lines[i].startswith("i=") or
                              re.match(r"^f=[a-z0-9]+\([^)]*\):\S+\{<[^{}]*\};\s*$", lines[i])):
        i += 1
    return "".join(lines[i:])


def normalise_candidate(spec, cand):
    """(raw_for_validate, shape_error). Shape rules: single_function keeps no
    main() (the harness supplies it); full_program/stdin_program must define
    main(); fences/prose are stripped."""
    ttype = spec.get("task_type", "full_program")
    src = sanitize(cand)
    if not src.strip():
        return None, "empty candidate"
    has_main = "f=main(" in src
    if ttype == "single_function":
        if has_main:
            return None, "single_function module must not define main() (the harness appends it)"
        return strip_harness_prefix(src), None
    if ttype in ("full_program", "stdin_program") and not has_main:
        return None, f"{ttype} must define f=main():i64"
    return src, None


def exec_single_function(spec, assembled_src, tmpdir, tag):
    """Driver-synthesised main() over the assembled module (audit_one path).
    Returns {"ran", "match", "reason", ...} or None when no test cases."""
    if not spec.get("test_cases"):
        return None
    dsrc, err = drv.append_main(spec, assembled_src)
    if err:
        return {"ran": False, "match": False, "reason": "driver: " + err}
    g = _exec_tests(dsrc, drv.expected_lines(spec), tmpdir, tag)
    return {"ran": g.get("build", False), "match": bool(g.get("tests_new")),
            "reason": g.get("reason") if not g.get("tests_new") else None,
            "exit": g.get("exit"), "extra_lines": g.get("extra_lines")}


def static_and_test_gates(spec, cand, tmpdir, lint_gate=True):
    """shape -> validate_one_gates (compile, signature, build+tests for
    full_program/stdin_program, idiom, structure, pattern, lint) -> driver
    tests for single_function. Returns a result dict with an ordered `gates`
    dict, `record` (validate's fresh record; tk_source = normalised source),
    `struct`-derived metrics and the first failure `reason`."""
    res = {"gates": {}, "reason": None, "record": None, "tk_source": None,
           "pattern_hits": [], "hints": [], "other_warnings": [], "runtime": None}
    raw, shape_err = normalise_candidate(spec, cand)
    res["gates"]["shape"] = shape_err is None
    if shape_err:
        res["reason"] = "shape: " + shape_err
        return res
    record, ok, reason, gates = validate_one_gates(spec, raw, tmpdir, lint_gate=lint_gate)
    if record is None:
        res["gates"]["compile"] = False
        res["reason"] = reason
        return res
    res["record"] = record
    res["tk_source"] = record["tk_source"]
    regen = record["regen"]
    lint = list(regen.get("lint_pattern_violations") or [])
    res["pattern_hits"] = [d for d in lint if not d.get("suppressed")
                           and d.get("severity") in idiom_judge.GATE_SEVERITIES]
    res["hints"] = [d for d in lint if not d.get("suppressed")
                    and d.get("severity") not in idiom_judge.GATE_SEVERITIES]
    for k in ("compile", "signature", "build", "tests"):
        res["gates"][k] = gates.get(k)
    # single_function tests run through the driver (validate_one_gates only
    # executes full_program / stdin_program)
    if gates.get("compile") and spec.get("task_type") == "single_function" \
            and spec.get("test_cases"):
        rt = exec_single_function(spec, record["tk_source"], tmpdir, "sf_" + spec["task_id"])
        res["runtime"] = rt
        res["gates"]["build"] = bool(rt and rt.get("ran"))
        res["gates"]["tests"] = bool(rt and rt.get("match"))
        if rt and not rt.get("match") and not reason:
            reason = "runtime: " + str(rt.get("reason"))
    else:
        res["runtime"] = regen.get("runtime")
    for k in ("idiom", "structure", "pattern"):
        res["gates"][k] = gates.get(k)
    res["gates"]["lint_other"] = gates.get("lint")   # informational (not a gate)
    res["idiom"] = record["judge"]["score"]
    res["min_bytes"] = regen.get("min_bytes")
    res["proxy_tokens"] = regen.get("proxy_tokens")
    res["max_depth"] = regen.get("max_depth")
    res["lint_exempt"] = regen.get("lint_exempt") or []
    res["lint_warnings"] = regen.get("lint_warnings")
    res["lint_other_warnings"] = other_warnings_after(regen)
    if reason and not res["reason"] and not str(reason).startswith("lint:"):
        res["reason"] = reason                        # validate's lint verdict is re-judged by lint_other_gate
    return res


def size_gates(res, before):
    """min_bytes <= before, proxy_tokens <= before (None before/after = n/a)."""
    mb, pb = res.get("min_bytes"), res.get("proxy_tokens")
    b_mb, b_pb = (before or {}).get("min_bytes"), (before or {}).get("proxy_tokens")
    res["gates"]["min_bytes"] = None if mb is None or b_mb is None else mb <= b_mb
    res["gates"]["proxy_tokens"] = None if pb is None or b_pb is None else pb <= b_pb
    if res["gates"]["min_bytes"] is False and not res["reason"]:
        res["reason"] = f"min_bytes: {mb} > {b_mb} before"
    if res["gates"]["proxy_tokens"] is False and not res["reason"]:
        res["reason"] = f"proxy_tokens: {pb} > {b_pb} before"
    return res


def diff_gate(spec, orig_src, cand_src, tmpdir, **kw):
    """131.14 differential check (20 extra typed inputs, original vs candidate
    binary, identical stdout). Returns (verdict True/False/None, detail).
    None = diff_check.py not available yet."""
    if not HAVE_DIFF_CHECK:
        return None, "diff_check.py not present (131.14 in flight) — gate skipped"
    fn = getattr(diff_check, "differential", None) or getattr(diff_check, "compare", None)
    if fn is None:                                    # TODO(131.14): pin the entrypoint name
        return None, "diff_check.py present but no differential()/compare() entrypoint — gate skipped"
    try:
        out = fn(spec, orig_src, cand_src, tmpdir, **kw)
    except Exception as e:                            # noqa: BLE001 — never let the sibling story crash the bank
        return False, f"diff_check raised {type(e).__name__}: {e}"
    if isinstance(out, dict):
        v = out.get("identical", out.get("ok"))
        if v is None:                                 # unverifiable (not executable) — n/a, recorded
            return None, out.get("reason") or "unverifiable"
        return bool(v), out.get("reason") or "ok"
    if isinstance(out, tuple):
        return bool(out[0]), str(out[1]) if len(out) > 1 else "ok"
    return bool(out), "ok"


# ------------------------------------------------------------------ perf ---
def _build_executable(spec, src, tmpdir, tag):
    """Build the record (driver main appended for single_function). Returns
    the binary path or None."""
    ttype = spec.get("task_type", "full_program")
    if ttype == "single_function":
        src, err = drv.append_main(spec, src)
        if err:
            return None
    elif ttype not in ("full_program", "stdin_program"):
        return None
    tkpath = os.path.join(tmpdir, tag + ".tk")
    binpath = tkpath + ".bin"
    with open(tkpath, "w") as f:
        f.write(src)
    flags = list(spec.get("build_flags") or []) if ttype == "stdin_program" else []
    try:
        b = subprocess.run([TKC, tkpath, "-o", binpath] + flags, capture_output=True,
                           text=True, errors="replace", timeout=90)
    except subprocess.TimeoutExpired:
        return None
    finally:
        if os.path.exists(tkpath):
            os.unlink(tkpath)
    return binpath if b.returncode == 0 and os.path.exists(binpath) else None


def _rusage_run(binpath, stdin_text=None, cwd=None, timeout=15):
    """One run: (wall_ms, maxrss_kb) via os.wait4 (per-child rusage; macOS
    reports ru_maxrss in bytes, Linux in KB)."""
    t0 = time.perf_counter()
    p = subprocess.Popen([binpath], stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=cwd)
    try:
        if stdin_text is not None:
            p.stdin.write(stdin_text.encode("utf-8", "replace"))
            p.stdin.close()
        _, _, ru = os.wait4(p.pid, 0)
    except Exception:                                 # noqa: BLE001
        p.kill()
        p.wait()
        return None
    wall = (time.perf_counter() - t0) * 1000.0
    rss = ru.ru_maxrss / 1024.0 if sys.platform == "darwin" else float(ru.ru_maxrss)
    return wall, rss


def _perf_sample(binpath, spec, cwd):
    """Total wall over the record's test inputs (one run for full_program /
    single_function, one per case for stdin_program) and the max RSS seen."""
    ttype = spec.get("task_type", "full_program")
    if ttype == "stdin_program":
        wall = rss = 0.0
        for tc in drv.stdin_cases(spec):
            r = _rusage_run(binpath, tc["input"], cwd)
            if r is None:
                return None
            wall += r[0]
            rss = max(rss, r[1])
        return wall, rss
    return _rusage_run(binpath, None, cwd)


def tier1_perf(spec, orig_src, cand_src, tmpdir, runs=PERF_RUNS):
    """Tier-1 perf gate: median of `runs` original-vs-candidate runs on the
    record's test inputs (wall ms + max RSS KB). verdict fail iff ratio >
    PERF_RATIO AND absolute delta > PERF_WALL_MS / PERF_RSS_KB for either
    metric; n/a for records that cannot be executed. Uses 131.14's helper when
    it exists (same return shape expected: {tier, ratio, verdict, ...})."""
    if _autofix_tier1 is not None:
        try:
            out = _autofix_tier1(spec, orig_src, cand_src, tmpdir)
            if isinstance(out, dict) and "verdict" in out:
                out.setdefault("tier", "1")
                out.setdefault("via", "pattern_autofix.tier1_perf")
                return out
        except Exception as e:                        # noqa: BLE001 — fall back to the local check
            pass
    res = {"tier": "1", "ratio": None, "verdict": "n/a", "via": "local-median-of-3"}
    if not spec.get("test_cases"):
        res["detail"] = "not executable (no test cases)"
        return res
    ob = _build_executable(spec, orig_src, tmpdir, "perf_orig")
    cb = _build_executable(spec, cand_src, tmpdir, "perf_cand")
    try:
        if not ob or not cb:
            res["detail"] = "build failed (orig=%s cand=%s)" % (bool(ob), bool(cb))
            return res
        with tempfile.TemporaryDirectory(dir=tmpdir) as cwd:
            o_w, o_r, c_w, c_r = [], [], [], []
            for _ in range(runs):                     # interleaved to share cache state
                so = _perf_sample(ob, spec, cwd)
                sc = _perf_sample(cb, spec, cwd)
                if so is None or sc is None:
                    res["detail"] = "run failed"
                    return res
                o_w.append(so[0]); o_r.append(so[1]); c_w.append(sc[0]); c_r.append(sc[1])
        ow, cw = statistics.median(o_w), statistics.median(c_w)
        orr, crr = statistics.median(o_r), statistics.median(c_r)
        wall_ratio = (cw / ow) if ow > 0 else (1.0 if cw == 0 else float("inf"))
        rss_ratio = (crr / orr) if orr > 0 else (1.0 if crr == 0 else float("inf"))
        wall_flag = wall_ratio > PERF_RATIO and (cw - ow) > PERF_WALL_MS
        rss_flag = rss_ratio > PERF_RATIO and (crr - orr) > PERF_RSS_KB
        res.update({"ratio": round(max(wall_ratio, rss_ratio), 3),
                    "wall_ms": {"orig": round(ow, 3), "cand": round(cw, 3), "ratio": round(wall_ratio, 3)},
                    "rss_kb": {"orig": round(orr, 1), "cand": round(crr, 1), "ratio": round(rss_ratio, 3)},
                    "verdict": "fail" if (wall_flag or rss_flag) else "pass"})
        if wall_flag or rss_flag:
            res["detail"] = ("wall" if wall_flag else "rss") + " regression"
        return res
    finally:
        for b in (ob, cb):
            if b and os.path.exists(b):
                os.unlink(b)


# ---------------------------------------------------------------- before ---
def before_metrics(rec, rec_path, tmpdir):
    """Live metrics of the record on disk: min_bytes, proxy_tokens, idiom,
    file/source shas. Falls back to the record's stored regen values when
    tkc analysis is unavailable."""
    src = rec["tk_source"]
    regen = rec.get("regen") or {}
    out = {"source_sha256": sha256_text(src), "file_sha256": sha256_file(rec_path),
           "min_bytes": regen.get("min_bytes"), "proxy_tokens": regen.get("proxy_tokens"),
           "idiom": (rec.get("judge") or {}).get("score"), "struct": None}
    struct = analyse_source(src, tmpdir, "before_" + rec.get("task_id", "x"))
    if struct:
        out["struct"] = struct
        out["min_bytes"] = struct.get("min_bytes")
        if struct.get("proxy_tokens") is not None:
            out["proxy_tokens"] = struct["proxy_tokens"]
        out["idiom"] = idiom_judge.score(src, struct.get("lint"))[0]
        out["lint_other_warnings"] = other_warnings(struct.get("lint"))
        out["max_depth"] = struct.get("max_depth")
    return out


def summary_line(res):
    """`gate=verdict` list in gate order, for CLI output."""
    marks = {True: "ok", False: "FAIL", None: "n/a"}
    return " ".join(f"{k}={marks.get(v, v)}" for k, v in res["gates"].items())


# ------------------------------------------------- 131.15 bank helpers ---
NEVER_EXEMPT = ("discarded-value-result",)   # quality_rubric "Exemptions": never exempt
_BASE_ID = re.compile(r"^(A-[A-Z]+-\d+)v\d+$")


def bucket_pattern_ids(row):
    """Ordered unique catalogue pattern ids the sweep attached to the row's
    violations (131.13 `violations[].pattern_id`) plus any top-level
    `patterns` list."""
    out = []
    for v in row.get("violations") or []:
        pid = v.get("pattern_id") if isinstance(v, dict) else None
        if pid and pid not in out:
            out.append(pid)
    for pid in row.get("patterns") or []:
        if pid and pid not in out:
            out.append(pid)
    return out


def bucket_est_saving(row):
    """131.13 `est_saving_tokens` (older sweeps: `est_saving`)."""
    v = row.get("est_saving_tokens")
    return row.get("est_saving") if v is None else v


def other_warnings(diags):
    """Lint warnings that are NOT pattern rules (unused-let, unused-import,
    …) in a metrics.lint diagnostic list."""
    return sum(1 for d in diags or []
               if d.get("severity") == "warning" and d.get("rule") not in idiom_judge.PATTERN_RULES
               and not d.get("suppressed"))


def other_warnings_after(regen):
    """Same count from a validate_one_gates record (lint_gate=True): total
    warnings minus the un-suppressed pattern warnings. None when unknown."""
    total = regen.get("lint_warnings")
    if total is None:
        return None
    pat = sum(1 for d in regen.get("lint_pattern_violations") or []
              if d.get("severity") == "warning" and not d.get("suppressed"))
    return max(0, total - pat)


def apply_exemptions(res, exempt):
    """Re-judge the pattern gate net of the wave's exemptions (spec mandate +
    sweep row): `pattern_hits` drops exempt rules (never NEVER_EXEMPT), the
    gate + reason follow. validate_one_gates only knew the spec mandate."""
    if res.get("record") is None:
        return res
    ex = [r for r in (exempt or ()) if r not in NEVER_EXEMPT]
    hits = res.get("pattern_hits") or []
    kept = [d for d in hits if d.get("rule") not in ex]
    dropped = sorted({d.get("rule") for d in hits if d.get("rule") in ex})
    res["pattern_hits"] = kept
    res["lint_exempt"] = sorted(set(res.get("lint_exempt") or []) | set(dropped))
    res["gates"]["pattern"] = not kept
    if kept and not res.get("reason"):
        res["reason"] = "pattern: " + idiom_judge.violation_summary(kept)
    elif not kept and str(res.get("reason") or "").startswith("pattern:"):
        res["reason"] = None
    return res


def lint_other_gate(res, before):
    """Non-pattern lint warnings must not grow: after <= before (None = n/a).
    Replaces validate's absolute `lint` verdict (a pre-existing unused-import
    on the record is not the rewrite's fault; a NEW one is)."""
    after = res.get("lint_other_warnings")
    b = (before or {}).get("lint_other_warnings")
    if after is None or b is None:
        res["gates"]["lint_other"] = None
        return res
    res["gates"]["lint_other"] = after <= b
    if after > b and not res.get("reason"):
        res["reason"] = f"lint: {after} non-pattern warnings > {b} before"
    return res


def a_test_for(task_id, corpus):
    """audit/a_tests/<base>.json (python_ref cross-check for the differential)
    or None."""
    m = _BASE_ID.match(task_id)
    p = os.path.join(corpus, "audit", "a_tests", (m.group(1) if m else task_id) + ".json")
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def _norm_perf(out, via):
    out = dict(out or {})
    out.setdefault("tier", "1")
    out["via"] = via
    if "verdict" not in out:
        out["verdict"] = "fail" if out.get("flag") else "pass"
    out.setdefault("ratio", None)
    return out


def diff_and_perf(spec, orig_src, cand_src, tmpdir, task_id=None, a_test=None,
                  runs=PERF_RUNS):
    """Differential + Tier-1 perf in one pass, sharing diff_check's prepared
    programs when both 131.14 helpers are importable (diff_check.prepare_program
    / compare + pattern_autofix.perf_compare); otherwise diff_gate + the local
    median-of-3. Returns {"diff": {"verdict": True|False|None, "reason", "via",
    "checks"}, "perf": {tier, ratio, verdict, via, ...}}. Never raises."""
    tid = task_id or spec.get("task_id", "task")
    diff = {"verdict": None, "reason": None, "via": None, "checks": None}
    perf = None
    shared = (HAVE_DIFF_CHECK and HAVE_AUTOFIX_PERF
              and hasattr(diff_check, "prepare_program") and hasattr(diff_check, "compare"))
    if shared:
        td = tempfile.mkdtemp(prefix="dp_", dir=tmpdir)
        porig = pcand = None
        try:
            gen_inputs, gen_why = (None, None)
            if spec.get("task_type") == "single_function" and hasattr(diff_check, "generate_inputs"):
                gen_inputs, gen_why = diff_check.generate_inputs(spec, tid)
            porig = diff_check.prepare_program(orig_src, spec, tid, td, "orig", gen_inputs)
            pcand = diff_check.prepare_program(cand_src, spec, tid, td, "cand", gen_inputs)
            out = diff_check.compare(porig, pcand, spec, tid, td, gen_inputs, a_test)
            v = out.get("verdict")
            diff.update({"verdict": {"identical": True, "diverged": False}.get(v),
                         "reason": out.get("reason") or v, "via": "diff_check.compare",
                         "checks": out.get("checks"), "first_diff": out.get("first_diff")})
            if gen_why and diff["checks"] is not None:
                diff["checks"]["generated"] = {"n": 0, "identical": None, "skipped": gen_why}
            if diff["verdict"] is not False:
                pr = _autofix_perf_compare(porig, pcand, td, runs=runs)
                perf = (_norm_perf(pr, "pattern_autofix.perf_compare") if pr else
                        {"tier": "1", "ratio": None, "verdict": "n/a", "via": "pattern_autofix.perf_compare",
                         "detail": (porig.reason or pcand.reason or "not runnable")})
        except Exception as e:                        # noqa: BLE001 — fall back to the separate gates
            diff = {"verdict": None, "reason": f"shared path raised {type(e).__name__}: {e}",
                    "via": "diff_check.compare", "checks": None}
            shared = False
        finally:
            for pr_ in (porig, pcand):
                if pr_ is not None:
                    try:
                        pr_.cleanup()
                    except Exception:                 # noqa: BLE001
                        pass
            import shutil
            shutil.rmtree(td, ignore_errors=True)
    if not shared:
        v, why = diff_gate(spec, orig_src, cand_src, tmpdir, task_id=tid, a_test=a_test)
        diff = {"verdict": v, "reason": why, "via": "diff_gate" if HAVE_DIFF_CHECK else None,
                "checks": None}
    if perf is None and diff["verdict"] is not False:
        try:
            perf = tier1_perf(spec, orig_src, cand_src, tmpdir, runs=runs)
        except Exception as e:                        # noqa: BLE001
            perf = {"tier": "1", "ratio": None, "verdict": "n/a", "via": "local-median-of-3",
                    "detail": f"raised {type(e).__name__}: {e}"}
    if perf is None:
        perf = {"tier": "1", "ratio": None, "verdict": "n/a", "via": None, "detail": "diverged — not measured"}
    return {"diff": diff, "perf": perf}


def perf_gate(res, perf, perf_sensitive=False):
    """gates.perf from a Tier-1 result (fail only on verdict == fail); records
    the Tier-2 flag (131.5 runs it serially after the wave, never here)."""
    res["perf"] = perf
    res["gates"]["perf"] = None if perf.get("verdict") == "n/a" else perf.get("verdict") == "pass"
    res["tier2_flag"] = bool(perf_sensitive)
    if res["gates"]["perf"] is False and not res.get("reason"):
        res["reason"] = f"perf: tier-1 {perf.get('detail') or 'regression'} ratio {perf.get('ratio')}"
    return res


def diff_gate_apply(res, diff):
    res["diff"] = diff
    res["gates"]["diff"] = diff.get("verdict")
    if diff.get("verdict") is False and not res.get("reason"):
        res["reason"] = f"diff: {diff.get('reason')}"
    return res

