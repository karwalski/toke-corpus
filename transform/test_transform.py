#!/usr/bin/env python3
"""Tests for Phase 1 to Phase 2 toke source transformation."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from phase1_to_phase2 import Phase1ToPhase2Transformer, tokenize, _sigil_type


class TestTokenizer(unittest.TestCase):
    """Test the tokenizer correctly splits toke source."""

    def test_simple(self):
        tokens = tokenize("M=sum;")
        kinds = [t.kind for t in tokens]
        self.assertEqual(kinds, ["ident", "op", "ident", "op"])

    def test_string_literal_preserved(self):
        tokens = tokenize('let s="hello world";')
        string_tok = [t for t in tokens if t.kind == "string"][0]
        self.assertEqual(string_tok.value, '"hello world"')

    def test_string_with_brackets(self):
        tokens = tokenize('let s="[test]";')
        string_tok = [t for t in tokens if t.kind == "string"][0]
        self.assertEqual(string_tok.value, '"[test]"')

    def test_numbers(self):
        tokens = tokenize("42")
        self.assertEqual(tokens[0].kind, "num")
        self.assertEqual(tokens[0].value, "42")


class TestSigilType(unittest.TestCase):
    """Test type name sigil conversion."""

    def test_str(self):
        self.assertEqual(_sigil_type("Str"), "$str")

    def test_user_type(self):
        self.assertEqual(_sigil_type("Point"), "$point")

    def test_camel_case(self):
        self.assertEqual(_sigil_type("ApiErr"), "$apierr")

    def test_primitives_unchanged(self):
        for t in ("i64", "u64", "f64", "bool", "void"):
            self.assertEqual(_sigil_type(t), t)

    def test_lowercase_unchanged(self):
        self.assertEqual(_sigil_type("myvar"), "myvar")


class TestDeclKeywords(unittest.TestCase):
    """Test Rule 1: Declaration keyword lowering."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_m_decl(self):
        self.assertEqual(self.t.transform("M=sum;"), "m=sum;")

    def test_f_decl(self):
        self.assertEqual(self.t.transform("F=f():i64{<0};"), "f=f():i64{<0};")

    def test_t_decl(self):
        r = self.t.transform("T=Err{Bad:bool};")
        self.assertEqual(r, "t=$err{$bad:bool};")

    def test_i_decl(self):
        r = self.t.transform("I=parse:std.parseint;")
        self.assertEqual(r, "i=parse:std.parseint;")

    def test_multiple_decls(self):
        r = self.t.transform("M=test;F=f():i64{<0};")
        self.assertEqual(r, "m=test;f=f():i64{<0};")

    def test_not_at_boundary(self):
        """M inside an expression should not be lowered."""
        r = self.t.transform("M=test;F=f(M:i64):i64{<M};")
        # The M in parameter position is not at statement boundary
        # Actually it IS an uppercase initial ident but not followed by = at boundary
        # Wait, M:i64 - M is param name, : is after it. The M= check requires = after.
        # Let me verify: M as param name with : after - the M is ident, next is :, not =
        # So it won't be lowered. Good.
        self.assertIn("f=f(M:i64)", r)


class TestTypeSigils(unittest.TestCase):
    """Test Rule 2: Type name sigils."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_param_type(self):
        r = self.t.transform("F=f(s:Str):i64{<0};")
        self.assertIn("s:$str", r)

    def test_return_type(self):
        r = self.t.transform("F=f():Str{<\"\"};")
        self.assertIn("):$str{", r)

    def test_primitive_unchanged(self):
        r = self.t.transform("F=f(x:i64):bool{<true};")
        self.assertIn("x:i64", r)
        self.assertIn("):bool{", r)

    def test_error_union_return(self):
        r = self.t.transform("F=f():i64!MyErr{<0};")
        self.assertIn("!$myerr", r)

    def test_error_propagation(self):
        r = self.t.transform("F=f():i64!Err{let x=g()!Err;<x};")
        self.assertEqual(r.count("!$err"), 2)

    def test_struct_literal(self):
        r = self.t.transform("F=f():i64{<MyErr{Bad:true}};")
        self.assertIn("$myerr{$bad:true", r)


class TestArrayLiterals(unittest.TestCase):
    """Test Rule 3: Array literal syntax."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_empty_array(self):
        r = self.t.transform("F=f():[i64]{<[]};")
        self.assertIn("@()", r)

    def test_number_array(self):
        r = self.t.transform("F=f():[i64]{let a=[1;2;3];<a};")
        self.assertIn("@(1;2;3)", r)

    def test_string_array(self):
        r = self.t.transform('F=f():[i64]{let a=["a";"b"];<a};')
        self.assertIn('@("a";"b")', r)

    def test_mut_empty_array(self):
        r = self.t.transform("F=f():[i64]{let a=mut.[];<a};")
        self.assertIn("mut.@()", r)

    def test_array_concat(self):
        r = self.t.transform("F=f(a:[i64]):[i64]{<a+[1]};")
        self.assertIn("+@(1)", r)


class TestArrayTypes(unittest.TestCase):
    """Test Rule 4: Array type notation."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_i64_array(self):
        r = self.t.transform("F=f(a:[i64]):i64{<0};")
        self.assertIn("a:@i64", r)

    def test_str_array(self):
        r = self.t.transform("F=f(a:[Str]):i64{<0};")
        self.assertIn("a:@$str", r)

    def test_return_array_type(self):
        r = self.t.transform("F=f():[i64]{<[]};")
        self.assertIn("):@i64", r)


class TestArrayIndexing(unittest.TestCase):
    """Test Rule 5: Array indexing."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_constant_index(self):
        r = self.t.transform("F=f(a:[i64]):i64{<a[0]};")
        self.assertIn("a.0", r)

    def test_constant_index_with_cast(self):
        r = self.t.transform("F=f(a:[i64]):i64{<a[0 as u64]};")
        self.assertIn("a.0 as u64", r)

    def test_variable_index(self):
        r = self.t.transform("F=f(a:[i64];i:i64):i64{<a[i]};")
        self.assertIn("a.get(i)", r)

    def test_expression_index(self):
        r = self.t.transform("F=f(a:[i64]):i64{<a[i+1]};")
        self.assertIn("a.get(i+1)", r)

    def test_nested_indexing(self):
        r = self.t.transform("F=f(a:[i64]):i64{<a[a[0]]};")
        self.assertIn("a.get(a.0)", r)

    def test_string_indexing(self):
        r = self.t.transform("F=f(s:Str):i64{<s[i]};")
        self.assertIn("s.get(i)", r)

    def test_cast_expression_index(self):
        r = self.t.transform("F=f(a:[Str]):Str{<a[(n-1)as u64]};")
        self.assertIn("a.get((n-1)as u64)", r)


class TestMapTypes(unittest.TestCase):
    """Test Rule 6: Map type notation."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_str_i64_map(self):
        r = self.t.transform("F=f(m:[Str:i64]):i64{<0};")
        self.assertIn("m:$($str:i64)", r)

    def test_i64_bool_map(self):
        r = self.t.transform("F=f():[i64]{let seen=[i64:bool];<[]};")
        self.assertIn("$(i64:bool)", r)

    def test_mut_map_init(self):
        r = self.t.transform("F=f():[i64]{let seen=mut.[i64:bool];<[]};")
        self.assertIn("mut.$(i64:bool)", r)


class TestMapLiterals(unittest.TestCase):
    """Test Rule 7: Map literal syntax."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_string_keyed_map(self):
        r = self.t.transform('F=f():i64{let m=["a":1;"b":2];<0};')
        self.assertIn('$("a":1;"b":2)', r)


class TestSumTypeVariants(unittest.TestCase):
    """Test Rule 8: Sum type variant sigils."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_simple_sum_type(self):
        r = self.t.transform("T=Result{Ok:i64;Err:bool};")
        self.assertEqual(r, "t=$result{$ok:i64;$err:bool};")

    def test_sum_type_with_uppercase_type(self):
        r = self.t.transform("T=Shape{Circle:f64;Rect:Point};")
        self.assertIn("$circle:f64", r)
        self.assertIn("$rect:$point", r)

    def test_struct_with_lowercase_fields(self):
        r = self.t.transform("T=Pair{found:bool;index:i64};")
        self.assertIn("found:bool", r)
        self.assertIn("index:i64", r)

    def test_match_arms(self):
        r = self.t.transform("F=f():i64{x|{Ok:v v;Err:e 0}};")
        self.assertIn("$ok:v", r)
        self.assertIn("$err:e", r)


class TestStringPreservation(unittest.TestCase):
    """Test that string literal contents are not transformed."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_brackets_in_string(self):
        r = self.t.transform('F=f():Str{<"[test]"};')
        self.assertIn('"[test]"', r)

    def test_keywords_in_string(self):
        r = self.t.transform('F=f():Str{<"M=test;F=foo"};')
        self.assertIn('"M=test;F=foo"', r)

    def test_uppercase_in_string(self):
        r = self.t.transform('F=f():Str{<"Hello Str World"};')
        self.assertIn('"Hello Str World"', r)


class TestComplexExamples(unittest.TestCase):
    """Test real corpus entry patterns."""

    def setUp(self):
        self.t = Phase1ToPhase2Transformer()

    def test_array_sum(self):
        src = "M=sum;F=sum(arr:[i64]):i64{let total=mut.0;lp(let i=0;i<arr.len;i=i+1){total=total+arr[i];};<total};"
        expected = "m=sum;f=sum(arr:@i64):i64{let total=mut.0;lp(let i=0;i<arr.len;i=i+1){total=total+arr.get(i);};<total};"
        self.assertEqual(self.t.transform(src), expected)

    def test_error_type_with_literal(self):
        src = 'M=safediv;T=MathErr{DivByZero:bool;Overflow:Str};F=safeDiv(a:i64;b:i64):i64!MathErr{if(b=0){<MathErr{DivByZero:true;Overflow:""};};<a/b};'
        r = self.t.transform(src)
        self.assertIn("m=safediv", r)
        self.assertIn("t=$matherr{$divbyzero:bool;$overflow:$str}", r)
        self.assertIn("!$matherr{", r)
        self.assertIn('$matherr{$divbyzero:true;$overflow:""}', r)

    def test_map_lookup(self):
        src = "M=safelookup;T=LookupErr{NotFound:Str;EmptyCollection:bool};F=safeLookup(m:[Str:i64];key:Str):i64!LookupErr{if(m.len=0 as u64){<LookupErr{EmptyCollection:true}};let result=m.get(key);result|{Ok:v v;Err:e LookupErr{NotFound:key}}};"
        r = self.t.transform(src)
        self.assertIn("m:$($str:i64)", r)
        self.assertIn("key:$str", r)
        self.assertIn("$lookuperr{$emptycollection:true}", r)
        self.assertIn("$ok:v v", r)
        self.assertIn("$lookuperr{$notfound:key}", r)

    def test_import(self):
        src = "M=parser;I=parse:std.parseint;F=f(s:Str):i64{<0};"
        r = self.t.transform(src)
        self.assertIn("m=parser", r)
        self.assertIn("i=parse:std.parseint", r)

    def test_grade_with_string_array(self):
        src = 'M=grade;F=grade(score:i64):Str{let grades=["A";"B";"C";"D";"F"];let lhs=[90;80;70;60;-1];<grades[0]};'
        r = self.t.transform(src)
        self.assertIn('@("A";"B";"C";"D";"F")', r)
        self.assertIn("@(90;80;70;60;-1)", r)
        self.assertIn("grades.0", r)

    def test_array_filter_positive(self):
        src = "M=filterPositive;T=Result{Ok:[i64];Err:bool};F=filterPositive(arr:[i64]):[i64]{let result=mut.[];lp(let i=0;i<arr.len;i=i+1){if(arr[i]>0){result=result+[arr[i]];};};<result};"
        r = self.t.transform(src)
        self.assertIn("@i64", r)
        self.assertIn("mut.@()", r)
        self.assertIn("arr.get(i)>0", r)
        self.assertIn("+@(arr.get(i))", r)

    def test_map_type_in_mut(self):
        src = "F=f():[i64]{let seen=mut.[i64:bool];<[]};"
        r = self.t.transform(src)
        self.assertIn("mut.$(i64:bool)", r)
        self.assertIn("<@()", r)


class TestJsonlTransform(unittest.TestCase):
    """Test JSONL file transformation."""

    def test_roundtrip(self):
        t = Phase1ToPhase2Transformer()
        entries = [
            {"id": "test-1", "tk_source": "M=sum;F=sum(arr:[i64]):i64{<0};"},
            {"id": "test-2", "tk_source": 'F=f():Str{<"hello"};'},
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
            input_path = f.name

        output_path = input_path.replace(".jsonl", "_p2.jsonl")
        try:
            from phase1_to_phase2 import transform_jsonl
            transform_jsonl(input_path, output_path)

            with open(output_path) as f:
                results = [json.loads(line) for line in f]

            self.assertEqual(len(results), 2)
            self.assertIn("m=sum", results[0]["tk_source"])
            self.assertIn("f=f()", results[1]["tk_source"])
        finally:
            os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
