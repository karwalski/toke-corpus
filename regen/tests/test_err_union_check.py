"""131.42 -- err-union check + the `return_type` validate gate.

Pure parts (spec/declared return-type parsing, normalisation, the gate
decision, AST walking, classification, routing rows) run without a compiler;
the end-to-end cases (tkc --dump-ast, driver execution,
run_shard.validate_one_gates) are skipped when tkc is absent.
"""
import json, os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import validate                  # noqa: E402
import err_union_check as euc    # noqa: E402
import pattern_common as pc      # noqa: E402
import audit, idiom_judge, metrics, tkc_pin   # noqa: E402

# 131.39: pin the compiler once for the module -- a concurrent `make` relinks
# ~/tk/toke/tkc mid-run (seen while writing these tests: tkc -> toke.bak)
try:
    PIN = tkc_pin.pin().install(validate, metrics, idiom_judge, audit)
except Exception:                                   # no compiler on this machine
    PIN = None
HAVE_TKC = PIN is not None
need_tkc = pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")


def _tkc():
    return PIN.argv0

# modelled on the real A-ERR-0016 base (parseint); its gamed variant v22 routes
# the marker through an `errstr` helper exactly like GAMED_HELPER
SPEC = {"task_id": "A-ERR-9000v1", "category": "A-ERR", "task_type": "single_function",
        "difficulty": 1, "domain_context_v03": "",
        "description_v03": ("Write a function f=parseint(num1:str):i64!$parseerr that parses num1 "
                            "as an integer, returning InvalidFormat if not numeric or EmptyInput if "
                            "empty. Define t=$parseerr{$invalidformat:str;$emptyinput:bool}. "
                            "Variant 1: use parameter names num1."),
        "description": "Write a function f=parseint(s:$str):i64!ParseErr ...",
        "input_types_v03": ["str"], "output_type_v03": "i64!ParseErr",
        "test_cases": [{"inputs": ["12"], "expected": 12},
                       {"inputs": [""], "expected": {"err": "EmptyInput"}},
                       {"inputs": ["x1"], "expected": {"err": "InvalidFormat"}}],
        "_tests_from": "a_tests"}
PREFIX = "m=harness;\ni=io:std.io;\ni=s:std.str;\n"
TYPE = "t=$parseerr{$invalidformat:str;$emptyinput:bool};\n"
GOOD = (TYPE + "f=parseint(num1:str):i64!$parseerr{\n  if(num1==\"\"){<$parseerr{$emptyinput:true}};\n"
        "  <mt s.toint(num1){$ok:n n;$err:e <$parseerr{$invalidformat:num1}}\n};\n")
GAMED = (TYPE + "f=parseint(num1:str):str{\n  if(num1==\"\"){<\"{'err': 'EmptyInput'}\"};\n"
         "  <mt s.toint(num1){$ok:n \"\\(n)\";$err:e \"{'err': 'InvalidFormat'}\"}\n};\n")
GAMED_HELPER = (TYPE + "f=errstr(e:$parseerr):str{\n  <mt e{$invalidformat:x \"{'err': 'InvalidFormat'}\";"
                "$emptyinput:b \"{'err': 'EmptyInput'}\"}\n};\n"
                "f=tryparse(num1:str):i64!$parseerr{\n  if(num1==\"\"){<$parseerr{$emptyinput:true}};\n"
                "  <mt s.toint(num1){$ok:n n;$err:e <$parseerr{$invalidformat:num1}}\n};\n"
                "f=parseint(num1:str):str{\n  <mt tryparse(num1){$ok:v \"\\(v)\";$err:e errstr(e)}\n};\n")
WIDENED = GOOD.replace(":i64!$parseerr{", ":u64!$parseerr{").replace("$ok:n n;", "$ok:n (n as u64);")
# second gaming shape (A-ERR-0021v3 / 0059v82): the named target is a correct
# union, a trailing same-arity wrapper returning the marker str is what
# driver.find_target (last matching decl) calls
WRAPPED = (GOOD + "f=showparse(num1:str):str{\n  <mt parseint(num1){$ok:v \"\\(v)\";$err:e "
           "mt e{$invalidformat:x \"{'err': 'InvalidFormat'}\";$emptyinput:b \"{'err': 'EmptyInput'}\"}}\n};\n")


# ------------------------------------------------------ validate helpers ---
def test_spec_return_type_prefers_the_v03_signature_then_the_field():
    assert validate.spec_return_type(SPEC) == "i64!$parseerr"
    assert validate.spec_return_type({"description": "f=f(a:i64):u64. Then", "output_type": "i64"}) == "u64"
    assert validate.spec_return_type({"description": "f=g(xs:@(i64)):@(i64)!LookupErr and"}) == "@(i64)!LookupErr"
    assert validate.spec_return_type({"output_type": "@(i64)!LookupErr"}) == "@(i64)!LookupErr"
    assert validate.spec_return_type({"description": "no sig here"}) is None


def test_declared_return_type_is_depth_aware_and_tolerates_space():
    src = "f=lookup(xs:@(i64);i:i64):@(i64)!$lookuperr{\n <xs\n};\nf=other(a:i64):i64 {\n <a\n};\n"
    assert validate.declared_return_type(src, "lookup") == "@(i64)!$lookuperr"
    assert validate.declared_return_type(src, "other") == "i64"
    assert validate.declared_return_type(src, "missing") is None
    assert validate.declared_return_type("f=noret(a:i64){<a};", "noret") is None


def test_norm_return_type_equates_spellings():
    n = validate.norm_return_type
    assert n("i64!ParseErr") == n("i64!$parseerr") == "i64!parseerr"
    assert n("@(@(i64))!LookupErr") == n("@(@(i64))!$lookuperr") == "@@i64!lookuperr"
    assert n("@(i64)") == "@i64" and n(" str ") == "str" and n(None) is None
    assert validate.is_error_union("i64!$e") and not validate.is_error_union("str")


def test_return_type_conforms_hard_soft_ok_and_allowlist(monkeypatch):
    ok, detail, soft, decl = validate.return_type_conforms(SPEC, PREFIX + GAMED)
    assert ok is False and decl == "str" and soft is None
    assert detail == "parseint declared `str`, spec `i64!$parseerr` requires an error union"
    ok, detail, soft, decl = validate.return_type_conforms(SPEC, PREFIX + GOOD)
    assert (ok, detail, soft, decl) == (True, "ok", None, "i64!$parseerr")
    ok, _, soft, decl = validate.return_type_conforms(SPEC, PREFIX + WIDENED)
    assert ok is True and decl == "u64!$parseerr"
    assert soft == "parseint declared `u64!$parseerr`, spec `i64!$parseerr`"
    monkeypatch.setattr(validate, "RETURN_TYPE_ALLOWLIST", {"i64!parseerr": {"u64!parseerr"}})
    ok, detail, soft, _ = validate.return_type_conforms(SPEC, PREFIX + WIDENED)
    assert ok is True and soft is None and detail == "ok"
    # wrapper shape: named target fine, the driver's callee is not -> hard fail
    ok, detail, soft, decl = validate.return_type_conforms(SPEC, PREFIX + WRAPPED)
    assert ok is False and decl == "i64!$parseerr" and soft is None
    assert detail == ("driver callee `showparse` declared `str` (target parseint is `i64!$parseerr`), "
                      "spec `i64!$parseerr` requires an error union")
    # not applicable: no target in the spec / function absent
    assert validate.return_type_conforms({"description": "print"}, PREFIX + GAMED)[3] is None
    assert validate.return_type_conforms(SPEC, "f=other(a:i64):i64{<a};")[3] is None


# --------------------------------------------------- err_union_check pure ---
def _node(kind, start, end, **extra):
    return {"kind": kind, "span": {"start": start, "end": end}, "children": [], **extra}


def _fake_ast(src, fns):
    """Hand-built dump-ast shape: fns = [(name, declared_return_text, [str
    literal values])]; spans index into src the way tkc's do."""
    out = [_node("MODULE_DECL", 0, 1)]
    for name, ret, lits in fns:
        s = src.index(f"f={name}(")
        rs = src.index("):", s) + 2
        head = ret.split("!")[0]
        rkids = [_node("TYPE_EXPR", rs, rs + len(head))]
        if "!" in ret:
            e = ret.split("!")[1].lstrip("$")
            rkids.append(_node("TYPE_IDENT", rs + len(ret) - len(e), rs + len(ret), name=e))
        rspec = _node("RETURN_SPEC", rs, rs + len(head))
        rspec["children"] = rkids
        body = _node("STMT_LIST", 0, 0)
        body["children"] = [_node("STR_LIT", 0, 0, value=l) for l in lits]
        f = _node("FUNC_DECL", s, s)
        f["children"] = [_node("IDENT", s + 2, s + 2 + len(name), name=name), rspec, body]
        out.append(f)
    return {"kind": "PROGRAM", "children": out}


HELPER_FNS = [("errstr", "str", ["\"{'err': 'InvalidFormat'}\"", "\"{'err': 'EmptyInput'}\""]),
              ("tryparse", "i64!$parseerr", ["\"\""]),
              ("parseint", "str", ["\"\\(v)\""])]
GOOD_FNS = [("parseint", "i64!$parseerr", ["\"\""])]


def test_ast_functions_slices_declared_return_and_collects_literals():
    src = PREFIX + GAMED_HELPER
    funcs = euc.ast_functions(_fake_ast(src, HELPER_FNS), src)
    assert funcs["parseint"]["declared"] == "str" and funcs["parseint"]["is_union"] is False
    assert funcs["tryparse"]["declared"] == "i64!$parseerr" and funcs["tryparse"]["is_union"] is True
    assert funcs["errstr"]["str_lits"] == ["\"{'err': 'InvalidFormat'}\"", "\"{'err': 'EmptyInput'}\""]


def test_marker_texts_cover_err_and_error_keys():
    spec = dict(SPEC, test_cases=[{"inputs": [[]], "expected": {"error": "DivByZero"}},
                                  {"inputs": [[1]], "expected": {"a": 1}}])       # a map, not a marker
    assert euc.marker_texts(spec) == {"{'err", "{'error': 'DivByZero'}"}
    assert euc.marker_texts(SPEC) == {"{'err", "{'err': 'EmptyInput'}", "{'err': 'InvalidFormat'}"}
    assert euc.has_err_case(spec) and not euc.has_err_case(dict(SPEC, test_cases=[]))


def test_classify_gamed_via_literal_in_helper():
    src = PREFIX + GAMED_HELPER
    row = euc.classify(SPEC, src, euc.ast_functions(_fake_ast(src, HELPER_FNS), src))
    assert row["verdict"] == "gamed_err_marker" and row["hard_fail"] is True
    assert row["evidence"] == ["literal"] and row["marker_in"] == ["errstr"]
    assert row["target"] == "parseint" and row["declared_return"] == "str"
    assert row["spec_return_norm"] == "i64!parseerr"
    assert row["ast_text_agree"] is True and row["flags"] == ["gamed_err_marker"]


def test_classify_gamed_via_printed_marker_without_a_literal():
    src = PREFIX + GAMED_HELPER
    funcs = euc.ast_functions(_fake_ast(src, HELPER_FNS), src)
    funcs["errstr"]["str_lits"] = ["\"built at run time\""]           # no literal to find
    row = euc.classify(SPEC, src, funcs)
    assert row["verdict"] == "wrong_return_type" and row["hard_fail"] is True and row["evidence"] == []
    row = euc.classify(SPEC, src, funcs, exec_result={"build": True, "printed_marker": True, "got": []})
    assert row["verdict"] == "gamed_err_marker" and row["evidence"] == ["printed"]
    # a run that does not print the marker is not evidence
    row = euc.classify(SPEC, src, funcs, exec_result={"build": True, "printed_marker": False, "got": []})
    assert row["verdict"] == "wrong_return_type"


def test_classify_wrapper_shape_uses_the_driver_callee():
    src = PREFIX + WRAPPED
    fns = GOOD_FNS + [("showparse", "str", ["\"{'err': 'InvalidFormat'}\"", "\"{'err': 'EmptyInput'}\""])]
    row = euc.classify(SPEC, src, euc.ast_functions(_fake_ast(src, fns), src))
    assert row["target"] == "parseint" and row["driver_callee"] == "showparse"
    assert row["declared_return"] == "i64!$parseerr" and row["callee_return"] == "str"
    assert row["verdict"] == "gamed_err_marker" and row["gaming_shape"] == "wrapper_returns_str"
    assert row["hard_fail"] is True and row["marker_in"] == ["showparse"]
    plain = euc.classify(SPEC, PREFIX + GAMED, euc.ast_functions(_fake_ast(PREFIX + GAMED, [("parseint", "str", [])]), PREFIX + GAMED))
    assert plain["gaming_shape"] == "target_returns_str" and plain["driver_callee"] == "parseint"


def test_ast_functions_spans_array_returns_whole_subtree():
    # `@T` returns: a 1-char `@` node with the element TYPE_EXPR nested under it
    src = "m=x;\nf=g(a:str):@(str)!$e{\n  <@(a)\n};\n"
    rs = src.index("):") + 2
    at = _node("TYPE_EXPR", rs, rs + 1)
    at["children"] = [_node("TYPE_EXPR", rs + 2, rs + 5)]
    rspec = _node("RETURN_SPEC", rs, rs + 1)
    rspec["children"] = [at, _node("TYPE_IDENT", rs + 8, rs + 9, name="e")]
    f = _node("FUNC_DECL", 5, 5)
    f["children"] = [_node("IDENT", 7, 8, name="g"), rspec, _node("STMT_LIST", 0, 0)]
    fn = euc.ast_functions({"kind": "PROGRAM", "children": [f]}, src)["g"]
    assert fn["declared"] == "@(str)!$e" and fn["is_union"] is True


def test_classify_correct_soft_mismatch_text_fallback_and_no_target():
    good = PREFIX + GOOD
    row = euc.classify(SPEC, good, euc.ast_functions(_fake_ast(good, GOOD_FNS), good))
    assert row["verdict"] == "correct" and row["hard_fail"] is False and row["flags"] == []
    wid = PREFIX + WIDENED
    row = euc.classify(SPEC, wid, euc.ast_functions(_fake_ast(wid, [("parseint", "u64!$parseerr", [])]), wid))
    assert row["verdict"] == "wrong_return_type" and row["hard_fail"] is False
    assert row["declared_return_norm"] == "u64!parseerr"
    # text fallback when the AST is unavailable
    row = euc.classify(SPEC, PREFIX + GAMED, {}, ast_ok=False)
    assert row["verdict"] == "gamed_err_marker" and row["marker_in"] == ["<text>"]
    bare = dict(SPEC, description_v03="no sig", description="no sig", output_type_v03=None, output_type=None)
    row = euc.classify(bare, good, {})
    assert row["verdict"] == "no_spec_return" and row["declared_return"] == "i64!$parseerr"
    row = euc.classify(SPEC, PREFIX + TYPE, {})                  # no function at all (131.40: 6 such)
    assert row["verdict"] == "no_target"


def test_summarise_counts_per_base_and_evidence():
    rows = [{"task_id": "A-ERR-1v1", "base": "A-ERR-1", "task_type": "single_function",
             "verdict": "gamed_err_marker", "flags": ["gamed_err_marker"], "evidence": ["literal"]},
            {"task_id": "A-ERR-1v2", "base": "A-ERR-1", "task_type": "single_function",
             "verdict": "gamed_err_marker", "flags": ["gamed_err_marker"], "evidence": ["literal", "printed"]},
            {"task_id": "A-ERR-2v1", "base": "A-ERR-2", "task_type": "single_function",
             "verdict": "correct", "flags": [], "evidence": []}]
    s = euc.summarise(rows)
    assert s["verdicts"] == {"gamed_err_marker": 2, "correct": 1}
    assert s["gamed_evidence"] == {"literal": 1, "literal+printed": 1}
    assert s["by_base"]["A-ERR-1"] == {"records": 2, "gamed_err_marker": 2}


def test_proof_sample_is_deterministic_and_excludes_gamed():
    man = {f"A-ERR-1v{i}": {"category": "A-ERR"} for i in range(30)}
    man.update({f"D-CLI-1v{i}": {"category": "D-CLI"} for i in range(30)})
    gamed = {f"A-ERR-1v{i}" for i in range(10)}
    ok1, g1 = euc.proof_sample([], man, gamed)
    ok2, g2 = euc.proof_sample([], man, gamed)
    assert (ok1, g1) == (ok2, g2) and len(ok1) == 20 and len(g1) == 5
    assert not (set(ok1) & gamed) and set(g1) <= gamed
    assert sum(t.startswith("D-CLI") for t in ok1) == 10       # round-robin over categories


# ---------------------------------------------------------------- routing ---
def test_a_tests_stamp_reports_bases_and_provenance(tmp_path):
    d = tmp_path / "audit" / "a_tests"
    d.mkdir(parents=True)
    (d / "A-ERR-0001.json").write_text(json.dumps({"provenance": "129.7-agent+ref-verified"}))
    (d / "A-ERR-0002.json").write_text(json.dumps({"provenance": "131.47-reauthored"}))
    (d / "A-ERR-0003.json").write_text(json.dumps({"provenance": "131.47-reauthored"}))
    st = euc.a_tests_stamp(str(tmp_path))
    assert st["bases"] == 3 and st["newest_mtime"].endswith("Z")
    assert list(st["provenance"]) == ["131.47-reauthored", "129.7-agent+ref-verified"]


def test_patch_row_is_sweep_schema_and_resolves_both_catalogue_entries():
    sweep = {"task_id": "A-ERR-0016v22", "category": "A-ERR", "task_type": "single_function",
             "source": "regen", "bucket": "LEAVE", "violations": [], "exemptions": ["cond-bind-if"],
             "proxy_tokens": 40, "est_saving_tokens": 0, "perf_sensitive": False}
    row = euc.patch_row(sweep, "A-ERR-0016v22", ts=1)
    assert row["bucket"] == "AGENT" and row["would_bucket"] == "LEAVE" and row["story"] == "131.42"
    assert row["violations"] == [{"rule": "gamed-err-marker", "severity": "error", "pattern_id": "err-propagate"},
                                 {"rule": "gamed-err-marker", "severity": "error", "pattern_id": "err-default"}]
    assert row["exemptions"] == ["cond-bind-if"] and row["proxy_tokens"] == 40   # original row kept
    assert pc.bucket_rules(row) == ["gamed-err-marker"]
    if os.path.exists(pc.CATALOGUE_PATH):
        cat = pc.load_catalogue()
        ids = [e["id"] for e in pc.entries_for_row(cat, pc.bucket_rules(row), row)]
        assert ids == ["err-propagate", "err-default"]
    # no sweep row: a minimal row is synthesised
    bare = euc.patch_row(None, "A-ERR-0001v11", ts=1)
    assert bare["category"] == "A-ERR" and bare["patch_of"] is None and bare["bucket"] == "AGENT"


def test_route_appends_dedup_writes_reasons_and_patch_and_merge_wins(tmp_path):
    corpus = tmp_path / "corpus"
    bdir = corpus / "audit" / "buckets"
    bdir.mkdir(parents=True)
    (bdir / "agent.txt").write_text("A-ARR-0001v1\nA-ERR-0016v22\n")
    sweep = corpus / "audit" / "pattern_sweep.jsonl"
    with open(sweep, "w") as f:
        for tid, b in (("A-ERR-0016v22", "AGENT"), ("A-ERR-0016v25", "LEAVE"), ("A-ARR-0001v1", "LEAVE")):
            f.write(json.dumps({"task_id": tid, "category": tid[:5], "task_type": "single_function",
                                "source": "regen", "bucket": b, "violations": [], "exemptions": []}) + "\n")
    gamed = ["A-ERR-0016v25", "A-ERR-0016v22", "A-ERR-0001v11"]
    res = euc.route(gamed, corpus=str(corpus), ts=7)
    assert res["agent_txt_added"] == 2 and res["agent_txt_already_present"] == 1 and res["agent_txt_total"] == 4
    lines = (bdir / "agent.txt").read_text().splitlines()
    assert lines == ["A-ARR-0001v1", "A-ERR-0016v22", "A-ERR-0001v11", "A-ERR-0016v25"]
    assert res["agent_txt_prior_route_removed"] == 0 and res["agent_txt_baseline"] == 2
    # idempotent: a second identical route restores the baseline, re-appends the same set
    res2 = euc.route(gamed, corpus=str(corpus), ts=7)
    assert res2["agent_txt_added"] == 2 and res2["agent_txt_prior_route_removed"] == 2
    assert (bdir / "agent.txt").read_text().splitlines() == lines
    # re-scan with a SMALLER gamed set (the 131.47 a_tests re-author changed
    # what "gamed" means): the stale id goes, the sweep's own AGENT row stays
    res3 = euc.route(["A-ERR-0016v22"], corpus=str(corpus), ts=8)
    assert (bdir / "agent.txt").read_text().splitlines() == ["A-ARR-0001v1", "A-ERR-0016v22"]
    assert res3["agent_txt_added"] == 0 and res3["agent_txt_prior_route_removed"] == 2
    assert [json.loads(l)["task_id"] for l in open(bdir / "agent_reasons.jsonl")] == ["A-ERR-0016v22"]
    euc.route(gamed, corpus=str(corpus), ts=7)                  # back to the full set for the rest
    reasons = [json.loads(l) for l in open(bdir / "agent_reasons.jsonl")]
    assert reasons == [{"task_id": t, "reason": "131.42 gamed_err_marker"} for t in sorted(gamed)]
    patch = corpus / "audit" / "pattern_sweep.131.42.patch.jsonl"
    prows = [json.loads(l) for l in open(patch)]
    assert [r["task_id"] for r in prows] == sorted(gamed) and res["patch_rows_with_sweep_row"] == 2
    assert all(r["bucket"] == "AGENT" and r["ts"] == 7 for r in prows)
    # `cat original patch > merged`: load_bucket takes the later line per id
    merged = corpus / "audit" / "merged.jsonl"
    merged.write_text(sweep.read_text() + patch.read_text())
    rows = pc.load_bucket(str(merged), "AGENT")
    assert set(rows) == set(gamed)
    assert rows["A-ERR-0016v25"]["would_bucket"] == "LEAVE" and rows["A-ERR-0016v25"]["story"] == "131.42"
    assert "A-ARR-0001v1" not in rows


# ------------------------------------------------------------- with tkc ---
@need_tkc
def test_dump_ast_matches_text_parser_on_real_sources(tmp_path):
    tkc = _tkc()
    for body, want, union in ((GAMED, "str", False), (GAMED_HELPER, "str", False),
                              (GOOD, "i64!$parseerr", True), (WIDENED, "u64!$parseerr", True),
                              (WRAPPED, "i64!$parseerr", True)):
        src = PREFIX + body
        ast = euc.dump_ast(tkc, src, str(tmp_path))
        assert ast is not None and ast["kind"] == "PROGRAM"
        f = euc.ast_functions(ast, src)["parseint"]
        assert f["declared"] == want and f["is_union"] is union
        assert validate.norm_return_type(validate.declared_return_type(src, "parseint")) == validate.norm_return_type(want)
    src = PREFIX + GAMED_HELPER
    row = euc.classify(SPEC, src, euc.ast_functions(euc.dump_ast(tkc, src, str(tmp_path)), src))
    assert row["verdict"] == "gamed_err_marker" and row["marker_in"] == ["errstr"] and row["ast_text_agree"] is True
    arr = "m=x;\nf=g(a:str):@str{\n  <@(a)\n};\nf=h(a:str):@(str)!$e{\n  <@(a)\n};\n"
    fa = euc.ast_functions(euc.dump_ast(tkc, arr, str(tmp_path)), arr)
    assert (fa["g"]["declared"], fa["g"]["is_union"]) == ("@str", False)
    assert (fa["h"]["declared"], fa["h"]["is_union"]) == ("@(str)!$e", True)
    assert euc.dump_ast(tkc, "fn broken( {", str(tmp_path)) is None


@need_tkc
def test_printed_marker_evidence_through_the_driver(tmp_path):
    tkc = _tkc()
    d = str(tmp_path)
    for body in (GAMED, GAMED_HELPER):
        res = euc.execute_tests(SPEC, PREFIX + body, d, "g", tkc)
        assert res["build"] is True and res["printed_marker"] is True, res
        assert "{'err': 'EmptyInput'}" in res["got"]
    res = euc.execute_tests(SPEC, PREFIX + GOOD, d, "ok", tkc)
    assert res["printed_marker"] in (False, None)            # a real union never prints the marker
    src = PREFIX + GAMED
    row = euc.classify(SPEC, src, euc.ast_functions(euc.dump_ast(tkc, src, d), src),
                       exec_result=euc.execute_tests(SPEC, src, d, "g2", tkc))
    assert row["evidence"] == ["literal", "printed"]
    assert euc.execute_tests(dict(SPEC, test_cases=[]), src, d, "none", tkc) is None


@need_tkc
def test_validate_one_gates_return_type_gate(tmp_path):
    import run_shard
    d = str(tmp_path)
    # gamed: gate off = accepted (the 129-era verdict); gate on = hard reject
    rec0, ok0, reason0, g0 = run_shard.validate_one_gates(SPEC, GAMED, d, return_type_gate=False)
    assert ok0 is True and g0["return_type"] is None and rec0["regen"]["return_type_ok"] is None, reason0
    rec, ok, reason, gates = run_shard.validate_one_gates(SPEC, GAMED, d)
    assert ok is False and gates["return_type"] is False
    assert reason == "return_type: parseint declared `str`, spec `i64!$parseerr` requires an error union"
    assert rec["regen"]["return_type_ok"] is False and rec["regen"]["declared_return_type"] == "str"
    # the other gates are untouched by the new one
    assert {k: v for k, v in g0.items() if k != "return_type"} == {k: v for k, v in gates.items() if k != "return_type"}
    # wrapper shape: rejected on the driver's callee
    rec, ok, reason, gates = run_shard.validate_one_gates(SPEC, WRAPPED, d)
    assert ok is False and gates["return_type"] is False and reason.startswith("return_type: driver callee `showparse`"), reason
    # correct union: accepted, gate True, no soft flag
    rec, ok, reason, gates = run_shard.validate_one_gates(SPEC, GOOD, d)
    assert ok is True and gates["return_type"] is True and rec["regen"]["return_type_mismatch"] is None, reason
    # widened payload: union present -> soft flag only (131.43 decides)
    rec, ok, reason, gates = run_shard.validate_one_gates(SPEC, WIDENED, d)
    assert ok is True and gates["return_type"] is True, reason
    assert rec["regen"]["return_type_mismatch"] == "parseint declared `u64!$parseerr`, spec `i64!$parseerr`"
    # stdin_program: gate not applicable
    sp = {"task_id": "L-1", "category": "D-CLI", "task_type": "stdin_program", "difficulty": 1,
          "description": "echo f=main():i64", "test_cases": []}
    rec, ok, reason, gates = run_shard.validate_one_gates(
        sp, 'm=e;\ni=io:std.io;\nf=main():i64{\n  io.println("x");\n  <0\n};\n', d)
    assert gates["return_type"] is None and rec["regen"]["return_type_ok"] is None
