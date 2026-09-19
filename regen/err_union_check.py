#!/usr/bin/env python3
"""131.42 — err-union check: which `T!Err` single_function records game the
a_tests err marker by returning it as a hard-coded `str`.

131.40 found 254 A-ERR records across 39 bases that "pass" with
`<"{'err': 'EmptyCollection'}"` (a `str`) instead of the spec's `T!Err`
union: the return type drifted (the signature gate is arity-only, 131.43)
and the driver's line comparison cannot tell a printed union payload from
a printed string. This tool
  1. parses every target function's DECLARED return type from `tkc
     --dump-ast` (pinned binary, regen/tkc_pin.py) — the RETURN_SPEC node's
     children span the declared text; a union has a TYPE_IDENT err child —
     and cross-checks it against the text parser the gate uses
     (validate.declared_return_type);
  2. classifies each record: gamed_err_marker (declared not a union AND
     evidence the marker is what gets printed: a string literal carrying
     `{'err` / the a_tests marker text in the target or a helper — OR the
     driver run PRINTS the marker text; A-ERR-0001v11 builds it at run
     time from `s.upper("d")+"iv"...` so no text search can see it),
     wrong_return_type (declared != spec, normalised; hard_fail when the
     spec is a union and the declaration is not), correct, no_target, ...;
  3. proves the 131.42 `return_type` gate (run_shard.validate_one_gates):
     20 unaffected banked records re-validated with the gate off/on are
     identical; 5 gamed ones flip to a `return_type:` reject;
  4. routes the gamed set: audit/buckets/agent.txt (append, dedup) +
     audit/buckets/agent_reasons.jsonl + audit/pattern_sweep.131.42.patch.jsonl
     (131.13 sweep schema, bucket AGENT, violations rule gamed-err-marker
     naming the err-propagate / err-default catalogue entries).
Reads records; writes NOTHING under corpus/regen_v04/<CAT>/ (no banking).

Outputs
  corpus/regen_v04/audit/err_union_check.jsonl        one row per record
  regen/freeze/err_union_check_131.42.json            tracked summary
  corpus/regen_v04/audit/buckets/agent.txt            appended (--route)
  corpus/regen_v04/audit/buckets/agent_reasons.jsonl  (--route)
  corpus/regen_v04/audit/pattern_sweep.131.42.patch.jsonl (--route)

Applying the patch (pattern_common.load_bucket: a later line for the same
task_id wins, so plain concatenation is the merge):
  cat corpus/regen_v04/audit/pattern_sweep.jsonl \\
      corpus/regen_v04/audit/pattern_sweep.131.42.patch.jsonl \\
      > corpus/regen_v04/audit/pattern_sweep.merged.jsonl
  python3 regen/pattern_prep.py --bucket corpus/regen_v04/audit/pattern_sweep.merged.jsonl

Scope (default): every single_function spec whose category is A-ERR or
whose return type is an error union. --all-task-types adds full_program /
stdin_program specs with a union return (blast radius of the hard gate).
"""
import argparse, json, os, random, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit                                    # noqa: E402
import driver as drv                            # noqa: E402
import idiom_judge, metrics, validate, tkc_pin  # noqa: E402
import pattern_common as pc                     # noqa: E402  (131.39 Part B: pin_tkc)

STORY = "131.42"
CORPUS = os.path.join(os.path.dirname(HERE), "corpus", "regen_v04")
OUT_JSONL = os.path.join(CORPUS, "audit", "err_union_check.jsonl")
OUT_SUMMARY = os.path.join(HERE, "freeze", f"err_union_check_{STORY}.json")
PREV_FINDING = os.path.join(HERE, "freeze", "u64_recheck_131.40.json")
MARKER = "{'err"                        # prefix of str({"err": ..}) AND str({"error": ..}) —
                                        # both keys occur in the 129.7 a_tests (56 / 34 bases)
REASON = f"{STORY} gamed_err_marker"
PATCH_PATTERNS = ("err-propagate", "err-default")
PATCH_RULE = "gamed-err-marker"
_BASE = re.compile(r"^([A-Z]-[A-Z]+-\d+)v\d+$")
PROOF_SEED = 131042


def base_of(tid):
    m = _BASE.match(tid)
    return m.group(1) if m else tid


# ------------------------------------------------------------ dump-ast ---
def dump_ast(tkc, src, workdir):
    """Parsed AST (dict) of src via `tkc --dump-ast`, or None when the
    compiler rejects the file (rc != 0 / no JSON)."""
    with tempfile.NamedTemporaryFile("w", suffix=".tk", dir=workdir, delete=False) as f:
        f.write(src)
        path = f.name
    try:
        p = subprocess.run([tkc, "--dump-ast", path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    if p.returncode != 0 or not p.stdout.strip().startswith("{"):
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def _walk(node):
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


def ast_functions(ast, src):
    """{name: {"declared": str|None, "is_union": bool, "str_lits": [..]}}
    for every top-level FUNC_DECL. `declared` is the source slice spanned
    by the RETURN_SPEC subtree (`i64!$parseerr`); `is_union` when the
    RETURN_SPEC carries a second (error type) child or the text has `!`."""
    out = {}
    for n in ast.get("children") or []:
        if n.get("kind") != "FUNC_DECL":
            continue
        kids = n.get("children") or []
        name = next((k.get("name") for k in kids if k.get("kind") == "IDENT"), None)
        if not name:
            continue
        rs = next((k for k in kids if k.get("kind") == "RETURN_SPEC"), None)
        declared, is_union = None, False
        if rs and rs.get("children"):
            # the declared text is the whole RETURN_SPEC subtree: an `@T`
            # return is a 1-char `@` node with the element TYPE_EXPR nested
            # under it (`@(str)!$e` = `@` > `str`, then TYPE_IDENT `e`)
            spans = [n["span"] for n in _walk(rs) if n.get("span") and n is not rs] or [rs["span"]]
            declared = src[min(x["start"] for x in spans):max(x["end"] for x in spans)].strip()
            is_union = len(rs["children"]) >= 2 or "!" in declared
        lits = [m.get("value", "") for m in _walk(n) if m.get("kind") == "STR_LIT"]
        out[name] = {"declared": declared, "is_union": is_union, "str_lits": lits}
    return out


# ------------------------------------------------------------- classify ---
ERR_KEYS = ("err", "error")               # == driver._ERR_KEYS (131.44)


def is_err_case(exp):
    return isinstance(exp, dict) and len(exp) == 1 and next(iter(exp)) in ERR_KEYS


def marker_texts(spec):
    """The generic marker plus every err expectation of the spec's (a_tests-
    injected) test cases rendered the way the pre-131.44 driver printed a
    dict — `str(dict)`: `{'err': 'EmptyInput'}` — which is the text the
    gamed records hard-code."""
    texts = {MARKER}
    for tc in spec.get("test_cases") or []:
        exp = tc.get("expected")
        if is_err_case(exp):
            texts.add(str(exp))
    return texts


def has_err_case(spec):
    return any(is_err_case(tc.get("expected")) for tc in spec.get("test_cases") or [])


def execute_tests(spec, src, workdir, name, tkc=None):
    """Run the record through the shared driver main and look at what it
    PRINTS: `printed_marker` when a stdout line is one of marker_texts(spec)
    (or starts with `{'err`) — evidence independent of how the driver of the
    day renders err expectations (pre-131.44 `{'err': ..}`, post `err:<v>`)
    and of whether the a_tests pass. None when the spec has no test cases."""
    if not spec.get("test_cases"):
        return None
    dsrc, err = drv.append_main(spec, src)
    if err:
        return {"build": None, "printed_marker": None, "got": [], "driver_fail": err}
    tkc = tkc or validate.TKC
    tkpath = os.path.join(workdir, name + ".drv.tk")
    binpath = tkpath + ".bin"
    with open(tkpath, "w") as f:
        f.write(dsrc)
    try:
        b = subprocess.run([tkc, tkpath, "-o", binpath], capture_output=True, text=True,
                           errors="replace", timeout=90)
        if b.returncode != 0:
            return {"build": False, "printed_marker": None, "got": [], "driver_fail": None}
        r = subprocess.run([binpath], capture_output=True, text=True, errors="replace", timeout=15)
        got = r.stdout.splitlines()
    except subprocess.TimeoutExpired:
        return {"build": None, "printed_marker": None, "got": [], "driver_fail": "timeout"}
    finally:
        for q in (tkpath, binpath):
            if os.path.exists(q):
                os.unlink(q)
    texts = marker_texts(spec)
    printed = any(l.strip() in texts or l.strip().startswith(MARKER) for l in got)
    return {"build": True, "exit": r.returncode, "printed_marker": printed, "got": got[:20],
            "driver_fail": None}


def classify(spec, src, funcs, ast_ok=True, exec_result=None):
    """One err_union_check row (without task ids / stamps). `funcs` is
    ast_functions() output, or {} when the AST was unavailable (then the
    text parser alone decides and `ast_ok` is False). `exec_result` is
    execute_tests() output for the hard-fail case (None = not run)."""
    stubs = drv._stub_names(spec)
    callee = drv.find_target(spec, src)                 # what the driver main calls
    named = validate.target_name(spec)
    target = named if named and (not funcs or named in funcs) and validate.declared_return_type(src, named) else callee
    want = validate.spec_return_type(spec)
    row = {"target": target, "driver_callee": callee, "gaming_shape": None,
           "spec_return": want, "spec_return_norm": validate.norm_return_type(want),
           "spec_is_union": validate.is_error_union(want), "ast_ok": ast_ok,
           "declared_return": None, "declared_return_norm": None, "declared_is_union": None,
           "declared_text": None, "ast_text_agree": None,
           "marker_texts_found": [], "marker_in": [], "evidence": [], "exec": exec_result,
           "flags": [], "hard_fail": False, "verdict": None}
    if not target:
        row["verdict"] = "no_target"
        return row
    text_decl = validate.declared_return_type(src, target)
    row["declared_text"] = text_decl
    fn = funcs.get(target)
    if fn and fn["declared"] is not None:
        declared, is_union = fn["declared"], fn["is_union"]
        row["ast_text_agree"] = (validate.norm_return_type(declared) ==
                                 validate.norm_return_type(text_decl))
    else:
        declared, is_union = text_decl, validate.is_error_union(text_decl)
    row["declared_return"] = declared
    row["declared_return_norm"] = validate.norm_return_type(declared)
    row["declared_is_union"] = is_union
    # second gaming shape: named target is a union, the driver's callee is not
    callee_union = None
    if callee and callee != target:
        cf = funcs.get(callee)
        cdecl = cf["declared"] if cf and cf["declared"] is not None else validate.declared_return_type(src, callee)
        callee_union = validate.is_error_union(cdecl) if cdecl is not None else None
        row["callee_return"] = cdecl
    # marker literals: in the target or any helper (the gamed records route
    # the marker through an `errstr` helper), never in context stubs / main
    texts = marker_texts(spec)
    found, where = set(), []
    for name, f in funcs.items():
        if name in stubs or name == "main":
            continue
        hit = [t for t in texts if any(t in lit for lit in f["str_lits"])]
        if hit:
            found.update(hit)
            where.append(name)
    if not funcs:                                   # no AST: text fallback
        body = src[idiom_judge.stub_prefix_len(src):]
        found = {t for t in texts if t in body}
        where = ["<text>"] if found else []
    row["marker_texts_found"], row["marker_in"] = sorted(found), where
    if declared is None:
        row["verdict"] = "unparsed_return"
        return row
    if want is None:
        row["verdict"] = "no_spec_return"
        return row
    if row["spec_is_union"] and (not is_union or callee_union is False):
        row["hard_fail"] = True
        row["gaming_shape"] = "target_returns_str" if not is_union else "wrapper_returns_str"
        if found:
            row["evidence"].append("literal")
        if exec_result and exec_result.get("printed_marker"):
            row["evidence"].append("printed")           # the driver run printed the marker
        if row["evidence"]:
            row["flags"].append("gamed_err_marker")
            row["verdict"] = "gamed_err_marker"
        else:
            row["flags"].append("wrong_return_type")
            row["verdict"] = "wrong_return_type"
        return row
    if row["spec_return_norm"] != row["declared_return_norm"]:
        row["flags"].append("wrong_return_type")
        row["verdict"] = "wrong_return_type"
    else:
        row["verdict"] = "correct"
    if found:
        row["flags"].append("marker_literal")
    return row


# ---------------------------------------------------------------- corpus ---
def a_tests_stamp(corpus=CORPUS):
    """What the 129.7 / 131.47 a_tests bank looked like when this scan ran:
    the err-marker expectations come from here, so a re-author wave (131.47
    re-authored 54 bases) changes what 'gamed' means for those bases."""
    d = os.path.join(corpus, "audit", "a_tests")
    prov, bases, newest = {}, 0, None
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith(".json"):
            continue
        bases += 1
        t = json.load(open(os.path.join(d, fn)))
        pv = t.get("provenance")
        prov[pv] = prov.get(pv, 0) + 1
        m = os.path.getmtime(os.path.join(d, fn))
        newest = m if newest is None else max(newest, m)
    return {"bases": bases, "provenance": dict(sorted(prov.items(), key=lambda kv: -kv[1])),
            "newest_mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest)) if newest else None}


def load_manifest():
    man = {}
    for line in open(os.path.join(CORPUS, "MANIFEST.jsonl")):
        e = json.loads(line)
        man[e["task_id"]] = e
    return man


def record_path(entry):
    return os.path.join(CORPUS, entry.get("path") or f"{entry['category']}/{entry['task_id']}.json")


def in_scope(spec, all_task_types):
    ret = validate.spec_return_type(spec) or ""
    if spec.get("task_type") == "single_function":
        return spec.get("category") == "A-ERR" or validate.is_error_union(ret)
    return all_task_types and validate.is_error_union(ret)


def check_all(specs, man, tkc, workdir, all_task_types=False, limit=None, reuse_exec=None):
    """reuse_exec: {task_id: exec dict} from a previous run (records are
    frozen, so the driver run need not be repeated)."""
    rows = []
    ids = sorted(t for t, s in specs.items() if t in man and in_scope(s, all_task_types))
    if limit:
        ids = ids[:limit]
    for i, tid in enumerate(ids):
        spec = specs[tid]
        src = json.load(open(record_path(man[tid])))["tk_source"]
        ast = dump_ast(tkc, src, workdir)
        funcs = ast_functions(ast, src) if ast else {}
        row = classify(spec, src, funcs, ast_ok=ast is not None)
        if row["hard_fail"] and spec.get("task_type") == "single_function":
            # execution evidence for the non-union declarations (the err
            # marker may be assembled at run time — no literal to find)
            ex = (reuse_exec or {}).get(tid) or execute_tests(spec, src, workdir, tid, tkc)
            row = classify(spec, src, funcs, ast_ok=ast is not None, exec_result=ex)
        row = {"task_id": tid, "base": base_of(tid), "category": spec.get("category"),
               "task_type": spec.get("task_type"), "tests_from": spec.get("_tests_from"), **row}
        rows.append(row)
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(ids)}", file=sys.stderr)
    return rows


def summarise(rows):
    verdicts, by_base, by_type, flags, evidence, shapes = {}, {}, {}, {}, {}, {}
    for r in rows:
        v = r["verdict"]
        verdicts[v] = verdicts.get(v, 0) + 1
        if v == "gamed_err_marker":
            e = "+".join(r["evidence"])
            evidence[e] = evidence.get(e, 0) + 1
            sh = r.get("gaming_shape")
            shapes[sh] = shapes.get(sh, 0) + 1
        by_type.setdefault(r["task_type"], {}).setdefault(v, 0)
        by_type[r["task_type"]][v] += 1
        b = by_base.setdefault(r["base"], {"records": 0})
        b["records"] += 1
        b[v] = b.get(v, 0) + 1
        for f in r["flags"]:
            flags[f] = flags.get(f, 0) + 1
    return {"verdicts": verdicts, "flags": flags, "gamed_evidence": evidence, "gamed_shapes": shapes,
            "by_task_type": by_type, "by_base": dict(sorted(by_base.items()))}


# ------------------------------------------------------------ gate proof ---
def proof_sample(rows, man, gamed, n_ok=20, n_gamed=5, seed=PROOF_SEED):
    """Deterministic: n_ok unaffected banked records spread round-robin over
    the categories (every task type), n_gamed from the gamed set."""
    rnd = random.Random(seed)
    by_cat = {}
    for tid, e in man.items():
        if tid not in gamed:
            by_cat.setdefault(e["category"], []).append(tid)
    cats = sorted(by_cat)
    for c in cats:
        rnd.shuffle(by_cat[c])
    picked, i = [], 0
    while len(picked) < n_ok and any(by_cat.values()):
        c = cats[i % len(cats)]
        if by_cat[c]:
            picked.append(by_cat[c].pop())
        i += 1
    return sorted(picked), sorted(rnd.sample(sorted(gamed), min(n_gamed, len(gamed))))


def revalidate(tid, spec, man, workdir):
    """(before, after) verdicts of run_shard.validate_one_gates with the
    131.42 gate off / on, on the banked source (harness prefix stripped so
    assemble() re-adds the canonical one)."""
    import run_shard
    src = json.load(open(record_path(man[tid])))["tk_source"]
    raw = src
    if spec.get("task_type") == "single_function":
        n = idiom_judge.stub_prefix_len(src)
        raw = src[n:] if n else src
    out = {}
    for label, gate in (("before", False), ("after", True)):
        rec, ok, reason, gates = run_shard.validate_one_gates(spec, raw, workdir, return_type_gate=gate)
        out[label] = {"ok": ok, "reason": reason, "gates": gates,
                      "assembled_matches_record": bool(rec) and rec["tk_source"] == src}
    return out


def gate_proof(rows, specs, man, workdir):
    gamed = {r["task_id"] for r in rows if r["verdict"] == "gamed_err_marker"}
    ok_ids, gamed_ids = proof_sample(rows, man, gamed)
    res = {"seed": PROOF_SEED, "unaffected": [], "gamed": [],
           "unaffected_identical": 0, "gamed_now_rejected": 0}
    for tid in ok_ids:
        v = revalidate(tid, specs[tid], man, workdir)
        gb = {k: x for k, x in v["before"]["gates"].items() if k != "return_type"}
        ga = {k: x for k, x in v["after"]["gates"].items() if k != "return_type"}
        identical = (v["before"]["ok"], v["before"]["reason"], gb) == (v["after"]["ok"], v["after"]["reason"], ga)
        res["unaffected_identical"] += identical
        res["unaffected"].append({"task_id": tid, "task_type": specs[tid].get("task_type"),
                                  "identical": identical, "ok": v["after"]["ok"],
                                  "reason": v["after"]["reason"],
                                  "return_type_gate": v["after"]["gates"]["return_type"]})
    for tid in gamed_ids:
        v = revalidate(tid, specs[tid], man, workdir)
        flipped = (v["before"]["ok"] is True and v["after"]["ok"] is False
                   and (v["after"]["reason"] or "").startswith("return_type:"))
        res["gamed_now_rejected"] += flipped
        res["gamed"].append({"task_id": tid, "before_ok": v["before"]["ok"],
                             "before_reason": v["before"]["reason"],
                             "after_ok": v["after"]["ok"], "after_reason": v["after"]["reason"],
                             "flipped": flipped})
    return res


# --------------------------------------------------------------- routing ---
def patch_row(sweep_row, tid, ts=None):
    """131.13 sweep-schema row routing tid to AGENT for the err-union
    rewrite: violations name the two catalogue entries pattern_prep inlines
    (entries_for_row: pattern_id per rule), bucket AGENT, original bucket
    kept as would_bucket."""
    row = dict(sweep_row or {"task_id": tid, "category": tid.split("-")[0] + "-" + tid.split("-")[1],
                             "task_type": "single_function", "source": "regen"})
    row.update({
        "task_id": tid, "ts": int(ts or time.time()), "story": STORY,
        "violations": [{"rule": PATCH_RULE, "severity": "error", "pattern_id": p} for p in PATCH_PATTERNS],
        "pattern_ids": list(PATCH_PATTERNS),
        "bucket": "AGENT",
        "bucket_reason": (f"{STORY} gamed_err_marker: returns the a_tests err marker as a hard-coded "
                          f"str; the spec return is an error union (regen/freeze/err_union_check_{STORY}.json)"),
        "would_bucket": (sweep_row or {}).get("bucket"),
        "patch_of": "131.13 pattern_sweep row" if sweep_row else None,
    })
    return row


def route(gamed_ids, corpus=CORPUS, sweep_path=None, ts=None):
    """Append gamed ids to buckets/agent.txt (dedup), write
    buckets/agent_reasons.jsonl and pattern_sweep.<STORY>.patch.jsonl.
    Returns counts."""
    bdir = os.path.join(corpus, "audit", "buckets")
    os.makedirs(bdir, exist_ok=True)
    agent_txt = os.path.join(bdir, "agent.txt")
    reasons_path = os.path.join(bdir, "agent_reasons.jsonl")
    existing = []
    if os.path.exists(agent_txt):
        existing = [l.strip() for l in open(agent_txt) if l.strip()]
    sweep_path = sweep_path or os.path.join(corpus, "audit", "pattern_sweep.jsonl")
    sweep_agent = set()
    if os.path.exists(sweep_path):
        for line in open(sweep_path):
            if line.strip():
                r = json.loads(line)
                if r.get("bucket") == "AGENT":
                    sweep_agent.add(r.get("task_id"))
    # re-routing after a re-scan (e.g. the 131.47 a_tests re-author changed
    # which records are gamed): drop what a PREVIOUS 131.42 route appended —
    # ids in our reasons file that the sweep did not already bucket AGENT —
    # so agent.txt returns to its 131.13 baseline before the fresh append
    prior = set()
    if os.path.exists(reasons_path):
        prior = {json.loads(l)["task_id"] for l in open(reasons_path) if l.strip()}
    removable = prior - sweep_agent
    baseline = [t for t in existing if t not in removable]
    removed = len(existing) - len(baseline)
    have = set(baseline)
    new = [t for t in sorted(gamed_ids) if t not in have]
    with open(agent_txt, "w") as f:
        for t in baseline + new:
            f.write(t + "\n")
    with open(reasons_path, "w") as f:
        for t in sorted(gamed_ids):
            f.write(json.dumps({"task_id": t, "reason": REASON}) + "\n")
    sweep = {}
    if os.path.exists(sweep_path):
        want = set(gamed_ids)
        for line in open(sweep_path):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("task_id") in want:
                sweep[r["task_id"]] = r            # later line wins (load_bucket)
    patch_path = os.path.join(corpus, "audit", f"pattern_sweep.{STORY}.patch.jsonl")
    with open(patch_path, "w") as f:
        for t in sorted(gamed_ids):
            f.write(json.dumps(patch_row(sweep.get(t), t, ts)) + "\n")
    return {"agent_txt": os.path.relpath(agent_txt, os.path.dirname(HERE)),
            "agent_txt_added": len(new), "agent_txt_already_present": len(gamed_ids) - len(new),
            "agent_txt_baseline": len(baseline), "agent_txt_prior_route_removed": removed,
            "agent_txt_total": len(baseline) + len(new),
            "agent_reasons": os.path.relpath(reasons_path, os.path.dirname(HERE)),
            "patch": os.path.relpath(patch_path, os.path.dirname(HERE)),
            "patch_rows": len(gamed_ids), "patch_rows_with_sweep_row": len(sweep),
            "apply": ("cat audit/pattern_sweep.jsonl audit/pattern_sweep.131.42.patch.jsonl "
                      "> audit/pattern_sweep.merged.jsonl && pattern_prep.py --bucket "
                      "audit/pattern_sweep.merged.jsonl  (load_bucket: later line per task_id wins)")}


# ------------------------------------------------------------------ main ---
def proof_only():
    """Redo the gate proof against the current run_shard (the check rows are
    reused from OUT_JSONL) and merge it into OUT_SUMMARY."""
    rows = [json.loads(l) for l in open(OUT_JSONL) if l.strip()]
    summary = json.load(open(OUT_SUMMARY))
    specs = audit.load_specs(CORPUS)
    man = load_manifest()
    import run_shard
    pinned = pc.pin_tkc(run_shard, sys.modules[__name__])
    with pinned, tempfile.TemporaryDirectory(prefix="err_union_") as tmp:
        summary["gate_proof"] = gate_proof(rows, specs, man, tmp)
        summary["gate_proof"]["tkc"] = pinned.stamp()
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({k: summary["gate_proof"][k] for k in ("unaffected_identical", "gamed_now_rejected")}))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all-task-types", action="store_true",
                    help="also check full_program/stdin_program specs with a union return")
    ap.add_argument("--no-proof", action="store_true", help="skip the 20+5 gate re-validation")
    ap.add_argument("--route", action="store_true",
                    help="write agent.txt / agent_reasons.jsonl / the sweep patch")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--reuse-exec", action="store_true",
                    help="reuse the driver-run evidence of the existing jsonl rows (frozen records)")
    ap.add_argument("--proof-only", action="store_true",
                    help="re-run only the 20+5 gate proof on the existing jsonl rows and merge it into the summary")
    a = ap.parse_args(argv)
    if a.proof_only:
        return proof_only()
    specs = audit.load_specs(CORPUS)
    man = load_manifest()
    import run_shard
    # 131.39 Part B: one pinned binary for the whole run, rebound in every
    # module that execs tkc (pattern_common.pin_tkc covers the siblings)
    pinned = pc.pin_tkc(run_shard, sys.modules[__name__])
    print(f"tkc {pinned.version} sha {pinned.sha256[:12]}", file=sys.stderr)
    with pinned, tempfile.TemporaryDirectory(prefix="err_union_") as tmp:
        reuse = None
        if a.reuse_exec and os.path.exists(OUT_JSONL):
            reuse = {r["task_id"]: r["exec"] for r in (json.loads(l) for l in open(OUT_JSONL) if l.strip())
                     if r.get("exec")}
        rows = check_all(specs, man, pinned.argv0, tmp, a.all_task_types, a.limit, reuse)
        for r in rows:
            r["tkc_bin_sha"] = pinned.sha256
        with open(OUT_JSONL, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        summ = summarise(rows)
        sf = [r for r in rows if r["task_type"] == "single_function"]
        gamed = sorted(r["task_id"] for r in sf if r["verdict"] == "gamed_err_marker")
        prev = set()
        if os.path.exists(PREV_FINDING):
            prev = set(json.load(open(PREV_FINDING))["related_finding_err_marker_hardcoded"]["task_ids"])
        disagree = [r["task_id"] for r in rows if r["ast_text_agree"] is False]
        summary = {
            "story": STORY, "date": time.strftime("%Y-%m-%d"), "tkc": pinned.stamp(),
            "a_tests": a_tests_stamp(),
            "scope": ("single_function specs: category A-ERR or error-union return"
                      + ("; plus full_program/stdin_program with a union return" if a.all_task_types else "")),
            "records_checked": len(rows),
            "single_function": {"records": len(sf), **summarise(sf)},
            **summ,
            "gamed_single_function": len(gamed),
            "gamed_bases": len({base_of(t) for t in gamed}),
            "gamed_task_ids": gamed,
            "vs_131_40": {"finding_131_40": len(prev), "only_in_131_40": sorted(prev - set(gamed)),
                          "only_in_131_42": sorted(set(gamed) - prev)},
            "ast_unavailable": sum(1 for r in rows if not r["ast_ok"]),
            "ast_vs_text_disagreements": len(disagree),
            "ast_vs_text_disagreement_ids": disagree[:20],
            "hard_gate_would_reject": sorted(r["task_id"] for r in rows if r["hard_fail"]),
            "jsonl": os.path.relpath(OUT_JSONL, os.path.dirname(HERE)),
        }
        summary["hard_gate_would_reject_n"] = len(summary["hard_gate_would_reject"])
        if not a.no_proof:
            print("gate proof: 20 unaffected + 5 gamed", file=sys.stderr)
            summary["gate_proof"] = gate_proof(rows, specs, man, tmp)
        if a.route:
            summary["routing"] = route(gamed)
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=1)
    brief = {k: summary[k] for k in ("records_checked", "verdicts", "flags", "gamed_evidence", "gamed_shapes", "gamed_single_function",
                                     "gamed_bases", "hard_gate_would_reject_n", "ast_vs_text_disagreements")}
    brief["vs_131_40"] = {k: (v if isinstance(v, int) else len(v)) for k, v in summary["vs_131_40"].items()}
    if "gate_proof" in summary:
        brief["gate_proof"] = {k: summary["gate_proof"][k] for k in ("unaffected_identical", "gamed_now_rejected")}
    if "routing" in summary:
        brief["routing"] = {k: summary["routing"][k] for k in ("agent_txt_added", "agent_txt_already_present", "patch_rows")}
    print(json.dumps(brief, indent=1))
    return summary


if __name__ == "__main__":
    main()
