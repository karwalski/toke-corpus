#!/usr/bin/env python3
"""Epic 131.70 — shared plumbing for the 131.17 library pattern-rewrite wave.

Used by library_prep.py (prompt + batch prep) and bank_library.py (main-thread
banking). Nothing here writes to the corpus or to toke-test-programs; the only
writers are library_prep (its own workdir) and bank_library --bank.

WHY THIS FILE EXISTS INSTEAD OF pattern_common
----------------------------------------------
The corpus wave (131.15) had to be withdrawn because its bank gate,
bank_pattern.diff_check, demands the candidate's stdout be byte-identical to
the ORIGINAL program's stdout — which makes a correct fix unbankable wherever
the original is the defect (131.69). The library path is clean of that defect:
run_shard.validate_one_gates -> validate.run_stdin_cases compares the
candidate against the manifest's independently authored `expected_output`
(validate.py:300), never against the original.

To keep it that way, and to make "no original-output comparison exists in this
path" a one-grep claim, NOTHING on the library path imports pattern_common,
bank_pattern, diff_check or pattern_autofix. The manifest is the oracle. The
ONLY thing the original program is used for here is:
  * a compile-time size/lint/idiom baseline (`before_metrics`), and
  * a Tier-1 wall/RSS timing baseline (`tier1_perf`) — timings only, the
    original's stdout is discarded (subprocess.DEVNULL).
Both are deltas for reporting/regression, not oracles. If you ever need the
original's *output* for anything but reporting a delta, stop: that is the
131.69 defect coming back.

Imports are confined to modules with no differential machinery in them:
run_shard, validate, metrics, idiom_judge, tkc_pin.

NOT assemble.sanitize. That helper deletes EVERY "```" run anywhere in the
text, which silently corrupts a library program that handles markdown —
L-MED-036, L-MED-129 and L-MSG-053 each carry "```" inside a string literal,
and sanitising them changed their output (MED-036: 7 lines where the manifest
wants 2). `normalise_source` below strips a wrapping fence and leading prose
only, and never touches the body.

CARVE-OUTS
  NUL_CARVE_OUT (131.32)  L-DAT-050 L-DEV-115 L-SEC-122 L-SYS-125 carry raw
      0x00 bytes inside string literals. `tkc --min` is NUL-terminated, so it
      truncates: DEV-115 reports min_bytes 138 for a 2,597-byte program. The
      "before" size baseline is therefore not merely absent but CORRUPT, and a
      correct rewrite (NUL -> escape) would blow the <=-before gates. These
      four get gates.min_bytes / gates.proxy_tokens = None (n/a), explicitly,
      and carry `nul_carve_out: true` in meta, results and provenance.
  EXCLUDED (127.64)       L-GAZ-117 (deterministic segfault) and L-DEV-123
      (E4031) fail on the pinned tkc as a compiler regression filed separately.
      Excluded from the wave rather than worked around.
"""
import hashlib, json, os, re, statistics, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import idiom_judge                                   # noqa: E402
import metrics                                       # noqa: E402
import run_shard                                     # noqa: E402
import validate                                      # noqa: E402
import tkc_pin                                       # noqa: E402
from run_shard import validate_one_gates             # noqa: E402

TKC = tkc_pin.default_tkc()

# ------------------------------------------------------------------ paths ---
CORPUS = os.environ.get("TOKE_CORPUS_DIR",
                        os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04"))
TOKE_ROOT = os.environ.get("TOKE_ROOT", os.path.expanduser("~/tk/toke"))
LIB_ROOT = os.environ.get("TOKE_TEST_PROGRAMS",
                          os.path.expanduser("~/tk/toke-test-programs"))
CATALOGUE_PATH = os.path.join(TOKE_ROOT, "patterns", "catalogue.json")
LINT_RULES_DOC = os.path.join(TOKE_ROOT, "docs", "lint-rules-v1.md")
CARD_PATH = os.path.join(HERE, "syntax_card.md")
SHARD_PATH = os.path.join(CORPUS, "shards", "library_131.jsonl")
SWEEP_PATH = os.path.join(CORPUS, "audit", "pattern_sweep.jsonl")

STORY = "131.17"
TOOLING_STORY = "131.70"
WAVE = "library_rewrite"          # distinct from 131.15's "agent" and 131.18's "library"
BATCH_SIZE = 20
MAX_BATCHES = 100
MAX_ATTEMPTS = 2
DONE_STATUSES = ("rewritten", "unchanged")
CANDIDATE_NAME = "solution.131.tk"
ORIGINAL_NAME = "solution.tk"
ARCHIVE_SUBDIR = os.path.join("results", "solutions-pre131")
SOLUTIONS_SUBDIR = os.path.join("results", "solutions")

# Tier-1 perf flag: ratio > 1.5 AND absolute delta > 5 ms / 1 MB (131.15 row)
PERF_RATIO = 1.5
PERF_WALL_MS = 5.0
PERF_RSS_KB = 1024
PERF_RUNS = 3
PERF_CONFIRM_RUNS = 9   # a flagged first pass is re-measured before it rejects

NUL_CARVE_OUT = ("L-DAT-050", "L-DEV-115", "L-SEC-122", "L-SYS-125")   # 131.32
NUL_CARVE_REASON = ("131.32 raw NUL bytes: `tkc --min` truncates at the NUL, so the "
                    "compile-time before baseline is corrupt — min_bytes / proxy_tokens "
                    "gates are n/a for this program")
EXCLUDED = {                                                            # 127.64
    "L-GAZ-117": "127.64: deterministic segfault on the pinned tkc (compiler regression)",
    "L-DEV-123": "127.64: E4031 on the pinned tkc (compiler regression)",
}

NEVER_EXEMPT = ("discarded-value-result",)   # quality_rubric "Exemptions": never exempt

# Gate order. The first eight come from run_shard.validate_one_gates
# (lint_gate=True); the last four are the wave's own.
VALIDATE_GATES = ("shape", "compile", "build", "tests", "idiom", "structure",
                  "pattern", "lint")
WAVE_GATES = ("lint_net", "min_bytes", "proxy_tokens", "perf")
BANK_GATES = VALIDATE_GATES + WAVE_GATES


class LibraryPaths:
    """Every on-disk location for the library wave, overridable for tests."""

    def __init__(self, corpus=CORPUS, lib_root=LIB_ROOT, workdir=None, ledger=None,
                 shard=None, sweep=None, catalogue=CATALOGUE_PATH, card=CARD_PATH,
                 toke_root=TOKE_ROOT, tkc=None):
        self.corpus = corpus
        self.lib_root = lib_root
        self.workdir = workdir or os.path.join(corpus, "work", "library_rewrite_131")
        self.ledger = ledger or os.path.join(corpus, "ledger", "rewrite_131_library.jsonl")
        self.shard = shard or os.path.join(corpus, "shards", "library_131.jsonl")
        self.sweep = sweep or os.path.join(corpus, "audit", "pattern_sweep.jsonl")
        self.catalogue = catalogue
        self.card = card
        self.toke_root = toke_root
        self.tkc = tkc or TKC

    def sub(self, *parts):
        return os.path.join(self.workdir, *parts)

    def solution_dir(self, cat, lib_id):
        return os.path.join(self.lib_root, SOLUTIONS_SUBDIR, cat, lib_id)

    def original(self, cat, lib_id):
        return os.path.join(self.solution_dir(cat, lib_id), ORIGINAL_NAME)

    def candidate(self, cat, lib_id):
        return os.path.join(self.solution_dir(cat, lib_id), CANDIDATE_NAME)

    def archive(self, cat, lib_id):
        return os.path.join(self.lib_root, ARCHIVE_SUBDIR, cat, lib_id, ORIGINAL_NAME)


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


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def tkc_bin_sha(tkc=None):
    return tkc_pin.bin_sha(tkc or TKC)


def pin_tkc(*modules):
    """131.39: pin the compiler once per run, BEFORE any loop/pool. Rebinds TKC
    here and in every sibling module the gates exec. Keep the Pinned alive."""
    mods = [sys.modules[__name__], validate, metrics, idiom_judge, run_shard]
    for m in modules:
        if m is not None and m not in mods:
            mods.append(m)
    return tkc_pin.pin().install(*mods)


def tkc_stamp(tkc=None, toke_root=TOKE_ROOT):
    """{tkc_sha, tkc_sha256, tkc_bin_sha, tkc_version, toke_git}. Never raises."""
    tkc = tkc or TKC
    out = {"tkc_sha": None, "tkc_sha256": None, "tkc_bin_sha": None,
           "tkc_version": None, "toke_git": None}
    try:
        out["tkc_sha256"] = sha256_file(tkc)
        out["tkc_sha"] = short(out["tkc_sha256"])
        out["tkc_bin_sha"] = out["tkc_sha256"]
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


# ------------------------------------------------------------------ input ---
def load_specs(path=None):
    """{task_id: spec} over shards/library_131.jsonl (the manifest-derived specs
    — test_cases here are the ORACLE for the tests gate)."""
    out = {}
    for line in open(path or SHARD_PATH, encoding="utf-8"):
        line = line.strip()
        if line:
            s = json.loads(line)
            out[s["task_id"]] = s
    return out


def load_sweep(path=None, bucket="AGENT", source="library"):
    """{task_id: row} over audit/pattern_sweep.jsonl filtered to the wave's own
    rows. `source` MUST stay "library": the 2,469 AGENT rows are 1,272 library
    + 1,197 regen, and the regen half belongs to the corpus wave (131.15). A
    plain bucket filter sweeps the two together."""
    rows = {}
    for line in open(path or SWEEP_PATH, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if bucket and r.get("bucket") != bucket:
            continue
        if source and r.get("source") != source:
            continue
        rows[r["task_id"]] = r                  # later line wins (resumable sweeps append)
    return rows


def wave_rows(sweep_path=None, shard_path=None, bucket="AGENT", source="library"):
    """(rows, specs, excluded) — the wave's input set with 127.64 removed."""
    rows = load_sweep(sweep_path, bucket, source)
    specs = load_specs(shard_path)
    excluded = {t: EXCLUDED[t] for t in sorted(rows) if t in EXCLUDED}
    for t in excluded:
        rows.pop(t, None)
    return rows, specs, excluded


def is_nul_carve_out(task_id, row=None):
    """131.32: the four raw-NUL programs. Sweep rows carry `nul_bytes` too."""
    return task_id in NUL_CARVE_OUT or bool((row or {}).get("nul_bytes"))


# ----------------------------------------------------------------- ledger ---
def read_ledger(path, wave=WAVE):
    """{task_id: {attempts, done, last}} over the append-only wave ledger."""
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
        if wave and e.get("wave") != wave:
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


# -------------------------------------------------------------- catalogue ---
_RULE_ROW = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|\s*$")


def load_rule_texts(doc_path=LINT_RULES_DOC):
    """{rule: (severity, one-line description)} from lint-rules-v1.md."""
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
    """{sha, sha256, entries, by_id, by_rule}."""
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
            "entries": entries, "by_id": by_id, "by_rule": by_rule}


def canonical_candidate(entry):
    form = (entry.get("verdict") or {}).get("canonical")
    for c in entry.get("candidates") or []:
        if c.get("form") == form:
            return c
    return (entry.get("candidates") or [None])[0]


def fixture_source(entry, toke_root=TOKE_ROOT, strip_main=True):
    c = canonical_candidate(entry)
    if not c or not c.get("fixture"):
        return None
    try:
        src = open(os.path.join(toke_root, c["fixture"]), encoding="utf-8").read()
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
    """One catalogue entry as prompt text."""
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


def bucket_rules(row):
    """Ordered unique rule ids the sweep flagged on this row."""
    out = []
    for v in row.get("violations") or []:
        rule = v.get("rule") if isinstance(v, dict) else v
        if rule and rule not in out:
            out.append(rule)
    return out


def bucket_exemptions(row):
    ex = row.get("exemptions") or []
    if isinstance(ex, dict):
        return list(ex)
    out = []
    for v in ex:
        rule = v.get("rule") if isinstance(v, dict) else v
        if rule and rule not in out:
            out.append(rule)
    return out


def bucket_est_saving(row):
    v = row.get("est_saving_tokens")
    return row.get("est_saving") if v is None else v


def entries_for_row(cat, rules, row):
    """Catalogue entries to inline: the sweep's own `violations[].pattern_id`
    where it named one, else every entry carrying the rule."""
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


# --------------------------------------------------------------- analysis ---
def analyse_source(src, tmpdir, tag="cur"):
    """metrics.analyse over a source string. None when the AST is unavailable."""
    path = os.path.join(tmpdir, tag + ".tk")
    with open(path, "w") as f:
        f.write(src)
    try:
        return metrics.analyse(path, src, tkc=TKC)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def other_warnings(diags):
    """Lint warnings that are NOT pattern rules (unused-let, unused-import, …)."""
    return sum(1 for d in diags or []
               if d.get("severity") == "warning" and d.get("rule") not in idiom_judge.PATTERN_RULES
               and not d.get("suppressed"))


def other_warnings_after(regen):
    total = regen.get("lint_warnings")
    if total is None:
        return None
    pat = sum(1 for d in regen.get("lint_pattern_violations") or []
              if d.get("severity") == "warning" and not d.get("suppressed"))
    return max(0, total - pat)


def live_rules(struct, exempt=()):
    """(must_fix, hints, exempt_hit) from a metrics.analyse result."""
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
    out = list(idiom_judge.mandate_exempt_rules(idiom_judge.style_mandate(spec)))
    for r in bucket_exemptions(row or {}):
        if r not in out:
            out.append(r)
    return out


def before_metrics(src, sol_path, tmpdir, task_id="x", nul_carve_out=False):
    """Compile-time baseline of the ORIGINAL program: min_bytes, proxy_tokens,
    idiom, depth, lint counts, shas.

    This is a SIZE/LINT/IDIOM baseline only. The original's OUTPUT is never
    read here — the manifest's expected_output is the tests oracle (131.70).

    131.32 carve-out: for a raw-NUL program `tkc --min` truncates at the NUL,
    so min_bytes / proxy_tokens are corrupt; they are forced to None (and the
    truncated readings kept under `nul_truncated` for the record) so the
    <=-before gates come out n/a rather than rejecting a correct rewrite."""
    norm = normalise_source(src)
    out = {"source_sha256": sha256_text(src),
           "normalised_sha256": sha256_text(norm),
           "file_sha256": sha256_file(sol_path) if os.path.exists(sol_path) else None,
           "min_bytes": None, "proxy_tokens": None, "idiom": None, "max_depth": None,
           "lint_warnings": None, "lint_other_warnings": None,
           "nul_carve_out": bool(nul_carve_out), "struct": None}
    struct = analyse_source(norm, tmpdir, "before_" + task_id)
    if struct:
        out["struct"] = struct
        out["idiom"] = round(idiom_judge.score(norm, struct.get("lint"))[0], 2)
        out["max_depth"] = struct.get("max_depth")
        out["max_func_bytes"] = struct.get("max_func_bytes")
        out["lint_warnings"] = struct.get("lint_warnings")
        out["lint_other_warnings"] = other_warnings(struct.get("lint"))
        if nul_carve_out:
            out["nul_truncated"] = {"min_bytes": struct.get("min_bytes"),
                                    "proxy_tokens": struct.get("proxy_tokens")}
            out["baseline_note"] = NUL_CARVE_REASON
        else:
            out["min_bytes"] = struct.get("min_bytes")
            out["proxy_tokens"] = struct.get("proxy_tokens")
    return out


def expected_output_lines(spec):
    """Prompt text for the test lock. For a stdin_program the lock IS the
    manifest: one execution per case, stdin -> exact stdout, exit 0."""
    tcs = spec.get("test_cases") or []
    if not tcs:
        return ["NO TEST LOCK for this program (no manifest cases): the gates are "
                "compile + build + lint/structure only. Behaviour must still be "
                "identical — change only form, never semantics."]
    lines = ["main() is run once per manifest case with the input on stdin; the whole "
             "stdout must match the manifest's expected_output exactly (exit 0):"]
    for i, tc in enumerate(tcs):
        lines.append(f"  case {i}: stdin={json.dumps(tc.get('input', ''))} -> stdout="
                     f"{json.dumps(tc.get('expected_output', ''))}")
        if tc.get("fixtures"):
            lines.append(f"    fixtures: {json.dumps(tc['fixtures'])[:400]}")
    return lines


# ------------------------------------------------------------------ gates ---
_DECL = re.compile(r"\s*(m=|i=|f=|t=)")
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9]*\s*$")


def normalise_source(text):
    """Strip a markdown wrapper an LLM may have put AROUND the module, and
    nothing else.

    Drops every line before the first toke declaration (`m=`/`i=`/`f=`/`t=` —
    a toke module always starts with one) and any trailing fence/blank lines,
    then normalises the trailing newline. Deliberately NOT assemble.sanitize:
    that one does `re.sub("```[a-z]*", "", text)` over the whole text, which
    deletes fences living inside string literals. Three library programs
    (L-MED-036, L-MED-129, L-MSG-053) are markdown tools whose literals
    contain "```"; sanitising them corrupts the program and fails the manifest
    tests gate for a reason that has nothing to do with the rewrite.

    Applied identically to the original (before_metrics) and to the candidate,
    so the before/after size comparison is like-for-like."""
    lines = (text or "").splitlines()
    start = next((i for i, l in enumerate(lines) if _DECL.match(l)), None)
    if start is None:
        return (text or "").strip() + "\n"
    end = len(lines)
    while end > start and (not lines[end - 1].strip() or _FENCE.match(lines[end - 1])):
        end -= 1
    return "\n".join(lines[start:end]).rstrip() + "\n"


def normalise_candidate(spec, cand):
    """(raw_for_validate, shape_error). Library programs are all stdin_program:
    a wrapping fence/prose is stripped, main() required."""
    ttype = spec.get("task_type", "stdin_program")
    src = normalise_source(cand)
    if not src.strip():
        return None, "empty candidate"
    if ttype != "stdin_program":
        return None, f"unexpected task_type {ttype!r} on the library path"
    if "f=main(" not in src:
        return None, "stdin_program must define f=main():i64"
    return src, None


def static_and_test_gates(spec, cand, tmpdir, case_timeout=None):
    """shape -> run_shard.validate_one_gates(lint_gate=True): compile, build,
    the MANIFEST tests gate, idiom, structure, pattern, lint.

    The tests gate is validate.run_stdin_cases: each case's stdout is compared
    to the manifest's `expected_output`. There is no comparison against the
    original program anywhere on this path."""
    res = {"gates": {}, "reason": None, "record": None, "tk_source": None,
           "pattern_hits": [], "hints": [], "runtime": None}
    raw, shape_err = normalise_candidate(spec, cand)
    res["gates"]["shape"] = shape_err is None
    if shape_err:
        res["reason"] = "shape: " + shape_err
        return res
    record, ok, reason, gates = validate_one_gates(spec, raw, tmpdir, lint_gate=True,
                                                   case_timeout=case_timeout)
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
    for k in ("compile", "build", "tests", "idiom", "structure", "pattern", "lint"):
        res["gates"][k] = gates.get(k)
    res["runtime"] = regen.get("runtime")
    res["idiom"] = record["judge"]["score"]
    res["min_bytes"] = regen.get("min_bytes")
    res["proxy_tokens"] = regen.get("proxy_tokens")
    res["max_depth"] = regen.get("max_depth")
    res["lint_exempt"] = regen.get("lint_exempt") or []
    res["lint_warnings"] = regen.get("lint_warnings")
    res["lint_other_warnings"] = other_warnings_after(regen)
    res["reason"] = reason
    return res


def apply_exemptions(res, exempt):
    """Re-judge the pattern gate net of the wave's exemptions (spec mandate +
    sweep row); validate_one_gates only knew the spec mandate."""
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


def lint_net_gate(res, before):
    """The wave's lint gate: total lint warnings after == 0 (131.17 acceptance,
    and 131.18 ingest's precondition) AND not worse than the original. Records
    lint_before / lint_after / lint_delta for the wave report."""
    after = res.get("lint_warnings")
    b = (before or {}).get("lint_warnings")
    res["lint_before"] = b
    res["lint_after"] = after
    res["lint_delta"] = None if (after is None or b is None) else after - b
    if after is None:
        res["gates"]["lint_net"] = None
        return res
    worse = b is not None and after > b
    res["gates"]["lint_net"] = (after == 0) and not worse
    if res["gates"]["lint_net"] is False and not res.get("reason"):
        res["reason"] = (f"lint_net: {after} warnings after"
                         + (f" (was {b})" if b is not None else "") + ", must be 0")
    return res


def size_gates(res, before, nul_carve_out=False):
    """min_bytes <= before, proxy_tokens <= before.

    131.32: the four raw-NUL programs have no usable compile-time `before`
    (`tkc --min` truncates at the NUL), so both gates are explicitly n/a for
    them — not silently passed, not spuriously failed."""
    mb, pb = res.get("min_bytes"), res.get("proxy_tokens")
    b_mb, b_pb = (before or {}).get("min_bytes"), (before or {}).get("proxy_tokens")
    if nul_carve_out:
        res["gates"]["min_bytes"] = None
        res["gates"]["proxy_tokens"] = None
        res["size_gate_note"] = NUL_CARVE_REASON
        return res
    res["gates"]["min_bytes"] = None if mb is None or b_mb is None else mb <= b_mb
    res["gates"]["proxy_tokens"] = None if pb is None or b_pb is None else pb <= b_pb
    if res["gates"]["min_bytes"] is False and not res.get("reason"):
        res["reason"] = f"min_bytes: {mb} > {b_mb} before"
    if res["gates"]["proxy_tokens"] is False and not res.get("reason"):
        res["reason"] = f"proxy_tokens: {pb} > {b_pb} before"
    return res


# ------------------------------------------------------------------- perf ---
def _build(src, spec, tmpdir, tag):
    """Build one stdin_program with its manifest build_flags. Path or None."""
    tkpath = os.path.join(tmpdir, tag + ".tk")
    binpath = tkpath + ".bin"
    with open(tkpath, "w") as f:
        f.write(src)
    flags = list(spec.get("build_flags") or [])
    try:
        b = subprocess.run([TKC, tkpath, "-o", binpath] + flags, capture_output=True,
                           text=True, errors="replace", timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        if os.path.exists(tkpath):
            os.unlink(tkpath)
    return binpath if b.returncode == 0 and os.path.exists(binpath) else None


def _rusage_run(binpath, stdin_text, cwd):
    """One run: (wall_ms, maxrss_kb). stdout/stderr go to DEVNULL — this
    function measures TIME, never output (131.70)."""
    t0 = time.perf_counter()
    p = subprocess.Popen([binpath], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, cwd=cwd)
    try:
        p.stdin.write((stdin_text or "").encode("utf-8", "replace"))
        p.stdin.close()
        _, _, ru = os.wait4(p.pid, 0)
    except Exception:                                 # noqa: BLE001
        p.kill()
        p.wait()
        return None
    wall = (time.perf_counter() - t0) * 1000.0
    rss = ru.ru_maxrss / 1024.0 if sys.platform == "darwin" else float(ru.ru_maxrss)
    return wall, rss


def _perf_pass(binpath, spec, cwd):
    """One pass = every manifest case once. (summed wall ms, max RSS KB)."""
    wall = rss = 0.0
    for i, tc in enumerate(spec.get("test_cases") or []):
        case_cwd = os.path.join(cwd, f"p{i}")
        os.makedirs(case_cwd, exist_ok=True)
        try:
            validate.materialise_fixtures(tc.get("fixtures"), case_cwd)
        except Exception:                             # noqa: BLE001
            return None
        r = _rusage_run(binpath, tc.get("input", ""), case_cwd)
        if r is None:
            return None
        wall += r[0]
        rss = max(rss, r[1])
    return wall, rss


def tier1_perf(spec, orig_src, cand_src, tmpdir, runs=PERF_RUNS,
               confirm_runs=PERF_CONFIRM_RUNS):
    """Tier-1 perf gate: median of `runs` interleaved original-vs-candidate
    passes over the manifest cases (wall ms + max RSS KB). fail iff ratio >
    1.5 AND absolute delta > 5 ms / 1 MB. n/a when either side will not build
    or run (the raw-NUL originals, typically).

    A flagged first measurement is CONFIRMED by a second, longer one before it
    rejects. Library programs run in single-digit milliseconds, so a wave
    running many gates in parallel produces scheduler noise well over the 5 ms
    threshold; an unconfirmed flake must not cost a correct rewrite. Both
    measurements are recorded (`first_pass`, `confirmed`).

    Self-contained on purpose: it never routes through diff_check /
    pattern_autofix, and it reads TIMINGS from the original, never stdout."""
    res = _tier1_measure(spec, orig_src, cand_src, tmpdir, runs)
    if res.get("verdict") != "fail" or not confirm_runs:
        return res
    again = _tier1_measure(spec, orig_src, cand_src, tmpdir, confirm_runs)
    again["first_pass"] = {k: res.get(k) for k in ("ratio", "wall_ms", "rss_kb", "verdict")}
    again["confirmed"] = again.get("verdict") == "fail"
    if again.get("verdict") == "n/a":                 # the re-run could not measure
        again["verdict"] = "n/a"
        again["detail"] = "flagged once, re-measure unavailable — not a rejection"
    elif not again["confirmed"]:
        again["detail"] = "flagged once, not reproduced on re-measure (noise)"
    return again


def _tier1_measure(spec, orig_src, cand_src, tmpdir, runs):
    res = {"tier": "1", "ratio": None, "verdict": "n/a", "via": "library-median-of-%d" % runs}
    if not spec.get("test_cases"):
        res["detail"] = "not executable (no manifest cases)"
        return res
    ob = _build(orig_src, spec, tmpdir, "perf_orig")
    cb = _build(cand_src, spec, tmpdir, "perf_cand")
    try:
        if not ob or not cb:
            res["detail"] = "build failed (orig=%s cand=%s)" % (bool(ob), bool(cb))
            return res
        with tempfile.TemporaryDirectory(dir=tmpdir) as cwd:
            o_w, o_r, c_w, c_r = [], [], [], []
            for n in range(runs):
                od = os.path.join(cwd, f"o{n}")
                cd = os.path.join(cwd, f"c{n}")
                os.makedirs(od, exist_ok=True)
                os.makedirs(cd, exist_ok=True)
                so = _perf_pass(ob, spec, od)         # interleaved: same cache state
                sc = _perf_pass(cb, spec, cd)
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
        res.update({"runs": runs, "ratio": round(max(wall_ratio, rss_ratio), 3),
                    "wall_ms": {"orig": round(ow, 3), "cand": round(cw, 3),
                                "ratio": round(wall_ratio, 3)},
                    "rss_kb": {"orig": round(orr, 1), "cand": round(crr, 1),
                               "ratio": round(rss_ratio, 3)},
                    "verdict": "fail" if (wall_flag or rss_flag) else "pass"})
        if wall_flag or rss_flag:
            res["detail"] = ("wall" if wall_flag else "rss") + " regression"
        return res
    finally:
        for b in (ob, cb):
            if b and os.path.exists(b):
                os.unlink(b)


def perf_gate(res, perf):
    res["perf"] = perf
    res["gates"]["perf"] = None if perf.get("verdict") == "n/a" else perf.get("verdict") == "pass"
    if res["gates"]["perf"] is False and not res.get("reason"):
        res["reason"] = f"perf: tier-1 {perf.get('detail') or 'regression'} ratio {perf.get('ratio')}"
    return res


def summary_line(res):
    marks = {True: "ok", False: "FAIL", None: "n/a"}
    return " ".join(f"{k}={marks.get(v, v)}" for k, v in res["gates"].items())
