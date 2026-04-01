#!/usr/bin/env python3
"""Transform Phase 1 toke source to Phase 2 syntax.

Transformation rules:
1. Declaration keywords: M= -> m=, F= -> f=, I= -> i=, T= -> t=, C= -> c=
2. Type name sigils: Uppercase-initial identifiers in type positions get $ prefix and lowercased
3. Array literals: [expr;expr;...] -> @(expr;expr;...)
4. Array type notation: [T] in type position -> @T or @($type)
5. Array indexing: arr[0] -> arr.0, arr[i] -> arr.get(i)
6. Map types: [K:V] in type position -> $(K:V)
7. Map literals: ["a":1;"b":2] -> $("a":1;"b":2)
8. Sum type variants: uppercase variant names get $ prefix in type declarations
"""

import json
import sys
import os
import subprocess
import argparse
import re
from pathlib import Path
from typing import Optional


# Primitive / lowercase types that must NOT get a $ sigil
PRIMITIVE_TYPES = frozenset({
    "i64", "u64", "f64", "bool", "void",
})

# toke keywords that are already lowercase and must not be touched
KEYWORDS = frozenset({
    "if", "el", "lp", "br", "let", "mut", "as", "true", "false",
})


def _is_uppercase_initial(name: str) -> bool:
    """Check if a name starts with an uppercase letter (A-Z)."""
    return bool(name) and name[0].isupper()


def _sigil_type(name: str) -> str:
    """Convert an uppercase type name to $lowercase form.

    Str -> $str, Point -> $point, ApiErr -> $apierr
    Primitives are returned unchanged.
    """
    if name in PRIMITIVE_TYPES:
        return name
    if _is_uppercase_initial(name):
        return "$" + name.lower()
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


class Phase1ToPhase2Transformer:
    """Mechanically transforms Phase 1 toke source to Phase 2."""

    def __init__(self):
        self.stats = {
            "decl_keywords": 0,
            "type_sigils": 0,
            "array_literals": 0,
            "array_types": 0,
            "array_indexing_const": 0,
            "array_indexing_var": 0,
            "map_types": 0,
            "map_literals": 0,
            "sum_variants": 0,
        }

    def transform(self, source: str) -> str:
        """Transform a Phase 1 toke source string to Phase 2."""
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
            # After t= we already lowered, now handle the type name and body
            if (tok.kind == "ident" and _is_uppercase_initial(tok.value)
                    and self._in_type_decl_context(result)):
                # This is the type name in T=TypeName{...}
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tok.value)))
                # Check if next is { - if so, transform the sum type body
                if i + 1 < n and tokens[i + 1].kind == "op" and tokens[i + 1].value == "{":
                    i += 1
                    result.append(tokens[i])  # the {
                    i += 1
                    # Transform sum type variants inside the braces
                    i = self._transform_sum_type_body(tokens, i, result)
                    continue
                i += 1
                continue

            # Struct/error literal: TypeName{field:val;...} in expression context
            if (tok.kind == "ident" and _is_uppercase_initial(tok.value)
                    and tok.value not in PRIMITIVE_TYPES
                    and i + 1 < n and tokens[i + 1].kind == "op"
                    and tokens[i + 1].value == "{"):
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tok.value)))
                i += 1
                result.append(tokens[i])  # the {
                i += 1
                # Transform variant/field names inside the literal
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
                # Transform match arms - variant names get $ prefix
                i = self._transform_match_body(tokens, i, result)
                continue

            # Error union: !TypeName in return type position
            if (tok.kind == "op" and tok.value == "!"
                    and i + 1 < n and tokens[i + 1].kind == "ident"
                    and _is_uppercase_initial(tokens[i + 1].value)
                    and tokens[i + 1].value not in PRIMITIVE_TYPES):
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
            # and return type after ):
            if (tok.kind == "op" and tok.value == ":"
                    and not self._inside_match_or_sum(result)):
                result.append(tok)
                i += 1
                # Check if next token is an uppercase type name (not in bracket)
                if (i < n and tokens[i].kind == "ident"
                        and _is_uppercase_initial(tokens[i].value)
                        and tokens[i].value not in PRIMITIVE_TYPES):
                    # But only if we're in type annotation context
                    # (after param name, after ), etc.)
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
                # Transform the return type
                i = self._transform_type_expr(tokens, i, result)
                continue

            result.append(tok)
            i += 1

        return result

    def _at_statement_boundary(self, result: list[Token]) -> bool:
        """Check if we're at a statement boundary (start or after ;)."""
        if not result:
            return True
        # Walk back past whitespace
        for j in range(len(result) - 1, -1, -1):
            if result[j].kind != "ws":
                return result[j].kind == "op" and result[j].value == ";"
        return True

    def _in_type_decl_context(self, result: list[Token]) -> bool:
        """Check if we just saw t= (or T= before lowering -> now t=)."""
        # Look back: should see = then t (lowercase, already transformed)
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

    def _transform_sum_type_body(self, tokens: list[Token], i: int,
                                  result: list[Token]) -> int:
        """Transform variant names inside a type declaration body.

        T=Shape{Circle:f64;Rect:Point} -> t=$shape{$circle:f64;$rect:$point}
        """
        n = len(tokens)
        depth = 1
        at_variant_start = True

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
                at_variant_start = True
                i += 1
                continue

            # Variant name at start position
            if at_variant_start and tok.kind == "ident":
                if _is_uppercase_initial(tok.value) and tok.value not in PRIMITIVE_TYPES:
                    self.stats["sum_variants"] += 1
                    result.append(Token("ident", "$" + tok.value.lower()))
                elif tok.value[0].islower():
                    # lowercase field name in struct-like type (e.g. Pair{found:bool})
                    result.append(tok)
                else:
                    result.append(tok)
                at_variant_start = False
                i += 1
                continue

            # Type after colon in variant definition
            if tok.kind == "op" and tok.value == ":":
                result.append(tok)
                i += 1
                # Transform the variant's type
                i = self._transform_type_expr(tokens, i, result)
                at_variant_start = False
                continue

            result.append(tok)
            i += 1

        return i

    def _transform_struct_literal_body(self, tokens: list[Token], i: int,
                                        result: list[Token]) -> int:
        """Transform variant/field names inside a struct/error literal.

        MathErr{DivByZero:true;Overflow:""} -> $matherr{$divbyzero:true;$overflow:""}
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

            # Field/variant name at start position
            if at_field_start and tok.kind == "ident" and _is_uppercase_initial(tok.value):
                self.stats["sum_variants"] += 1
                result.append(Token("ident", "$" + tok.value.lower()))
                at_field_start = False
                i += 1
                continue

            at_field_start = False

            # Recursively handle nested constructs (e.g., array literals in values)
            if tok.kind == "op" and tok.value == "[":
                i = self._transform_bracket(tokens, i, result)
                continue

            result.append(tok)
            i += 1

        return i

    def _transform_match_body(self, tokens: list[Token], i: int,
                               result: list[Token]) -> int:
        """Transform match arms: Ok:v expr -> $ok:v expr."""
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

            # Variant name at arm start
            if at_arm_start and tok.kind == "ident" and _is_uppercase_initial(tok.value):
                self.stats["sum_variants"] += 1
                result.append(Token("ident", "$" + tok.value.lower()))
                at_arm_start = False
                i += 1
                continue

            at_arm_start = False

            # Handle TypeName{...} struct literals within match arm expressions
            if (tok.kind == "ident" and _is_uppercase_initial(tok.value)
                    and tok.value not in PRIMITIVE_TYPES
                    and i + 1 < n and tokens[i + 1].kind == "op"
                    and tokens[i + 1].value == "{"):
                self.stats["type_sigils"] += 1
                result.append(Token("ident", _sigil_type(tok.value)))
                i += 1
                result.append(tokens[i])  # {
                i += 1
                depth += 1
                # The struct literal body will be handled by subsequent iterations
                # with at_field_start-like logic... but we need the struct literal
                # body handler. Let's use it directly.
                depth -= 1  # undo depth increment, the method handles it
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

        # Determine context: what's before the [?
        prev = self._prev_significant(result)

        # --- Map type: [K:V] in type position ---
        # Pattern: contains exactly one colon with ident:ident (types on both sides)
        if self._is_map_type(inner_tokens, result):
            self.stats["map_types"] += 1
            result.append(Token("op", "$"))
            result.append(Token("op", "("))
            for t in self._transform_type_list(inner_tokens, is_map=True):
                result.append(t)
            result.append(Token("op", ")"))
            return i

        # --- Map literal: ["key":val;...] ---
        if self._is_map_literal(inner_tokens):
            self.stats["map_literals"] += 1
            result.append(Token("op", "$"))
            result.append(Token("op", "("))
            for t in inner_tokens:
                result.append(t)
            result.append(Token("op", ")"))
            return i

        # --- Array indexing: identifier or ) before [ ---
        if self._is_indexing_context(result):
            return self._transform_indexing(inner_tokens, i, result)

        # --- Array type: [T] in type position (single type name) ---
        if self._is_array_type(inner_tokens, result):
            self.stats["array_types"] += 1
            result.append(Token("op", "@"))
            # Transform the inner type
            for t in self._transform_type_list(inner_tokens, is_map=False):
                result.append(t)
            return i

        # --- Array literal: [expr;expr;...] or [] ---
        self.stats["array_literals"] += 1
        result.append(Token("op", "@"))
        result.append(Token("op", "("))
        # Recursively transform the inner expressions
        if inner_tokens:
            inner_transformed = self._transform_tokens(inner_tokens)
            for t in inner_transformed:
                result.append(t)
        result.append(Token("op", ")"))
        return i

    def _is_map_type(self, inner: list[Token], result: list[Token]) -> bool:
        """Check if [K:V] is a map type / map constructor.

        Map types have exactly: TypeOrPrim : TypeOrPrim
        This pattern is always a map (never an array literal), because
        array literals use semicolons not colons between elements.
        """
        if len(inner) != 3:
            return False
        if inner[1].kind != "op" or inner[1].value != ":":
            return False
        if inner[0].kind != "ident" or inner[2].kind != "ident":
            return False
        # Both sides must be type names (primitive or uppercase-initial)
        k, v = inner[0].value, inner[2].value
        k_is_type = k in PRIMITIVE_TYPES or _is_uppercase_initial(k)
        v_is_type = v in PRIMITIVE_TYPES or _is_uppercase_initial(v)
        return k_is_type and v_is_type

    def _is_map_literal(self, inner: list[Token]) -> bool:
        """Check if bracket contents look like a map literal: "key":val;... ."""
        if not inner:
            return False
        # Map literal starts with a string key followed by colon
        if inner[0].kind == "string" and len(inner) > 1:
            if inner[1].kind == "op" and inner[1].value == ":":
                return True
        return False

    def _is_indexing_context(self, result: list[Token]) -> bool:
        """Check if [ follows an identifier, ), or ] (array indexing)."""
        prev = self._prev_significant(result)
        if prev is None:
            return False
        if prev.kind == "ident":
            return True
        if prev.kind == "op" and prev.value in (")", "]"):
            return True
        # After a number could be indexing in some edge cases but
        # in toke, number[x] doesn't happen - it's always ident[x]
        return False

    def _is_array_type(self, inner: list[Token], result: list[Token]) -> bool:
        """Check if [T] is an array type (single type name in type position)."""
        if len(inner) != 1:
            return False
        if inner[0].kind != "ident":
            return False
        name = inner[0].value
        if not (name in PRIMITIVE_TYPES or _is_uppercase_initial(name)):
            return False
        return self._in_type_position(result)

    def _in_type_position(self, result: list[Token]) -> bool:
        """Determine if we're in a type annotation position.

        Type positions follow:
        - : (parameter/variable type annotation)
        - ): (return type)
        - mut. (mutable init with type)
        - = after t/T (type declaration)
        """
        prev = self._prev_significant(result)
        if prev is None:
            return False
        if prev.kind == "op" and prev.value == ":":
            return True
        if prev.kind == "op" and prev.value == ")":
            # Check if the one before ) and : pattern exists
            # Actually ): is return type - but [ comes after :
            # Let me check for the full pattern
            return False
        # After = in type declaration
        if prev.kind == "op" and prev.value == "=":
            # Check if before = is t (type decl)
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
        """Transform a type expression starting at position i.

        Handles: TypeName, [Type], [K:V], !TypeName
        """
        n = len(tokens)
        if i >= n:
            return i

        tok = tokens[i]

        # [Type] or [K:V] - array or map type
        if tok.kind == "op" and tok.value == "[":
            return self._transform_bracket(tokens, i, result)

        # Uppercase type name
        if tok.kind == "ident" and _is_uppercase_initial(tok.value) and tok.value not in PRIMITIVE_TYPES:
            self.stats["type_sigils"] += 1
            result.append(Token("ident", _sigil_type(tok.value)))
            i += 1
            return i

        # Primitive type
        if tok.kind == "ident" and tok.value in PRIMITIVE_TYPES:
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

        # Anything else (e.g., lowercase type)
        result.append(tok)
        i += 1
        return i

    def _transform_type_list(self, inner: list[Token], is_map: bool) -> list[Token]:
        """Transform types inside array type or map type notation."""
        out = []
        for tok in inner:
            if tok.kind == "ident" and _is_uppercase_initial(tok.value) and tok.value not in PRIMITIVE_TYPES:
                self.stats["type_sigils"] += 1
                out.append(Token("ident", _sigil_type(tok.value)))
            else:
                out.append(tok)
        return out

    def _transform_indexing(self, inner: list[Token], next_i: int,
                             result: list[Token]) -> int:
        """Transform array/string indexing: arr[0]->arr.0, arr[i]->arr.get(i)."""
        # Check if index is a simple numeric constant
        if self._is_constant_index(inner):
            self.stats["array_indexing_const"] += 1
            # arr[0] -> arr.0
            result.append(Token("op", "."))
            for t in inner:
                result.append(t)
        else:
            self.stats["array_indexing_var"] += 1
            # arr[i] -> arr.get(i)
            # But inner may contain nested brackets that need transformation
            result.append(Token("op", "."))
            result.append(Token("ident", "get"))
            result.append(Token("op", "("))
            inner_transformed = self._transform_tokens(inner)
            for t in inner_transformed:
                result.append(t)
            result.append(Token("op", ")"))
        return next_i

    def _is_constant_index(self, inner: list[Token]) -> bool:
        """Check if the index expression is a simple numeric constant.

        Constant: just a number, or number with 'as' cast (e.g. 0 as u64).
        Variable: anything with identifiers (other than 'as' keyword).
        """
        # Filter out whitespace (spaces tokenized as op " ")
        significant = [t for t in inner
                       if not (t.kind == "ws" or (t.kind == "op" and t.value == " "))]
        if not significant:
            return False

        # Simple number: [0], [1], etc.
        if len(significant) == 1 and significant[0].kind == "num":
            return True

        # Number with cast: [0 as u64]
        if (len(significant) == 3
                and significant[0].kind == "num"
                and significant[1].kind == "ident" and significant[1].value == "as"
                and significant[2].kind == "ident" and significant[2].value in PRIMITIVE_TYPES):
            return True

        return False

    def _in_type_annotation_context(self, result: list[Token]) -> bool:
        """Check if a colon we just emitted is a type annotation colon.

        Type annotation colons appear after:
        - Parameter names in function signatures: (name:Type)
        - Variable declarations: let name:Type (rare in toke, usually inferred)
        - Return type: ):Type
        """
        # The colon is the last token in result
        # Look at what's before the colon
        j = len(result) - 2  # skip the colon itself
        while j >= 0 and result[j].kind == "ws":
            j -= 1
        if j < 0:
            return False

        prev = result[j]
        # After identifier (parameter name)
        if prev.kind == "ident":
            return True
        # After ) for return type
        if prev.kind == "op" and prev.value == ")":
            return True
        # After ] for return type like ):[i64]
        if prev.kind == "op" and prev.value == "]":
            return True
        return False

    def _is_return_type_context(self, tokens: list[Token], i: int) -> bool:
        """Check if ) at position i is followed by : for a return type.

        Only true for function signature return types, not if/el/lp parentheses.
        """
        # Need to verify this is a function signature's closing paren
        # Heuristic: walk backward to find F= or f= pattern
        # Actually, simpler: just check that ): follows and there's no { before this )
        # In toke, return types only appear in F=name(...):RetType{...}
        # But also in type annotations within parameters, e.g., (name:Type;name2:Type):RetType
        # The key insight: if ) is followed by : and the next token after : is a type,
        # it's a return type. But we should avoid matching things like if(x):... which
        # doesn't exist in toke syntax.
        # In toke, only function signatures have ): pattern.
        n = len(tokens)
        if i + 1 >= n:
            return False
        if tokens[i + 1].kind != "op" or tokens[i + 1].value != ":":
            return False

        # Check what's after the colon - must be a type expression
        if i + 2 < n:
            next_tok = tokens[i + 2]
            if next_tok.kind == "ident" and (next_tok.value in PRIMITIVE_TYPES
                                              or _is_uppercase_initial(next_tok.value)):
                return True
            if next_tok.kind == "op" and next_tok.value == "[":
                return True
        return False

    def _inside_match_or_sum(self, result: list[Token]) -> bool:
        """Check if we're currently inside a match arm or sum type variant.

        Used to avoid treating variant:type colons as type annotations
        when we've already handled them specially.
        """
        # This is tricky - we avoid this by handling match/sum bodies in
        # their own methods. This method is a safety check.
        return False

    def _prev_significant(self, result: list[Token]) -> Optional[Token]:
        """Get the previous non-whitespace token from result."""
        for j in range(len(result) - 1, -1, -1):
            if result[j].kind != "ws":
                return result[j]
        return None


def transform_jsonl(input_path: str, output_path: str, validate: bool = False,
                    tkc_path: str = None, dry_run: bool = False,
                    show_stats: bool = False):
    """Transform all entries in a JSONL file."""
    transformer = Phase1ToPhase2Transformer()
    entries = []
    errors = []
    total = 0
    validated = 0
    valid = 0

    with open(input_path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"Line {lineno}: JSON parse error: {e}")
                continue

            original = entry.get("tk_source", "")
            transformed = transformer.transform(original)
            entry["tk_source"] = transformed

            if validate and tkc_path:
                ok = _validate_with_tkc(transformed, tkc_path)
                validated += 1
                if ok:
                    valid += 1
                else:
                    errors.append(f"Line {lineno} ({entry.get('id', '?')}): validation failed")

            entries.append(entry)

            if dry_run and total <= 10:
                print(f"--- Entry {entry.get('id', '?')} ---")
                print(f"  BEFORE: {original[:200]}")
                print(f"  AFTER:  {transformed[:200]}")
                print()

    if not dry_run and output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"Wrote {len(entries)} entries to {output_path}")

    if show_stats:
        print(f"\nTransformation statistics:")
        print(f"  Total entries: {total}")
        for k, v in transformer.stats.items():
            print(f"  {k}: {v}")
        if validate:
            print(f"  Validated: {validated}, Valid: {valid}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors[:20]:
            print(f"  {e}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")

    return len(errors) == 0


def transform_corpus_dir(corpus_dir: str, output_dir: str, validate: bool = False,
                          tkc_path: str = None, dry_run: bool = False,
                          show_stats: bool = False):
    """Transform all JSON files in corpus directory structure."""
    transformer = Phase1ToPhase2Transformer()
    total = 0
    errors = []
    validated = 0
    valid = 0

    corpus_path = Path(corpus_dir)
    output_path = Path(output_dir) if output_dir else None

    for json_file in sorted(corpus_path.rglob("*.json")):
        total += 1
        try:
            with open(json_file) as f:
                entry = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            errors.append(f"{json_file}: {e}")
            continue

        original = entry.get("tk_source", "")
        transformed = transformer.transform(original)
        entry["tk_source"] = transformed

        if validate and tkc_path:
            ok = _validate_with_tkc(transformed, tkc_path)
            validated += 1
            if ok:
                valid += 1
            else:
                errors.append(f"{json_file.name} ({entry.get('id', '?')}): validation failed")

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

    if not dry_run and output_path:
        print(f"Wrote {total} entries to {output_path}")

    if show_stats:
        print(f"\nTransformation statistics:")
        print(f"  Total entries: {total}")
        for k, v in transformer.stats.items():
            print(f"  {k}: {v}")
        if validate:
            print(f"  Validated: {validated}, Valid: {valid}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors[:20]:
            print(f"  {e}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")

    return len(errors) == 0


def _validate_with_tkc(source: str, tkc_path: str) -> bool:
    """Validate transformed source with tkc --profile2 --check."""
    try:
        proc = subprocess.run(
            [tkc_path, "--profile2", "--check", "-"],
            input=source, capture_output=True, text=True, timeout=10
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _find_tkc() -> Optional[str]:
    """Find the tkc binary."""
    candidates = [
        os.path.expanduser("~/tk/tkc/tkc"),
        os.path.expanduser("~/tk/tkc/bin/tkc"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transform Phase 1 toke corpus to Phase 2 syntax"
    )
    parser.add_argument("--input-jsonl", help="Input JSONL file path")
    parser.add_argument("--output-jsonl", help="Output JSONL file path")
    parser.add_argument("--corpus-dir", help="Input corpus directory")
    parser.add_argument("--output-dir", help="Output corpus directory")
    parser.add_argument("--validate", action="store_true",
                        help="Validate each entry with tkc --profile2")
    parser.add_argument("--tkc", default=None, help="Path to tkc binary")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show transformations without writing")
    parser.add_argument("--stats", action="store_true",
                        help="Print transformation statistics")
    parser.add_argument("--single", help="Transform a single tk_source string (for testing)")

    args = parser.parse_args()

    tkc_path = args.tkc or _find_tkc()

    if args.single:
        t = Phase1ToPhase2Transformer()
        result = t.transform(args.single)
        print(f"IN:  {args.single}")
        print(f"OUT: {result}")
        if args.stats:
            for k, v in t.stats.items():
                if v > 0:
                    print(f"  {k}: {v}")
        sys.exit(0)

    if args.input_jsonl:
        ok = transform_jsonl(
            args.input_jsonl,
            args.output_jsonl or args.input_jsonl.replace(".jsonl", "_p2.jsonl"),
            validate=args.validate,
            tkc_path=tkc_path,
            dry_run=args.dry_run,
            show_stats=args.stats,
        )
        sys.exit(0 if ok else 1)

    if args.corpus_dir:
        ok = transform_corpus_dir(
            args.corpus_dir,
            args.output_dir or args.corpus_dir + "_p2",
            validate=args.validate,
            tkc_path=tkc_path,
            dry_run=args.dry_run,
            show_stats=args.stats,
        )
        sys.exit(0 if ok else 1)

    parser.print_help()
    sys.exit(1)
