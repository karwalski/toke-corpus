"""131.83: interpolation-segment failures in banked-accepted records."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import interp_name_check_131_83 as inc   # noqa: E402


def test_interp_spans_nested_and_comment():
    src = 'f=main():i64{\n  io.println("a \\(f(g(1);"x)")) b");\n  <0\n};\n# "\\(nope)"\n'
    spans = inc.interp_spans(src)
    assert [src[a:b] for a, b in spans] == ['\\(f(g(1);"x)"))']


def test_diagnostics_parses_json_lines_only():
    out = inc.diagnostics('{"error_code":"E4031","message":"m","span_start":3}\nnoise', '')
    assert [d["error_code"] for d in out] == ["E4031"]


def test_route_reason_names_the_real_diagnostic(tmp_path):
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "pattern_sweep.jsonl").write_text(
        json.dumps({"task_id": "A-SRT-1v1", "story": "131.13", "bucket": "REGEN", "violations": []}) + "\n")
    hit = {"error_code": "E4031", "message": "type mismatch: expected '@(u64)', got '@(i64)'",
           "segment": "\\(f(a;1 as u64))"}
    rows = [{"task_id": "A-SRT-1v1", "in_scope": True, "in_interpolation": [hit]},
            {"task_id": "A-SRT-2v1", "in_scope": True, "in_interpolation": [hit]},
            {"task_id": "A-SRT-3v1", "in_scope": False, "in_interpolation": []}]
    specs = {"A-SRT-1v1": {"category": "A-SRT", "task_type": "full_program", "test_cases": []},
             "A-SRT-2v1": {"category": "A-SRT", "task_type": "single_function", "test_cases": [{"x": 1}]}}
    res = inc.route(rows, str(tmp_path), specs, ts=1)
    assert res["regen_task_ids"] == ["A-SRT-1v1"] and res["agent_task_ids"] == ["A-SRT-2v1"]
    out = [json.loads(x) for x in open(tmp_path / "audit" / "pattern_sweep.131.83.patch.jsonl")]
    reason = out[0]["bucket_reason"]
    assert "E4031" in reason and "not declared" not in reason
    assert out[0]["violations"][-1]["rule"] == inc.RULE == "interp-segment-unchecked"


def test_route_no_tests_outside_regen_class_goes_agent(tmp_path):
    """131.109: the two 131.83 MIG records (migrate_fix, no tests) went REGEN,
    a dead end since 131.16 regenerates only A-*/full_program."""
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "pattern_sweep.jsonl").write_text(
        json.dumps({"task_id": "MIG-X-1", "story": "131.13", "bucket": "LEAVE", "violations": []}) + "\n")
    hit = {"error_code": "E3011", "message": "undeclared", "segment": "\\(n)"}
    rows = [{"task_id": "MIG-X-1", "in_scope": True, "in_interpolation": [hit]}]
    specs = {"MIG-X-1": {"category": "D-CFG", "task_type": "migrate_fix", "test_cases": []}}
    res = inc.route(rows, str(tmp_path), specs, ts=1)
    assert res["agent_task_ids"] == ["MIG-X-1"] and res["regen_task_ids"] == []
    patch = (tmp_path / "audit" / "pattern_sweep.131.83.patch.jsonl").read_text()
    out = [json.loads(x) for x in patch.splitlines()]
    assert out[0]["bucket"] == "AGENT" and "131.109" in out[0]["bucket_reason"]
