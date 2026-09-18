"""131.10 — idiom judge on tkc --lint pattern rules + validate gate helpers.

Pure tests use synthetic diag lists (the JSON shape `tkc --lint --diag-json`
emits: {code, rule, severity, stage, message, file, pos, span, fix}); the
integration tests run the real compiler and are skipped when it is absent.
"""
import json, os, sys, tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import idiom_judge as ij  # noqa: E402
import metrics            # noqa: E402

HAVE_TKC = os.path.exists(ij.TKC)

STUB_SRC = ("m=harness;\ni=io:std.io;\ni=s:std.str;\n"
            "f=shr(a:i64;b:i64):i64{<0};\n"
            "f=setnthbit(n:i64;m:i64):i64{\n  if(m<0){<n};\n  <n|(1<<m)\n};\n")


def diag(rule, severity, start=100, **kw):
    d = {"schema_version": "1.0", "code": rule, "rule": rule, "severity": severity,
         "stage": "lint", "message": kw.get("message", rule), "file": "x.tk",
         "pos": {"offset": start, "line": 1, "col": 1}, "span": {"start": start, "end": start + 5}}
    return d


# ------------------------------------------------------------ stub prefix ---
def test_stub_prefix_covers_header_imports_and_context_stubs():
    n = ij.stub_prefix_len(STUB_SRC)
    assert STUB_SRC[:n] == "m=harness;\ni=io:std.io;\ni=s:std.str;\nf=shr(a:i64;b:i64):i64{<0};\n"
    assert ij.stub_prefix_len("m=demo;\ni=io:std.io;\n") == 0


def test_strip_stub_diags_drops_only_prefix_diagnostics():
    n = ij.stub_prefix_len(STUB_SRC)
    inside = diag("unused-import", "warning", start=11)
    stub_fn = diag("unused-let", "warning", start=n - 3)
    record = diag("unused-import", "warning", start=n + 4)
    kept = ij.strip_stub_diags([inside, stub_fn, record], STUB_SRC)
    assert kept == [record]
    # non-harness source: nothing stripped
    assert len(ij.strip_stub_diags([inside], "m=demo;\ni=io:std.io;\n")) == 1


# ------------------------------------------------------------------ score ---
def test_score_maps_rule_ids_to_penalties():
    s, notes = ij.score("m=x;", diags=[diag("mut-flag-if", "warning"), diag("mut-flag-if", "warning")])
    assert s == pytest.approx(0.70)
    assert notes == ["mut-flag-if×2 (-0.30)"]
    s, _ = ij.score("m=x;", diags=[diag("discarded-value-result", "error")])
    assert s == pytest.approx(0.80)
    s, _ = ij.score("m=x;", diags=[diag("single-use-let", "hint"), diag("loop-rebuilds-array", "hint")])
    assert s == pytest.approx(0.93)
    s, _ = ij.score("m=x;", diags=[diag("string-concat-chain", "warning")])
    assert s == pytest.approx(0.85)


def test_score_caps_at_three_per_rule_and_floors_at_zero():
    s, notes = ij.score("m=x;", diags=[diag("flag-soup", "warning")] * 7)
    assert s == pytest.approx(0.55)
    assert notes == ["flag-soup×7 (-0.45)"]
    many = [diag(r, "warning") for r in ij.PATTERN_RULES for _ in range(3)]
    s, _ = ij.score("m=x;", diags=many)
    assert s == 0.0


def test_score_ignores_non_pattern_rules_and_stub_diags():
    s, notes = ij.score(STUB_SRC, diags=[diag("unused-import", "warning", start=11),
                                         diag("mutable-never-mutated", "warning", start=200),
                                         diag("mut-flag-if", "warning", start=20)])  # inside stub
    assert s == 1.0 and notes == []


def test_hand_rolled_parser_stays_regex():
    src = ('m=p;i=s:std.str;f=scan(t:str):i64{let n=mut.0;'
           'lp(let i=0;i<s.len(t);i=i+1){if(t.charat(i)=="x"){n=n+t.slice(i;i+1).len}};<n};')
    s, notes = ij.score(src, diags=[])
    assert s == pytest.approx(0.80)
    assert notes == ["hand-rolled-parser×1 (-0.20)"]


def test_legacy_regex_judge_is_preserved():
    src = 'm=x;f=g(a:i64):i64{let r=mut.0;if(a>1){r=1}el{r=2};<r};'
    s, notes = ij.legacy_score(src)
    assert s == pytest.approx(0.85) and notes == ["mut-flag-if×1 (-0.15)"]
    # the 129 regex only saw a nested concat in FIRST-argument position — that
    # quirk is preserved on purpose (legacy_score reproduces the frozen gate)
    s2, _ = ij.legacy_score('m=x;f=g():str{<s.concat(s.concat("b";"c");"a")};')
    assert s2 == pytest.approx(0.85)
    s3, _ = ij.legacy_score('m=x;f=g():str{<s.concat("a";s.concat("b";"c"))};')
    assert s3 == 1.0


# -------------------------------------------------------------- hard gate ---
def test_hard_gate_fails_on_error_and_warning_but_not_hint():
    diags = [diag("single-use-let", "hint"), diag("loop-rebuilds-array", "hint"),
             diag("unused-let", "warning"), diag("mut-flag-if", "warning"),
             diag("discarded-value-result", "error")]
    fail = ij.hard_gate(diags)
    assert [d["rule"] for d in fail] == ["mut-flag-if", "discarded-value-result"]
    assert ij.violation_summary(fail) == "mut-flag-if×1,discarded-value-result×1"
    assert ij.hard_gate([diag("single-use-let", "hint")]) == []


def test_style_mandate_exempts_the_contradicted_rule_only():
    spec = {"description": "Do X. Variant 7: use parameter names a and b. "
                           "Accumulate the result in a mutable binding."}
    m = ij.style_mandate(spec)
    assert m.startswith("use parameter names a and b. Accumulate")
    ex = ij.mandate_exempt_rules(m)
    assert set(ex) == {"mut-flag-if", "flag-soup", "loop-rebuilds-array"}
    diags = [diag("mut-flag-if", "warning"), diag("discarded-value-result", "error")]
    fail = ij.hard_gate(diags, ex)
    assert [d["rule"] for d in fail] == ["discarded-value-result"]   # never exempt
    assert ij.mandate_exempt_rules("Use nested if/el blocks for the control flow.") == ()
    assert ij.mandate_exempt_rules(None) == ()
    assert ij.style_mandate({"description": "no variant clause"}) is None


# ---------------------------------------------------------------- budget ---
def test_over_budget_uses_task_type_key_then_category():
    b = {"budget": {"A-ARR/single_function": 60, "A-ARR": 90}}
    assert metrics.over_budget("A-ARR", "single_function", 61, b) is True
    assert metrics.over_budget("A-ARR", "single_function", 60, b) is False
    assert metrics.over_budget("A-ARR", "full_program", 80, b) is False    # category fallback
    assert metrics.over_budget("D-NET", "full_program", 999, b) is None    # unknown
    assert metrics.over_budget("A-ARR", "full_program", None, b) is None
    assert metrics.load_proxy_budget(os.path.join(tempfile.gettempdir(), "no_such_budget.json")) == {}


# ------------------------------------------------------------ integration ---
@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_tkc_pattern_rules_drive_score_and_gate():
    src = ('m=demo;\ni=io:std.io;\nf=main():i64{\n  let arr=mut.@(1;2);\n  arr.set(0;5);\n'
           '  let ok=mut.false;\n  if(arr.len==2){ok=true};\n  if(arr.len==3){ok=true};\n'
           '  io.println("\\(arr.len) \\(ok)");\n  <0\n};\n')
    diags = ij.lint_diags(src=src)
    rules = sorted(d["rule"] for d in diags)
    assert "discarded-value-result" in rules and "flag-soup" in rules
    s, notes = ij.score(src, diags)
    assert s == pytest.approx(0.65)
    fail = ij.hard_gate(diags)
    assert {d["rule"] for d in fail} == {"discarded-value-result", "flag-soup"}
    # score() without diags runs tkc itself and agrees
    assert ij.score(src)[0] == pytest.approx(s)


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_metrics_lint_exempts_stub_imports_and_reports_pattern_violations(tmp_path):
    p = tmp_path / "h.tk"
    p.write_text(STUB_SRC)
    li = metrics.lint(str(p))
    assert not any(d["rule"] == "unused-import" for d in li), li
    plain = tmp_path / "d.tk"
    plain.write_text("m=demo;\ni=io:std.io;\ni=s:std.str;\nf=main():i64{<0};\n")
    li2 = metrics.lint(str(plain))
    assert sum(1 for d in li2 if d["rule"] == "unused-import") == 2   # not a harness: kept
    m = metrics.analyse(str(p))
    assert m["lint_pattern_violations"] == []
    assert "proxy_tokens" in m


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_validate_one_gates_rejects_pattern_warning_and_records_fields(tmp_path):
    import run_shard
    spec = {"task_id": "T-0001", "category": "D-CLI", "task_type": "full_program",
            "difficulty": 1, "description": "print. Variant 1: use parameter names a."}
    bad = ('m=demo;\ni=io:std.io;\nf=grade(x:i64):i64{\n  let g=mut.0;\n  if(x>0){g=1}el{g=2};\n  <g\n};\n'
           'f=main():i64{\n  io.println("\\(grade(3))");\n  <0\n};\n')
    rec, ok, reason, gates = run_shard.validate_one_gates(spec, bad, str(tmp_path))
    assert ok is False and reason.startswith("pattern: mut-flag-if×1"), reason
    assert gates["pattern"] is False and gates["compile"] is True
    assert rec["regen"]["lint_pattern_violations"][0]["rule"] == "mut-flag-if"
    assert rec["tk_tokens"] == rec["regen"]["proxy_tokens"]
    # same source, spec mandates the mutable binding -> exempt, accepted
    spec2 = dict(spec, description="print. Variant 2: Accumulate the result in a mutable binding.")
    rec2, ok2, reason2, gates2 = run_shard.validate_one_gates(spec2, bad, str(tmp_path))
    assert ok2 is True and gates2["pattern"] is True, reason2
    assert rec2["regen"]["lint_exempt"] == ["mut-flag-if"]
    good = ('m=demo;\ni=io:std.io;\nf=grade(x:i64):i64{<if(x>0){1}el{2}};\n'
            'f=main():i64{\n  io.println("\\(grade(3))");\n  <0\n};\n')
    rec3, ok3, reason3, gates3 = run_shard.validate_one_gates(spec, good, str(tmp_path))
    assert ok3 is True and gates3["pattern"] is True, reason3
    assert rec3["regen"]["lint_pattern_violations"] == []
    if metrics.proxy_counter() is not None:
        assert isinstance(rec3["regen"]["proxy_tokens"], int) and rec3["regen"]["proxy_tokens"] > 0


# ----------------------- 127.33: expression-if branch value is a value ---
# tkc < 127.33 reported `discarded-value-result` on the tail call of an
# expression-if / mt-arm branch (48 hits / 31 records in the 131.10 rescore) and
# the judge carried a text-scanning suppressor (`suppress_linter_fps`). lint.c
# is fixed and the suppressor is gone; these pin the compiler behaviour the
# corpus hard gate relies on.
@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_tkc_expr_if_branch_value_is_not_a_discarded_result(tmp_path):
    src = ('m=demo;\nf=g(out:@i64;v:i64):@i64{\n  let o=mut.out;\n'
           '  o=if(v>2){o.append(v)}el{o};\n'
           '  let y=if(v>1){o.push(9)}el if(v>0){o}el{o.append(1)};\n'
           '  if(v>5){o.append(v)};\n'
           '  <if(v>0){y.append(v)}el{y}\n};\n'
           'f=h(a:@i64;i:i64):@i64{\n  let m=mut.@(0);\n  let k=mut.i;\n'
           '  m=if(k<2){let v=a.get(k);k=k+1;m.append(v)}el{m.append(0);m};\n  <m\n};\n')
    p = tmp_path / "expr_if.tk"
    p.write_text(src)
    li = metrics.lint(str(p))
    dv = [d for d in li if d["rule"] == "discarded-value-result"]
    # bind / assign / return / `el if` chain / multi-statement tail are values;
    # only the statement-form if (line 6) and the non-tail call (line 12) fire
    assert sorted(d["line"] for d in dv) == [6, 12]
    assert not any(d.get("suppressed") for d in li)
    gate = ij.hard_gate(li)
    assert sorted((d["rule"], d["line"]) for d in gate) == \
        [("discarded-value-result", 6), ("discarded-value-result", 12)]


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_tkc_expr_if_branch_value_passes_hard_gate_end_to_end(tmp_path):
    src = ('m=demo;\ni=io:std.io;\nf=main():i64{\n  let out=mut.@(1;2);\n'
           '  out=if(out.len>1){out.append(3)}el{out};\n  io.println("\\(out.len)");\n  <0\n};\n')
    p = tmp_path / "fp.tk"
    p.write_text(src)
    li = metrics.lint(str(p))
    assert [d for d in li if d["rule"] == "discarded-value-result"] == []
    assert metrics.pattern_violations(li) == []
    assert ij.hard_gate(li) == []
    assert ij.score(src, li)[0] == pytest.approx(1.0)
