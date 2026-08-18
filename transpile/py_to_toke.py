"""Python-to-toke transpiler using the ast module.

Parses type-annotated Python functions and emits equivalent toke
Phase 2 source code. Targets pure functions with standard type
annotations. Falls back to TranspileError for unsupported constructs.
"""

import ast
import logging
from typing import Optional

logger = logging.getLogger(__name__)


TYPE_MAP = {
    "int": "i64",
    "float": "f64",
    "bool": "bool",
    "str": "$str",
    "None": "$void",
}

BUILTIN_SKIPS = {"print", "input"}


class TranspileError(Exception):
    pass


class _FunctionContext:
    """Per-function transpilation context."""

    def __init__(self):
        self.mutated_vars: set[str] = set()
        self.declared_vars: set[str] = set()
        self.param_names: set[str] = set()
        self.array_params: set[str] = set()


class PyToTokeTranspiler:
    """Transpiles Python source code to toke Phase 2 language."""

    def transpile(self, py_source: str, module_name: str) -> str:
        """Parse Python source and emit toke equivalent.

        Args:
            py_source: Python source with type-annotated functions
            module_name: Name for the m= declaration

        Returns:
            Complete toke source code starting with m=module_name;

        Raises:
            TranspileError: If source can't be parsed or contains unsupported constructs
        """
        try:
            tree = ast.parse(py_source)
        except SyntaxError as exc:
            raise TranspileError(f"Failed to parse Python source: {exc}") from exc

        functions = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name.startswith("_"):
                    logger.debug("Skipping private function: %s", node.name)
                    continue
                functions.append(self._transpile_function(node))

        if not functions:
            raise TranspileError("No functions found in Python source")

        output = f"m={module_name};\n" + "\n".join(functions) + "\n"
        return self._postprocess(output)

    def _postprocess(self, source: str) -> str:
        source = source.replace(";;", ";")
        return source

    def _transpile_function(self, func: ast.FunctionDef) -> str:
        func_name = self._sanitize_name(func.name)

        ret_type = self._map_return_annotation(func.returns)
        params = self._map_params(func)

        ctx = _FunctionContext()
        ctx.param_names = {p[0] for p in params}
        for pname, ptype in params:
            if ptype.startswith("@("):
                ctx.array_params.add(pname)

        self._scan_mutations(func.body, ctx)

        body = self._emit_body(func.body, ctx, indent=2)

        param_str = ";".join(f"{n}:{t}" for n, t in params)
        return f"f={func_name}({param_str}):{ret_type}{{\n{body}}};\n"

    def _sanitize_name(self, name: str) -> str:
        """Convert Python names to toke-compatible identifiers.

        toke Phase 2 identifiers: lowercase a-z, digits 0-9, no underscores.
        """
        result = name.lower().replace("_", "")
        if not result:
            raise TranspileError(f"Name '{name}' produces empty toke identifier")
        if result[0].isdigit():
            result = "fn" + result
        return result

    def _map_return_annotation(self, ann: Optional[ast.expr]) -> str:
        if ann is None:
            return "$void"
        return self._map_type(ann)

    def _map_type(self, ann: ast.expr) -> str:
        """Map a Python type annotation to a toke type string."""
        if isinstance(ann, ast.Constant):
            if ann.value is None:
                return "$void"
            raise TranspileError(f"Unsupported type constant: {ann.value}")

        if isinstance(ann, ast.Name):
            name = ann.id
            if name in TYPE_MAP:
                return TYPE_MAP[name]
            raise TranspileError(f"Unsupported type: {name}")

        if isinstance(ann, ast.Attribute):
            raise TranspileError(f"Dotted types not supported: {ast.dump(ann)}")

        if isinstance(ann, ast.Subscript):
            return self._map_subscript_type(ann)

        raise TranspileError(f"Unsupported type annotation: {ast.dump(ann)}")

    def _map_subscript_type(self, ann: ast.Subscript) -> str:
        """Map list[T], dict[K,V], Optional[T], tuple[...] etc."""
        if not isinstance(ann.value, ast.Name):
            raise TranspileError(f"Unsupported generic type: {ast.dump(ann)}")

        container = ann.value.id

        if container in ("list", "List"):
            inner = self._map_type(ann.slice)
            return f"@({inner})"

        if container in ("dict", "Dict"):
            if isinstance(ann.slice, ast.Tuple) and len(ann.slice.elts) == 2:
                key_type = self._map_type(ann.slice.elts[0])
                val_type = self._map_type(ann.slice.elts[1])
                return f"@({key_type}:{val_type})"
            raise TranspileError("dict type requires exactly 2 type parameters")

        if container in ("Optional", "optional"):
            inner = self._map_type(ann.slice)
            return inner

        raise TranspileError(f"Unsupported generic type: {container}")

    def _map_params(self, func: ast.FunctionDef) -> list[tuple[str, str]]:
        params = []
        for arg in func.args.args:
            name = self._sanitize_name(arg.arg)
            if arg.annotation is None:
                raise TranspileError(
                    f"Parameter '{arg.arg}' has no type annotation"
                )
            toke_type = self._map_type(arg.annotation)
            params.append((name, toke_type))
        return params

    def _scan_mutations(self, stmts: list[ast.stmt], ctx: _FunctionContext) -> None:
        """Scan all statements to find variables that are reassigned.

        Uses a local seen set (not ctx.declared_vars) so that the emit
        pass can still tell first-assignment from reassignment.
        """
        seen: set[str] = set(ctx.param_names)
        for stmt in stmts:
            self._scan_mutations_node(stmt, ctx, seen)

    def _scan_mutations_node(
        self, node: ast.AST, ctx: _FunctionContext, seen: set[str]
    ) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = self._sanitize_name(target.id)
                    if name in seen:
                        ctx.mutated_vars.add(name)
                    else:
                        seen.add(name)

        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                ctx.mutated_vars.add(self._sanitize_name(node.target.id))

        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                seen.add(self._sanitize_name(node.target.id))

        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                ctx.mutated_vars.add(self._sanitize_name(node.target.id))
            for stmt in node.body:
                self._scan_mutations_node(stmt, ctx, seen)
            for stmt in node.orelse:
                self._scan_mutations_node(stmt, ctx, seen)

        elif isinstance(node, ast.While):
            for stmt in node.body:
                self._scan_mutations_node(stmt, ctx, seen)
            for stmt in node.orelse:
                self._scan_mutations_node(stmt, ctx, seen)

        elif isinstance(node, ast.If):
            for stmt in node.body:
                self._scan_mutations_node(stmt, ctx, seen)
            for stmt in node.orelse:
                self._scan_mutations_node(stmt, ctx, seen)

    def _emit_body(
        self, stmts: list[ast.stmt], ctx: _FunctionContext, indent: int
    ) -> str:
        lines = []
        for stmt in stmts:
            line = self._emit_stmt(stmt, ctx, indent)
            if line is not None:
                lines.append(line)
        if lines:
            return "\n".join(lines) + "\n"
        return ""

    def _emit_stmt(
        self, node: ast.stmt, ctx: _FunctionContext, indent: int
    ) -> Optional[str]:
        pad = " " * indent

        if isinstance(node, ast.Return):
            if node.value is None:
                return f"{pad}<$void"
            expr = self._emit_expr(node.value, ctx)
            return f"{pad}<{expr}"

        if isinstance(node, ast.Assign):
            return self._emit_assign(node, ctx, indent)

        if isinstance(node, ast.AugAssign):
            return self._emit_aug_assign(node, ctx, indent)

        if isinstance(node, ast.AnnAssign):
            return self._emit_ann_assign(node, ctx, indent)

        if isinstance(node, ast.If):
            return self._emit_if(node, ctx, indent)

        if isinstance(node, ast.While):
            return self._emit_while(node, ctx, indent)

        if isinstance(node, ast.For):
            return self._emit_for(node, ctx, indent)

        if isinstance(node, ast.Break):
            return f"{pad}br;"

        if isinstance(node, ast.Continue):
            raise TranspileError("Continue statements not supported in toke")

        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Call):
                call = node.value
                fname = self._get_call_name(call)
                if fname in BUILTIN_SKIPS:
                    return None
                args = self._emit_call_args(call, ctx)
                return f"{pad}{self._sanitize_name(fname)}({args});"
            return None

        if isinstance(node, ast.Pass):
            return None

        raise TranspileError(f"Unsupported statement: {type(node).__name__}")

    def _emit_assign(
        self, node: ast.Assign, ctx: _FunctionContext, indent: int
    ) -> Optional[str]:
        pad = " " * indent
        if len(node.targets) != 1:
            raise TranspileError("Multiple assignment targets not supported")

        target = node.targets[0]
        if not isinstance(target, ast.Name):
            if isinstance(target, ast.Subscript):
                arr = self._emit_expr(target.value, ctx)
                idx = self._emit_expr(target.slice, ctx)
                val = self._emit_expr(node.value, ctx)
                return f"{pad}{arr}.set({idx};{val});"
            raise TranspileError(
                f"Unsupported assignment target: {type(target).__name__}"
            )

        name = self._sanitize_name(target.id)
        val = self._emit_expr(node.value, ctx)

        if name in ctx.mutated_vars and name not in ctx.declared_vars:
            ctx.declared_vars.add(name)
            return f"{pad}let {name}=mut.{val};"

        if name not in ctx.declared_vars:
            ctx.declared_vars.add(name)
            if name in ctx.mutated_vars:
                return f"{pad}let {name}=mut.{val};"
            return f"{pad}let {name}={val};"

        return f"{pad}{name}={val};"

    def _emit_aug_assign(
        self, node: ast.AugAssign, ctx: _FunctionContext, indent: int
    ) -> str:
        pad = " " * indent
        target = self._emit_expr(node.target, ctx)
        val = self._emit_expr(node.value, ctx)
        op = self._map_binop(node.op)

        if isinstance(node.op, ast.Mod):
            return f"{pad}{target}={target}-{target}/{val}*{val};"

        return f"{pad}{target}={target}{op}{val};"

    def _emit_ann_assign(
        self, node: ast.AnnAssign, ctx: _FunctionContext, indent: int
    ) -> Optional[str]:
        pad = " " * indent
        if not isinstance(node.target, ast.Name):
            raise TranspileError("Complex annotated assignment not supported")

        name = self._sanitize_name(node.target.id)

        if node.value is None:
            toke_type = self._map_type(node.annotation)
            default = self._default_value(toke_type)
            if name in ctx.mutated_vars:
                ctx.declared_vars.add(name)
                return f"{pad}let {name}=mut.{default};"
            ctx.declared_vars.add(name)
            return f"{pad}let {name}={default};"

        val = self._emit_expr(node.value, ctx)
        ctx.declared_vars.add(name)
        if name in ctx.mutated_vars:
            return f"{pad}let {name}=mut.{val};"
        return f"{pad}let {name}={val};"

    def _default_value(self, toke_type: str) -> str:
        if toke_type in ("i64", "u64"):
            return "0"
        if toke_type == "f64":
            return "0.0"
        if toke_type == "bool":
            return "false"
        if toke_type == "$str":
            return '""'
        if toke_type.startswith("@("):
            return "@()"
        return "0"

    def _emit_if(
        self, node: ast.If, ctx: _FunctionContext, indent: int
    ) -> str:
        pad = " " * indent
        cond, negated = self._emit_condition(node.test, ctx)

        if negated:
            true_stmts = node.orelse
            false_stmts = node.body
        else:
            true_stmts = node.body
            false_stmts = node.orelse

        true_body = self._emit_branch_body(true_stmts, ctx, indent)

        if not false_stmts:
            if negated:
                false_body = self._emit_branch_body(false_stmts, ctx, indent)
                return f"{pad}if({cond}){{}}el{{{true_body}}};"
            return f"{pad}if({cond}){{{true_body}}};"

        if (
            len(false_stmts) == 1
            and isinstance(false_stmts[0], ast.If)
        ):
            elif_node = false_stmts[0]
            elif_str = self._emit_if(elif_node, ctx, indent)
            elif_str = elif_str.strip()
            if elif_str.endswith(";"):
                elif_str = elif_str[:-1]
            return f"{pad}if({cond}){{{true_body}}}el{{{elif_str}}};"

        false_body = self._emit_branch_body(false_stmts, ctx, indent)
        return f"{pad}if({cond}){{{true_body}}}el{{{false_body}}};"

    def _emit_condition(
        self, node: ast.expr, ctx: _FunctionContext
    ) -> tuple[str, bool]:
        """Emit a condition, returning (expr, is_negated).

        Handles Python operators that don't exist in toke by
        negating and swapping branches.
        """
        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            op = node.ops[0]
            left = self._emit_expr(node.left, ctx)
            right = self._emit_expr(node.comparators[0], ctx)

            if isinstance(op, ast.NotEq):
                return f"{left}={right}", True
            if isinstance(op, ast.LtE):
                return f"{left}>{right}", True
            if isinstance(op, ast.GtE):
                return f"{left}<{right}", True
            if isinstance(op, ast.Eq):
                return f"{left}={right}", False
            if isinstance(op, ast.Lt):
                return f"{left}<{right}", False
            if isinstance(op, ast.Gt):
                return f"{left}>{right}", False

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = self._emit_expr(node.operand, ctx)
            return inner, True

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return self._emit_and_condition(node, ctx)
            if isinstance(node.op, ast.Or):
                return self._emit_or_condition(node, ctx)

        return self._emit_expr(node, ctx), False

    def _emit_and_condition(
        self, node: ast.BoolOp, ctx: _FunctionContext
    ) -> tuple[str, bool]:
        """For && we return a marker; _emit_if_and handles nesting."""
        parts = [self._emit_expr(v, ctx) for v in node.values]
        return "&&".join(parts), False

    def _emit_or_condition(
        self, node: ast.BoolOp, ctx: _FunctionContext
    ) -> tuple[str, bool]:
        parts = [self._emit_expr(v, ctx) for v in node.values]
        return "||".join(parts), False

    def _emit_branch_body(
        self, stmts: list[ast.stmt], ctx: _FunctionContext, indent: int
    ) -> str:
        parts = []
        for stmt in stmts:
            line = self._emit_stmt(stmt, ctx, indent + 2)
            if line is not None:
                parts.append(line)
        if parts:
            return "\n" + "\n".join(parts) + "\n" + " " * indent
        return ""

    def _emit_while(
        self, node: ast.While, ctx: _FunctionContext, indent: int
    ) -> str:
        pad = " " * indent
        cond = self._emit_expr(node.test, ctx)
        body = self._emit_branch_body(node.body, ctx, indent)
        return f"{pad}lp(let _w=0;{cond};_w=0){{{body}}};"

    def _emit_for(
        self, node: ast.For, ctx: _FunctionContext, indent: int
    ) -> str:
        """Emit a for loop.

        Handles:
          for i in range(n):       -> lp(let i=0;i<n;i=i+1){...};
          for i in range(a, b):    -> lp(let i=a;i<b;i=i+1){...};
          for i in range(a, b, s): -> lp(let i=a;i<b;i=i+s){...};
          for x in arr:            -> lp(let _idx=0;_idx<arr.len;_idx=_idx+1){let x=arr.get(_idx);...};
        """
        pad = " " * indent

        if not isinstance(node.target, ast.Name):
            raise TranspileError("Only simple for-loop targets supported")

        var = self._sanitize_name(node.target.id)

        if isinstance(node.iter, ast.Call) and self._get_call_name(node.iter) == "range":
            return self._emit_for_range(node, var, ctx, indent)

        arr = self._emit_expr(node.iter, ctx)
        idx_var = f"_idx{var}"
        body_prefix = f"    let {var}={arr}.get({idx_var});\n"
        raw_body = self._emit_branch_body(node.body, ctx, indent)
        if raw_body.startswith("\n"):
            body_content = raw_body.lstrip("\n").rstrip()
            full_body = "\n" + " " * (indent + 2) + body_prefix.strip() + "\n" + body_content + "\n" + " " * indent
        else:
            full_body = "\n" + " " * (indent + 2) + body_prefix.strip() + raw_body
        return f"{pad}lp(let {idx_var}=0;{idx_var}<{arr}.len;{idx_var}={idx_var}+1){{{full_body}}};"

    def _emit_for_range(
        self, node: ast.For, var: str, ctx: _FunctionContext, indent: int
    ) -> str:
        pad = " " * indent
        call = node.iter
        args = call.args

        if len(args) == 1:
            start = "0"
            end = self._emit_expr(args[0], ctx)
            step = "1"
        elif len(args) == 2:
            start = self._emit_expr(args[0], ctx)
            end = self._emit_expr(args[1], ctx)
            step = "1"
        elif len(args) == 3:
            start = self._emit_expr(args[0], ctx)
            end = self._emit_expr(args[1], ctx)
            step = self._emit_expr(args[2], ctx)
        else:
            raise TranspileError(f"range() with {len(args)} args not supported")

        body = self._emit_branch_body(node.body, ctx, indent)

        is_negative_step = False
        if isinstance(call.args[-1], ast.UnaryOp) and isinstance(call.args[-1].op, ast.USub):
            is_negative_step = True
        elif isinstance(call.args[-1], ast.Constant) and isinstance(call.args[-1].value, (int, float)):
            is_negative_step = call.args[-1].value < 0

        if step == "1":
            update = f"{var}={var}+1"
        elif is_negative_step:
            abs_step = step.replace("0-", "", 1) if step.startswith("0-") else step
            if abs_step.isdigit():
                update = f"{var}={var}-{abs_step}"
            else:
                update = f"{var}={var}+{step}"
        else:
            update = f"{var}={var}+{step}"

        if is_negative_step:
            cond = f"{var}>{end}"
        else:
            cond = f"{var}<{end}"

        return f"{pad}lp(let {var}={start};{cond};{update}){{{body}}};"

    def _emit_expr(self, node: ast.expr, ctx: _FunctionContext) -> str:
        if isinstance(node, ast.Constant):
            return self._emit_constant(node)

        if isinstance(node, ast.Name):
            name = node.id
            if name == "True":
                return "true"
            if name == "False":
                return "false"
            if name == "None":
                return "$void"
            return self._sanitize_name(name)

        if isinstance(node, ast.BinOp):
            return self._emit_binop(node, ctx)

        if isinstance(node, ast.UnaryOp):
            return self._emit_unary(node, ctx)

        if isinstance(node, ast.Compare):
            return self._emit_compare(node, ctx)

        if isinstance(node, ast.BoolOp):
            return self._emit_boolop(node, ctx)

        if isinstance(node, ast.Call):
            return self._emit_call(node, ctx)

        if isinstance(node, ast.Subscript):
            return self._emit_subscript(node, ctx)

        if isinstance(node, ast.Attribute):
            return self._emit_attribute(node, ctx)

        if isinstance(node, ast.List):
            elts = [self._emit_expr(e, ctx) for e in node.elts]
            return "@(" + ";".join(elts) + ")"

        if isinstance(node, ast.IfExp):
            return self._emit_ifexp(node, ctx)

        raise TranspileError(f"Unsupported expression: {type(node).__name__}")

    def _emit_constant(self, node: ast.Constant) -> str:
        val = node.value
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, int):
            return str(val)
        if isinstance(val, float):
            s = repr(val)
            if "." not in s and "e" not in s and "E" not in s:
                s = s + ".0"
            return s
        if isinstance(val, str):
            escaped = val.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        if val is None:
            return "$void"
        raise TranspileError(f"Unsupported constant: {type(val).__name__}")

    def _emit_binop(self, node: ast.BinOp, ctx: _FunctionContext) -> str:
        left = self._emit_expr(node.left, ctx)
        right = self._emit_expr(node.right, ctx)

        if isinstance(node.op, ast.Mod):
            return f"{left}-{left}/{right}*{right}"

        if isinstance(node.op, ast.FloorDiv):
            return f"{left}/{right}"

        if isinstance(node.op, ast.Pow):
            raise TranspileError("Power operator not supported in toke")

        op = self._map_binop(node.op)
        return f"{left}{op}{right}"

    def _map_binop(self, op: ast.operator) -> str:
        if isinstance(op, ast.Add):
            return "+"
        if isinstance(op, ast.Sub):
            return "-"
        if isinstance(op, ast.Mult):
            return "*"
        if isinstance(op, (ast.Div, ast.FloorDiv)):
            return "/"
        if isinstance(op, ast.Mod):
            return "%"
        raise TranspileError(f"Unsupported operator: {type(op).__name__}")

    def _emit_unary(self, node: ast.UnaryOp, ctx: _FunctionContext) -> str:
        operand = self._emit_expr(node.operand, ctx)
        if isinstance(node.op, ast.USub):
            return f"0-{operand}"
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.Not):
            return f"!({operand})"
        raise TranspileError(f"Unsupported unary: {type(node.op).__name__}")

    def _emit_compare(self, node: ast.Compare, ctx: _FunctionContext) -> str:
        if len(node.comparators) != 1:
            raise TranspileError("Chained comparisons not supported")

        left = self._emit_expr(node.left, ctx)
        right = self._emit_expr(node.comparators[0], ctx)
        op = node.ops[0]

        if isinstance(op, ast.Eq):
            return f"{left}={right}"
        if isinstance(op, ast.NotEq):
            return f"!({left}={right})"
        if isinstance(op, ast.Lt):
            return f"{left}<{right}"
        if isinstance(op, ast.Gt):
            return f"{left}>{right}"
        if isinstance(op, ast.LtE):
            return f"!({left}>{right})"
        if isinstance(op, ast.GtE):
            return f"!({left}<{right})"

        raise TranspileError(f"Unsupported comparison: {type(op).__name__}")

    def _emit_boolop(self, node: ast.BoolOp, ctx: _FunctionContext) -> str:
        """Emit and/or as nested if expressions.

        a and b -> if(a){b}el{false}
        a or b  -> if(a){true}el{b}
        """
        parts = [self._emit_expr(v, ctx) for v in node.values]
        if isinstance(node.op, ast.And):
            result = parts[-1]
            for part in reversed(parts[:-1]):
                result = f"if({part}){{{result}}}el{{false}}"
            return result
        if isinstance(node.op, ast.Or):
            result = parts[-1]
            for part in reversed(parts[:-1]):
                result = f"if({part}){{true}}el{{{result}}}"
            return result
        raise TranspileError(f"Unsupported bool op: {type(node.op).__name__}")

    def _emit_call(self, node: ast.Call, ctx: _FunctionContext) -> str:
        fname = self._get_call_name(node)

        if fname in BUILTIN_SKIPS:
            return '""'

        if fname == "len":
            if node.args:
                arg = self._emit_expr(node.args[0], ctx)
                return f"{arg}.len"
            return "0"

        if fname == "abs":
            if node.args:
                arg = self._emit_expr(node.args[0], ctx)
                return f"if({arg}<0){{0-{arg}}}el{{{arg}}}"
            return "0"

        if fname == "min":
            if len(node.args) == 2:
                a = self._emit_expr(node.args[0], ctx)
                b = self._emit_expr(node.args[1], ctx)
                return f"if({a}<{b}){{{a}}}el{{{b}}}"
            raise TranspileError("min() requires exactly 2 arguments for transpilation")

        if fname == "max":
            if len(node.args) == 2:
                a = self._emit_expr(node.args[0], ctx)
                b = self._emit_expr(node.args[1], ctx)
                return f"if({a}>{b}){{{a}}}el{{{b}}}"
            raise TranspileError("max() requires exactly 2 arguments for transpilation")

        if fname == "int":
            if node.args:
                arg = self._emit_expr(node.args[0], ctx)
                return f"{arg} as i64"
            return "0"

        if fname == "float":
            if node.args:
                arg = self._emit_expr(node.args[0], ctx)
                return f"{arg} as f64"
            return "0.0"

        if fname == "bool":
            if node.args:
                return self._emit_expr(node.args[0], ctx)
            return "false"

        if fname == "str":
            raise TranspileError("str() conversion not supported in toke")

        if fname in ("append", "extend", "pop"):
            raise TranspileError(f"{fname}() not supported in toke transpilation")

        args = self._emit_call_args(node, ctx)
        return f"{self._sanitize_name(fname)}({args})"

    def _get_call_name(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        raise TranspileError(f"Unsupported call target: {type(node.func).__name__}")

    def _emit_call_args(self, node: ast.Call, ctx: _FunctionContext) -> str:
        parts = [self._emit_expr(a, ctx) for a in node.args]
        return ";".join(parts)

    def _emit_subscript(self, node: ast.Subscript, ctx: _FunctionContext) -> str:
        value = self._emit_expr(node.value, ctx)
        index = self._emit_expr(node.slice, ctx)

        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            return f"{value}.get({index})"

        return f"{value}.get({index})"

    def _emit_attribute(self, node: ast.Attribute, ctx: _FunctionContext) -> str:
        value = self._emit_expr(node.value, ctx)
        attr = node.attr

        if attr == "append":
            raise TranspileError("list.append() not supported")

        return f"{value}.{attr}"

    def _emit_ifexp(self, node: ast.IfExp, ctx: _FunctionContext) -> str:
        """Emit Python ternary: val_if_true if cond else val_if_false"""
        cond, negated = self._emit_condition(node.test, ctx)
        true_expr = self._emit_expr(node.body, ctx)
        false_expr = self._emit_expr(node.orelse, ctx)
        if negated:
            true_expr, false_expr = false_expr, true_expr
        return f"if({cond}){{{true_expr}}}el{{{false_expr}}}"
