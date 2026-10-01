"""131.84a: surface_check() referenced an undefined name and crashed on every row."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from validate_training_format import surface_check


def test_clean_program_has_no_issues() -> None:
    src = 'm=x;\nf=main():i64{let a="B";if(a=="b"){<1};if(1!=2){<2};<0};'
    assert surface_check(src) == []


def test_brackets_outside_strings_are_flagged() -> None:
    assert surface_check("m=x;\nf=main():i64{let a=@(1);<a[0]};") == ["square_bracket"]


def test_brackets_and_uppercase_inside_strings_are_ignored() -> None:
    assert surface_check('m=x;\nf=main():i64{let a="[X]";<0};') == []


def test_uppercase_and_missing_module_decl() -> None:
    assert surface_check("f=Main():i64{<0};") == ["missing_module_decl", "uppercase_letter"]
