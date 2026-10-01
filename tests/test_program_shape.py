"""128.28 -- a corpus "program" must define at least one function body."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from program_shape import (
    bodyless_reason,
    check_record,
    defined_functions,
    function_decls,
    main,
    scan_rows,
)

# The seven declaration-only sources found in toke-model/training-data/train.jsonl
# (corpus ids A-A-ERR-0046-107a8d7a, -0064v1-36893f0d, -0064v4-8daec7c8,
# -0078v11-b155556f, -0080v12-b2c6d848, -0080v2-86b2b3f7, -0084v16-8acaea32).
BODYLESS = [
    ("M=booleanparser;T=ParseErr{InvalidFormat:Str;EmptyInput:bool;OutOfRange:Str};"
     "F=parseBool(s:Str):bool!ParseErr"),
    "M=safetrim;T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str};F=safeTrim(s:Str):Str",
    "M=toBase;T=MathErr{DivByZero:bool;Overflow:Str};F=safeToBase(n:i64;base:i64):Str",
    "M=safeconv;T=MathErr{DivByZero:bool;Overflow:Str};F=safeToBase(first:i64;second:i64):Str",
    "M=divorzero;T=DivErr{DivByZero:bool};F=zfallibleDivide(a:f64;b:f64):f64",
    "M=divfallback;T=DivErr{DivByZero:bool};F=safeDiv(x:f64;y:f64):f64",
    "M=divWithFallbackModule;T=DivByZeroErr{IsZero:bool};F=divide(num:f64;den:f64):f64",
]

PROGRAMS = [
    "m=t;\nf=add(a:i64;b:i64):i64{<a+b};",
    # nested-paren parameter and a map type before the body
    "m=t;\nf=flat(p:@(@u64);q:@(str:i64)):@u64{<@()};",
    # extern beside a definition: legal, not rejected
    "m=t;\nf=ext(a:i64):i64;\nf=main():i64{<ext(1)};",
    # legacy uppercase with a body
    "M=t;F=safeDiv(x:f64;y:f64):f64!DivErr{if(y=0.0){<DivErr{DivByZero:true}};<x/y};",
    # a string containing what looks like a declaration does not count
    'm=t;\nf=main():i64{let s="f=x(a:i64):i64;";<0};',
]


def test_the_seven_records_are_rejected() -> None:
    for src in BODYLESS:
        reason = bodyless_reason(src)
        assert reason is not None and reason.startswith("declaration-only"), src


def test_programs_pass() -> None:
    for src in PROGRAMS:
        assert bodyless_reason(src) is None, src


def test_no_function_at_all_is_rejected() -> None:
    assert bodyless_reason("m=t;\nt=$e{$z:bool};") == "no function declared"


def test_decls_and_bodies() -> None:
    src = "m=t;\nf=ext(a:i64):i64;\nf=g(a:@(str:i64)):i64{if(a.len>0){<1};<0};\nf=h():void;"
    assert function_decls(src) == [("ext", False), ("g", True), ("h", False)]
    assert defined_functions(src) == ["g"]
    # nothing inside a body counts as a top-level declaration
    assert [n for n, _ in function_decls('m=t;\nf=g():i64{let s="f=k():i64;";<0};')] == ["g"]


def test_interface_records_are_exempt() -> None:
    src = BODYLESS[1]
    assert check_record({"task_type": "interface"}, src) is None
    assert check_record({"record_type": "tki"}, src) is None
    assert check_record({"task_type": "single_function"}, src) is not None
    assert check_record({}, src) is not None


def test_scan_rows_reads_every_row_shape() -> None:
    bad = BODYLESS[0]
    good = PROGRAMS[0]
    chatml = ("<|im_start|>system\nx<|im_end|>\n<|im_start|>user\nWrite it.<|im_end|>\n"
              "<|im_start|>assistant\n{src}<|im_end|>\n")
    rows = [
        {"id": "a", "tk_source": bad},
        {"id": "b", "tk_source": good},
        {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": bad}]},
        {"text": chatml.format(src=bad)},
        {"text": chatml.format(src=good)},
        {"text": chatml.format(src="Prose answer, not toke.")},
        {"id": "c", "task_type": "interface", "tk_source": bad},
    ]
    scanned, flagged = scan_rows(rows)
    assert scanned == 6
    assert [n for n, _, _ in flagged] == [1, 3, 4]


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    clean = tmp_path / "clean.jsonl"
    clean.write_text("".join(json.dumps({"tk_source": s}) + "\n" for s in PROGRAMS))
    assert main([str(clean)]) == 0
    dirty = tmp_path / "dirty.jsonl"
    dirty.write_text("".join(json.dumps({"id": f"r{i}", "tk_source": s}) + "\n"
                             for i, s in enumerate(PROGRAMS + BODYLESS)))
    assert main([str(dirty)]) == 1
    out = capsys.readouterr().out
    assert f"{len(PROGRAMS) + len(BODYLESS)} toke rows, {len(BODYLESS)} without a function body" in out
