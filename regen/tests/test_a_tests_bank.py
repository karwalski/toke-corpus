"""131.47 — a_tests bank tooling: --workdir / --provenance / archive-before-
replace / --dry-run on bank_a_tests, and verify_a_tests' 131.44 normalised
types + explicit arity rule (a packed-arity re-submission is REJECTED) plus
the driver probe under the pinned tkc (probe tests skip without tkc).

Synthetic base A-TST-0048: `f=foldsum(arr:@(u64);init:u64):u64` with the
sampler-mangled `input_types_v03: ["@(u64"]` (effective types come from the
signature -> ["@u64", "u64"]), a 129.7-style packed bank file already in the
bank dir, and a worker output in <workdir>/gen. Nothing here touches the
real corpus: every path is under tmp_path."""
import json, os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import verify_a_tests as va   # noqa: E402
import bank_a_tests as ba     # noqa: E402
import tkc_pin                # noqa: E402

HAVE_TKC = os.path.exists(tkc_pin.default_tkc())
needs_tkc = pytest.mark.skipif(not HAVE_TKC, reason="tkc not built")

BASE = "A-TST-0048"
SPEC = {"task_id": BASE + "v3", "category": "A-TST", "task_type": "single_function",
        "difficulty": 2, "input_types_v03": ["@(u64"], "output_type_v03": "u64",
        "description": "Write a function f=foldsum(arr:@(u64);init:u64):u64 that folds "
                       "the array from left to right using addition, starting with init. "
                       "Variant 3: use parameter names lhs and rhs.",
        "domain_context_v03": "", "test_cases": []}
ERR_SPEC = {"task_id": "A-TSE-0005v8", "category": "A-TSE", "task_type": "single_function",
            "difficulty": 1, "input_types_v03": ["@(u64"], "output_type_v03": "u64!LookupErr",
            "description": "Write a function f=safeget(arr:@(u64);idx:i64):u64!LookupErr that "
                           "returns the element at index idx, or NotFound error if idx is out "
                           "of bounds. Define t=$lookuperr{NotFound:$str;EmptyCollection:bool}.",
            "domain_context_v03": "", "test_cases": []}
REF = "def foldsum(arr, init):\n    acc = init\n    for v in arr:\n        acc = acc + v\n    return acc\n"
GOOD = {"base": BASE, "function": "foldsum", "python_ref": REF,
        "test_cases": [{"inputs": [[1, 2, 3], 7], "expected": 13},
                       {"inputs": [[], 7], "expected": 7},
                       {"inputs": [[1000000], 0], "expected": 1000000}]}
PACKED_REF = ("def foldsum(packed):\n    arr, init = packed\n    acc = init\n"
              "    for v in arr:\n        acc = acc + v\n    return acc\n")
PACKED = {"base": BASE, "function": "foldsum", "python_ref": PACKED_REF,
          "test_cases": [{"inputs": [[[1, 2, 3], 7]], "expected": 13},
                         {"inputs": [[[], 7]], "expected": 7},
                         {"inputs": [[[5], 0]], "expected": 5}]}
OLD_BANK = {"base": BASE, "python_ref": PACKED_REF, "test_cases": PACKED["test_cases"],
            "input_types": ["@(u64"], "output_type": "u64",
            "provenance": "129.7-agent+ref-verified"}


@pytest.fixture
def wave(tmp_path):
    wd = tmp_path / "atest_131"
    for d in ("specs", "prompts", "batches", "gen"):
        os.makedirs(wd / d)
    json.dump(SPEC, open(wd / "specs" / f"{BASE}.json", "w"))
    json.dump({"batch": 0, "base_ids": [BASE], "mode": "reauthor",
               "reasons": {BASE: "case 0: 1 inputs vs 2 types"}},
              open(wd / "batches" / "batch_000.json", "w"))
    bank = tmp_path / "audit" / "a_tests"
    os.makedirs(bank)
    json.dump(OLD_BANK, open(bank / f"{BASE}.json", "w"))
    return {"wd": str(wd), "bank": str(bank), "gen": str(wd / "gen" / f"tests_{BASE}.json"),
            "old": str(bank / f"{BASE}.json"),
            "archive": str(bank / ba.ARCHIVE_SUBDIR / f"{BASE}.json")}


def _snapshot(root):
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            out[p] = open(p, "rb").read()
    return out


# ---------------------------------------------------------------- verify ----
def test_input_types_are_normalised_from_the_signature():
    assert va.input_types(SPEC) == ["@u64", "u64"]
    assert va.input_types({"input_types_v03": ["@(u64", "u64"], "description": ""}) == ["@u64", "u64"]


def test_verify_rejects_packed_arity_resubmission():
    errs = va.verify(BASE, PACKED, SPEC)
    assert errs and all("inputs vs 2 types" in e for e in errs)
    assert "packed-arity" in errs[0]


def test_verify_accepts_correct_arity_and_reruns_the_reference():
    assert va.verify(BASE, GOOD, SPEC) == []
    wrong = json.loads(json.dumps(GOOD))
    wrong["test_cases"][0]["expected"] = 14
    errs = va.verify(BASE, wrong, SPEC)
    assert errs == ["case 0: ref([[1, 2, 3], 7]) = 13 but expected 14"]


def test_verify_type_and_unsigned_checks_use_normalised_types():
    bad = json.loads(json.dumps(GOOD))
    bad["test_cases"][0]["inputs"] = [[1, 2], "7"]
    assert any("not u64" in e for e in va.verify(BASE, bad, SPEC))
    neg = json.loads(json.dumps(GOOD))
    neg["test_cases"][1]["inputs"] = [[-1], 7]
    neg["test_cases"][1]["expected"] = 6
    assert any("negative input -1 for unsigned u64" in e for e in va.verify(BASE, neg, SPEC))
    other = json.loads(json.dumps(GOOD))
    other["base"] = "A-TST-0001"
    assert va.verify(BASE, other, SPEC)[0].startswith("file is for base")


def test_verify_err_marker_must_be_a_declared_variant():
    ref = ("def safeget(arr, idx):\n    if idx < 0 or idx >= len(arr):\n"
           "        return {'err': 'NotFound'}\n    return arr[idx]\n")
    doc = {"base": "A-TSE-0005", "python_ref": ref,
           "test_cases": [{"inputs": [[3, 9], 1], "expected": 9},
                          {"inputs": [[], 0], "expected": {"err": "NotFound"}},
                          {"inputs": [[4], 2], "expected": {"err": "NotFound"}}]}
    assert va.verify("A-TSE-0005", doc, ERR_SPEC) == []
    bogus = json.loads(json.dumps(doc))
    bogus["python_ref"] = ref.replace("NotFound", "Missing")
    bogus["test_cases"][1]["expected"] = {"err": "Missing"}
    bogus["test_cases"][2]["expected"] = {"err": "Missing"}
    errs = va.verify("A-TSE-0005", bogus, ERR_SPEC)
    assert errs and "not a variant" in errs[0] and "notfound" in errs[0]


def test_stub_module_spellings():
    assert va.tk_type("@$str:i64") == "@(str:i64)"
    assert va.tk_type("@@i64") == "@@i64"
    assert va.tk_type("$str") == "str"
    stub = va.stub_module(SPEC)
    assert "f=foldsum(p0:@u64;p1:u64):u64{<(0 as u64)};" in stub
    estub = va.stub_module(ERR_SPEC)
    assert "t=$lookuperr{$notfound:str;$emptycollection:bool};" in estub
    assert "f=safeget(p0:@u64;p1:i64):u64!$lookuperr{<$lookuperr{$notfound:\"\"}};" in estub


@needs_tkc
def test_driver_probe_under_pinned_tkc():
    import validate
    with tkc_pin.pin().install(validate) as p:
        assert validate.TKC == p.path          # the probe compiles with the pinned copy
        errs, note = va.driver_probe(SPEC, GOOD["test_cases"])
        assert errs == [] and note.startswith("probe ok")
        errs, note = va.driver_probe(SPEC, [{"inputs": [[1, -2], 7], "expected": 6}])
        assert errs and "negative input -2 for unsigned type u64" in errs[0]
        errs, note = va.driver_probe(ERR_SPEC, [{"inputs": [[3], 0], "expected": 3},
                                                {"inputs": [[], 0], "expected": {"err": "NotFound"}}])
        assert errs == [] and "u64!$lookuperr" in note
        assert va.verify(BASE, GOOD, SPEC, probe=True) == []
        assert va.driver_probe({"input_types_v03": ["@str:i64", "str"], "output_type_v03": "i64",
                                "description": ""}, [])[1].startswith("probe skipped: map")


# ------------------------------------------------------------------ bank ----
def test_bank_dry_run_writes_nothing(wave, tmp_path):
    json.dump(GOOD, open(wave["gen"], "w"))
    before = _snapshot(str(tmp_path))
    s = ba.bank(wave["wd"], wave["bank"], "131.47-agent+ref-verified", dry_run=True, probe=False)
    assert s["banked"] == 1 and s["archived"] == 1 and s["rejected"] == 0
    assert s["banked_bases"] == [BASE] and s["archived_bases"] == [BASE]
    assert _snapshot(str(tmp_path)) == before            # no bank, no archive, no .done
    assert os.path.exists(wave["gen"]) and not os.path.exists(wave["gen"] + ".done")
    assert json.load(open(wave["old"]))["provenance"] == "129.7-agent+ref-verified"


def test_bank_archives_before_replace_and_stamps_provenance(wave):
    json.dump(GOOD, open(wave["gen"], "w"))
    stamp = {"tkc_bin_sha": "deadbeef", "tkc_version": "toke 2.8.0"}
    s = ba.bank(wave["wd"], wave["bank"], "131.47-agent+ref-verified", dry_run=False,
                probe=False, tkc_stamp=stamp)
    assert s["banked"] == 1 and s["archived"] == 1 and s["rejected"] == 0
    old = json.load(open(wave["archive"]))
    assert old["provenance"] == "129.7-agent+ref-verified"
    assert old["test_cases"] == PACKED["test_cases"]      # the old file, byte-for-content
    assert old["replaced_by"]["provenance"] == "131.47-agent+ref-verified"
    assert old["replaced_by"]["reason"] == "case 0: 1 inputs vs 2 types"
    new = json.load(open(wave["old"]))
    assert new["provenance"] == "131.47-agent+ref-verified"
    assert new["input_types"] == ["@u64", "u64"]          # normalised, not `@(u64`
    assert new["test_cases"] == GOOD["test_cases"] and new["python_ref"] == REF
    assert new["replaces"]["provenance"] == "129.7-agent+ref-verified"
    assert new["replaces"]["archived"].endswith(f"{ba.ARCHIVE_SUBDIR}/{BASE}.json")
    assert new["verified"]["tkc_bin_sha"] == "deadbeef"
    assert not os.path.exists(wave["gen"]) and os.path.exists(wave["gen"] + ".done")
    # a second replacement never clobbers the archived original
    os.rename(wave["gen"] + ".done", wave["gen"])
    s = ba.bank(wave["wd"], wave["bank"], "131.47-agent+ref-verified", probe=False)
    assert s["archived"] == 1
    assert json.load(open(wave["archive"]))["provenance"] == "129.7-agent+ref-verified"
    assert os.path.exists(wave["archive"].replace(".json", ".2.json"))


def test_bank_rejects_packed_resubmission_and_leaves_the_old_file(wave):
    json.dump(PACKED, open(wave["gen"], "w"))
    s = ba.bank(wave["wd"], wave["bank"], "131.47-agent+ref-verified", probe=False)
    assert s["banked"] == 0 and s["rejected"] == 1 and s["archived"] == 0
    assert "packed-arity" in s["rejects"][BASE][0]
    assert not os.path.exists(wave["archive"])
    assert json.load(open(wave["old"])) == OLD_BANK
    assert os.path.exists(wave["gen"] + ".done")


def test_bank_default_provenance_and_workdir_are_the_129_7_ones():
    assert ba.DEFAULT_PROVENANCE == "129.7-agent+ref-verified"
    assert va.WD.endswith("work/a_tests_129")
    assert ba.BANK_DIR.endswith("audit/a_tests")


@needs_tkc
def test_bank_with_probe_end_to_end(wave):
    import validate
    json.dump(GOOD, open(wave["gen"], "w"))
    with tkc_pin.pin().install(validate) as p:
        s = ba.bank(wave["wd"], wave["bank"], "131.47-agent+ref-verified", probe=True,
                    tkc_stamp=p.stamp())
    assert s["banked"] == 1 and s["probe_notes"][BASE].startswith("probe ok")
    new = json.load(open(wave["old"]))
    assert new["verified"]["tkc_bin_sha"] == p.sha256
    assert new["verified"]["probe"].startswith("probe ok")
