"""131.14 — pattern_autofix: harness-prefix restore, id selection, Tier-1
perf thresholds (mocked runs), bank_one on a temp corpus (archive, provenance,
ledger, manifest stamp, stale-sha skip), summary shape, and one real tiny
end-to-end record through tkc --lint --fix (skipped when tkc is absent)."""
import argparse, hashlib, json, os, sys, tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import diff_check as dc          # noqa: E402
import manifest_tool             # noqa: E402
import pattern_autofix as pa     # noqa: E402
import tkc_pin                   # noqa: E402  (131.39)

HAVE_TKC = os.path.exists(pa.TKC)


# ---------------------------------------------------------------- prefix ---
def test_harness_prefix_spec_aware():
    spec = {"domain_context_v03": "f=len(a:@str):i64"}
    o = "m=harness;\ni=io:std.io;\nf=len(a:@str):i64{<0};\nf=x(a:i64):i64{<a};\n"
    assert pa.harness_prefix_len(o, spec) == len("m=harness;\ni=io:std.io;\nf=len(a:@str):i64{<0};\n")
    assert pa.harness_prefix_len(o, None) == len(o)          # stub-shaped one-liner target counted without a spec
    assert pa.harness_prefix_len("m=t;\nf=main():i64{<0};\n", spec) == 0


def test_restore_prefix_keeps_harness_and_body_fix():
    spec = {"domain_context_v03": "f=len(a:@str):i64"}
    o = "m=harness;\ni=io:std.io;\ni=s:std.str;\nf=len(a:@str):i64{<0};\nf=x(a:i64):i64{<a};\n"
    fixed = "m=harness;\nf=len(a:@str):i64{<0};\nf=x(a:i64):i64{<a+0};\n"       # imports dropped + body changed
    cand, n = pa.restore_prefix(o, fixed, spec)
    assert cand == "m=harness;\ni=io:std.io;\ni=s:std.str;\nf=len(a:@str):i64{<0};\nf=x(a:i64):i64{<a+0};\n"
    prefix_only = "m=harness;\nf=len(a:@str):i64{<0};\nf=x(a:i64):i64{<a};\n"
    assert pa.restore_prefix(o, prefix_only, spec)[0] == o
    assert pa.restore_prefix("m=t;\nf=main():i64{<0};\n", "m=t;\nf=main():i64{<1};\n", spec) == \
        ("m=t;\nf=main():i64{<1};\n", 0)


def test_gate_spec_single_function_verbatim():
    spec = {"task_type": "single_function", "test_cases": [{"inputs": [1], "expected": 1}]}
    g = pa.gate_spec(spec)
    assert g["task_type"] == "full_program" and g["test_cases"] == []
    assert spec["test_cases"]                                  # original untouched
    fp = {"task_type": "full_program", "test_cases": [1]}
    assert pa.gate_spec(fp) is fp


# ------------------------------------------------------------- selection ---
def test_bucket_ids_and_sweep_examples(tmp_path):
    b = tmp_path / "bucket.jsonl"
    b.write_text('{"task_id": "A-1", "bucket": "AUTO"}\n{"task_id": "A-2", "bucket": "AGENT"}\n')
    assert pa.bucket_ids(str(b)) == ["A-1", "A-2"]
    assert pa.bucket_ids(str(b), "auto") == ["A-1"]
    p = tmp_path / "ids.txt"
    p.write_text("# comment\nA-3 extra\n\nA-4\n")
    assert pa.bucket_ids(str(p)) == ["A-3", "A-4"]
    ex = tmp_path / "examples.txt"
    ex.write_text("=== rule\n  /tmp/x/corpus/D-TST-0016v68.tk:5 fix=no\n  /tmp/x/corpus/D-TST-0016v68.tk:9 fix=no\n"
                  "  /tmp/x/corpus/A-ARR-0001v2.tk:1 fix=yes\n")
    assert pa.sweep_example_ids(str(ex)) == ["D-TST-0016v68", "A-ARR-0001v2"]
    args = argparse.Namespace(bucket=str(b), bucket_name="AUTO", examples=str(ex), task_id=["A-1", "Z-9"],
                              sample=2, seed=131)
    ids = pa.select_task_ids(args, ["M-1", "M-2", "M-3", "M-4"])
    assert ids[:4] == ["A-1", "D-TST-0016v68", "A-ARR-0001v2", "Z-9"] and len(ids) == 6
    assert ids == pa.select_task_ids(args, ["M-1", "M-2", "M-3", "M-4"])   # seeded


# ------------------------------------------------------------------ perf ---
def _prog(tag):
    p = dc.Program(tag)
    p.mode, p.test_bin = "main", tag + ".bin"
    return p


def _perf(monkeypatch, wall, rss):
    def fake(prog, wd, timeout=dc.RUN_TIMEOUT):
        return [("spec cases", {"stdout": b"", "stderr": b"", "exit": 0, "timed_out": False,
                                "wall_ms": wall[prog.tag], "rss_kb": rss[prog.tag]})]
    monkeypatch.setattr(dc, "run_spec_cases", fake)


def test_perf_compare_flags_only_ratio_and_delta(monkeypatch):
    _perf(monkeypatch, {"orig": 10.0, "cand": 30.0}, {"orig": 1000.0, "cand": 1000.0})
    r = pa.perf_compare(_prog("orig"), _prog("cand"), "/tmp")
    assert r["flag"] and r["verdict"] == "fail" and r["ratio"] == 3.0 and r["runs"] == pa.PERF_RUNS
    _perf(monkeypatch, {"orig": 1.0, "cand": 3.0}, {"orig": 1000.0, "cand": 1000.0})      # ratio 3 but delta 2 ms
    assert not pa.perf_compare(_prog("orig"), _prog("cand"), "/tmp")["flag"]
    _perf(monkeypatch, {"orig": 100.0, "cand": 140.0}, {"orig": 1000.0, "cand": 1000.0})  # delta 40 ms but ratio 1.4
    assert not pa.perf_compare(_prog("orig"), _prog("cand"), "/tmp")["flag"]
    _perf(monkeypatch, {"orig": 1.0, "cand": 1.0}, {"orig": 1000.0, "cand": 2100.0})      # rss x2.1, +1.07 MB
    r = pa.perf_compare(_prog("orig"), _prog("cand"), "/tmp")
    assert r["flag"] and r["rss_ratio"] == 2.1
    _perf(monkeypatch, {"orig": 1.0, "cand": 1.0}, {"orig": 500.0, "cand": 1400.0})       # rss x2.8 but +0.9 MB
    assert not pa.perf_compare(_prog("orig"), _prog("cand"), "/tmp")["flag"]


def test_perf_compare_not_runnable(monkeypatch):
    p = dc.Program("orig")
    assert pa.perf_compare(p, _prog("cand"), "/tmp") is None
    monkeypatch.setattr(dc, "run_spec_cases", lambda prog, wd, timeout=dc.RUN_TIMEOUT: [])
    assert pa.perf_compare(_prog("orig"), _prog("cand"), "/tmp") is None


# ------------------------------------------------------------------ bank ---
def _record(tid, cat, src):
    return {"id": "P3-" + tid, "version": 2, "task_id": tid, "tk_source": src, "tk_tokens": 50,
            "judge": {"accepted": True, "score": 0.9},
            "regen": {"category": cat, "task_type": "full_program", "source_sha256": hashlib.sha256(src.encode()).hexdigest(),
                      "min_bytes": 100, "proxy_tokens": 50, "max_depth": 1, "repaired": "129.4-5"}}


def test_bank_one_archives_stamps_ledger_and_skips_stale(tmp_path):
    corpus = str(tmp_path / "corpus")
    rec_path = os.path.join(corpus, "D-TST", "D-TST-0001v1.json")
    os.makedirs(os.path.dirname(rec_path))
    rec = _record("D-TST-0001v1", "D-TST", "m=t;\ni=s:std.str;\nf=main():i64{<0};\n")
    with open(rec_path, "w") as f:
        json.dump(rec, f)
    prev_sha = pa.sha256_file(rec_path)
    mpath = os.path.join(corpus, "MANIFEST.jsonl")
    manifest_tool.Manifest(mpath).stamp("D-TST-0001v1", rec_path)
    cand_path = str(tmp_path / "cand.tk")
    with open(cand_path, "w") as f:
        f.write("m=t;\nf=main():i64{<0};\n")
    res = {"task_id": "D-TST-0001v1", "category": "D-TST", "decision": "bank", "reason": "fixed:unused-import",
           "prev_sha256": prev_sha, "candidate_path": cand_path,
           "fix": {"rules_with_fix": ["unused-import", "single-use-let"]},
           "min_bytes": {"before": 100, "after": 90}, "proxy_tokens": {"before": 50, "after": 45},
           "max_depth": {"before": 1, "after": 1}, "idiom": {"before": 0.9, "after": 1.0},
           "lint_exempt": [], "over_budget": False,
           "perf": {"ratio": 1.02, "verdict": "pass"}, "diff": {"verdict": "identical"}}
    ledger = os.path.join(corpus, "ledger", "rewrite_131.jsonl")
    entry = pa.bank_one(res, corpus, manifest_tool.Manifest(mpath), ledger, ("tkcsha", "catsha", "gitsha"),
                        now="2026-09-19T00:00:00Z")
    assert entry["status"] == "rewritten" and entry["attempts"] == 1 and entry["wave"] == "auto"
    assert entry["prev_sha256"] == prev_sha and entry["new_sha256"] == pa.sha256_file(rec_path)
    archived = os.path.join(corpus, "audit", "replaced", "131", "D-TST-0001v1.tk")
    assert open(archived).read() == rec["tk_source"]
    new = json.load(open(rec_path))
    assert new["tk_source"] == "m=t;\nf=main():i64{<0};\n" and new["version"] == 2
    rw = new["regen"]["rewrite131"]
    assert rw["wave"] == "auto" and rw["story"] == "131.14" and rw["prev_sha256"] == prev_sha
    assert rw["tkc_sha"] == "tkcsha" and rw["catalogue_sha"] == "catsha" and rw["toke_git"] == "gitsha"
    assert rw["tkc_bin_sha"] == tkc_pin.bin_sha(pa.TKC)     # 131.39: 3-tuple shas -> the live binary's sha
    assert rw["rules_fixed"] == ["unused-import", "single-use-let"]
    assert "fn-chain-vs-let" in rw["patterns_fixed"] or "single-use-let" in rw["patterns_fixed"]
    assert rw["proxy_tokens_before"] == 50 and rw["proxy_tokens_after"] == 45
    assert rw["perf"] == {"tier": "1", "ratio": 1.02, "verdict": "pass"} and rw["diff_check"] == "identical"
    assert new["regen"]["repaired"] == "129.4-5"                      # 129 history kept
    assert new["regen"]["min_bytes"] == 90 and new["tk_tokens"] == 45 and new["judge"]["score"] == 1.0
    row = manifest_tool.Manifest(mpath).get("D-TST-0001v1")
    assert row["sha256"] == entry["new_sha256"]                        # 131.35 re-stamped
    # second bank with the stale prev_sha: skipped, archive untouched, attempts 2, wave-scoped
    with open(ledger, "a") as f:
        f.write(json.dumps({"task_id": "D-TST-0001v1", "wave": "agent", "attempts": 7}) + "\n")
    entry2 = pa.bank_one(res, corpus, manifest_tool.Manifest(mpath), ledger, ("tkcsha", "catsha", "gitsha"))
    assert entry2["status"] == "skipped" and "stale" in entry2["reason"] and entry2["attempts"] == 2
    assert open(archived).read() == rec["tk_source"]
    lines = [json.loads(l) for l in open(ledger)]
    assert [l["status"] for l in lines if l.get("wave") == "auto"] == ["rewritten", "skipped"]


# --------------------------------------------------------------- summary ---
def test_summarise_counts():
    results = [
        {"task_id": "a", "decision": "bank", "reason": "fixed:unused-import", "changed": True, "task_type": "full_program",
         "diff": {"verdict": "identical", "checks": {"ref": {"status": "skipped"}}}, "perf": {"flag": False},
         "fix": {"rules_with_fix": ["unused-import"]}, "elapsed_s": 1.0},
        {"task_id": "b", "decision": "reject", "reason": "diverged:spec cases differ", "changed": True,
         "task_type": "single_function", "diff": {"verdict": "diverged", "checks": {"ref": {"status": "n/a"}}},
         "perf": None, "fix": {"rules_with_fix": ["single-use-let"]}, "elapsed_s": 2.0},
        {"task_id": "c", "decision": "noop", "reason": "harness_prefix_only", "changed": False,
         "task_type": "single_function", "elapsed_s": 0.5},
        {"task_id": "d", "decision": "reject", "reason": "preexisting:lint:unused-let", "changed": True,
         "task_type": "full_program", "diff": None, "perf": {"flag": True, "ratio": 2.0}, "fix": {"rules_with_fix": []},
         "elapsed_s": 0.5},
    ]
    s = pa.summarise(results, {"shas": ("t", "c", "g"), "perf_runs": 3, "n_generated": 20, "unknown": ["MIG-1"]})
    assert s["decisions"] == {"bank": 1, "reject": 2, "noop": 1} and s["changed"] == 3
    assert s["reasons"] == {"bank:fixed": 1, "noop:harness_prefix_only": 1, "reject:diverged": 1, "reject:preexisting": 1}
    assert s["diff_verdicts"] == {"identical": 1, "diverged": 1, "not_run": 1}
    assert s["bank_rate_of_changed"] == round(1 / 3, 4) and s["harness_prefix_only"] == 1
    assert s["preexisting_rejects"] == 1 and s["perf_flags"] == ["d"] and s["unknown_task_ids"] == ["MIG-1"]
    assert s["fix_rules"] == {"unused-import": 1, "single-use-let": 1} and s["mode"] == "dry-run"
    assert s["by_task_type"]["single_function"] == {"reject": 1, "noop": 1}


# ------------------------------------------------------------ real tkc ---
def _run_one(tmp, tid, cat, spec, src):
    rec_path = os.path.join(tmp, tid + ".json")
    with open(rec_path, "w") as f:
        json.dump(_record(tid, cat, src), f)
    for d in ("tmp", "cand"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    opts = {"tmpdir": os.path.join(tmp, "tmp"), "cand_dir": os.path.join(tmp, "cand"), "perf_runs": 1,
            "n_generated": 5}
    return pa.autofix_one((tid, rec_path, spec, None, opts))


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_autofix_one_full_program_banks_unused_import():
    spec = {"task_id": "D-TST-0001v1", "category": "D-TST", "task_type": "full_program",
            "description": "Print the sum of one and two.", "description_v03": "Print the sum of one and two.",
            "test_cases": [{"inputs": [], "expected": 3}]}
    src = 'm=t;\ni=io:std.io;\ni=s:std.str;\nf=main():i64{\n  io.println("\\(1+2)");\n  <0\n};\n'
    res = _run_one(tempfile.mkdtemp(prefix="pa_e2e_"), "D-TST-0001v1", "D-TST", spec, src)
    assert res["decision"] == "bank", json.dumps(res, indent=1)
    assert res["diff"]["tkc_bin_sha"] == tkc_pin.bin_sha(pa.TKC)     # 131.39: diff_check stamps the binary
    assert res["reason"] == "fixed:unused-import" and res["changed"]
    assert res["min_bytes"]["after"] < res["min_bytes"]["before"]
    assert res["diff"]["verdict"] == "identical" and res["diff"]["checks"]["spec_cases"]["mode"] == "main"
    assert res["perf"]["verdict"] in ("pass", "fail") and res["gates"]["tests"] is True
    assert "i=s:std.str;" not in open(res["candidate_path"]).read()
    assert res["lint_after"]["warnings"] == 0


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_autofix_one_single_function_prefix_only_is_noop():
    spec = {"task_id": "A-ARR-0001v1", "category": "A-ARR", "task_type": "single_function",
            "description": "Write a function that: Sum an array.", "description_v03": "Write a function that: Sum an array.",
            "input_types_v03": ["@i64"], "output_type_v03": "i64", "domain_context_v03": "",
            "test_cases": [{"inputs": [[1, 2]], "expected": 3}]}
    src = ("m=harness;\ni=io:std.io;\ni=s:std.str;\n"
           "f=sum(xs:@i64):i64{\n  let t=mut.0;\n  lp(let i=0;i<xs.len;i=i+1){t=t+xs.get(i)};\n  <t\n};\n")
    res = _run_one(tempfile.mkdtemp(prefix="pa_e2e_"), "A-ARR-0001v1", "A-ARR", spec, src)
    assert res["decision"] == "noop" and res["reason"] == "harness_prefix_only", json.dumps(res, indent=1)
    assert res["fix"]["harness_fixes"] == 2 and res["fix"]["rules_with_fix"] == [] and not res["changed"]


@pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")
def test_tier1_perf_shared_entrypoint():
    spec = {"task_id": "D-TST-0002v1", "category": "D-TST", "task_type": "full_program",
            "test_cases": [{"inputs": [], "expected": 3}]}
    src = 'm=t;\ni=io:std.io;\nf=main():i64{\n  io.println("\\(1+2)");\n  <0\n};\n'
    with tempfile.TemporaryDirectory() as td:
        out = pa.tier1_perf(spec, src, src, td, runs=1)
        assert out["tier"] == "1" and out["verdict"] in ("pass", "fail") and out["ratio"] is not None
        assert pa.tier1_perf({"task_type": "full_program", "test_cases": []}, "m=t;\n", "m=t;\n", td)["verdict"] == "n/a"
        assert os.listdir(td) == []
