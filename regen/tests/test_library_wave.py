"""131.70 — library_prep / bank_library end to end over a synthetic library
tree in tmp_path (prep -> worker candidate -> dry-run -> bank -> idempotency).

Owned by 131.70. Deliberately a separate file from the 131.69 agent's tests
(test_pattern_*.py, test_diff_*.py) — nothing here touches pattern_common,
bank_pattern or diff_check.

The programs are real toke 2.8.0 stdin_programs built and executed by the real
tkc, so the manifest tests gate is genuinely exercised. The decisive case is
`test_output_may_differ_from_original_where_the_manifest_is_silent`: the
candidate prints something different from the original on an input the
manifest does not cover, and it MUST still bank. A byte-identity-against-the-
original gate (the 131.69 defect this wave must not inherit) would reject it.
"""
import json, os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import library_common as lc       # noqa: E402
import library_prep               # noqa: E402
import bank_library               # noqa: E402

pytestmark = pytest.mark.skipif(not os.path.exists(lc.TKC), reason="tkc not built")

CAT = "L-TST"
LIB_CAT = "testing"

# original: a single-use-let the sweep flags, and an UNCOVERED branch printing
# "empty" — the manifest says nothing about blank input.
ORIG = ('m=echo;i=io:std.io;i=s:std.str;f=main():$i64{let line=io.readln();'
        'let t=s.trim(line);if(s.len(t)==0){io.println("empty");<0};'
        'io.println(t);<0}\n')
# candidate: the let inlined (smaller, lint-clean) and the uncovered branch
# printing something else. Every manifest case still matches exactly.
GOOD = ('m=echo;i=io:std.io;i=s:std.str;f=main():$i64{let t=s.trim(io.readln());'
        'if(s.len(t)==0){io.println("none");<0};io.println(t);<0}\n')
# candidate that breaks a manifest case
WRONG = ('m=echo;i=io:std.io;i=s:std.str;f=main():$i64{let t=s.trim(io.readln());'
         'if(s.len(t)==0){io.println("none");<0};io.println(s.concat(t;"!"));<0}\n')
# a markdown-handling program: "```" lives inside a string literal
FENCED = ('m=fence;i=io:std.io;i=s:std.str;f=main():$i64{let t=s.trim(io.readln());'
          'io.println(s.concat("```";t));<0}\n')


def _spec(tid, lib_id, cases, desc="Echo one trimmed stdin line."):
    return {"task_id": tid, "category": CAT, "task_type": "stdin_program",
            "build_flags": ["--allow-all"], "description": desc,
            "test_cases": cases, "difficulty": 1, "fixtures": [],
            "library": {"id": lib_id, "category": LIB_CAT, "manifest": "testing.json",
                        "title": "Echo", "difficulty": 1}}


def _row(tid, source="library", bucket="AGENT", nul=False):
    return {"task_id": tid, "category": CAT, "task_type": "stdin_program",
            "source": source, "bucket": bucket, "nul_bytes": nul,
            "violations": [{"rule": "single-use-let", "severity": "hint",
                            "pattern_id": "fn-chain-vs-let"}],
            "est_saving_tokens": 2}


ECHO_CASES = [{"input": "hi\n", "expected_output": "hi"},
              {"input": "there\n", "expected_output": "there"}]


@pytest.fixture
def world(tmp_path):
    """A synthetic corpus + library tree. Returns (paths, specs)."""
    corpus = tmp_path / "corpus"
    lib = tmp_path / "lib"
    (corpus / "shards").mkdir(parents=True)
    (corpus / "audit").mkdir(parents=True)
    specs = {
        "L-TST-001": _spec("L-TST-001", "TST-001", ECHO_CASES),
        "L-TST-002": _spec("L-TST-002", "TST-002", ECHO_CASES),
        "L-TST-003": _spec("L-TST-003", "TST-003",
                           [{"input": "x\n", "expected_output": "```x"}]),
        # a regen row that must NOT be swept in
        "A-CND-900": _spec("A-CND-900", "CND-900", ECHO_CASES),
        # a 127.64 exclusion
        "L-GAZ-117": _spec("L-GAZ-117", "GAZ-117", ECHO_CASES),
    }
    sources = {"L-TST-001": ORIG, "L-TST-002": ORIG, "L-TST-003": FENCED,
               "A-CND-900": ORIG, "L-GAZ-117": ORIG}
    with open(corpus / "shards" / "library_131.jsonl", "w") as f:
        for tid, s in specs.items():
            lid = s["library"]["id"]
            d = lib / "results" / "solutions" / LIB_CAT / lid
            d.mkdir(parents=True)
            (d / "solution.tk").write_text(sources[tid])
            s["source_program"] = str(d / "solution.tk")
            f.write(json.dumps(s) + "\n")
    with open(corpus / "audit" / "pattern_sweep.jsonl", "w") as f:
        for tid in ("L-TST-001", "L-TST-002", "L-TST-003", "L-GAZ-117"):
            f.write(json.dumps(_row(tid)) + "\n")
        f.write(json.dumps(_row("A-CND-900", source="regen")) + "\n")
        f.write(json.dumps(_row("L-TST-099", bucket="LEAVE")) + "\n")
    paths = lc.LibraryPaths(corpus=str(corpus), lib_root=str(lib),
                            workdir=str(tmp_path / "work"),
                            ledger=str(tmp_path / "ledger.jsonl"))
    return paths, specs


def _cand(paths, tid, specs, text):
    lid = specs[tid]["library"]["id"]
    p = paths.candidate(LIB_CAT, lid)
    with open(p, "w") as f:
        f.write(text)
    return p


# ------------------------------------------------------------ input set ---
def test_sweep_filters_on_source_not_just_bucket(world):
    paths, _ = world
    rows = lc.load_sweep(paths.sweep)
    assert set(rows) == {"L-TST-001", "L-TST-002", "L-TST-003", "L-GAZ-117"}
    assert "A-CND-900" not in rows, "source==regen rows belong to the corpus wave"
    assert "L-TST-099" not in rows, "non-AGENT buckets are out of scope"


def test_127_64_programs_are_excluded_not_worked_around(world):
    paths, _ = world
    rows, _specs, excluded = lc.wave_rows(paths.sweep, paths.shard)
    assert "L-GAZ-117" not in rows
    assert "L-GAZ-117" in excluded and "127.64" in excluded["L-GAZ-117"]


# ------------------------------------------------------------------ prep ---
def test_prep_writes_the_wave(world):
    paths, specs = world
    m = library_prep.prepare(paths, embed_card=False)
    assert m["prepared"] == 3 and m["batches"] == 1
    assert m["skipped"] == {"done": 0, "exhausted": 0, "no_spec": 0, "no_solution": 0}
    for tid in ("L-TST-001", "L-TST-002", "L-TST-003"):
        assert os.path.exists(paths.sub("meta", tid + ".json"))
        assert os.path.exists(paths.sub("specs", tid + ".json"))
    prompt = open(paths.sub("prompts", "L-TST-001.txt")).read()
    assert "THE ORACLE IS THE MANIFEST" in prompt
    assert '-> stdout="hi"' in prompt, "the manifest lock must be in the prompt"
    assert "solution.131.tk" in prompt and "never edit solution.tk" in prompt
    meta = json.load(open(paths.sub("meta", "L-TST-001.json")))
    assert meta["before"]["min_bytes"] > 0 and meta["before"]["proxy_tokens"] > 0
    assert meta["card_sha"] and meta["catalogue_sha"] and meta["tkc_bin_sha"]


def test_prep_is_idempotent_after_banking(world):
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    _cand(paths, "L-TST-001", specs, GOOD)
    bank_library.run(paths, bank=True, run_perf=False, only=["L-TST-001"])
    m = library_prep.prepare(paths, embed_card=False)
    assert m["skipped"]["done"] == 1
    assert "L-TST-001" not in [t for b in range(m["batches"])
                               for t in json.load(open(paths.sub("batches", f"batch_{b:03d}.json")))["task_ids"]]


# ------------------------------------------------------- the 131.69 rule ---
def test_output_may_differ_from_original_where_the_manifest_is_silent(world):
    """The decisive property. ORIG prints "empty" on blank input, GOOD prints
    "none"; the manifest covers neither. Both satisfy every manifest case, so
    GOOD must BANK. A byte-identity-against-the-original gate would reject it —
    that gate is what forced the corpus wave's withdrawal (131.69)."""
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    _cand(paths, "L-TST-001", specs, GOOD)
    s = bank_library.run(paths, bank=False, run_perf=False, only=["L-TST-001"])
    ev = json.load(open(paths.sub("dryrun", "L-TST-001.json")))
    assert ev["verdict"] == "rewritten", ev["reason"]
    assert ev["gates"]["tests"] is True
    assert ev["source_changed"] is True
    assert ev["tests"]["oracle"] == "manifest:expected_output"
    assert s["would_bank_rate"] == 1.0


def test_tier1_perf_is_measured_from_timings_not_output(world):
    """The perf gate builds both sides and times them; stdout goes to DEVNULL.
    An equivalent rewrite must not be rejected, and a flagged first pass is
    re-measured before it can reject (library programs run in single-digit
    milliseconds, so scheduler noise clears the 5 ms threshold easily)."""
    paths, specs = world
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        perf = lc.tier1_perf(specs["L-TST-001"], ORIG, GOOD, td)
    assert perf["verdict"] in ("pass", "n/a"), perf
    assert perf["via"].startswith("library-median-of")
    if perf["verdict"] == "pass":
        assert perf["wall_ms"]["orig"] > 0 and perf["wall_ms"]["cand"] > 0


def test_wrong_output_against_the_manifest_is_rejected(world):
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    _cand(paths, "L-TST-001", specs, WRONG)
    bank_library.run(paths, bank=False, run_perf=False, only=["L-TST-001"])
    ev = json.load(open(paths.sub("dryrun", "L-TST-001.json")))
    assert ev["verdict"] == "failed"
    assert ev["failed_gates"] == ["tests"], ev["gates"]


def test_no_original_output_comparison_anywhere_in_the_path():
    """The tooling contract, checked from the source. If this ever fails,
    someone has reintroduced the 131.69 defect."""
    banned = ("diff_check", "bank_pattern", "pattern_common", "pattern_autofix")
    for mod in (lc, library_prep, bank_library):
        src = open(mod.__file__).read()
        for name in banned:
            assert f"import {name}" not in src, f"{mod.__name__} imports {name}"
            assert f"from {name}" not in src, f"{mod.__name__} imports from {name}"
        assert not hasattr(mod, "diff_check")


# ------------------------------------------------------------- carve-out ---
def test_nul_programs_get_na_size_gates_not_failures():
    """131.32: `tkc --min` truncates at the NUL, so the before baseline is
    corrupt (DEV-115: 138 bytes for a 2,597-byte program). Both <=-before
    gates must come out n/a — neither silently passed nor failed on a
    meaningless comparison."""
    assert lc.is_nul_carve_out("L-DEV-115")
    assert lc.is_nul_carve_out("L-XXX-001", {"nul_bytes": True})
    assert not lc.is_nul_carve_out("L-TST-001", {"nul_bytes": False})
    before = {"min_bytes": 138, "proxy_tokens": 38}
    res = {"gates": {}, "reason": None, "min_bytes": 1929, "proxy_tokens": 395}
    lc.size_gates(res, before, nul_carve_out=True)
    assert res["gates"]["min_bytes"] is None and res["gates"]["proxy_tokens"] is None
    assert res["reason"] is None and "131.32" in res["size_gate_note"]
    # the same numbers WITHOUT the carve-out are a hard fail — i.e. the
    # carve-out is load-bearing, not decoration
    res2 = {"gates": {}, "reason": None, "min_bytes": 1929, "proxy_tokens": 395}
    lc.size_gates(res2, before, nul_carve_out=False)
    assert res2["gates"]["min_bytes"] is False


def test_size_gates_reject_growth():
    res = {"gates": {}, "reason": None, "min_bytes": 500, "proxy_tokens": 90}
    lc.size_gates(res, {"min_bytes": 400, "proxy_tokens": 84})
    assert res["gates"]["min_bytes"] is False
    assert "min_bytes: 500 > 400" in res["reason"]


def test_lint_net_gate_requires_zero():
    res = {"gates": {}, "reason": None, "lint_warnings": 2}
    lc.lint_net_gate(res, {"lint_warnings": 5})
    assert res["gates"]["lint_net"] is False and res["lint_delta"] == -3
    res = {"gates": {}, "reason": None, "lint_warnings": 0}
    lc.lint_net_gate(res, {"lint_warnings": 5})
    assert res["gates"]["lint_net"] is True


# ---------------------------------------------------------- normalisation ---
def test_normalise_source_keeps_fences_inside_string_literals():
    """assemble.sanitize deletes every "```" anywhere, which corrupts the
    markdown-handling library programs (L-MED-036, L-MED-129, L-MSG-053)."""
    assert lc.normalise_source(FENCED) == FENCED
    assert '"```"' in lc.normalise_source(FENCED)


def test_normalise_source_strips_a_wrapping_fence_and_prose():
    wrapped = "Here you go:\n```toke\n" + ORIG.strip() + "\n```\n"
    assert lc.normalise_source(wrapped) == ORIG


def test_fenced_program_survives_the_gates(world):
    """The regression that the sanitize bug caused: a program whose literals
    contain "```" must still pass its manifest case."""
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    _cand(paths, "L-TST-003", specs, FENCED)
    bank_library.run(paths, bank=False, run_perf=False, only=["L-TST-003"])
    ev = json.load(open(paths.sub("dryrun", "L-TST-003.json")))
    assert ev["gates"]["tests"] is True, ev["reason"]
    assert ev["verdict"] == "unchanged"


# ------------------------------------------------------------------ bank ---
def test_dry_run_writes_nothing_to_the_library_tree(world):
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    cand = _cand(paths, "L-TST-001", specs, GOOD)
    sol = paths.original(LIB_CAT, "TST-001")
    before = open(sol).read()
    bank_library.run(paths, bank=False, run_perf=False, only=["L-TST-001"])
    assert open(sol).read() == before
    assert os.path.exists(cand) and not os.path.exists(cand + ".done")
    assert not os.path.exists(paths.archive(LIB_CAT, "TST-001"))
    assert not os.path.exists(paths.ledger)


def test_bank_archives_promotes_and_records_provenance(world):
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    cand = _cand(paths, "L-TST-001", specs, GOOD)
    sol = paths.original(LIB_CAT, "TST-001")
    orig = open(sol).read()
    shas = {"card_sha": "cardsha00", "catalogue_sha": "catsha00",
            "tkc_sha": lc.short(lc.tkc_bin_sha(paths.tkc))}
    s = bank_library.run(paths, bank=True, run_perf=False, only=["L-TST-001"], shas=shas)
    assert s["rewritten"] == 1 and s["banked_rate"] == 1.0
    # archive-not-delete, candidate promoted, candidate retired
    assert open(paths.archive(LIB_CAT, "TST-001")).read() == orig
    assert open(sol).read().strip() == GOOD.strip()
    assert not os.path.exists(cand) and os.path.exists(cand + ".done")
    # provenance in the rewrite131 shape
    prov = json.load(open(paths.sub("provenance", "L-TST-001.json")))["rewrite131"]
    for k in ("card_sha", "catalogue_sha", "tkc_sha", "tkc_bin_sha", "prev_sha256",
              "min_bytes_before", "min_bytes_after", "proxy_tokens_before",
              "proxy_tokens_after", "perf", "attempts", "wave", "story"):
        assert prov.get(k) is not None, k
    assert prov["oracle"] == "manifest:expected_output"
    assert prov["original_output_compared"] is False
    assert prov["min_bytes_after"] < prov["min_bytes_before"]
    entry = [json.loads(l) for l in open(paths.ledger)][-1]
    assert entry["status"] == "rewritten" and entry["wave"] == lc.WAVE
    assert entry["prev_sha256"] == lc.sha256_text(orig)


def test_bank_archive_is_first_touch_only(world):
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    _cand(paths, "L-TST-001", specs, GOOD)
    orig = open(paths.original(LIB_CAT, "TST-001")).read()
    bank_library.run(paths, bank=True, run_perf=False, only=["L-TST-001"])
    _cand(paths, "L-TST-001", specs, GOOD.replace('"none"', '"nil"'))
    bank_library.run(paths, bank=True, run_perf=False, only=["L-TST-001"])
    assert open(paths.archive(LIB_CAT, "TST-001")).read() == orig, \
        "the freeze-129 original must survive a second bank"


def test_failed_candidate_goes_to_the_retry_queue(world):
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    _cand(paths, "L-TST-002", specs, WRONG)
    bank_library.run(paths, bank=True, run_perf=False, only=["L-TST-002"])
    q = [json.loads(l) for l in open(paths.sub("retry_queue.jsonl"))]
    assert q[-1]["task_id"] == "L-TST-002" and q[-1]["exhausted"] is False
    assert open(paths.original(LIB_CAT, "TST-002")).read() == ORIG, \
        "a rejected candidate must never touch solution.tk"


def test_stale_original_is_skipped_not_banked(world):
    paths, specs = world
    library_prep.prepare(paths, embed_card=False)
    _cand(paths, "L-TST-001", specs, GOOD)
    with open(paths.original(LIB_CAT, "TST-001"), "a") as f:
        f.write("\n")                       # the tree moved under the prompt
    bank_library.run(paths, bank=False, run_perf=False, only=["L-TST-001"])
    ev = json.load(open(paths.sub("dryrun", "L-TST-001.json")))
    assert ev["verdict"] == "skipped" and "stale" in ev["reason"]


def test_baseline_mode_scores_the_originals(world):
    paths, _ = world
    library_prep.prepare(paths, embed_card=False)
    s = bank_library.run(paths, bank=False, baseline=True, run_perf=False)
    assert s["mode"] == "baseline" and s["candidates"] == 3
    assert s["original_output_compared"] is False
    ev = json.load(open(paths.sub("baseline", "L-TST-001.json")))
    assert ev["verdict"] == "unchanged"     # ORIG already clears every gate
