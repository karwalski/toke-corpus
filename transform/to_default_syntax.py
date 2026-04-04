#!/usr/bin/env python3
"""Transform Phase 1 (legacy 80-char) toke source to default (56-char) syntax.

Story 11.6.1 — Regenerate corpus in default syntax.

Transformation rules (all applied mechanically):

1. Declaration keywords:  M= -> m=,  F= -> f=,  I= -> i=,  T= -> t=,  C= -> c=
2. Type name sigils:      Str -> $str,  Point -> $point  (uppercase-initial in type pos)
3. Array literals:        [1;2;3] -> @(1;2;3),  [] -> @()
4. Array types:           [i64] -> @i64,  [Point] -> @$point
5. Array indexing:        arr[0] -> arr.get(0),  arr[i] -> arr.get(i)
                          (ALL indexing uses .get(), never .N dot-index)
6. Map types:             [K:V] -> @($k:$v)  where k,v get $ prefix if needed
7. Map literals:          ["a":1;"b":2] -> @("a":1;"b":2)
8. Sum type variants:     uppercase variant names get $ prefix in type declarations
9. Logical operators:     already && and || in legacy — no change needed
10. No uppercase letters: all type/variant names lowercased with $ prefix

Default syntax reference (from grammar tests):
  - m=mod; f=name(a:i64;b:str):i64{...};
  - t=$point{x:i64;y:i64};
  - f=mk():$point{<$point{x:0;y:0}};
  - let a=@(10;20;30);  a.get(0)
  - let m=@(1:10;2:20);
  - f=first(a:@i64):i64{...}
  - f=mk(m:@($i64:$str)):i64{...}
  - Primitives (i64, u64, f64, bool, str, void, i8, i16, i32, u8, u16, u32, f32)
    do NOT get $ prefix in regular type annotations
  - User-defined types always get $ prefix: $point, $vec2, $apierr
  - Inside map type @(...), both key and value can have $ prefix: @($i64:$str)
"""

import json
import sys
import os
import subprocess
import argparse
import re
from pathlib import Path
from typing import Optional


# Primitive / scalar types that must NOT get a $ sigil in normal type positions
PRIMITIVE_TYPES = frozenset({
    "i64", "u64", "f64", "bool", "void", "str",
    "i8", "i16", "i32", "u8", "u16", "u32", "f32",
})

# Special scalar types that KEEP their casing (recognized by parser is_scalar)
# These must NOT be lowered or get $ prefix in regular type positions.
# Str and Byte are special: the parser recognizes them as TK_TYPE_IDENT scalars.
SCALAR_TYPES = frozenset({
    "Str", "Byte",
})

# Legacy uppercase names that map to primitives (NOT including Str/Byte which stay)
LEGACY_TYPE_MAP = {
    "Bool": "bool",
}

# toke keywords that are already lowercase and must not be touched
KEYWORDS = frozenset({
    "if", "el", "lp", "br", "let", "mut", "as", "true", "false",
    "ret", "fn", "pub", "mod", "use", "for", "in", "while",
})


def _is_uppercase_initial(name: str) -> bool:
    """Check if a name starts with an uppercase letter (A-Z)."""
    return bool(name) and name[0].isupper()


def _sigil_type(name: str) -> str:
    """Convert a type name to default syntax form.

    Str -> Str (special scalar, stays uppercase)
    Byte -> Byte (special scalar, stays uppercase)
    Bool -> bool (legacy mapping)
    Point -> $point (user-defined, gets $)
    i64, u64, etc. -> unchanged
    """
    # Special scalars keep their form
    if name in SCALAR_TYPES:
        return name
    # Legacy mappings
    if name in LEGACY_TYPE_MAP:
        return LEGACY_TYPE_MAP[name]
    # Lowercase primitives stay as-is
    if name.lower() in PRIMITIVE_TYPES:
        return name.lower()
    # Uppercase-initial user types get $ prefix
    if _is_uppercase_initial(name):
        return "$" + name.lower()
    return name


def _sigil_type_for_map(name: str) -> str:
    """Convert a type name for use inside @(...) map type notation.

    Inside map types, even primitives get $ prefix: @($i64:$str)
    Str -> $str, Byte -> $u8 inside map types.
    """
    if name == "Str":
        return "$str"
    if name == "Byte":
        return "$u8"
    if name in LEGACY_TYPE_MAP:
        prim = LEGACY_TYPE_MAP[name]
        return "$" + prim
    if name.lower() in PRIMITIVE_TYPES:
        return "$" + name.lower()
    if _is_uppercase_initial(name):
        return "$" + name.lower()
    # Already lowercase non-primitive
    if name in PRIMITIVE_TYPES:
        return "$" + name
    return name


class Token:
    """A token from toke source code."""
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: str):
        self.kind = kind      # "ident", "string", "num", "op", "ws"
        self.value = value

    def __repr__(self):
        return f"Token({self.kind!r}, {self.value!r})"


def tokenize(source: str) -> list[Token]:
    """Tokenize toke source into a list of tokens.

    Respects string literals (content between double quotes is preserved).
    """
    tokens = []
    i = 0
    n = len(source)

    while i < n:
        ch = source[i]

        # String literal
        if ch == '"':
            j = i + 1
            while j < n and source[j] != '"':
                if source[j] == '\\' and j + 1 < n:
                    j += 1  # skip escaped char
                j += 1
            j += 1  # include closing quote
            tokens.append(Token("string", source[i:j]))
            i = j

        # Identifier or keyword
        elif ch.isalpha() or ch == '_':
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == '_'):
                j += 1
            tokens.append(Token("ident", source[i:j]))
            i = j

        # Number
        elif ch.isdigit():
            j = i + 1
            while j < n and (source[j].isdigit() or source[j] == '.'):
                j += 1
            tokens.append(Token("num", source[i:j]))
            i = j

        # Operators and punctuation (single char)
        else:
            tokens.append(Token("op", ch))
            i += 1

    return tokens


class ToDefaultSyntaxTransformer:
    """Mechanically transforms legacy toke source to default (56-char) syntax."""

    def __init__(self):
        self.stats = {
            "decl_keywords": 0,
            "type_sigils": 0,
            "array_literals": 0,
            "array_types": 0,
            "array_indexing": 0,
            "map_types": 0,
            "map_literals": 0,
            "sum_variants": 0,
        }

    def transform(self, source: str) -> str:
        """Transform a legacy toke source string to default syntax."""
        tokens = tokenize(source)
        tokens = self._transform_tokens(tokens)
        return "".join(t.value for t in tokens)

    def _transform_tokens(self, tokens: list[Token]) -> list[Token]:
        """Walk the token stream and apply all transformations."""
        result = []
        i = 0
        n = len(tokens)

        while i < n:
            tok = tokens[i]

            # Rule 1: Declaration keywords at statement boundaries
            if (tok.kind == "ident" and tok.value in ("M", "F", "I", "T", "C")
                    and i + 1 < n and tokens[i + 1].kind == "op"
                    and tokens[i + 1].value == "="
                    and self._at_statement_boundary(result)):
                self.stats["decl_keywords"] += 1
                result.append(Token("ident", tok.value.lower()))
                i += 1
                continue

            # Rule 8 + Rule 2: Type declaration body T=Name{...}
            if (tok.kind == "ident" and _is_uppercase_initial(tok.value)
                    and self._in_type_decl_context(result)):
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tok.value)))
                # Check if next is { - if so, transform the type body
                if i + 1 < n and tokens[i + 1].kind == "op" and tokens[i + 1].value == "{":
                    i += 1
                    result.append(tokens[i])  # the {
                    i += 1
                    i = self._transform_type_body(tokens, i, result)
                    continue
                i += 1
                continue

            # Struct/error literal: TypeName{field:val;...} in expression context
            if (tok.kind == "ident" and _is_uppercase_initial(tok.value)
                    and tok.value not in PRIMITIVE_TYPES
                    and tok.value not in LEGACY_TYPE_MAP
                    and tok.value not in SCALAR_TYPES
                    and i + 1 < n and tokens[i + 1].kind == "op"
                    and tokens[i + 1].value == "{"):
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tok.value)))
                i += 1
                result.append(tokens[i])  # the {
                i += 1
                i = self._transform_struct_literal_body(tokens, i, result)
                continue

            # Match expression: expr|{Variant:binding expr;...}
            if (tok.kind == "op" and tok.value == "|"
                    and i + 1 < n and tokens[i + 1].kind == "op"
                    and tokens[i + 1].value == "{"):
                result.append(tok)  # |
                i += 1
                result.append(tokens[i])  # {
                i += 1
                i = self._transform_match_body(tokens, i, result)
                continue

            # Error union: !TypeName in return type position
            if (tok.kind == "op" and tok.value == "!"
                    and i + 1 < n and tokens[i + 1].kind == "ident"
                    and _is_uppercase_initial(tokens[i + 1].value)
                    and tokens[i + 1].value not in PRIMITIVE_TYPES
                    and tokens[i + 1].value not in LEGACY_TYPE_MAP
                    and tokens[i + 1].value not in SCALAR_TYPES):
                result.append(tok)  # !
                i += 1
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tokens[i].value)))
                i += 1
                continue

            # Square brackets: need to distinguish type, literal, indexing, map
            if tok.kind == "op" and tok.value == "[":
                i = self._transform_bracket(tokens, i, result)
                continue

            # Type annotations after colon in parameter/variable positions
            if (tok.kind == "op" and tok.value == ":"
                    and not self._inside_match_or_sum(result)):
                result.append(tok)
                i += 1
                if (i < n and tokens[i].kind == "ident"
                        and _is_uppercase_initial(tokens[i].value)):
                    if self._in_type_annotation_context(result):
                        self.stats["type_sigils"] += 1
                        result.append(Token("ident", _sigil_type(tokens[i].value)))
                        i += 1
                        continue
                continue

            # Return type position: ):TypeName
            if (tok.kind == "op" and tok.value == ")"
                    and i + 1 < n and tokens[i + 1].kind == "op"
                    and tokens[i + 1].value == ":"
                    and self._is_return_type_context(tokens, i)):
                result.append(tok)  # )
                i += 1
                result.append(tokens[i])  # :
                i += 1
                i = self._transform_type_expr(tokens, i, result)
                continue

            result.append(tok)
            i += 1

        return result

    def _at_statement_boundary(self, result: list[Token]) -> bool:
        """Check if we're at a statement boundary (start or after ; or newline)."""
        if not result:
            return True
        for j in range(len(result) - 1, -1, -1):
            tok = result[j]
            if tok.kind == "ws":
                continue
            # Newlines and semicolons are statement boundaries
            if tok.kind == "op" and tok.value in (";", "\n", "\r"):
                return True
            return False
        return True

    def _in_type_decl_context(self, result: list[Token]) -> bool:
        """Check if we just saw t= (or T= before lowering -> now t=)."""
        if len(result) < 2:
            return False
        j = len(result) - 1
        while j >= 0 and result[j].kind == "ws":
            j -= 1
        if j < 0 or result[j].value != "=":
            return False
        j -= 1
        while j >= 0 and result[j].kind == "ws":
            j -= 1
        if j < 0:
            return False
        return result[j].kind == "ident" and result[j].value == "t"

    def _transform_type_body(self, tokens: list[Token], i: int,
                              result: list[Token]) -> int:
        """Transform field/variant names inside a type declaration body.

        Uppercase variant names are KEPT uppercase because the parser
        requires TK_TYPE_IDENT in match arms that reference them.
        Type annotations after ':' are transformed normally.

        T=Shape{Circle:f64;Rect:Point} -> t=$shape{Circle:f64;Rect:$point}
        T=Pair{x:i64;y:i64} -> t=$pair{x:i64;y:i64}  (lowercase fields stay)
        """
        n = len(tokens)
        depth = 1
        at_field_start = True

        while i < n and depth > 0:
            tok = tokens[i]

            if tok.kind == "op" and tok.value == "{":
                depth += 1
                result.append(tok)
                i += 1
                continue

            if tok.kind == "op" and tok.value == "}":
                depth -= 1
                result.append(tok)
                i += 1
                if depth == 0:
                    return i
                continue

            if tok.kind == "op" and tok.value == ";":
                result.append(tok)
                at_field_start = True
                i += 1
                continue

            # Variant/field name at start position — keep uppercase for match compat
            if at_field_start and tok.kind == "ident":
                # Keep variant/field names as-is (uppercase variants must stay
                # uppercase so match arms can reference them as TK_TYPE_IDENT)
                result.append(tok)
                at_field_start = False
                i += 1
                continue

            # Type after colon in variant/field definition
            if tok.kind == "op" and tok.value == ":":
                result.append(tok)
                i += 1
                i = self._transform_type_expr(tokens, i, result)
                at_field_start = False
                continue

            # Handle [ brackets within type body
            if tok.kind == "op" and tok.value == "[":
                i = self._transform_bracket(tokens, i, result)
                continue

            result.append(tok)
            i += 1

        return i

    def _transform_struct_literal_body(self, tokens: list[Token], i: int,
                                        result: list[Token]) -> int:
        """Transform field/variant names inside a struct/error literal.

        Field/variant names keep their case (must match declaration).
        MathErr{DivByZero:true;Overflow:""} -> $matherr{DivByZero:true;Overflow:""}
        """
        n = len(tokens)
        depth = 1
        at_field_start = True

        while i < n and depth > 0:
            tok = tokens[i]

            if tok.kind == "op" and tok.value == "{":
                depth += 1
                result.append(tok)
                i += 1
                continue

            if tok.kind == "op" and tok.value == "}":
                depth -= 1
                result.append(tok)
                i += 1
                if depth == 0:
                    return i
                continue

            if tok.kind == "op" and tok.value == ";":
                result.append(tok)
                at_field_start = True
                i += 1
                continue

            # Field/variant name — keep as-is (must match type declaration)
            if at_field_start and tok.kind == "ident":
                result.append(tok)
                at_field_start = False
                i += 1
                continue

            at_field_start = False

            # Recursively handle nested type names in values
            if (tok.kind == "ident" and _is_uppercase_initial(tok.value)
                    and tok.value not in PRIMITIVE_TYPES
                    and tok.value not in LEGACY_TYPE_MAP
                    and i + 1 < n and tokens[i + 1].kind == "op"
                    and tokens[i + 1].value == "{"):
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tok.value)))
                i += 1
                result.append(tokens[i])  # {
                i += 1
                i = self._transform_struct_literal_body(tokens, i, result)
                continue

            # Handle [ brackets in values
            if tok.kind == "op" and tok.value == "[":
                i = self._transform_bracket(tokens, i, result)
                continue

            result.append(tok)
            i += 1

        return i

    def _transform_match_body(self, tokens: list[Token], i: int,
                               result: list[Token]) -> int:
        """Transform match arms — keep variant names uppercase (TK_TYPE_IDENT required).

        Ok:v expr -> Ok:v expr  (parser requires TK_TYPE_IDENT for match arms)
        """
        n = len(tokens)
        depth = 1
        at_arm_start = True

        while i < n and depth > 0:
            tok = tokens[i]

            if tok.kind == "op" and tok.value == "{":
                depth += 1
                result.append(tok)
                i += 1
                continue

            if tok.kind == "op" and tok.value == "}":
                depth -= 1
                result.append(tok)
                i += 1
                if depth == 0:
                    return i
                continue

            if tok.kind == "op" and tok.value == ";":
                result.append(tok)
                at_arm_start = True
                i += 1
                continue

            # Variant name at arm start — KEEP uppercase (parser needs TK_TYPE_IDENT)
            if at_arm_start and tok.kind == "ident" and _is_uppercase_initial(tok.value):
                # Do NOT apply $ prefix or lowering — match arms need uppercase
                result.append(tok)
                at_arm_start = False
                i += 1
                continue

            at_arm_start = False

            # Handle TypeName{...} struct literals within match arm expressions
            if (tok.kind == "ident" and _is_uppercase_initial(tok.value)
                    and tok.value not in PRIMITIVE_TYPES
                    and tok.value not in LEGACY_TYPE_MAP
                    and i + 1 < n and tokens[i + 1].kind == "op"
                    and tokens[i + 1].value == "{"):
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tok.value)))
                i += 1
                result.append(tokens[i])  # {
                i += 1
                i = self._transform_struct_literal_body(tokens, i, result)
                continue

            # Handle [ brackets within match arm expressions
            if tok.kind == "op" and tok.value == "[":
                i = self._transform_bracket(tokens, i, result)
                continue

            result.append(tok)
            i += 1

        return i

    def _transform_bracket(self, tokens: list[Token], i: int,
                            result: list[Token]) -> int:
        """Handle [ ... ] - distinguish array literal, array type, indexing, map."""
        n = len(tokens)

        # Collect all tokens inside the brackets (handling nesting)
        bracket_start = i
        inner_tokens = []
        i += 1  # skip [
        depth = 1
        while i < n and depth > 0:
            if tokens[i].kind == "op" and tokens[i].value == "[":
                depth += 1
            elif tokens[i].kind == "op" and tokens[i].value == "]":
                depth -= 1
                if depth == 0:
                    break
            inner_tokens.append(tokens[i])
            i += 1
        # i now points at closing ]

        bracket_end = i
        i += 1  # skip ]

        # --- Map type: [K:V] in type position ---
        if self._is_map_type(inner_tokens, result):
            self.stats["map_types"] += 1
            result.append(Token("op", "@"))
            result.append(Token("op", "("))
            for t in self._transform_map_type_inner(inner_tokens):
                result.append(t)
            result.append(Token("op", ")"))
            return i

        # --- Map literal: ["key":val;...] or [key:val;...] with string keys ---
        if self._is_map_literal(inner_tokens):
            self.stats["map_literals"] += 1
            result.append(Token("op", "@"))
            result.append(Token("op", "("))
            # Recursively transform inner expressions (may have nested brackets)
            inner_transformed = self._transform_tokens(inner_tokens)
            for t in inner_transformed:
                result.append(t)
            result.append(Token("op", ")"))
            return i

        # --- Array indexing: identifier or ) before [ ---
        # Empty brackets [] after identifier are NOT indexing — they're empty array literals
        if self._is_indexing_context(result) and inner_tokens:
            return self._transform_indexing(inner_tokens, i, result)

        # --- Array type: [T] in type position ---
        if self._is_array_type(inner_tokens, result):
            self.stats["array_types"] += 1
            result.append(Token("op", "@"))
            # Transform the inner type
            for t in self._transform_array_type_inner(inner_tokens):
                result.append(t)
            return i

        # --- Nested array type: [[T]] in type position -> @@T ---
        if self._is_nested_array_type(inner_tokens, result):
            self.stats["array_types"] += 1
            result.append(Token("op", "@"))
            # Recursively transform the inner bracket as a type
            self._transform_bracket_as_type(inner_tokens, result)
            return i

        # --- Array literal: [expr;expr;...] or [] ---
        self.stats["array_literals"] += 1
        result.append(Token("op", "@"))
        result.append(Token("op", "("))
        if inner_tokens:
            inner_transformed = self._transform_tokens(inner_tokens)
            for t in inner_transformed:
                result.append(t)
        result.append(Token("op", ")"))
        return i

    def _is_map_type(self, inner: list[Token], result: list[Token]) -> bool:
        """Check if [K:V] is a map type.

        Map types have exactly: TypeOrPrim : TypeOrPrim
        """
        if len(inner) != 3:
            return False
        if inner[1].kind != "op" or inner[1].value != ":":
            return False
        if inner[0].kind != "ident" or inner[2].kind != "ident":
            return False
        k, v = inner[0].value, inner[2].value
        k_is_type = k.lower() in PRIMITIVE_TYPES or _is_uppercase_initial(k)
        v_is_type = v.lower() in PRIMITIVE_TYPES or _is_uppercase_initial(v)
        return k_is_type and v_is_type

    def _is_map_literal(self, inner: list[Token]) -> bool:
        """Check if bracket contents look like a map literal: "key":val;... ."""
        if not inner:
            return False
        if inner[0].kind == "string" and len(inner) > 1:
            if inner[1].kind == "op" and inner[1].value == ":":
                return True
        return False

    def _is_indexing_context(self, result: list[Token]) -> bool:
        """Check if [ follows an expression (array/string indexing).

        Excludes 'as' keyword (cast) — 'as [u64]' is a type cast, not indexing.
        """
        prev = self._prev_significant(result)
        if prev is None:
            return False
        if prev.kind == "ident":
            # 'as' is a cast keyword — 'as [T]' is a type cast, not indexing
            if prev.value == "as":
                return False
            return True
        if prev.kind == "op" and prev.value in (")", "]"):
            return True
        # String literal followed by [ is string indexing: "hello"[0]
        if prev.kind == "string":
            return True
        return False

    def _is_array_type(self, inner: list[Token], result: list[Token]) -> bool:
        """Check if [T] is an array type (single type name in type position)."""
        if len(inner) != 1:
            return False
        if inner[0].kind != "ident":
            return False
        name = inner[0].value
        if not (name.lower() in PRIMITIVE_TYPES or _is_uppercase_initial(name)):
            return False
        return self._in_type_position(result)

    def _is_nested_array_type(self, inner: list[Token], result: list[Token]) -> bool:
        """Check if [T] is a nested array type like [[u64]]."""
        if not inner:
            return False
        # Must be in type position
        if not self._in_type_position(result):
            return False
        # Inner tokens should start and end with brackets (the inner array type)
        if inner[0].kind == "op" and inner[0].value == "[":
            # Check balanced brackets
            depth = 0
            for t in inner:
                if t.kind == "op" and t.value == "[":
                    depth += 1
                elif t.kind == "op" and t.value == "]":
                    depth -= 1
            return depth == 0
        return False

    def _transform_bracket_as_type(self, inner: list[Token], result: list[Token]) -> None:
        """Transform bracket contents as a type expression (for nested types).

        Called when we know the inner tokens form a [T] type expression.
        """
        # inner tokens = [, type_tokens..., ]
        # Extract the type inside the brackets
        if not inner or inner[0].value != "[":
            return
        type_tokens = inner[1:-1]  # skip [ and ]
        if len(type_tokens) == 1 and type_tokens[0].kind == "ident":
            # Simple type: [i64] -> @i64
            self.stats["array_types"] += 1
            result.append(Token("op", "@"))
            for t in self._transform_array_type_inner(type_tokens):
                result.append(t)
        elif type_tokens and type_tokens[0].kind == "op" and type_tokens[0].value == "[":
            # Nested: [[T]] -> @@T (recursive)
            self.stats["array_types"] += 1
            result.append(Token("op", "@"))
            self._transform_bracket_as_type(type_tokens, result)
        else:
            # Fallback: just emit @(inner)
            result.append(Token("op", "@"))
            result.append(Token("op", "("))
            for t in type_tokens:
                result.append(t)
            result.append(Token("op", ")"))

    def _in_type_position(self, result: list[Token]) -> bool:
        """Determine if we're in a type annotation position.

        Type positions follow:
        - : (parameter/variable type annotation)
        - as (cast expression)
        - = after t (type declaration)
        """
        prev = self._prev_significant(result)
        if prev is None:
            return False
        if prev.kind == "op" and prev.value == ":":
            return True
        # 'as' keyword for cast expressions: val as [T] -> val as @T
        if prev.kind == "ident" and prev.value == "as":
            return True
        if prev.kind == "op" and prev.value == "=":
            j = len(result) - 1
            while j >= 0 and result[j].kind == "ws":
                j -= 1
            if j >= 0 and result[j].value == "=":
                j -= 1
                while j >= 0 and result[j].kind == "ws":
                    j -= 1
                if j >= 0 and result[j].kind == "ident" and result[j].value == "t":
                    return True
        return False

    def _transform_type_expr(self, tokens: list[Token], i: int,
                              result: list[Token]) -> int:
        """Transform a type expression starting at position i."""
        n = len(tokens)
        if i >= n:
            return i

        tok = tokens[i]

        # [Type] or [K:V] - array or map type
        if tok.kind == "op" and tok.value == "[":
            return self._transform_bracket(tokens, i, result)

        # Uppercase type name -> apply sigil
        if tok.kind == "ident" and _is_uppercase_initial(tok.value):
            self.stats["type_sigils"] += 1
            result.append(Token("ident", _sigil_type(tok.value)))
            i += 1
            return i

        # Primitive type
        if tok.kind == "ident" and tok.value.lower() in PRIMITIVE_TYPES:
            result.append(tok)
            i += 1
            return i

        # !ErrorType
        if tok.kind == "op" and tok.value == "!":
            result.append(tok)
            i += 1
            if i < n and tokens[i].kind == "ident" and _is_uppercase_initial(tokens[i].value):
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tokens[i].value)))
                i += 1
            return i

        # Anything else
        result.append(tok)
        i += 1
        return i

    def _transform_array_type_inner(self, inner: list[Token]) -> list[Token]:
        """Transform the type inside [T] -> @T for array type.

        [i64] -> @i64   (primitive stays as-is)
        [Str] -> @Str   (Str is a special scalar, stays uppercase)
        [Byte] -> @Byte
        [Point] -> @$point  (user type gets $)
        """
        out = []
        for tok in inner:
            if tok.kind == "ident":
                if tok.value in SCALAR_TYPES:
                    out.append(tok)  # Str, Byte stay as-is
                elif tok.value in LEGACY_TYPE_MAP:
                    out.append(Token("ident", LEGACY_TYPE_MAP[tok.value]))
                elif _is_uppercase_initial(tok.value) and tok.value.lower() not in PRIMITIVE_TYPES:
                    self.stats["type_sigils"] += 1
                    out.append(Token("ident", "$" + tok.value.lower()))
                else:
                    out.append(tok)
            else:
                out.append(tok)
        return out

    def _transform_map_type_inner(self, inner: list[Token]) -> list[Token]:
        """Transform types inside [K:V] -> @($k:$v) for map type.

        Inside @() map types, both key and value get $ prefix.
        [i64:Str] -> @($i64:$str)
        [Str:Point] -> @($str:$point)
        """
        out = []
        for tok in inner:
            if tok.kind == "ident":
                out.append(Token("ident", _sigil_type_for_map(tok.value)))
            elif tok.kind == "op" and tok.value == ":":
                out.append(tok)
            else:
                out.append(tok)
        return out

    def _transform_indexing(self, inner: list[Token], next_i: int,
                             result: list[Token]) -> int:
        """Transform array/string indexing: arr[X] -> arr.get(X).

        ALL indexing uses .get() form in default syntax. Never dot-index.
        """
        self.stats["array_indexing"] += 1
        result.append(Token("op", "."))
        result.append(Token("ident", "get"))
        result.append(Token("op", "("))
        # Recursively transform inner expression (may have nested brackets)
        inner_transformed = self._transform_tokens(inner)
        for t in inner_transformed:
            result.append(t)
        result.append(Token("op", ")"))
        return next_i

    def _in_type_annotation_context(self, result: list[Token]) -> bool:
        """Check if a colon we just emitted is a type annotation colon.

        Type annotation colons appear after:
        - Parameter names in function signatures: (name:Type)
        - Variable declarations: let name:Type
        - Return type: ):Type
        """
        j = len(result) - 2  # skip the colon itself
        while j >= 0 and result[j].kind == "ws":
            j -= 1
        if j < 0:
            return False

        prev = result[j]
        if prev.kind == "ident":
            return True
        if prev.kind == "op" and prev.value == ")":
            return True
        if prev.kind == "op" and prev.value == "]":
            return True
        return False

    def _is_return_type_context(self, tokens: list[Token], i: int) -> bool:
        """Check if ) at position i is followed by : for a return type."""
        n = len(tokens)
        if i + 1 >= n:
            return False
        if tokens[i + 1].kind != "op" or tokens[i + 1].value != ":":
            return False

        if i + 2 < n:
            next_tok = tokens[i + 2]
            if next_tok.kind == "ident" and (next_tok.value.lower() in PRIMITIVE_TYPES
                                              or _is_uppercase_initial(next_tok.value)):
                return True
            if next_tok.kind == "op" and next_tok.value == "[":
                return True
        return False

    def _inside_match_or_sum(self, result: list[Token]) -> bool:
        """Safety check for match/sum context."""
        return False

    def _prev_significant(self, result: list[Token]) -> Optional[Token]:
        """Get the previous non-whitespace token from result."""
        for j in range(len(result) - 1, -1, -1):
            if result[j].kind != "ws":
                return result[j]
        return None


# ---------------------------------------------------------------------------
# Second-pass regex fixups for issues the token-based transformer misses
# ---------------------------------------------------------------------------

def _regex_fixups(source: str) -> str:
    """Apply regex-based fixups for patterns the tokenizer misses.

    These catch edge cases the token-walking approach cannot easily handle.
    """
    # Fix: uppercase standalone type references that got missed
    # e.g., "Str" appearing as a type name without { following it
    # Pattern: (:Str; or :Str) or :Str{ etc. (already handled by token pass)

    # Fix: remaining legacy type patterns (Bool -> bool, but NOT Str/Byte)
    for legacy, prim in LEGACY_TYPE_MAP.items():
        # Replace standalone legacy type after colon (type annotation)
        source = re.sub(rf':({legacy})\b(?!\w)', lambda m: ':' + prim, source)
        # Replace in function return type
        source = re.sub(rf'\):({legacy})\b(?!\w)', lambda m: '):' + prim, source)

    # Fix: .0, .1, .2 etc. dot-index patterns -> .get(N)
    # But NOT after "mut" (mut.0 is mutable init, not indexing)
    # And NOT in decimal numbers like 3.14
    # Pattern: identifier.digit but not mut.digit
    source = re.sub(
        r'(?<!mut)(?<=[a-z_)])\.(\d+)(?![.\d])',
        lambda m: '.get(' + m.group(1) + ')',
        source
    )

    return source


# ---------------------------------------------------------------------------
# Corpus transformation
# ---------------------------------------------------------------------------

def transform_corpus_dir(corpus_dir: str, output_dir: str,
                          validate: bool = False, tkc_path: str = None,
                          dry_run: bool = False, show_stats: bool = False,
                          max_entries: int = None,
                          sample_size: int = 0):
    """Transform all JSON files in corpus directory structure."""
    transformer_stats = {
        "decl_keywords": 0,
        "type_sigils": 0,
        "array_literals": 0,
        "array_types": 0,
        "array_indexing": 0,
        "map_types": 0,
        "map_literals": 0,
        "sum_variants": 0,
    }
    total = 0
    errors = []
    validated = 0
    valid = 0
    failure_categories = {}

    corpus_path = Path(corpus_dir)
    output_path = Path(output_dir) if output_dir else None

    # Collect all JSON files
    json_files = sorted(corpus_path.rglob("*.json"))
    json_files = [f for f in json_files
                  if f.name not in ("manifest.json", "schema.json")]

    if max_entries:
        json_files = json_files[:max_entries]

    for json_file in json_files:
        total += 1
        try:
            with open(json_file) as f:
                entry = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            errors.append(f"{json_file}: {e}")
            continue

        if "tk_source" not in entry:
            continue

        original = entry["tk_source"]

        # Apply token-based transformation
        t = ToDefaultSyntaxTransformer()
        transformed = t.transform(original)

        # Apply regex fixups for edge cases
        transformed = _regex_fixups(transformed)

        # Accumulate stats
        for k, v in t.stats.items():
            transformer_stats[k] = transformer_stats.get(k, 0) + v

        entry["tk_source"] = transformed
        entry["syntax"] = "default"

        # Validate with tkc if requested
        if validate and tkc_path:
            ok, err_codes = _validate_with_tkc(transformed, tkc_path)
            validated += 1
            if ok:
                valid += 1
            else:
                for code in err_codes:
                    failure_categories[code] = failure_categories.get(code, 0) + 1
                if not err_codes:
                    failure_categories["unknown"] = failure_categories.get("unknown", 0) + 1

        if not dry_run and output_path:
            rel = json_file.relative_to(corpus_path)
            out_file = output_path / rel
            out_file.parent.mkdir(parents=True, exist_ok=True)
            with open(out_file, "w") as f:
                json.dump(entry, f, indent=2, ensure_ascii=False)

        if dry_run and total <= 10:
            print(f"--- {json_file.name} ---")
            print(f"  BEFORE: {original[:200]}")
            print(f"  AFTER:  {transformed[:200]}")
            print()

        if total % 5000 == 0:
            sys.stderr.write(f"\r  Processed {total}/{len(json_files)}...")
            sys.stderr.flush()

    if not dry_run and output_path:
        print(f"\nWrote {total} entries to {output_path}")

    if show_stats:
        print(f"\nTransformation statistics:")
        print(f"  Total entries processed: {total}")
        for k, v in transformer_stats.items():
            print(f"  {k}: {v}")
        if validate:
            pct = (valid / validated * 100) if validated else 0
            print(f"\n  Validated: {validated}")
            print(f"  Passed:    {valid} ({pct:.1f}%)")
            print(f"  Failed:    {validated - valid}")
            if failure_categories:
                print(f"\n  Failure categories:")
                for code, count in sorted(failure_categories.items(),
                                           key=lambda x: -x[1]):
                    print(f"    {code}: {count}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors[:20]:
            print(f"  {e}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")

    return valid, validated, total, failure_categories


def transform_to_jsonl(corpus_dir: str, output_file: str,
                        validate: bool = False, tkc_path: str = None,
                        show_stats: bool = False, max_entries: int = None,
                        diagnose: int = 0):
    """Transform corpus directory and write to JSONL file."""
    transformer_stats = {
        "decl_keywords": 0,
        "type_sigils": 0,
        "array_literals": 0,
        "array_types": 0,
        "array_indexing": 0,
        "map_types": 0,
        "map_literals": 0,
        "sum_variants": 0,
    }
    total = 0
    written = 0
    errors = []
    validated = 0
    valid = 0
    failure_categories = {}

    corpus_path = Path(corpus_dir)

    # Collect all JSON files
    json_files = sorted(corpus_path.rglob("*.json"))
    json_files = [f for f in json_files
                  if f.name not in ("manifest.json", "schema.json")]

    if max_entries:
        json_files = json_files[:max_entries]

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)

    with open(output_file, "w") as out:
        for json_file in json_files:
            total += 1
            try:
                with open(json_file) as f:
                    entry = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                errors.append(f"{json_file}: {e}")
                continue

            if "tk_source" not in entry:
                continue

            original = entry["tk_source"]

            # Apply token-based transformation
            t = ToDefaultSyntaxTransformer()
            transformed = t.transform(original)

            # Apply regex fixups
            transformed = _regex_fixups(transformed)

            # Accumulate stats
            for k, v in t.stats.items():
                transformer_stats[k] = transformer_stats.get(k, 0) + v

            entry["tk_source"] = transformed
            entry["syntax"] = "default"

            # Validate with tkc if requested
            if validate and tkc_path:
                ok, err_codes = _validate_with_tkc(transformed, tkc_path)
                validated += 1
                if ok:
                    valid += 1
                else:
                    for code in err_codes:
                        failure_categories[code] = failure_categories.get(code, 0) + 1
                    if not err_codes:
                        failure_categories["unknown"] = failure_categories.get("unknown", 0) + 1
                    # Print diagnosis samples
                    if diagnose > 0 and (validated - valid) <= diagnose:
                        print(f"\n--- FAIL {entry.get('id','?')} [{','.join(err_codes)}] ---")
                        print(f"  ORIG:  {original[:250]}")
                        print(f"  TRANS: {transformed[:250]}")

            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1

            if total % 5000 == 0:
                sys.stderr.write(f"\r  Processed {total}/{len(json_files)}...")
                sys.stderr.flush()

    print(f"\nWrote {written} entries to {output_file}")

    if show_stats:
        print(f"\nTransformation statistics:")
        print(f"  Total entries processed: {total}")
        print(f"  Written: {written}")
        for k, v in transformer_stats.items():
            print(f"  {k}: {v}")
        if validate:
            pct = (valid / validated * 100) if validated else 0
            print(f"\n  Validated: {validated}")
            print(f"  Passed:    {valid} ({pct:.1f}%)")
            print(f"  Failed:    {validated - valid}")
            if failure_categories:
                print(f"\n  Failure categories:")
                for code, count in sorted(failure_categories.items(),
                                           key=lambda x: -x[1]):
                    print(f"    {code}: {count}")

    return valid, validated, total, failure_categories


def _validate_with_tkc(source: str, tkc_path: str) -> tuple[bool, list[str]]:
    """Validate transformed source with tkc --check --diag-json."""
    import tempfile
    try:
        fd, path = tempfile.mkstemp(suffix=".tk")
        with os.fdopen(fd, "w") as f:
            f.write(source)

        proc = subprocess.run(
            [tkc_path, "--check", "--diag-json", path],
            capture_output=True, text=True, timeout=10,
        )
        error_codes = []
        if proc.returncode != 0:
            for line in proc.stdout.splitlines() + proc.stderr.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        diag = json.loads(line)
                        if "code" in diag:
                            error_codes.append(diag["code"])
                    except json.JSONDecodeError:
                        pass
            # If no diag-json codes found, try to extract from plain text
            if not error_codes:
                all_output = proc.stdout + proc.stderr
                # Look for E#### patterns
                import re as _re
                codes = _re.findall(r'\b(E\d{4})\b', all_output)
                error_codes = codes if codes else ["exit_" + str(proc.returncode)]
        return proc.returncode == 0, error_codes
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return False, [f"exec_error:{exc}"]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _find_tkc() -> Optional[str]:
    """Find the tkc binary."""
    candidates = [
        os.path.expanduser("~/tk/toke/tkc"),
        os.path.expanduser("~/tk/tkc/tkc"),
        os.path.expanduser("~/tk/tkc/bin/tkc"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transform legacy toke corpus to default (56-char) syntax"
    )
    parser.add_argument("--corpus-dir",
                        default=os.path.expanduser("~/tk/toke-corpus/corpus"),
                        help="Input corpus directory (default: ~/tk/toke-corpus/corpus)")
    parser.add_argument("--output-dir",
                        help="Output corpus directory (JSON files)")
    parser.add_argument("--output-jsonl",
                        default=os.path.expanduser("~/tk/toke-corpus/data/corpus_default.jsonl"),
                        help="Output JSONL file (default: ~/tk/toke-corpus/data/corpus_default.jsonl)")
    parser.add_argument("--validate", action="store_true",
                        help="Validate each entry with tkc --check")
    parser.add_argument("--tkc", default=None, help="Path to tkc binary")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show first 10 transformations without writing")
    parser.add_argument("--stats", action="store_true",
                        help="Print transformation statistics")
    parser.add_argument("--max", type=int, default=None,
                        help="Process at most N entries")
    parser.add_argument("--single", help="Transform a single tk_source string")
    parser.add_argument("--diagnose", type=int, default=0,
                        help="Show N failure samples with full error output")
    parser.add_argument("--validate-jsonl",
                        help="Validate a random sample from an existing JSONL file")
    parser.add_argument("--sample-size", type=int, default=600,
                        help="Number of entries to randomly sample for validation")

    args = parser.parse_args()

    tkc_path = args.tkc or _find_tkc()

    if args.validate_jsonl:
        import random as _random
        _random.seed(42)
        entries = []
        with open(args.validate_jsonl) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        sample = _random.sample(entries, min(args.sample_size, len(entries)))
        if not tkc_path:
            print("Error: tkc binary not found. Use --tkc to specify path.")
            sys.exit(1)

        passed = 0
        failed = 0
        fail_codes = {}
        fail_samples = []
        for entry in sample:
            src = entry.get("tk_source", "")
            ok, err_codes = _validate_with_tkc(src, tkc_path)
            if ok:
                passed += 1
            else:
                failed += 1
                for code in err_codes:
                    fail_codes[code] = fail_codes.get(code, 0) + 1
                if len(fail_samples) < args.diagnose:
                    fail_samples.append((entry.get("id", "?"), src[:200], err_codes))

        total = passed + failed
        pct = passed / total * 100 if total else 0
        print(f"Sample: {total} entries (random from {len(entries)} total)")
        print(f"Passed: {passed} ({pct:.1f}%)")
        print(f"Failed: {failed}")
        if fail_codes:
            print(f"Failure codes:")
            for c, n in sorted(fail_codes.items(), key=lambda x: -x[1]):
                print(f"  {c}: {n}")
        for eid, src, codes in fail_samples:
            print(f"\n--- FAIL {eid} [{','.join(codes)}] ---")
            print(f"  SRC: {src}")
        sys.exit(0)

    if args.single:
        t = ToDefaultSyntaxTransformer()
        transformed = t.transform(args.single)
        transformed = _regex_fixups(transformed)
        print(f"IN:  {args.single}")
        print(f"OUT: {transformed}")
        if args.stats:
            for k, v in t.stats.items():
                if v > 0:
                    print(f"  {k}: {v}")
        sys.exit(0)

    if args.output_dir:
        valid, validated, total, cats = transform_corpus_dir(
            args.corpus_dir,
            args.output_dir,
            validate=args.validate,
            tkc_path=tkc_path,
            dry_run=args.dry_run,
            show_stats=args.stats,
            max_entries=args.max,
        )
    else:
        valid, validated, total, cats = transform_to_jsonl(
            args.corpus_dir,
            args.output_jsonl,
            validate=args.validate,
            tkc_path=tkc_path,
            show_stats=args.stats,
            max_entries=args.max,
            diagnose=args.diagnose,
        )

    sys.exit(0)
