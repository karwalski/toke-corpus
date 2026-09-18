"""131.44 (a) — a_tests injection: the 129.7 bank carries the sampler-mangled
`@(u64` spelling; driver._norm_type / norm_types normalise it and
audit.load_specs compares normalised, so the 76 affected records get their
tests. Also pins the paren-aware sig_input_types (2-level nesting)."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import audit          # noqa: E402
import driver as drv  # noqa: E402


def test_norm_type_mangled_and_balanced_spellings():
    assert drv._norm_type("@(u64") == "@u64"
    assert drv._norm_type("@(@(u64") == "@@u64"
    assert drv._norm_type("@(str:i64") == "@str:i64"
    assert drv._norm_type("@(u64)") == "@u64"
    assert drv._norm_type("@(@(i64))") == "@@i64"
    assert drv._norm_type("@(str:i64)") == "@str:i64"
    assert drv._norm_type(" u64 ") == "u64"
    assert drv._norm_type("@u64") == "@u64"


def test_norm_types_list():
    assert drv.norm_types(["@(u64", "u64"]) == ["@u64", "u64"]
    assert drv.norm_types(None) == []


def test_sig_input_types_nested_two_levels():
    s = {"description": "Write a function f=flatten(arrs:@(@(u64))):@(u64) that flattens."}
    assert drv.sig_input_types(s) == ["@@u64"]
    s = {"description": "f=g(m:@(str:i64);k:str):i64 looks up."}
    assert drv.sig_input_types(s) == ["@str:i64", "str"]
    assert drv.sig_input_types({"description": "no signature here"}) is None


def test_effective_input_types_recovers_nested_mangled_spec():
    s = {"description": "Write a function f=flatten(arrs:@(@(u64))):@(u64) x",
         "input_types_v03": ["@(@(u64"]}
    assert drv.effective_input_types(s) == ["@@u64"]


def _spec(types_v03, desc_sig, task_type="single_function"):
    return {"task_id": "A-TST-0001v1", "category": "A-TST", "task_type": task_type,
            "description": "Write a function " + desc_sig + " that does x. Variant 1: y.",
            "input_types_v03": types_v03, "output_type_v03": "u64",
            "domain_context_v03": "", "test_cases": []}


def _corpus(tmp_path, spec, a_types):
    os.makedirs(tmp_path / "shards")
    os.makedirs(tmp_path / "audit" / "a_tests")
    with open(tmp_path / "shards" / "shard_00.jsonl", "w") as f:
        f.write(json.dumps(spec) + "\n")
    with open(tmp_path / "audit" / "a_tests" / "A-TST-0001.json", "w") as f:
        json.dump({"base": "A-TST-0001", "python_ref": "def f(a, b): return b",
                   "test_cases": [{"inputs": [[1, 2], 7], "expected": 7}],
                   "input_types": a_types, "output_type": "u64"}, f)
    return str(tmp_path)


def test_load_specs_injects_mangled_spelling(tmp_path):
    spec = _spec(["@(u64"], "f=foldsum(arr:@(u64);init:u64):u64")
    specs = audit.load_specs(_corpus(tmp_path, spec, ["@(u64", "u64"]))
    s = specs["A-TST-0001v1"]
    assert s["_tests_from"] == "a_tests"
    assert s["test_cases"] == [{"inputs": [[1, 2], 7], "expected": 7}]


def test_load_specs_skips_arity_mismatch(tmp_path):
    # bank authored at arity 1 (packed args) — stays un-injected (131.44 (c))
    spec = _spec(["@(u64"], "f=foldsum(arr:@(u64);init:u64):u64")
    specs = audit.load_specs(_corpus(tmp_path, spec, ["@(u64"]))
    assert not specs["A-TST-0001v1"].get("test_cases")
    assert "_tests_from" not in specs["A-TST-0001v1"]


def test_load_specs_skips_full_program(tmp_path):
    spec = _spec(["@(u64"], "f=foldsum(arr:@(u64);init:u64):u64", task_type="full_program")
    specs = audit.load_specs(_corpus(tmp_path, spec, ["@(u64", "u64"]))
    assert not specs["A-TST-0001v1"].get("test_cases")
