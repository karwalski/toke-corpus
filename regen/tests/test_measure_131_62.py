"""131.62 (a): the detectors behind regen/freeze/measure_131.62.json. Each
shape the pre-132.15 spec page prescribed is caught, string text never
counts, interpolation code does, and the canonical forms are not flagged."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import measure_131_62 as m


def feats(src):
    return m.features(src)


def test_code_only_blanks_strings_keeps_interpolation():
    code = m.code_only('let s="a^b ~ mt \\"q\\" \\(x^y) z";')
    assert "a^b" not in code and "mt" not in code
    assert "x^y" in code


def test_constructs_counted_in_code_only():
    _, f, _ = feats('m=p;\nf=g(a:i64;b:i64):i64{\n  <mt h(a) {$ok:v v^b;$err:e ~a}\n};\n')
    assert f["mt"] == 1 and f["caret"] == 1 and f["tilde"] == 1 and f["sc"] == 0
    _, f, _ = feats('m=p;\nf=g():i64{\n  sc{let x=1};\n  let desc=1;\n  <0\n};\n')
    assert f["sc"] == 1
    _, f, _ = feats('m=p;\nf=g():str{\n  <"mt sc ^ ~"\n};\n')
    assert f == {"mt": 0, "sc": 0, "caret": 0, "tilde": 0}


def test_page_prescribed_shapes_flagged():
    _, _, w = feats("f=g(a:i64;b:i64):i64{\n  if(!(a==b)){<1};\n  <0\n};\n")
    assert w["neq_emulation"] and w["any_page_prescribed"]
    _, _, w = feats("f=g(a:i64;b:i64):i64{\n  if(!(a>b)){<1};\n  <0\n};\n")
    assert w["le_ge_emulation"]
    for expr in ("a-a/b*b", "a-(a/b)*b", "(((c-97)+13)-((((c-97)+13)/26)*26))"):
        _, _, w = feats("f=g(a:i64;b:i64;c:i64):i64{\n  <%s\n};\n" % expr)
        assert w["mod_emulation"], expr
    _, _, w = feats("f=g(a:i64;b:i64):i64{\n  if(a>0){\n    if(b>0){<1}\n  };\n  <0\n};\n")
    assert w["nested_if_and"]


def test_canonical_forms_not_flagged():
    src = ("f=g(a:i64;b:i64):i64{\n  if(a!=b&&a<=b){<a%b};\n"
           "  if(a>0){if(b>0){<1}}el{<2};\n  let r=(a/10)*10;\n  <r\n};\n")
    _, _, w = feats(src)
    assert not any(w[k] for k in m.PAGE_PRESCRIBED), w


def test_legacy_input_extraction():
    prompt = ("TASK: migrate\n\nLegacy source (compiled under an older toke; now fails):\n"
              "m=x;f=g(a:i64):bool{<!(a=0)};\n\nCurrent compiler diagnostics:\n{}\n")
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write(prompt)
    try:
        legacy = m.legacy_input(fh.name)
    finally:
        os.unlink(fh.name)
    assert legacy == "m=x;f=g(a:i64):bool{<!(a=0)};"
    _, _, w = feats(legacy)
    assert w["neq_emulation"]          # the legacy spelling !(a=b) counts too


def test_handrolled_int_parse_needs_accumulation():
    _, _, w = feats("f=g(c:i64):bool{\n  <c>=48&&c<=57\n};\n")
    assert not w["handrolled_int_parse"]
    _, _, w = feats("f=g(v:i64;c:i64):i64{\n  <v*10+(c-48)\n};\n")
    assert w["handrolled_int_parse"]
