#!/usr/bin/env python3
"""Epic 131.13 — corpus + library conformance sweep ("test the corpus").

Every frozen regen record (MANIFEST.jsonl, 23,382) and every library program
(shards/library_131.jsonl, 1,583) is linted with `tkc --lint --diag-json`; each
pattern-rule hit is mapped to a `patterns/catalogue.json` entry, exemptions are
applied, a catalogue-estimated token saving and a perf-sensitivity flag are
derived, and the record is put in ONE bucket that the downstream stories
consume: 131.14 `pattern_autofix.py` (AUTO), 131.15 `pattern_prep.py` (AGENT),
131.16 (REGEN), 131.17 (library_*).

Subcommands
  run     parallel, resumable (append-only ledger audit/pattern_sweep.ledger.jsonl,
          the audit.py / rescore_131.py idempotency pattern). One tkc lint per
          record; `--min` + proxy count only when the rescore_131 row cannot be
          reused (sha mismatch / library programs).
  report  dedupe the ledger -> audit/pattern_sweep.jsonl (the bucket file),
          audit/pattern_sweep_summary.json, regen/PATTERN_SWEEP_131.md,
          audit/buckets/{auto,agent,regen,library_agent,library_auto}.txt and
          the budget factor in regen/freeze/proxy_budget_v04.json.

Row schema (agreed with 131.14 / 131.15):
  task_id, category, task_type, source ("regen"|"library"),
  violations: [{rule, severity, span, line, pattern_id, fix, deterministic, message}],
  exemptions: [{rule, reason, span, pattern_id}],
  proxy_tokens, min_bytes, est_saving_tokens, perf_sensitive, bucket
  (+ est_saving alias, pattern_ids, bucket_reason, would_bucket, other_lint,
  hard_fail_net, over_budget, over_budget_129, style_mandate, library{...}).

Bucket protocol (thresholds are CLI parameters, recorded in the summary):
  REGEN   class carve-out applied first: A-side full_program (no test lock,
          regenerated once with card v2 by 131.16 — never rewritten); the
          bucket the row WOULD have taken is kept in `would_bucket`.
  EXEMPT  >= 1 hit and every hit is exempt (style mandate / migrate_fix
          pre-form / 127.33 linter FP / harness stub prefix).
  AUTO    >= 1 net violation and every net violation carries a deterministic
          `fix` from tkc (single-use-let inline, discarded-value-result
          receiver; the string-concat-chain fix is dormant — never AUTO unless
          --concat-fix-armed).
  AGENT   any net error/warning violation without a deterministic fix, OR
          est_saving_tokens >= --agent-saving (3), OR perf_sensitive.
  LEAVE   the rest: 0 net violations, or only fix-less hints saving < 3 and
          not perf-flagged.
Library rows additionally go to AGENT when they fail a 131.17 gate that a
`--fix` cannot repair (structure depth/fn-size, new idiom floor, compile,
raw NUL bytes) and to AUTO when their only remaining failure is lint
warnings that all carry a fix (`library.gate_fail` records why).

est_saving_tokens = sum over net violations of (proxy8k of the catalogue's
flagged non-canonical form - proxy8k of its canonical form), floored at 0 per
violation. perf_sensitive = a matched entry has a `hot_path` or its canonical
form is expected-worse at runtime than a sibling form (Tier-2 territory).

Records, library sources, MANIFEST.jsonl and every ledger are read-only here.
"""
import argparse, collections, glob, hashlib, json, multiprocessing, os, re, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import idiom_judge                                # noqa: E402
import metrics                                    # noqa: E402

CORPUS = os.path.expanduser("~/tk/toke-corpus/corpus/regen_v04")
TOKE_ROOT = os.environ.get("TOKE_ROOT", os.path.expanduser("~/tk/toke"))
CATALOGUE_PATH = os.path.join(TOKE_ROOT, "patterns", "catalogue.json")
BUDGET_PATH = os.path.join(HERE, "freeze", "proxy_budget_v04.json")
MD_PATH = os.path.join(HERE, "PATTERN_SWEEP_131.md")
STORY = "131.13"
BATCH_SIZE = 20
BUCKETS = ("EXEMPT", "AUTO", "AGENT", "LEAVE", "REGEN")
PATTERN_RULES = list(idiom_judge.PATTERN_RULES)
HAND_PARSER = "hand-rolled-parser"          # judge regex rule (no tkc rule)
RULES = PATTERN_RULES + [HAND_PARSER]
# rules whose tkc `fix` is deterministic (docs/lint-rules-v1.md); the
# string-concat-chain fix is dormant (127.7/127.10) and joins only when armed
DETERMINISTIC_RULES = {"single-use-let", "discarded-value-result"}
CONCAT_RULE = "string-concat-chain"
REGEN_CLASS = ("A-", "full_program")       # category prefix, task_type
_A_REGEN = re.compile(r"^A-")

# ------------------------------------------------------- rule -> pattern ---
# Each pattern rule maps to (pattern_id, flagged_form) — the catalogue entry
# whose non-canonical form the diagnostic describes. Where the diagnostic
# message / context distinguishes shapes, a chooser picks among candidates.
# Hand-mapped hints (story brief): single-use-let -> fn-chain-vs-let,
# loop-rebuilds-array -> iter-map / iter-filter.
RULE_PATTERNS = {
    "mut-flag-if": [("cond-bind-if", "b")],
    "flag-soup": [("cond-bool-combine", "b")],
    # inside a `lp` body the chain is the accumulator anti-pattern (str-build-loop a);
    # elsewhere it is a nested concat (str-interp-vs-join c)
    "string-concat-chain": [("str-interp-vs-join", "c"), ("str-build-loop", "a")],
    "single-use-let": [("fn-chain-vs-let", "b")],
    "loop-rebuilds-array": [("iter-map", "b"), ("iter-filter", "b")],
    "discarded-value-result": [],          # value-semantics bug; no catalogue form
    HAND_PARSER: [("parse-json", "b")],
}
ALT_PATTERNS = {                           # same rule, other catalogue entries (informational)
    "mut-flag-if": ["cond-elif-chain", "cond-clamp", "cond-bool-render"],
    "single-use-let": ["err-default"],
    HAND_PARSER: ["parse-delim-split", "parse-fields", "parse-int", "io-read-lines"],
}


def _sha_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _sha_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_catalogue(path=CATALOGUE_PATH):
    """{sha, by_id, by_rule, perf_sensitive:{id: reason}} from catalogue.json."""
    raw = open(path, "rb").read()
    cat = json.loads(raw)
    entries = cat["entries"] if isinstance(cat, dict) else cat
    by_id = {e["id"]: e for e in entries}
    by_rule = {}
    for e in entries:
        r = (e.get("lint") or {}).get("rule")
        if r:
            by_rule.setdefault(r, []).append(e["id"])
    perf = {}
    for e in entries:
        why = entry_perf_reason(e)
        if why:
            perf[e["id"]] = why
    return {"sha256": hashlib.sha256(raw).hexdigest(), "sha": hashlib.sha256(raw).hexdigest()[:12],
            "entries": entries, "by_id": by_id, "by_rule": by_rule, "perf_sensitive": perf,
            "n": len(entries)}


def entry_perf_reason(entry):
    """Why a catalogue entry is perf-sensitive, or None: a `hot_path` form
    exists (the canonical is not the fastest — a rewrite must pick the right
    one), or the canonical form is expected-worse at runtime than a sibling
    (one of the 131.11 'expected verdict' entries a measurement may flip)."""
    v = entry.get("verdict") or {}
    if v.get("hot_path"):
        return f"hot_path={v['hot_path']}"
    can = v.get("canonical")
    verdicts = {c.get("form"): c.get("runtime_verdict") for c in entry.get("candidates") or []}
    cv = verdicts.get(can)
    if cv in ("slower", "worse-bigO") or (cv == "tied" and "best" in verdicts.values()):
        return f"canonical {can} runtime {cv} vs a 'best' sibling"
    return None


def form_tokens(entry, form):
    for c in entry.get("candidates") or []:
        if c.get("form") == form:
            return (c.get("tokens") or {}).get("proxy8k")
    return None


def pattern_saving(cat, pattern_id, flagged_form):
    """proxy8k(flagged form) - proxy8k(canonical form), floored at 0; None
    when the entry / form is unknown or blocked (no measurement)."""
    e = cat["by_id"].get(pattern_id)
    if not e:
        return None
    can = (e.get("verdict") or {}).get("canonical")
    a, b = form_tokens(e, flagged_form), form_tokens(e, can)
    if a is None or b is None:
        return None
    return max(0, a - b)


# ------------------------------------------------------------ context ---
def _inside_loop(src, offset):
    """True when the byte offset sits inside a `lp(...){...}` body (string-aware
    walk over the enclosing blocks, reusing idiom_judge's helpers)."""
    b = src.encode("utf-8")
    try:
        i = len(b[:offset].decode("utf-8"))
    except UnicodeDecodeError:
        return False
    mask = idiom_judge._string_mask(src)
    depth = 0
    for k in range(i - 1, -1, -1):
        if mask[k]:
            continue
        c = src[k]
        if c in ")}]":
            depth += 1
        elif c in "({[":
            if depth == 0:
                if c == "{":
                    p = idiom_judge._prev_code(src, mask, k)
                    if p >= 0 and src[p] == ")":
                        lp = idiom_judge._match_back(src, mask, p)
                        q = idiom_judge._prev_code(src, mask, lp) if lp >= 0 else -1
                        if q >= 1 and src[q - 1:q + 1] == "lp" and \
                                not (q >= 2 and (src[q - 2].isalnum() or src[q - 2] == "_")):
                            return True
                # keep climbing: an if inside a lp still counts
            else:
                depth -= 1
    return False


def choose_pattern(rule, diag, src):
    """(pattern_id, flagged_form) for one diagnostic, or (None, None)."""
    opts = RULE_PATTERNS.get(rule) or []
    if not opts:
        return None, None
    msg = diag.get("message") or ""
    if rule == "loop-rebuilds-array":
        # copy/slice -> iter-filter (filter(&p) is the catalogue's per-element
        # keep form), transform / range -> iter-map
        return ("iter-filter", "b") if "copies" in msg else ("iter-map", "b")
    if rule == CONCAT_RULE:
        start = (diag.get("span") or {}).get("start")
        if start is None:
            start = (diag.get("pos") or {}).get("offset")
        if start is not None and _inside_loop(src, start):
            return "str-build-loop", "a"
        return "str-interp-vs-join", "c"
    return opts[0]


# ----------------------------------------------------------- exemptions ---
def preform_exempt_rules(spec):
    """migrate_fix: rules whose anti-pattern the LEGACY source already carries
    (the task is a syntax migration; carrying the pre-form over is not the
    migrator's fault). The legacy source does not parse under 2.8.0 (`=`
    equality), so the 129 regex judge is the only detector available."""
    if (spec or {}).get("task_type") != "migrate_fix":
        return ()
    legacy = spec.get("legacy_source") or ""
    if not legacy:
        return ()
    out = []
    if idiom_judge._legacy_mut_flag_if(legacy):
        out.append("mut-flag-if")
    if idiom_judge._legacy_flag_soup(legacy):
        out.append("flag-soup")
    if idiom_judge._legacy_nested_concat(legacy):
        out.append(CONCAT_RULE)
    return tuple(out)


_DVR_RECV = re.compile(r"write `(\w+)=\1\.")
_VEC_IMPORT = re.compile(r"(?:^|;)\s*i=(\w+):std\.(?:vec|set)\s*(?:;|$)", re.M)


def vec_handle_receiver(diag, src):
    """True when a discarded-value-result hit's receiver is a std.vec / std.set
    handle (`let x=v.new()` with `i=v:std.vec`) called method-style: those are
    reference collections that mutate in place (docs/lint-rules-v1.md excludes
    only the alias form `v.push(x;1)`), so the hit is a suspected linter FP —
    found by this sweep on L-SCI-122 / L-MED-128 (tests pass 2/2)."""
    m = _DVR_RECV.search(diag.get("message") or "")
    if not m:
        return False
    aliases = [a.group(1) for a in _VEC_IMPORT.finditer(src)]
    if not aliases:
        return False
    pat = re.compile(r"let\s+%s\s*=\s*(?:%s)\.new\s*\(" % (re.escape(m.group(1)), "|".join(map(re.escape, aliases))))
    return bool(pat.search(src))


# --------------------------------------------------------------- bucket ---
def classify(violations, exemptions, est_saving, perf_sensitive, thresholds,
             regen_class=False, library=None):
    """(bucket, reason, would_bucket). `violations` are NET (non-exempt)."""
    t = thresholds
    # harness stub-prefix diagnostics are the harness's, not a hit on the record
    exemptions = [e for e in exemptions if e.get("reason") != "stub_prefix"]
    gate = [v for v in violations if v.get("severity") in idiom_judge.GATE_SEVERITIES]
    nondet_gate = [v for v in gate if not v.get("deterministic")]
    if violations and all(v.get("deterministic") for v in violations):
        b, why = "AUTO", "all %d net violation(s) carry a deterministic fix" % len(violations)
    elif not violations and exemptions:
        b, why = "EXEMPT", "every hit exempt (%s)" % ",".join(sorted({e["reason"] for e in exemptions}))
    elif nondet_gate:
        b, why = "AGENT", "gate violation without fix: " + ",".join(sorted({v["rule"] for v in nondet_gate}))
    elif est_saving is not None and est_saving >= t["agent_saving"]:
        b, why = "AGENT", f"est_saving {est_saving} >= {t['agent_saving']}"
    elif perf_sensitive:
        b, why = "AGENT", "perf_sensitive"
    elif violations:
        b, why = "LEAVE", f"only fix-less hints, est_saving {est_saving} < {t['agent_saving']}"
    else:
        b, why = "LEAVE", "0 hits"
    if library:
        fails = library.get("gate_fail") or []
        hard = [g for g in fails if g != "lint"]
        if hard:
            b, why = "AGENT", "library gate: " + ",".join(hard) + ("; " + why if b != "LEAVE" else "")
        elif "lint" in fails and b in ("LEAVE", "EXEMPT"):
            if library.get("lint_all_fixable"):
                b, why = "AUTO", "library lint warnings all carry a fix"
            else:
                b, why = "AGENT", "library lint warnings without fix: " + ",".join(library.get("lint_nofix") or [])
        elif "lint" in fails and b == "AUTO" and not library.get("lint_all_fixable"):
            b, why = "AGENT", "library lint warnings without fix: " + ",".join(library.get("lint_nofix") or [])
    if regen_class:
        return "REGEN", "A-side full_program: regenerated by 131.16 (no test lock)", b
    return b, why, None


# ---------------------------------------------------------------- worker ---
_W = {}


def _init_worker(opts):
    _W["cat"] = load_catalogue(opts["catalogue"])
    _W["opts"] = opts
    metrics.proxy_counter()           # load the tokenizer once per process


def sweep_one(job):
    """One record / library program. Never raises past the row."""
    task_id, src_path, meta, spec, prior = job
    opts, cat = _W["opts"], _W["cat"]
    row = {"task_id": task_id, "ts": int(time.time()), "story": STORY}
    row.update({k: meta.get(k) for k in ("category", "task_type", "source", "difficulty")})
    tmpdir = opts["tmpdir"]
    tkpath = os.path.join(tmpdir, task_id + ".tk")
    try:
        if meta["source"] == "library":
            src = open(src_path, "rb").read().decode("utf-8", "replace")
        else:
            rec = json.load(open(src_path))
            src = rec["tk_source"]
        row["source_sha256"] = _sha_text(src)
        row["nul_bytes"] = "\x00" in src
        with open(tkpath, "w", encoding="utf-8") as f:
            f.write(src)
        try:
            raw = idiom_judge.lint_diags(path=tkpath)
            reuse = (prior and prior.get("source_sha256") == row["source_sha256"]
                     and prior.get("proxy_tokens") is not None)
            if reuse:
                mn = None
                row["min_bytes"], row["proxy_tokens"] = prior["min_bytes"], prior["proxy_tokens"]
                row["metrics_from"] = "rescore_131"
            else:
                mn = metrics.min_form(tkpath)
                row["min_bytes"] = len(mn.encode()) if mn is not None else None
                row["proxy_tokens"] = metrics.proxy_tokens(mn)
                row["metrics_from"] = "computed"
            struct = metrics.analyse(tkpath, src) if meta["source"] == "library" else None
        finally:
            if os.path.exists(tkpath):
                os.unlink(tkpath)
        stub_n = idiom_judge.stub_prefix_len(src)
        stripped = idiom_judge.strip_stub_diags(raw, src)
        diags = idiom_judge.suppress_linter_fps(stripped, src)
        # exemption rule sets
        mandate = idiom_judge.style_mandate(spec) if spec else None
        row["style_mandate"] = mandate
        mandate_rules = idiom_judge.mandate_exempt_rules(mandate)
        preform_rules = preform_exempt_rules(spec) if opts["preform_exempt"] else ()
        violations, exemptions = [], []
        for d in raw:
            if stub_n and idiom_judge.is_stub_import(d, src, stub_n):
                exemptions.append({"rule": d.get("rule"), "reason": "stub_prefix",
                                   "span": d.get("span"), "pattern_id": None})
        for d in diags:
            rule = d.get("rule")
            if rule not in idiom_judge.PATTERN_RULES:
                continue
            pid, form = choose_pattern(rule, d, src)
            if d.get("suppressed"):
                exemptions.append({"rule": rule, "reason": "127.33-expr-if-branch-value",
                                   "span": d.get("span"), "pattern_id": pid})
                continue
            if rule == "discarded-value-result" and vec_handle_receiver(d, src):
                exemptions.append({"rule": rule, "reason": "suspect-fp-vec-handle-receiver",
                                   "span": d.get("span"), "pattern_id": pid})
                continue
            if rule in mandate_rules:
                exemptions.append({"rule": rule, "reason": "style_mandate", "span": d.get("span"),
                                   "pattern_id": pid})
                continue
            if rule in preform_rules:
                exemptions.append({"rule": rule, "reason": "migrate_fix_preform", "span": d.get("span"),
                                   "pattern_id": pid})
                continue
            has_fix = bool(d.get("fix"))
            det = has_fix and (rule in DETERMINISTIC_RULES
                               or (rule == CONCAT_RULE and opts["concat_fix_armed"]))
            violations.append({"rule": rule, "severity": d.get("severity"), "span": d.get("span"),
                               "line": (d.get("pos") or {}).get("line"), "pattern_id": pid,
                               "flagged_form": form, "fix": has_fix, "deterministic": det,
                               "saving": pattern_saving(cat, pid, form) if pid else 0,
                               "message": (d.get("message") or "")[:120]})
        # judge-only regex rule (needs stdlib knowledge tkc lacks)
        n_hp = idiom_judge._hand_parser(src)
        for _ in range(n_hp):
            pid, form = RULE_PATTERNS[HAND_PARSER][0]
            violations.append({"rule": HAND_PARSER, "severity": "warning", "span": None, "line": None,
                               "pattern_id": pid, "flagged_form": form, "fix": False,
                               "deterministic": False, "saving": pattern_saving(cat, pid, form),
                               "message": "judge regex: lp scanning charat/charcode + slice", "via": "judge-regex"})
        row["other_lint"] = [{"rule": d.get("rule"), "severity": d.get("severity"), "span": d.get("span"),
                              "fix": bool(d.get("fix"))}
                             for d in diags if d.get("rule") not in idiom_judge.PATTERN_RULES]
        est = sum(v["saving"] or 0 for v in violations)
        perf_ids = sorted({v["pattern_id"] for v in violations
                           if v["pattern_id"] and v["pattern_id"] in cat["perf_sensitive"]})
        row["violations"] = violations
        row["exemptions"] = exemptions
        row["pattern_ids"] = sorted({v["pattern_id"] for v in violations if v["pattern_id"]})
        row["est_saving_tokens"] = est
        row["est_saving"] = est                      # alias (pattern_prep reads est_saving)
        row["perf_sensitive"] = bool(perf_ids)
        row["perf_patterns"] = perf_ids
        # tkc gate rules only (hand-rolled-parser is a judge penalty, not a lint gate)
        row["hard_fail_net"] = sorted({v["rule"] for v in violations
                                       if v["severity"] in idiom_judge.GATE_SEVERITIES
                                       and v["rule"] in idiom_judge.PATTERN_RULES})
        row["hard_fail_gross"] = sorted(set(row["hard_fail_net"]) | {
            e["rule"] for e in exemptions if e["reason"] != "stub_prefix"
            and e["rule"] in idiom_judge.PATTERN_RULES
            and e["rule"] not in ("single-use-let", "loop-rebuilds-array")})
        idiom_new, notes = idiom_judge.score(src, diags)
        row["idiom_score_new"] = round(idiom_new, 4)
        lib = None
        if meta["source"] == "library":
            lib = dict(meta.get("library") or {})
            fails = []
            if row["nul_bytes"]:
                fails.append("nul_bytes")
            if struct is None:
                fails.append("compile")
            else:
                lib["max_depth"] = struct.get("max_depth")
                lib["max_func_bytes"] = struct.get("max_func_bytes")
                if struct.get("max_depth", 0) > 4 or struct.get("max_func_bytes", 0) > 600:
                    fails.append("structure")
            if idiom_new < idiom_judge.IDIOM_FLOOR:
                fails.append("idiom")
            warns = [o for o in row["other_lint"] if o["severity"] == "warning"]
            if warns:
                fails.append("lint")
            lib["lint_all_fixable"] = all(o["fix"] for o in warns) if warns else True
            lib["lint_nofix"] = sorted({o["rule"] for o in warns if not o["fix"]})
            lib["gate_fail"] = fails
            lib["idiom_new"] = row["idiom_score_new"]
            row["library"] = lib
        regen_class = (meta["source"] == "regen" and _A_REGEN.match(meta.get("category") or "")
                       and meta.get("task_type") == REGEN_CLASS[1])
        b, why, would = classify(violations, exemptions, est, row["perf_sensitive"],
                                 opts["thresholds"], regen_class, lib)
        row["bucket"], row["bucket_reason"], row["would_bucket"] = b, why, would
    except Exception as e:  # defensive: one bad record must not kill the sweep
        row["error"] = f"{type(e).__name__}: {e}"
        row.setdefault("violations", [])
        row.setdefault("exemptions", [])
        row.setdefault("est_saving_tokens", 0)
        row.setdefault("perf_sensitive", False)
        row["bucket"], row["bucket_reason"] = "AGENT", "sweep error: " + row["error"]
        row["would_bucket"] = None
    return row


# ------------------------------------------------------------------- run ---
def load_specs(corpus):
    specs = {}
    for sh in sorted(glob.glob(os.path.join(corpus, "shards", "*.jsonl"))):
        for line in open(sh):
            s = json.loads(line)
            specs[s["task_id"]] = s
    return specs


def load_prior(path):
    prior = {}
    if path and os.path.exists(path):
        for line in open(path):
            r = json.loads(line)
            prior[r["task_id"]] = {"source_sha256": r.get("source_sha256"),
                                   "min_bytes": r.get("min_bytes"), "proxy_tokens": r.get("proxy_tokens")}
    return prior


def load_library_dryrun(path):
    out = {}
    if path and os.path.exists(path):
        for line in open(path):
            r = json.loads(line)
            out[r["task_id"]] = {"library_id": r.get("library_id"), "dryrun_ok": r.get("ok"),
                                 "dryrun_reason": r.get("reason"), "dryrun_gates": r.get("gates"),
                                 "dryrun_idiom_legacy": r.get("idiom"), "dryrun_min_bytes": r.get("min_bytes"),
                                 "dryrun_max_depth": r.get("max_depth")}
    return out


def build_jobs(args, done):
    corpus = args.corpus_dir
    specs = load_specs(corpus)
    prior = load_prior(os.path.join(corpus, "audit", "rescore_131.jsonl"))
    dry = load_library_dryrun(os.path.join(corpus, "ledger", "library_131_dryrun.jsonl"))
    jobs = []
    if args.source in ("all", "regen"):
        man = {}
        for line in open(os.path.join(corpus, "MANIFEST.jsonl")):
            e = json.loads(line)
            man[e["task_id"]] = e                # last line wins (retried tasks)
        for tid, e in man.items():
            if tid in done or (args.category and e.get("category") != args.category):
                continue
            rec_path = os.path.join(corpus, e.get("path") or f"{e['category']}/{tid}.json")
            if not os.path.exists(rec_path):
                continue
            spec = specs.get(tid, {})
            meta = {"category": e.get("category") or spec.get("category"),
                    "task_type": e.get("task_type") or spec.get("task_type"),
                    "difficulty": e.get("difficulty", spec.get("difficulty")), "source": "regen"}
            jobs.append((tid, rec_path, meta, spec, prior.get(tid)))
    if args.source in ("all", "library"):
        lib_shard = os.path.join(corpus, "shards", "library_131.jsonl")
        if os.path.exists(lib_shard):
            for line in open(lib_shard):
                s = json.loads(line)
                tid = s["task_id"]
                if tid in done or (args.category and s.get("category") != args.category):
                    continue
                if not os.path.exists(s["source_program"]):
                    continue
                meta = {"category": s.get("category"), "task_type": s.get("task_type", "stdin_program"),
                        "difficulty": s.get("difficulty"), "source": "library",
                        "library": {"id": s["library"]["id"], "category": s["library"]["category"],
                                    "source_program": s["source_program"], **dry.get(tid, {})}}
                jobs.append((tid, s["source_program"], meta, s, None))
    return jobs


def thresholds_of(args):
    return {"agent_saving": args.agent_saving, "concat_fix_armed": bool(args.concat_fix_armed),
            "preform_exempt": bool(args.preform_exempt), "regen_class": "A-*/full_program",
            "budget_max_over": args.budget_max_over, "factor_step": args.factor_step}


def cmd_run(args):
    corpus = args.corpus_dir
    audit_dir = os.path.join(corpus, "audit")
    tmpdir = os.path.join(audit_dir, "tmp_sweep131")
    os.makedirs(tmpdir, exist_ok=True)
    ledger_path = os.path.join(audit_dir, "pattern_sweep.ledger.jsonl")
    done = set()
    if os.path.exists(ledger_path) and not args.redo:
        for line in open(ledger_path):
            done.add(json.loads(line)["task_id"])
    jobs = build_jobs(args, done)
    if args.limit:
        jobs = jobs[:args.limit]
    tkc_sha = _sha_file(idiom_judge.TKC)
    cat = load_catalogue(args.catalogue)
    opts = {"tmpdir": tmpdir, "catalogue": args.catalogue, "thresholds": thresholds_of(args),
            "concat_fix_armed": bool(args.concat_fix_armed), "preform_exempt": bool(args.preform_exempt)}
    print(f"sweeping {len(jobs)} ({len(done)} already done), {args.workers} workers, "
          f"tkc sha256 {tkc_sha[:12]}, catalogue {cat['sha']} ({cat['n']} entries)", file=sys.stderr)
    t0 = time.time()
    n = 0
    counts = collections.Counter()
    with multiprocessing.Pool(args.workers, initializer=_init_worker, initargs=(opts,)) as pool, \
            open(ledger_path, "a") as led:
        for row in pool.imap_unordered(sweep_one, jobs, chunksize=16):
            row["tkc_sha256"] = tkc_sha
            row["catalogue_sha"] = cat["sha"]
            led.write(json.dumps(row) + "\n")
            led.flush()
            n += 1
            counts[row["bucket"]] += 1
            if n % 1000 == 0:
                print(f"  {n}/{len(jobs)} {dict(counts)} {time.time() - t0:.0f}s", file=sys.stderr)
    tkc_end = _sha_file(idiom_judge.TKC) if os.path.exists(idiom_judge.TKC) else None
    if tkc_end != tkc_sha:
        print(f"WARNING: tkc changed during the sweep ({tkc_sha[:12]} -> {(tkc_end or 'missing')[:12]})",
              file=sys.stderr)
    print(json.dumps({"swept": n, "buckets": dict(counts), "elapsed_s": round(time.time() - t0, 1),
                      "ledger": ledger_path, "tkc_sha256": tkc_sha, "tkc_sha256_end": tkc_end}))


# ---------------------------------------------------------------- report ---
def _pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(p / 100 * len(vals)))]


def _dist(vals):
    vals = [v for v in vals if v is not None]
    return {"n": len(vals), "sum": sum(vals), "p50": _pct(vals, 50), "p90": _pct(vals, 90),
            "max": max(vals) if vals else None}


def budget_grid(rows, medians, max_over, step, lo=1.0, hi=4.0):
    """[(factor, over, share)] over the regen rows; chosen = smallest factor
    with share <= max_over. Budget = round(factor × median), the 131.10
    convention (metrics.over_budget compares n > budget)."""
    keyed = collections.defaultdict(list)
    for r in rows:
        if r.get("source") != "regen" or r.get("proxy_tokens") is None:
            continue
        keyed[f"{r['category']}/{r['task_type']}"].append(r["proxy_tokens"])
    n = sum(len(v) for v in keyed.values())
    grid, chosen = [], None
    steps = int(round((hi - lo) / step))
    for i in range(steps + 1):
        f = round(lo + i * step, 2)          # exact decimal factor (no float drift into round())
        over = 0
        for k, vals in keyed.items():
            m = medians.get(k)
            if m is None:
                continue
            lim = round(f * m)
            over += sum(1 for v in vals if v > lim)
        share = over / n if n else 0.0
        grid.append({"factor": f, "over": over, "share": round(share, 4)})
        if chosen is None and share <= max_over:
            chosen = f
    return grid, chosen, n


def write_budget(path, rows, factor, grid, max_over, n):
    b = json.load(open(path))
    old = b.get("factor")
    if b.get("factor_129_proposal") is None:
        b["factor_129_proposal"] = old
    b["factor"] = factor
    b["story"] = STORY
    b["story_origin"] = b.get("story_origin") or "131.10"
    b["factor_selected"] = {"story": STORY, "rule": f"smallest factor of the category/task_type median with <= "
                                                    f"{max_over:.0%} of records over budget",
                            "records": n, "over": next(g["over"] for g in grid if g["factor"] == factor),
                            "share": next(g["share"] for g in grid if g["factor"] == factor),
                            # compact grid (0.25 steps + the chosen factor); the full grid is in
                            # audit/pattern_sweep_summary.json
                            "grid": [g for g in grid if abs(g["factor"] * 4 - round(g["factor"] * 4)) < 1e-6
                                     or g["factor"] == factor],
                            "selected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    b["mode"] = "soft"
    b["recommendation"] = ("keep over_budget a SOFT flag until the 131.15 agent wave has run: the wave lowers "
                           "proxy_tokens on the AGENT bucket and the medians move; re-run "
                           "pattern_sweep.py report --rebudget afterwards and decide the hard gate in 131.19")
    med = b.get("median") or {}
    cmed = b.get("category_median") or {}
    b["budget"] = {k: round(factor * m) for k, m in sorted({**cmed, **med}.items())}
    with open(path, "w") as f:
        json.dump(b, f, indent=1)
        f.write("\n")
    return b


def agg(rows):
    by_bucket = collections.Counter(r["bucket"] for r in rows)
    would = collections.Counter(r.get("would_bucket") for r in rows if r.get("would_bucket"))
    hits_gross = collections.Counter()
    hits_net = collections.Counter()
    rec_gross = collections.Counter()
    rec_net = collections.Counter()
    pid_gross = collections.Counter()
    pid_net = collections.Counter()
    pid_saving = collections.Counter()
    pid_records = collections.Counter()
    ex_reason = collections.Counter()
    ex_rule = collections.Counter()
    other = collections.Counter()
    other_fix = collections.Counter()
    perf_pat = collections.Counter()
    det_hits = collections.Counter()
    for r in rows:
        seen_g, seen_n, seen_p = set(), set(), set()
        for v in r.get("violations") or []:
            hits_gross[v["rule"]] += 1
            hits_net[v["rule"]] += 1
            seen_g.add(v["rule"])
            seen_n.add(v["rule"])
            if v.get("deterministic"):
                det_hits[v["rule"]] += 1
            pid = v.get("pattern_id") or "(none)"
            pid_gross[pid] += 1
            pid_net[pid] += 1
            pid_saving[pid] += v.get("saving") or 0
            seen_p.add(pid)
        for e in r.get("exemptions") or []:
            ex_reason[e["reason"]] += 1
            ex_rule[e["rule"]] += 1
            if e["reason"] != "stub_prefix":
                hits_gross[e["rule"]] += 1
                seen_g.add(e["rule"])
                pid_gross[e.get("pattern_id") or "(none)"] += 1
        for ru in seen_g:
            rec_gross[ru] += 1
        for ru in seen_n:
            rec_net[ru] += 1
        for p in seen_p:
            pid_records[p] += 1
        for o in r.get("other_lint") or []:
            other[o["rule"]] += 1
            if o.get("fix"):
                other_fix[o["rule"]] += 1
        for p in r.get("perf_patterns") or []:
            perf_pat[p] += 1
    sav = {b: _dist([r["est_saving_tokens"] for r in rows if r["bucket"] == b]) for b in BUCKETS}
    return {
        "records": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "buckets": {b: by_bucket.get(b, 0) for b in BUCKETS},
        "would_bucket_of_regen": {b: would.get(b, 0) for b in BUCKETS if would.get(b)},
        "records_with_any_hit": sum(1 for r in rows if r.get("violations") or
                                    [e for e in r.get("exemptions") or [] if e["reason"] != "stub_prefix"]),
        "records_with_net_violation": sum(1 for r in rows if r.get("violations")),
        "hard_fail_net_records": sum(1 for r in rows if r.get("hard_fail_net")),
        "hits_gross_by_rule": {ru: hits_gross[ru] for ru in RULES if hits_gross[ru]},
        "hits_net_by_rule": {ru: hits_net[ru] for ru in RULES if hits_net[ru]},
        "records_gross_by_rule": {ru: rec_gross[ru] for ru in RULES if rec_gross[ru]},
        "records_net_by_rule": {ru: rec_net[ru] for ru in RULES if rec_net[ru]},
        "deterministic_hits_by_rule": dict(det_hits),
        "hits_gross_by_pattern": dict(pid_gross.most_common()),
        "hits_net_by_pattern": dict(pid_net.most_common()),
        "records_by_pattern": dict(pid_records.most_common()),
        "saving_by_pattern": dict(pid_saving.most_common()),
        "exemptions_by_reason": dict(ex_reason.most_common()),
        "exemptions_by_rule": dict(ex_rule.most_common()),
        "other_lint_by_rule": dict(other.most_common()),
        "other_lint_fixable_by_rule": dict(other_fix),
        "perf_sensitive_records": sum(1 for r in rows if r.get("perf_sensitive")),
        "perf_sensitive_by_pattern": dict(perf_pat.most_common()),
        "est_saving_by_bucket": sav,
        "est_saving_total": sum(r.get("est_saving_tokens") or 0 for r in rows),
        "proxy_tokens": _dist([r.get("proxy_tokens") for r in rows]),
        "over_budget": sum(1 for r in rows if r.get("over_budget")),
        "over_budget_129": sum(1 for r in rows if r.get("over_budget_129")),
        "agent_batches": -(-by_bucket.get("AGENT", 0) // BATCH_SIZE),
        "auto_batches": -(-by_bucket.get("AUTO", 0) // BATCH_SIZE),
    }


def cmd_report(args):
    corpus = args.corpus_dir
    audit_dir = os.path.join(corpus, "audit")
    ledger_path = os.path.join(audit_dir, "pattern_sweep.ledger.jsonl")
    rows = {}
    for line in open(ledger_path):
        r = json.loads(line)
        rows[r["task_id"]] = r                  # last write wins
    rows = sorted(rows.values(), key=lambda r: (r.get("source") != "regen", r["task_id"]))
    cat = load_catalogue(args.catalogue)
    thresholds = thresholds_of(args)

    # ---- budget factor (regen rows; frozen medians from the 131.10 file)
    budget = json.load(open(BUDGET_PATH))
    medians = budget.get("median") or {}
    grid, factor, n_b = budget_grid(rows, medians, args.budget_max_over, args.factor_step)
    if args.factor:
        factor = args.factor
    if factor is None:
        factor = grid[-1]["factor"]
    if not args.no_write_budget:
        budget = write_budget(BUDGET_PATH, rows, factor, grid, args.budget_max_over, n_b)
    b129 = {k: round(1.5 * m) for k, m in medians.items()}
    for r in rows:
        k = f"{r.get('category')}/{r.get('task_type')}"
        n = r.get("proxy_tokens")
        m = medians.get(k)
        r["over_budget"] = (n > round(factor * m)) if (n is not None and m is not None) else None
        r["over_budget_129"] = (n > b129[k]) if (n is not None and k in b129) else None
        r["budget_factor"] = factor

    final_path = os.path.join(audit_dir, "pattern_sweep.jsonl")
    with open(final_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    regen = [r for r in rows if r.get("source") == "regen"]
    lib = [r for r in rows if r.get("source") == "library"]
    cats = sorted({r["category"] for r in rows})
    summary = {
        "story": STORY,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ledger": ledger_path,
        "bucket_file": final_path,
        "tkc_sha256": sorted({r.get("tkc_sha256") for r in rows if r.get("tkc_sha256")}),
        "catalogue_sha": cat["sha"],
        "catalogue_entries": cat["n"],
        "thresholds": thresholds,
        "bucket_protocol": ["REGEN carve-out (A-*/full_program)", "EXEMPT", "AUTO", "AGENT", "LEAVE"],
        "deterministic_rules": sorted(DETERMINISTIC_RULES | ({CONCAT_RULE} if args.concat_fix_armed else set())),
        "rule_patterns": {k: v for k, v in RULE_PATTERNS.items()},
        "alt_patterns": ALT_PATTERNS,
        "catalogue_rules": cat["by_rule"],
        "perf_sensitive_patterns": cat["perf_sensitive"],
        "budget": {"factor": factor, "factor_129_proposal": budget.get("factor_129_proposal", 1.5),
                   "max_over_share": args.budget_max_over, "records": n_b, "grid": grid,
                   "mode": "soft", "file": BUDGET_PATH},
        "total": agg(rows),
        "regen": agg(regen),
        "library": agg(lib),
        "by_category": {c: agg([r for r in rows if r["category"] == c]) for c in cats},
        "by_task_type": {t: agg([r for r in rows if r["task_type"] == t])
                         for t in sorted({r["task_type"] for r in rows})},
        "library_gate_fail": dict(collections.Counter(
            g for r in lib for g in (r.get("library") or {}).get("gate_fail") or []).most_common()),
        "library_bucket_reasons": dict(collections.Counter(
            r["bucket_reason"].split(":")[0] for r in lib).most_common()),
        "samples_by_rule": {},
        "preform_exempt_records": sum(1 for r in rows if any(
            e["reason"] == "migrate_fix_preform" for e in r.get("exemptions") or [])),
    }
    samples = collections.defaultdict(list)
    for r in rows:
        for v in r.get("violations") or []:
            if len(samples[v["rule"]]) < 8 and r["task_id"] not in samples[v["rule"]]:
                samples[v["rule"]].append(r["task_id"])
    summary["samples_by_rule"] = dict(samples)
    with open(os.path.join(audit_dir, "pattern_sweep_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)

    bdir = os.path.join(audit_dir, "buckets")
    os.makedirs(bdir, exist_ok=True)
    files = {"auto": [r for r in regen if r["bucket"] == "AUTO"],
             "agent": [r for r in regen if r["bucket"] == "AGENT"],
             "regen": [r for r in regen if r["bucket"] == "REGEN"],
             "library_agent": [r for r in lib if r["bucket"] == "AGENT"],
             "library_auto": [r for r in lib if r["bucket"] == "AUTO"]}
    for name, sub in files.items():
        with open(os.path.join(bdir, name + ".txt"), "w") as f:
            for r in sorted(sub, key=lambda r: r["task_id"]):
                f.write(r["task_id"] + "\n")
    write_md(summary, rows)
    print(json.dumps({"records": len(rows), "bucket_file": final_path,
                      "buckets": summary["total"]["buckets"], "regen_buckets": summary["regen"]["buckets"],
                      "library_buckets": summary["library"]["buckets"], "factor": factor,
                      "agent_batches": summary["regen"]["agent_batches"],
                      "library_agent_batches": summary["library"]["agent_batches"], "md": MD_PATH,
                      "bucket_files": {k: len(v) for k, v in files.items()}}))


def _row(cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def write_md(S, rows):
    T, R, Lb = S["total"], S["regen"], S["library"]
    th = S["thresholds"]
    L = []
    L.append("# PATTERN_SWEEP_131 — corpus + library conformance sweep (story 131.13)\n")
    L.append(f"Generated {S['generated']} · {T['records']:,} rows ({R['records']:,} regen records + "
             f"{Lb['records']:,} library programs) · tkc sha256 "
             f"{', '.join(s[:12] for s in S['tkc_sha256'])} · catalogue `{S['catalogue_sha']}` "
             f"({S['catalogue_entries']} entries) · sweep errors {T['errors']}\n")
    L.append("**What this is.** Every frozen record and every library program linted with the real "
             "`tkc --lint --diag-json` pattern rules, each hit mapped to a `patterns/catalogue.json` "
             "entry, exemptions applied, a catalogue-estimated proxy-token saving and a perf-sensitivity "
             "flag derived, and ONE bucket assigned per row. The bucket file "
             "`corpus/regen_v04/audit/pattern_sweep.jsonl` is the input to 131.14 (AUTO), 131.15 (AGENT), "
             "131.16 (REGEN) and 131.17 (library). No record was modified.\n")
    L.append("## Thresholds and protocol (CLI parameters, also in `pattern_sweep_summary.json`)\n")
    L.append(_row(["parameter", "value"]))
    L.append("|---|---|")
    L.append(_row(["`--agent-saving` (AGENT when est_saving_tokens ≥)", th["agent_saving"]]))
    L.append(_row(["`--concat-fix-armed` (string-concat-chain fix counts as deterministic)", th["concat_fix_armed"]]))
    L.append(_row(["`--preform-exempt` (migrate_fix pre-forms exempt)", th["preform_exempt"]]))
    L.append(_row(["REGEN carve-out class", "`" + th["regen_class"] + "`"]))
    L.append(_row(["deterministic rules", ", ".join(f"`{r}`" for r in S["deterministic_rules"])]))
    L.append(_row(["`--budget-max-over` (share of records allowed over budget)", f"{th['budget_max_over']:.0%}"]))
    L.append(_row(["bucket order", " → ".join(S["bucket_protocol"])]))
    L.append("")
    L.append("Protocol: **REGEN** = A-side `full_program` (no test lock; regenerated once with card v2 by "
             "131.16 — never rewritten; the bucket the row would otherwise take is kept in `would_bucket`). "
             "**EXEMPT** = ≥ 1 hit, all exempt. **AUTO** = every net violation carries a deterministic tkc "
             "`fix` (`single-use-let` inline, `discarded-value-result` receiver; the concat fix is dormant). "
             "**AGENT** = a gate (error/warning) violation without a fix, or est. saving ≥ "
             f"{th['agent_saving']} proxy tokens, or a perf-sensitive pattern. **LEAVE** = 0 net violations, "
             "or only fix-less hints below the saving threshold and not perf-flagged. Library rows also go to "
             "AGENT on a non-lint 131.17 gate failure (structure, new-judge idiom floor, compile, NUL bytes) "
             "and to AUTO when only fixable lint warnings remain.\n")

    L.append("## Buckets\n")
    L.append(_row(["scope", "records"] + list(BUCKETS) + ["AGENT batches (20)", "AUTO batches (20)"]))
    L.append("|---|---|" + "---|" * len(BUCKETS) + "---|---|")
    for name, a in (("regen", R), ("library", Lb), ("**all**", T)):
        L.append(_row([name, f"{a['records']:,}"] + [f"{a['buckets'][b]:,}" for b in BUCKETS]
                      + [a["agent_batches"], a["auto_batches"]]))
    L.append("")
    if R.get("would_bucket_of_regen"):
        L.append("REGEN rows would otherwise bucket as: " + ", ".join(
            f"{b} {n:,}" for b, n in R["would_bucket_of_regen"].items()) + ".\n")
    L.append("### Per category\n")
    L.append(_row(["category", "records"] + list(BUCKETS) + ["net-violation records", "hard-fail net",
                                                             "perf_sensitive", "est_saving Σ"]))
    L.append("|---|---|" + "---|" * len(BUCKETS) + "---|---|---|---|")
    for c, a in S["by_category"].items():
        L.append(_row([c, f"{a['records']:,}"] + [f"{a['buckets'][b]:,}" for b in BUCKETS]
                      + [f"{a['records_with_net_violation']:,}", f"{a['hard_fail_net_records']:,}",
                         f"{a['perf_sensitive_records']:,}", f"{a['est_saving_total']:,}"]))
    L.append("")
    L.append("### Per task_type\n")
    L.append(_row(["task_type", "records"] + list(BUCKETS) + ["net-violation records"]))
    L.append("|---|---|" + "---|" * len(BUCKETS) + "---|")
    for t, a in S["by_task_type"].items():
        L.append(_row([t, f"{a['records']:,}"] + [f"{a['buckets'][b]:,}" for b in BUCKETS]
                      + [f"{a['records_with_net_violation']:,}"]))
    L.append("")

    L.append("## Violations by rule (all rows)\n")
    L.append(_row(["rule", "pattern(s)", "records gross", "records net", "hits gross", "hits net",
                   "deterministic hits", "exempt hits"]))
    L.append("|---|---|---|---|---|---|---|---|")
    for ru in RULES:
        pats = ", ".join(f"`{p}`" for p, _ in RULE_PATTERNS.get(ru, [])) or "—"
        L.append(_row([f"`{ru}`", pats, f"{T['records_gross_by_rule'].get(ru, 0):,}",
                       f"{T['records_net_by_rule'].get(ru, 0):,}", f"{T['hits_gross_by_rule'].get(ru, 0):,}",
                       f"{T['hits_net_by_rule'].get(ru, 0):,}", f"{T['deterministic_hits_by_rule'].get(ru, 0):,}",
                       f"{T['exemptions_by_rule'].get(ru, 0):,}"]))
    L.append("")
    L.append("Exemptions by reason: " + ", ".join(f"`{k}` {v:,}" for k, v in T["exemptions_by_reason"].items())
             + f". Records exempt only through a migrate_fix pre-form: {S['preform_exempt_records']:,} "
             "(NOTE: `run_shard.validate_one_gates` does not know this exemption — a re-validate of those "
             "records still hard-fails on the warning; decision for the main thread).\n")
    L.append("### By pattern_id (gross → net hits, records, Σ est. saving)\n")
    L.append(_row(["pattern_id", "hits gross", "hits net", "records", "Σ est_saving", "perf_sensitive"]))
    L.append("|---|---|---|---|---|---|")
    for pid, g in T["hits_gross_by_pattern"].items():
        L.append(_row([f"`{pid}`", f"{g:,}", f"{T['hits_net_by_pattern'].get(pid, 0):,}",
                       f"{T['records_by_pattern'].get(pid, 0):,}", f"{T['saving_by_pattern'].get(pid, 0):,}",
                       S["perf_sensitive_patterns"].get(pid, "") or ""]))
    L.append("")
    L.append("Rule → pattern mapping: " + "; ".join(
        f"`{r}` → " + (", ".join(f"`{p}`({f})" for p, f in v) if v else "no catalogue entry")
        for r, v in RULE_PATTERNS.items()) + ". `string-concat-chain` inside a `lp` body → `str-build-loop`, "
        "else `str-interp-vs-join`; `loop-rebuilds-array` 'copies' → `iter-filter`, transform / index → "
        "`iter-map`. Alternatives sharing a rule (not distinguishable from the diagnostic): "
        + "; ".join(f"`{r}`: {', '.join(v)}" for r, v in ALT_PATTERNS.items()) + ".\n")

    L.append("## Estimated saving per bucket (proxy8k tokens, per record)\n")
    L.append(_row(["bucket", "records", "Σ est_saving", "p50", "p90", "max"]))
    L.append("|---|---|---|---|---|---|")
    for b in BUCKETS:
        d = T["est_saving_by_bucket"][b]
        L.append(_row([b, f"{d['n']:,}", f"{d['sum']:,}", d["p50"], d["p90"], d["max"]]))
    L.append(f"| **all** | {T['records']:,} | {T['est_saving_total']:,} | | | |\n")
    L.append("### Top-10 pattern_ids by estimated saving\n")
    L.append(_row(["#", "pattern_id", "Σ est_saving", "net hits", "records"]))
    L.append("|---|---|---|---|---|")
    for i, (pid, s) in enumerate(list(T["saving_by_pattern"].items())[:10], 1):
        L.append(_row([i, f"`{pid}`", f"{s:,}", f"{T['hits_net_by_pattern'].get(pid, 0):,}",
                       f"{T['records_by_pattern'].get(pid, 0):,}"]))
    L.append("")

    L.append("## perf_sensitive\n")
    L.append(f"Records flagged: **{T['perf_sensitive_records']:,}** (regen {R['perf_sensitive_records']:,}, "
             f"library {Lb['perf_sensitive_records']:,}). A record is perf-sensitive when a matched catalogue "
             "entry has a `hot_path` form or its canonical form is expected-worse at runtime than a sibling. "
             "Catalogue entries that qualify: "
             + ", ".join(f"`{k}` ({v})" for k, v in S["perf_sensitive_patterns"].items()) + ".\n")
    if T["perf_sensitive_by_pattern"]:
        L.append("By pattern: " + ", ".join(f"`{k}` {v:,}" for k, v in T["perf_sensitive_by_pattern"].items()) + "\n")
    L.append("Of the tkc pattern rules only `hand-rolled-parser` (judge regex → `parse-json`, hot path `c`) "
             "reaches a perf-sensitive entry: the linter's rules describe token-shape patterns, not the "
             "runtime trade-offs, so Tier-2 in 131.15 is confined to those rows.\n")

    L.append("## AGENT wave size\n")
    L.append(_row(["scope", "AGENT records", "batches of 20"]))
    L.append("|---|---|---|")
    L.append(_row(["regen (131.15)", f"{R['buckets']['AGENT']:,}", R["agent_batches"]]))
    L.append(_row(["library (131.17)", f"{Lb['buckets']['AGENT']:,}", Lb["agent_batches"]]))
    L.append(_row(["AUTO regen (131.14)", f"{R['buckets']['AUTO']:,}", R["auto_batches"]]))
    L.append(_row(["AUTO library", f"{Lb['buckets']['AUTO']:,}", Lb["auto_batches"]]))
    L.append(_row(["REGEN (131.16, fixed)", f"{R['buckets']['REGEN']:,}", R["buckets"]["REGEN"] and -(-R["buckets"]["REGEN"] // BATCH_SIZE)]))
    L.append("")

    L.append("## Library split (1,583 programs → 131.17)\n")
    L.append("Buckets: " + ", ".join(f"{b} {Lb['buckets'][b]:,}" for b in BUCKETS) + ". Gate failures on the "
             "current source (any combination; new pattern judge, `metrics.analyse` structure): "
             + ", ".join(f"`{k}` {v:,}" for k, v in S["library_gate_fail"].items()) + ". Bucket reasons: "
             + ", ".join(f"{k} {v:,}" for k, v in S["library_bucket_reasons"].items()) + ".\n")
    L.append("Library other-lint warnings (gated for ingest): " + ", ".join(
        f"`{k}` {v:,} ({Lb['other_lint_fixable_by_rule'].get(k, 0):,} fixable)"
        for k, v in Lb["other_lint_by_rule"].items()) + ".\n")

    B = S["budget"]
    L.append("## Proxy budget factor\n")
    L.append(f"Rule: smallest factor of the frozen category/task_type median with ≤ {B['max_over_share']:.0%} of "
             f"the {B['records']:,} regen records over budget (budget = round(factor × median), "
             f"`metrics.over_budget` semantics). **Selected factor = {B['factor']}** (131.10 proposal 1.5 kept as "
             f"`factor_129_proposal`, flagged {T['over_budget_129']:,}); over budget at the selected factor: "
             f"{T['over_budget']:,}. Written to `regen/freeze/proxy_budget_v04.json` (`factor`, `budget`, "
             "`factor_selected`, `mode: soft`).\n")
    L.append(_row(["factor", "over budget", "share"]))
    L.append("|---|---|---|")
    for g in B["grid"]:
        if abs(g["factor"] * 4 - round(g["factor"] * 4)) < 1e-6 or g["factor"] == B["factor"]:
            mark = " **←**" if g["factor"] == B["factor"] else ""
            L.append(_row([f"{g['factor']}{mark}", f"{g['over']:,}", f"{g['share']:.1%}"]))
    L.append("")
    L.append("**Recommendation: keep `over_budget` SOFT until after 131.15.** The agent wave lowers "
             "proxy_tokens on the AGENT bucket (the long tail is exactly where the verbose forms live) and "
             "the medians move; the REGEN class is regenerated wholesale. Re-run `pattern_sweep.py report "
             "--rebudget` after 131.15/131.16 and decide the hard gate in 131.19.\n")

    L.append("## Unclassified rule hits\n")
    fp_vec = T["exemptions_by_reason"].get("suspect-fp-vec-handle-receiver", 0)
    L.append("Pattern-rule hits with no catalogue entry: "
             f"`discarded-value-result` {T['hits_net_by_rule'].get('discarded-value-result', 0):,} net hits "
             "(value-semantics bug, deterministic receiver fix — AUTO, saving 0)"
             + (f"; **{fp_vec} gross hits exempted as a suspected linter false positive** — the receiver is a "
                "`std.vec` handle (`i=v:std.vec; let x=v.new(); x.push(…)`) called method-style, a reference "
                "collection that mutates in place (both programs pass their tests 2/2; the rule only excludes "
                "the alias form `v.push(x;1)`). Needs a 127/131 story, like 127.33." if fp_vec else "")
             + ". Other lint rules seen "
             "(not gated on the shard path; gated for library ingest): "
             + (", ".join(f"`{k}` {v:,} ({T['other_lint_fixable_by_rule'].get(k, 0):,} fixable)"
                          for k, v in T["other_lint_by_rule"].items()) or "none") + ".\n")
    L.append("## Samples per rule\n")
    for ru, ids in S["samples_by_rule"].items():
        L.append(f"- `{ru}`: " + ", ".join(f"`{i}`" for i in ids))
    L.append("")
    L.append("Per-row file: `corpus/regen_v04/audit/pattern_sweep.jsonl` (schema in `pattern_sweep.py`); "
             "aggregates `audit/pattern_sweep_summary.json`; id lists `audit/buckets/{auto,agent,regen,"
             "library_agent,library_auto}.txt`.\n")
    with open(MD_PATH, "w") as f:
        f.write("\n".join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--corpus-dir", default=CORPUS)
    ap.add_argument("--catalogue", default=CATALOGUE_PATH)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--category")
    ap.add_argument("--source", choices=["all", "regen", "library"], default="all")
    ap.add_argument("--redo", action="store_true", help="ignore the ledger and re-sweep everything")
    ap.add_argument("--agent-saving", type=int, default=3, help="AGENT when est_saving_tokens >= N")
    ap.add_argument("--concat-fix-armed", action="store_true",
                    help="treat the string-concat-chain fix as deterministic (TKC_LINT_CONCAT_FIX armed)")
    ap.add_argument("--preform-exempt", dest="preform_exempt", action="store_true", default=True)
    ap.add_argument("--no-preform-exempt", dest="preform_exempt", action="store_false")
    ap.add_argument("--budget-max-over", type=float, default=0.05, help="max share of records over budget")
    ap.add_argument("--factor-step", type=float, default=0.05)
    ap.add_argument("--factor", type=float, help="force the budget factor instead of searching")
    ap.add_argument("--no-write-budget", action="store_true", help="report only; leave proxy_budget_v04.json")
    ap.add_argument("--rebudget", action="store_true", help="(report) alias for the default: rewrite the factor")
    args = ap.parse_args()
    {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
