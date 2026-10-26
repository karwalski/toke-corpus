"""131.90 (2) -- metrics `functions[].bytes` is the whole function including its
closing `}` (FUNC_DECL full spans since tkc 131.31). Pins the metric basis so a
compiler that changes it again fails here instead of silently shifting the
600-byte gate and every byte comparison."""
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import metrics  # noqa: E402

TKC = os.environ.get("TKC") or os.path.expanduser("~/tk/toke/tkc")

SRC = "m=prog;\nf=add(a:i64;b:i64):i64{\n  <a+b\n};\nf=one():i64{<1};\n"


def test_extent_takes_widest_child_span() -> None:
    node = {"span": {"start": 10, "end": 20},
            "children": [{"span": {"start": 12, "end": 21}, "children": []},
                         {"span": {"start": 9, "end": 15}}]}
    assert metrics._extent(node) == (9, 21)
    assert metrics._extent({"children": []}) == (None, None)


@pytest.mark.skipif(not (os.path.exists(TKC) or shutil.which("tkc")), reason="tkc absent")
def test_function_bytes_include_closing_brace(tmp_path) -> None:
    p = tmp_path / "q.tk"
    p.write_text(SRC)
    m = metrics.analyse(str(p), src=SRC, tkc=TKC)
    assert m is not None
    got = {f["name"]: f["bytes"] for f in m["functions"]}
    want_add = len("f=add(a:i64;b:i64):i64{\n  <a+b\n}")
    want_one = len("f=one():i64{<1}")
    assert got == {"add": want_add, "one": want_one}
    # negative control: the pre-131.31 basis (no closing brace) is NOT what we get
    assert got["add"] != want_add - 1
