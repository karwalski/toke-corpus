"""Grammar-based fuzzer for generating random syntactically-valid toke programs.

Walks grammar productions top-down to produce type-aware toke source code.
Uses a seeded random generator for reproducibility.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PRIMITIVE_TYPES = ("i64", "u64", "f64", "bool", "$str")
NUMERIC_TYPES = ("i64", "u64", "f64")
INTEGER_TYPES = ("i64", "u64")

# Operators grouped by what they return
ARITH_OPS = ("+", "-", "*", "/")
CMP_OPS = ("<", ">", "=")


@dataclass
class VarInfo:
    name: str
    typ: str
    mutable: bool = False


@dataclass
class FuncInfo:
    name: str
    params: list[tuple[str, str]]  # (name, type)
    ret: str


# ---------------------------------------------------------------------------
# Fuzzer
# ---------------------------------------------------------------------------

class GrammarFuzzer:
    """Generates random syntactically-valid toke Phase 2 programs."""

    def __init__(
        self,
        max_depth: int = 6,
        max_functions: int = 4,
        max_statements: int = 5,
        max_params: int = 3,
    ) -> None:
        self.max_depth = max_depth
        self.max_functions = max_functions
        self.max_statements = max_statements
        self.max_params = max_params

        # State reset per generation
        self._rng: random.Random = random.Random()
        self._depth: int = 0
        self._funcs: list[FuncInfo] = []
        self._scope: list[VarInfo] = []
        self._name_counter: int = 0

    # -- public api ---------------------------------------------------------

    def generate(self, seed: int = 42) -> str:
        """Return a single randomly generated toke program."""
        self._reset(seed)
        return self._program()

    def generate_batch(self, n: int, seed: int = 42) -> list[str]:
        """Return *n* randomly generated toke programs."""
        results: list[str] = []
        for i in range(n):
            self._reset(seed + i)
            results.append(self._program())
        return results

    # -- reset --------------------------------------------------------------

    def _reset(self, seed: int) -> None:
        self._rng = random.Random(seed)
        self._depth = 0
        self._funcs = []
        self._scope = []
        self._name_counter = 0

    # -- name generation ----------------------------------------------------

    def _fresh_name(self, prefix: str = "x") -> str:
        self._name_counter += 1
        return f"{prefix}{self._name_counter}"

    def _random_name(self) -> str:
        length = self._rng.randint(2, 6)
        return "".join(self._rng.choices(string.ascii_lowercase, k=length))

    # -- type helpers -------------------------------------------------------

    def _random_type(self) -> str:
        return self._rng.choice(PRIMITIVE_TYPES)

    def _random_numeric_type(self) -> str:
        return self._rng.choice(NUMERIC_TYPES)

    def _is_numeric(self, typ: str) -> bool:
        return typ in NUMERIC_TYPES

    # -- program / module ---------------------------------------------------

    def _program(self) -> str:
        mod_name = self._fresh_name("mod")
        parts = [f"m={mod_name};"]
        num_funcs = self._rng.randint(1, self.max_functions)
        for _ in range(num_funcs):
            parts.append(self._function_decl())
        return "\n".join(parts)

    # -- function -----------------------------------------------------------

    def _function_decl(self) -> str:
        fname = self._fresh_name("fn")
        num_params = self._rng.randint(0, self.max_params)
        params: list[tuple[str, str]] = []
        for _ in range(num_params):
            pname = self._fresh_name("p")
            ptyp = self._random_type()
            params.append((pname, ptyp))

        ret_type = self._random_type()
        finfo = FuncInfo(name=fname, params=params, ret=ret_type)
        self._funcs.append(finfo)

        # Build scope for body
        saved_scope = list(self._scope)
        self._scope = [VarInfo(name=p[0], typ=p[1]) for p in params]

        param_str = ";".join(f"{p[0]}:{p[1]}" for p in params)
        body = self._body(ret_type)

        self._scope = saved_scope

        return f"f={fname}({param_str}):{ret_type}{{\n{body}\n}};"

    # -- body / statements --------------------------------------------------

    def _body(self, ret_type: str) -> str:
        num_stmts = self._rng.randint(1, self.max_statements)
        stmts: list[str] = []
        for i in range(num_stmts):
            # Last statement can be a return
            if i == num_stmts - 1 and self._rng.random() < 0.5:
                stmts.append(self._return_stmt(ret_type))
            else:
                stmts.append(self._stmt(ret_type))
        return "\n".join(stmts)

    def _stmt(self, ret_type: str) -> str:
        if self._depth >= self.max_depth:
            # At max depth only emit flat statements
            return self._let_stmt()

        weights = [
            (self._let_stmt, 30),
            (lambda: self._assign_stmt(), 15),
            (lambda: self._if_stmt(ret_type), 15),
            (lambda: self._loop_stmt(ret_type), 10),
            (lambda: self._return_stmt(ret_type), 10),
            (lambda: self._expr_stmt(), 10),
        ]
        funcs, ws = zip(*weights)
        chosen = self._rng.choices(funcs, weights=ws, k=1)[0]
        return chosen()

    def _let_stmt(self) -> str:
        vname = self._fresh_name("v")
        vtype = self._random_type()
        mutable = self._rng.random() < 0.3
        expr = self._expr(vtype)
        self._scope.append(VarInfo(name=vname, typ=vtype, mutable=mutable))
        if mutable:
            return f"let {vname}=mut.{expr};"
        return f"let {vname}={expr};"

    def _assign_stmt(self) -> str:
        mutable_vars = [v for v in self._scope if v.mutable]
        if not mutable_vars:
            return self._let_stmt()
        var = self._rng.choice(mutable_vars)
        expr = self._expr(var.typ)
        return f"{var.name}={expr};"

    def _if_stmt(self, ret_type: str) -> str:
        self._depth += 1
        cond = self._expr("bool")
        body = self._body(ret_type)
        if self._rng.random() < 0.4:
            el_body = self._body(ret_type)
            self._depth -= 1
            return f"if({cond}){{\n{body}\n}}el{{\n{el_body}\n}};"
        self._depth -= 1
        return f"if({cond}){{\n{body}\n}};"

    def _loop_stmt(self, ret_type: str) -> str:
        self._depth += 1
        iname = self._fresh_name("i")
        itype = self._rng.choice(INTEGER_TYPES)
        init_expr = self._int_literal()
        self._scope.append(VarInfo(name=iname, typ=itype, mutable=True))
        cond = f"{iname}<{self._int_literal()}"
        step = f"{iname}={iname}+1"
        body = self._body(ret_type)
        self._depth -= 1
        return f"lp(let {iname}={init_expr};{cond};{step}){{\n{body}\n}};"

    def _return_stmt(self, ret_type: str) -> str:
        expr = self._expr(ret_type)
        return f"<{expr}"

    def _expr_stmt(self) -> str:
        typ = self._random_type()
        return f"{self._expr(typ)};"

    def _break_stmt(self) -> str:
        return "br;"

    # -- expressions --------------------------------------------------------

    def _expr(self, typ: str) -> str:
        if self._depth >= self.max_depth:
            return self._terminal_expr(typ)

        weights: list[tuple] = [
            (lambda: self._terminal_expr(typ), 50),
            (lambda: self._binop_expr(typ), 25),
            (lambda: self._call_expr(typ), 15),
        ]
        if typ == "bool":
            weights.append((lambda: self._unary_expr(typ), 10))

        funcs, ws = zip(*weights)
        chosen = self._rng.choices(funcs, weights=ws, k=1)[0]
        return chosen()

    def _terminal_expr(self, typ: str) -> str:
        # Try to use a variable in scope of the right type
        matching = [v for v in self._scope if v.typ == typ]
        if matching and self._rng.random() < 0.4:
            return self._rng.choice(matching).name
        return self._literal(typ)

    def _literal(self, typ: str) -> str:
        if typ == "i64":
            return self._int_literal()
        if typ == "u64":
            return str(self._rng.randint(0, 200))
        if typ == "f64":
            return self._float_literal()
        if typ == "bool":
            return self._rng.choice(("true", "false"))
        if typ == "$str":
            return self._string_literal()
        # Fallback
        return self._int_literal()

    def _int_literal(self) -> str:
        return str(self._rng.randint(0, 100))

    def _float_literal(self) -> str:
        return f"{self._rng.uniform(0, 100):.2f}"

    def _string_literal(self) -> str:
        length = self._rng.randint(1, 8)
        chars = self._rng.choices(string.ascii_lowercase + string.digits, k=length)
        return '"' + "".join(chars) + '"'

    def _binop_expr(self, typ: str) -> str:
        self._depth += 1
        if typ == "bool":
            # Comparison of two numeric values
            num_type = self._random_numeric_type()
            left = self._expr(num_type)
            op = self._rng.choice(CMP_OPS)
            right = self._expr(num_type)
            self._depth -= 1
            return f"{left}{op}{right}"
        if self._is_numeric(typ):
            left = self._expr(typ)
            op = self._rng.choice(ARITH_OPS)
            right = self._expr(typ)
            self._depth -= 1
            return f"{left}{op}{right}"
        # For non-numeric/non-bool, just return a literal
        self._depth -= 1
        return self._literal(typ)

    def _unary_expr(self, typ: str) -> str:
        if typ == "bool":
            self._depth += 1
            inner = self._expr("bool")
            self._depth -= 1
            return f"!{inner}"
        return self._literal(typ)

    def _call_expr(self, typ: str) -> str:
        # Find a function that returns the desired type
        matching = [f for f in self._funcs if f.ret == typ]
        if not matching:
            return self._literal(typ)
        func = self._rng.choice(matching)
        self._depth += 1
        args = [self._expr(pt) for _, pt in func.params]
        self._depth -= 1
        arg_str = ";".join(args)
        return f"{func.name}({arg_str})"
