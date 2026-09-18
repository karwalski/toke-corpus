"""131.15 — pattern_prep / check_pattern / bank_pattern over a synthetic
25-row AGENT bucket in a temp corpus (2 batches: 20 + 5).

Every record is a tiny toke 2.8.0 program carrying the mut-flag-if
anti-pattern (catalogue `cond-bind-if`); 21 are full_program, 4
single_function (driver-synthesised main). Needs tkc on disk (~/tk/toke/tkc
or $TKC) — the whole wave is exercised for real: prep -> worker candidates ->
check_pattern -> bank --dry-run (writes nothing) -> bank --bank (records,
ledger, manifest stamp, replaced archive, retry/tier2 queues, .done) ->
re-prep idempotency -> exhausted retry.
"""
import hashlib, json, os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pattern_common as pc      # noqa: E402
import pattern_prep              # noqa: E402
import check_pattern             # noqa: E402
import bank_pattern              # noqa: E402
import manifest_tool             # noqa: E402

pytestmark = pytest.mark.skipif(not os.path.exists(pc.TKC), reason="tkc not built")

N = 25
SF = {21, 22, 23, 24}            # single_function rows
PERF_SENSITIVE = {5}
CLEAN = {6}                      # record already canonical (sweep row stale)


def _tid(k):
    return f"A-CND-8000v{k}"


def _body(k, form):
    a, b = k + 1, k + 2
    if form == "flag":
        return "let x=mut.0;\n  if(c){x=a}el{x=b};\n  <x"
    if form == "canonical":
        return "if(c){<a}el{<b}"
    if form == "swapped":                     # behaviour change: tests must catch it
        return "if(c){<b}el{<a}"
    if form == "bigger":                      # clean lint but larger --min than the flag form
        return "let r=if(c){a}el{b};\n  let q=r*1;\n  let z=q+0;\n  <z"
    raise ValueError(form)


def _src(k, form):
    fn = f"f=pick(c:bool;a:i64;b:i64):i64{{\n  {_body(k, form)}\n}};\n"
    if k in SF:
        return "m=harness;\ni=io:std.io;\ni=s:std.str;\n" + fn
    a, b = k + 1, k + 2
    return ("m=synth;\ni=io:std.io;\n" + fn +
            f"f=main():i64{{\n  io.println(\"\\(pick(true;{a};{b}))\");\n"
            f"  io.println(\"\\(pick(false;{a};{b}))\");\n  <0\n}};\n")


def _spec(k):
    a, b = k + 1, k + 2
    return {"task_id": _tid(k), "category": "A-CND", "difficulty": 1,
            "task_type": "single_function" if k in SF else "full_program",
            "description": f"Write a function f=pick(c:bool;a:i64;b:i64):i64 that returns a when c "
                           f"is true, else b. Variant {k}: use parameter names c, a and b.",
            "input_types": ["bool", "i64", "i64"], "output_type": "i64",
            "test_cases": [{"inputs": [True, a, b], "expected": a},
                           {"inputs": [False, a, b], "expected": b}]}


def _record(k, src):
    return {"id": "P3-" + _tid(k), "version": 2, "phase": "C", "task_id": _tid(k),
            "tk_source": src, "tk_tokens": None, "attempts": 1, "model": "test",
            "validation": {"compiler_exit_code": 0, "error_codes": []},
            "judge": {"accepted": True, "score": 0.85},
            "regen": {"syntax_version": "v0.4-2.8.0", "card_sha": "x", "category": "A-CND",
                      "task_type": "single_function" if k in SF else "full_program",
                      "difficulty": 1, "source_sha256": hashlib.sha256(src.encode()).hexdigest()}}


def _row(k):
    return {"task_id": _tid(k), "category": "A-CND",
            "task_type": "single_function" if k in SF else "full_program", "source": "record",
            "violations": [{"rule": "mut-flag-if", "severity": "warning",
                            "span": {"start": 56, "end": 90}, "pattern_id": "cond-bind-if"}],
            "exemptions": [], "proxy_tokens": 30, "min_bytes": 167, "est_saving_tokens": 6,
            "perf_sensitive": k in PERF_SENSITIVE, "bucket": "AGENT"}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("wave")
    c = root / "regen_v04"
    (c / "A-CND").mkdir(parents=True)
    (c / "shards").mkdir()
    (c / "ledger").mkdir()
    specs, rows = [], []
    man = manifest_tool.Manifest(str(c / "MANIFEST.jsonl"))
    for k in range(N):
        src = _src(k, "canonical" if k in CLEAN else "flag")
        p = c / "A-CND" / (_tid(k) + ".json")
        p.write_text(json.dumps(_record(k, src)))
        man.stamp(_tid(k), str(p), save=False)
        specs.append(_spec(k))
        rows.append(_row(k))
    man.save()
    (c / "shards" / "shard_synth.jsonl").write_text("".join(json.dumps(s) + "\n" for s in specs))
    # a non-AGENT row must be ignored by the prep
    rows.append({**_row(0), "task_id": "A-CND-8999v1", "bucket": "AUTO"})
    bucket = root / "pattern_sweep.jsonl"
    bucket.write_text("".join(json.dumps(r) + "\n" for r in rows))
    paths = pc.Paths(corpus=str(c), workdir=str(root / "work" / "pattern_131"))
    return {"root": root, "dir": str(c), "bucket": str(bucket), "paths": paths}


def _write_candidate(paths, k, form):
    with open(paths.sub("gen", pc.GEN_PREFIX + _tid(k) + ".tk"), "w") as f:
        f.write(_src(k, form))


def _ledger(paths):
    return [json.loads(l) for l in open(paths.ledger)] if os.path.exists(paths.ledger) else []


# ------------------------------------------------------------------ units ---
def test_bucket_helpers():
    row = _row(3)
    assert pc.bucket_rules(row) == ["mut-flag-if"]
    assert pc.bucket_pattern_ids(row) == ["cond-bind-if"]
    assert pc.bucket_est_saving(row) == 6
    assert pc.bucket_est_saving({"est_saving": 2}) == 2
    assert pc.bucket_exemptions({"exemptions": [{"rule": "single-use-let"}, "flag-soup"]}) == \
        ["single-use-let", "flag-soup"]


def test_entries_for_row_prefers_named_pattern():
    cat = pc.load_catalogue()
    assert len(cat["by_rule"]["mut-flag-if"]) > 1
    named = pc.entries_for_row(cat, ["mut-flag-if"], _row(0))
    assert [e["id"] for e in named] == ["cond-bind-if"]
    unnamed = pc.entries_for_row(cat, ["mut-flag-if"], {"violations": [{"rule": "mut-flag-if"}]})
    assert len(unnamed) == len(cat["by_rule"]["mut-flag-if"])


def test_apply_exemptions_never_exempts_discarded_value_result():
    res = {"record": {}, "gates": {"pattern": False}, "reason": "pattern: x",
           "pattern_hits": [{"rule": "discarded-value-result", "severity": "error"},
                            {"rule": "single-use-let", "severity": "warning"}], "lint_exempt": []}
    pc.apply_exemptions(res, ["discarded-value-result", "single-use-let"])
    assert [d["rule"] for d in res["pattern_hits"]] == ["discarded-value-result"]
    assert res["gates"]["pattern"] is False and res["lint_exempt"] == ["single-use-let"]
    res2 = {"record": {}, "gates": {"pattern": False}, "reason": "pattern: single-use-let×1",
            "pattern_hits": [{"rule": "single-use-let", "severity": "warning"}], "lint_exempt": []}
    pc.apply_exemptions(res2, ["single-use-let"])
    assert res2["gates"]["pattern"] is True and res2["reason"] is None


# ------------------------------------------------------------- the wave ---
def test_wave_end_to_end(corpus):
    paths = corpus["paths"]
    # ---- prep: 25 AGENT rows -> 2 batches, manifest stamped
    m = pattern_prep.prepare(paths, corpus["bucket"])
    assert m["bucket_rows"] == N and m["prepared"] == N and m["deferred"] == 0
    assert m["batches"] == 2 and m["batch_size"] == 20
    b0 = json.load(open(paths.sub("batches", "batch_000.json")))
    b1 = json.load(open(paths.sub("batches", "batch_001.json")))
    assert len(b0["task_ids"]) == 20 and len(b1["task_ids"]) == 5
    assert set(b0["task_ids"]) | set(b1["task_ids"]) == {_tid(k) for k in range(N)}
    for key in ("card_sha", "catalogue_sha", "tkc_sha", "tkc_version"):
        assert m[key], key
    assert m["card_sha"] == pc.short(pc.sha256_file(paths.card))
    assert m["catalogue_sha"] == pc.short(pc.sha256_file(paths.catalogue))
    assert m["tkc_sha"] == pc.short(pc.sha256_file(paths.tkc))
    disk = json.load(open(paths.sub("manifest.json")))
    assert disk["card_sha"] == m["card_sha"] and disk["unbanked_candidates"] == 0
    # prompt: only the named catalogue entry, the card, the test lock, the source
    prompt = open(paths.sub("prompts", _tid(0) + ".txt")).read()
    assert prompt.count("### pattern `") == 1 and "### pattern `cond-bind-if`" in prompt
    assert "mut-flag-if [warning, MUST fix]" in prompt
    assert "SYNTAX CARD" in prompt and m["card_sha"] in prompt
    assert "inputs=[true, 1, 2] -> expected 1" in prompt
    assert "let x=mut.0;" in prompt
    sf_prompt = open(paths.sub("prompts", _tid(21) + ".txt")).read()
    assert "Module shape: single_function" in sf_prompt and "m=harness;" in sf_prompt
    clean_prompt = open(paths.sub("prompts", _tid(6) + ".txt")).read()
    assert "(none reported by tkc right now)" in clean_prompt
    assert "[sweep-flagged; not reported by tkc" in clean_prompt
    meta0 = json.load(open(paths.sub("meta", _tid(0) + ".json")))
    assert meta0["rules_must"] == ["mut-flag-if"] and meta0["patterns"] == ["cond-bind-if"]
    assert meta0["before"]["min_bytes"] and meta0["before"]["proxy_tokens"]
    assert meta0["before"]["file_sha256"] == pc.sha256_file(paths.record("A-CND", _tid(0)))
    assert json.load(open(paths.sub("meta", _tid(5) + ".json")))["perf_sensitive"] is True

    # ---- worker candidates
    _write_candidate(paths, 0, "canonical")      # rewritten
    _write_candidate(paths, 21, "canonical")     # rewritten (single_function, driver + generated inputs)
    _write_candidate(paths, 2, "flag")           # identical, still violating -> failed (pattern)
    _write_candidate(paths, 3, "swapped")        # behaviour change -> failed (tests)
    _write_candidate(paths, 4, "bigger")         # lint-clean but larger -> failed (min_bytes)
    _write_candidate(paths, 5, "canonical")      # rewritten + tier-2 flag
    _write_candidate(paths, 6, "canonical")      # == current clean source -> unchanged
    _write_candidate(paths, 7, "canonical")      # record changed after prep -> skipped
    rec7 = paths.record("A-CND", _tid(7))
    rec7_obj = json.load(open(rec7))
    with open(rec7, "w") as f:
        json.dump(rec7_obj, f, indent=1)                # same content, different bytes

    # ---- check_pattern (worker view)
    ok, lines = check_pattern.check(paths, _tid(0))
    assert ok and lines[0].startswith("PASS"), lines
    assert "pattern=ok" in lines[1] and "min_bytes=ok" in lines[1]
    ok, lines = check_pattern.check(paths, _tid(21))
    assert ok, lines
    ok, lines = check_pattern.check(paths, _tid(2))
    assert not ok and lines[0].startswith("FAIL pattern: mut-flag-if"), lines
    assert any(l.startswith("pattern mut-flag-if [warning]") for l in lines)
    ok, lines = check_pattern.check(paths, _tid(3))
    assert not ok and lines[0].startswith("FAIL runtime:"), lines
    ok, lines = check_pattern.check(paths, _tid(4))
    assert not ok and lines[0].startswith("FAIL min_bytes:"), lines
    ok, lines = check_pattern.check(paths, _tid(9))
    assert not ok and "no candidate" in lines[0]

    # ---- bank --dry-run (default): evaluates everything, writes nothing
    before_shas = {k: pc.sha256_file(paths.record("A-CND", _tid(k))) for k in range(N)}
    s = bank_pattern.run(paths, bank=False, shas={"tkc_sha": "t", "card_sha": "c", "catalogue_sha": "k"})
    assert s["mode"] == "dry-run" and s["candidates"] == 8
    assert (s["rewritten"], s["unchanged"], s["failed"], s["skipped"]) == (3, 1, 3, 1)
    assert s["tier2_flagged"] == 1 and s["tier1_regressions"] == 0 and s["lint_net"] == 0
    assert s["proxy_tokens_after"] < s["proxy_tokens_before"]
    assert not os.path.exists(paths.ledger)
    assert not os.path.exists(paths.sub("retry_queue.jsonl"))
    assert all(f.endswith(".tk") for f in os.listdir(paths.sub("gen")))
    assert {pc.sha256_file(paths.record("A-CND", _tid(k))) for k in range(N)} == set(before_shas.values())
    d0 = json.load(open(paths.sub("dryrun", _tid(0) + ".json")))
    assert d0["verdict"] == "rewritten" and d0["gates"]["diff"] is True and d0["gates"]["perf"] is True
    assert d0["diff"]["via"] and d0["perf"]["via"]
    assert d0["rules_fixed"] == ["mut-flag-if"] and d0["patterns_fixed"] == ["cond-bind-if"]
    d21 = json.load(open(paths.sub("dryrun", _tid(21) + ".json")))
    assert d21["verdict"] == "rewritten" and d21["gates"]["tests"] is True
    assert (d21["diff"].get("checks") or {}).get("generated", {}).get("identical") is True
    d3 = json.load(open(paths.sub("dryrun", _tid(3) + ".json")))
    assert d3["verdict"] == "failed" and d3["gates"]["tests"] is False and "diff" not in d3["gates"]
    assert json.load(open(paths.sub("dryrun", _tid(5) + ".json")))["tier2_flag"] is True
    assert json.load(open(paths.sub("dryrun", _tid(6) + ".json")))["verdict"] == "unchanged"
    d7 = json.load(open(paths.sub("dryrun", _tid(7) + ".json")))
    assert d7["verdict"] == "skipped" and "stale" in d7["reason"]

    # ---- bank --bank
    s = bank_pattern.run(paths, bank=True, shas={"tkc_sha": "tkcsha", "card_sha": "cardsha",
                                                  "catalogue_sha": "catsha"})
    assert (s["rewritten"], s["unchanged"], s["failed"], s["skipped"]) == (3, 1, 3, 1)
    assert s["banked_rate"] == round(4 / 7, 4) and s["wave_gate_95pct"] is False
    gen = sorted(os.listdir(paths.sub("gen")))
    assert len(gen) == 8 and all(f.endswith(".tk.done") for f in gen)
    led = _ledger(paths)
    assert len(led) == 8 and all(e["wave"] == "agent" for e in led)
    by = {e["task_id"]: e for e in led}
    assert by[_tid(0)]["status"] == "rewritten" and by[_tid(0)]["new_sha256"]
    assert by[_tid(0)]["prev_sha256"] == before_shas[0]
    assert by[_tid(6)]["status"] == "unchanged" and by[_tid(6)]["new_sha256"] is None
    assert by[_tid(2)]["status"] == "failed" and by[_tid(2)]["attempts"] == 1
    assert by[_tid(7)]["status"] == "skipped"
    for k in (0, 5, 21):
        rec = json.load(open(paths.record("A-CND", _tid(k))))
        # assemble() re-terminates single_function modules with "\n" (real records came through it too)
        assert rec["tk_source"].rstrip("\n") == _src(k, "canonical").rstrip("\n")
        r131 = rec["regen"]["rewrite131"]
        assert r131["wave"] == "agent" and r131["story"] == "131.15"
        assert r131["card_sha"] == m["card_sha"] and r131["catalogue_sha"] == m["catalogue_sha"]
        assert r131["tkc_sha"] == "tkcsha" and r131["prev_sha256"] == before_shas[k]
        assert r131["patterns_fixed"] == ["cond-bind-if"] and r131["lint_violations"] == 0
        assert r131["proxy_tokens_after"] < r131["proxy_tokens_before"]
        assert r131["perf"]["verdict"] == "pass" and r131["diff_check"] is True
        assert rec["regen"]["min_bytes"] == r131["min_bytes_after"]
        assert rec["regen"]["source_sha256"] == hashlib.sha256(rec["tk_source"].encode()).hexdigest()
        assert rec["regen"]["lint_pattern_violations"] == []
        assert open(os.path.join(paths.replaced, _tid(k) + ".tk")).read() == _src(k, "flag")
        assert pc.sha256_file(paths.record("A-CND", _tid(k))) == by[_tid(k)]["new_sha256"]
    assert json.load(open(paths.record("A-CND", _tid(5))))["regen"]["rewrite131"]["tier2_pending"] is True
    assert "rewrite131" not in json.load(open(paths.record("A-CND", _tid(6))))["regen"]
    assert pc.sha256_file(paths.record("A-CND", _tid(2))) == before_shas[2]
    man = manifest_tool.Manifest(paths.manifest)
    assert len(man) == N
    for k in (0, 5, 21):
        row = man.get(_tid(k))
        assert row["sha256"] == pc.sha256_file(paths.record("A-CND", _tid(k)))
        assert row["shard"] is None and row["id"] == "P3-" + _tid(k)
    assert man.get(_tid(2))["sha256"] == before_shas[2]
    chk = manifest_tool.check(corpus["dir"], paths.manifest)
    assert chk["counts"]["stale"] == 1 and chk["samples"]["stale"] == [_tid(7)]   # only the record WE rewrote outside the bank
    retry = [json.loads(l) for l in open(paths.sub("retry_queue.jsonl"))]
    assert {r["task_id"] for r in retry} == {_tid(2), _tid(3), _tid(4)}
    assert all(r["exhausted"] is False for r in retry)
    tier2 = [json.loads(l) for l in open(paths.sub("tier2_queue.jsonl"))]
    assert [t["task_id"] for t in tier2] == [_tid(5)]
    assert os.path.exists(s["report"])

    # ---- re-prep: done ids skipped, rejects re-issued as attempt 2, stale re-issued
    m2 = pattern_prep.prepare(paths, corpus["bucket"])
    assert m2["skipped"]["done"] == 4 and m2["prepared"] == N - 4 and m2["batches"] == 2
    assert m2["stale_batches_removed"] == 0 and m2["unbanked_candidates"] == 0
    p2 = open(paths.sub("prompts", _tid(2) + ".txt")).read()
    assert "RETRY (attempt 2)" in p2 and "pattern: mut-flag-if" in p2
    assert json.load(open(paths.sub("meta", _tid(2) + ".json")))["attempts"] == 2
    assert json.load(open(paths.sub("meta", _tid(7) + ".json")))["attempts"] == 1
    assert not os.path.exists(paths.sub("prompts", _tid(0) + ".txt")) or \
        json.load(open(paths.sub("meta", _tid(0) + ".json")))["attempts"] == 1   # untouched, not re-issued
    ids2 = json.load(open(paths.sub("batches", "batch_000.json")))["task_ids"] + \
        json.load(open(paths.sub("batches", "batch_001.json")))["task_ids"]
    assert _tid(0) not in ids2 and _tid(6) not in ids2 and _tid(2) in ids2 and _tid(7) in ids2

    # ---- second failure exhausts the retry; a third prep skips it
    _write_candidate(paths, 2, "flag")
    s3 = bank_pattern.run(paths, bank=True, only=[_tid(2)])
    assert s3["failed"] == 1 and s3["exhausted"] == 1
    m3 = pattern_prep.prepare(paths, corpus["bucket"])
    assert m3["skipped"]["exhausted"] == 1 and m3["skipped"]["done"] == 4
    assert m3["prepared"] == N - 5
    # a stale higher-numbered batch file from an earlier, larger prep is removed
    with open(paths.sub("batches", "batch_007.json"), "w") as f:
        json.dump({"batch": 7, "task_ids": []}, f)
    m4 = pattern_prep.prepare(paths, corpus["bucket"], limit=3)
    assert m4["batches"] == 1 and m4["stale_batches_removed"] >= 1
    assert not os.path.exists(paths.sub("batches", "batch_007.json"))


def test_prep_max_batches_defers(corpus):
    paths = pc.Paths(corpus=corpus["dir"], workdir=str(corpus["root"] / "work" / "small"),
                     ledger=str(corpus["root"] / "empty_ledger.jsonl"))
    m = pattern_prep.prepare(paths, corpus["bucket"], max_batches=1)
    assert m["batches"] == 1 and m["prepared"] == 20 and m["deferred"] == N - 20
    assert len(m["deferred_task_ids"]) == N - 20
    assert not os.path.exists(paths.sub("batches", "batch_001.json"))
