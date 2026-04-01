"""C-to-toke transpiler using pycparser.

Parses C source code and emits equivalent toke language code.
Handles simple, single-function C files with basic types and
standard control flow. Falls back to TranspileError for
unsupported constructs.
"""

import logging
import re
from typing import Optional

import pycparser
from pycparser import c_ast

logger = logging.getLogger(__name__)

TYPE_MAP = {
    "int": "i64",
    "int64_t": "i64",
    "long": "i64",
    "unsigned": "u64",
    "uint64_t": "u64",
    "size_t": "u64",
    "double": "f64",
    "float": "f64",
    "bool": "bool",
    "_Bool": "bool",
    "void": "void",
}

FAKE_TYPEDEFS = (
    "typedef long int64_t;\n"
    "typedef unsigned long uint64_t;\n"
    "typedef unsigned long size_t;\n"
    "typedef int bool;\n"
    "int true = 1;\n"
    "int false = 0;\n"
)

INT64_MAX = "9223372036854775807"
INT64_MIN = "(-9223372036854775807-1)"
UINT64_MAX = "18446744073709551615"


class TranspileError(Exception):
    pass


class CToTokeTranspiler:
    """Transpiles C source code to toke language."""

    def transpile(self, c_source: str, module_name: str) -> str:
        """Parse C source and emit toke equivalent.

        Args:
            c_source: Complete C source code (may include #include, main(), etc.)
            module_name: Name for the M= declaration

        Returns:
            Complete toke source code starting with M=module_name;

        Raises:
            TranspileError: If C source can't be parsed or contains unsupported constructs
        """
        preprocessed = self._preprocess(c_source)

        try:
            parser = pycparser.CParser()
            ast = parser.parse(preprocessed, filename="<transpile>")
        except pycparser.c_parser.ParseError as exc:
            raise TranspileError(f"Failed to parse C source: {exc}") from exc
        except Exception as exc:
            raise TranspileError(f"Unexpected parse error: {exc}") from exc

        functions = []
        for ext in ast.ext or []:
            if isinstance(ext, c_ast.FuncDef):
                name = ext.decl.name
                if name == "main":
                    logger.debug("Skipping main() function")
                    continue
                functions.append(self._transpile_function(ext))
            elif isinstance(ext, c_ast.Decl):
                pass
            else:
                logger.debug("Skipping top-level node: %s", type(ext).__name__)

        if not functions:
            raise TranspileError("No non-main functions found in C source")

        output = f"M={module_name};\n" + "\n".join(functions) + "\n"
        output = self._postprocess(output)
        return output

    def _preprocess(self, source: str) -> str:
        """Strip includes/defines/comments/printf and inject fake typedefs."""
        # Strip C comments before anything else (pycparser can't handle them)
        source = re.sub(r'/\*.*?\*/', '', source, flags=re.DOTALL)
        source = re.sub(r'//[^\n]*', '', source)
        # Strip printf/puts/fprintf lines (not needed in toke, and PRId64 breaks pycparser)
        source = re.sub(r'^\s*(printf|puts|fprintf|putchar)\s*\(.*?\)\s*;', '', source, flags=re.MULTILINE)
        # Strip PRId64/PRIu64 format macros if still present
        source = re.sub(r'"[^"]*"\s*PRI[dux]64', '""', source)
        source = re.sub(r'PRI[dux]64\s*"[^"]*"', '""', source)
        lines = source.split("\n")
        filtered = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#include"):
                continue
            if stripped.startswith("#define"):
                continue
            # Skip lines that are just printf with multiline args
            if re.match(r'^\s*printf\s*\(', stripped):
                continue
            filtered.append(line)
        result = "\n".join(filtered)
        result = result.replace("INT64_MAX", INT64_MAX)
        result = result.replace("INT64_MIN", INT64_MIN)
        result = result.replace("UINT64_MAX", UINT64_MAX)
        return FAKE_TYPEDEFS + result

    def _postprocess(self, source: str) -> str:
        """Clean up generated toke source."""
        source = source.replace(";;", ";")
        return source

    def _transpile_function(self, func_def: c_ast.FuncDef) -> str:
        """Transpile a single C function to toke."""
        decl = func_def.decl
        func_name = decl.name
        func_decl = decl.type

        if isinstance(func_decl, c_ast.PtrDecl):
            raise TranspileError(
                f"Function pointers not supported: {func_name}"
            )

        ret_type = self._map_return_type(func_decl.type)
        params, array_params, dropped_len_params = self._map_params(
            func_decl.args
        )

        ctx = _FunctionContext(
            array_params=array_params,
            dropped_len_params=dropped_len_params,
        )
        self._scan_mutations(func_def.body, ctx)

        body = self._emit_body(func_def.body, ctx, indent=2)

        param_str = ";".join(f"{n}:{t}" for n, t in params)
        return f"F={func_name}({param_str}):{ret_type}{{\n{body}}};\n"

    def _map_return_type(self, type_node: c_ast.Node) -> str:
        """Map a C return type to toke type."""
        return self._resolve_type(type_node)

    def _resolve_type(self, type_node: c_ast.Node) -> str:
        """Resolve a pycparser type node to a toke type string."""
        if isinstance(type_node, c_ast.TypeDecl):
            return self._resolve_type(type_node.type)

        if isinstance(type_node, c_ast.IdentifierType):
            names = type_node.names
            c_type = " ".join(names)
            if c_type in TYPE_MAP:
                return TYPE_MAP[c_type]
            for name in names:
                if name in TYPE_MAP:
                    return TYPE_MAP[name]
            raise TranspileError(f"Unsupported type: {c_type}")

        if isinstance(type_node, c_ast.PtrDecl):
            inner = type_node.type
            if isinstance(inner, c_ast.TypeDecl) and isinstance(
                inner.type, c_ast.IdentifierType
            ):
                inner_names = inner.type.names
                if "char" in inner_names:
                    return "Str"
            inner_type = self._resolve_type(inner)
            return f"[{inner_type}]"

        if isinstance(type_node, c_ast.ArrayDecl):
            inner_type = self._resolve_type(type_node.type)
            return f"[{inner_type}]"

        if isinstance(type_node, c_ast.Struct):
            raise TranspileError("Struct types not supported")

        if isinstance(type_node, c_ast.Union):
            raise TranspileError("Union types not supported")

        raise TranspileError(
            f"Unsupported type node: {type(type_node).__name__}"
        )

    def _map_params(
        self, param_list: Optional[c_ast.ParamList]
    ) -> tuple[list[tuple[str, str]], dict[str, str], set[str]]:
        """Map C function parameters to toke parameters.

        Returns:
            (params, array_params, dropped_len_params)
            - params: list of (name, toke_type)
            - array_params: dict mapping len_param_name -> array_param_name
            - dropped_len_params: set of param names that were dropped
        """
        if param_list is None:
            return [], {}, set()

        raw_params = []
        for param in param_list.params or []:
            if isinstance(param, c_ast.EllipsisParam):
                raise TranspileError("Variadic functions not supported")
            if not isinstance(param, c_ast.Decl):
                continue

            name = param.name
            if name is None:
                continue

            toke_type = self._resolve_type(param.type)
            is_array = toke_type.startswith("[")
            raw_params.append((name, toke_type, is_array))

        array_params = {}
        dropped_len_params: set[str] = set()
        array_names = [(n, t) for n, t, is_arr in raw_params if is_arr]

        if array_names:
            for arr_name, _ in array_names:
                for pname, ptype, is_arr in raw_params:
                    if not is_arr and ptype in ("u64", "i64"):
                        if pname in ("len", "length", "n", "size", "count"):
                            array_params[pname] = arr_name
                            dropped_len_params.add(pname)
                            break
                        if pname.startswith(arr_name) and pname.endswith(
                            ("_len", "_length", "_size", "Len", "Length")
                        ):
                            array_params[pname] = arr_name
                            dropped_len_params.add(pname)
                            break

        result_params = [
            (n, t) for n, t, _ in raw_params if n not in dropped_len_params
        ]
        return result_params, array_params, dropped_len_params

    def _scan_mutations(
        self, compound: c_ast.Compound, ctx: "_FunctionContext"
    ) -> None:
        """Scan a compound statement to find which variables are reassigned."""
        if compound.block_items is None:
            return
        self._scan_mutations_stmts(compound.block_items, ctx)

    def _scan_mutations_stmts(
        self, stmts: list[c_ast.Node], ctx: "_FunctionContext"
    ) -> None:
        """Recursively scan statements for assignments."""
        for stmt in stmts:
            self._scan_mutations_node(stmt, ctx)

    def _scan_mutations_node(
        self, node: c_ast.Node, ctx: "_FunctionContext"
    ) -> None:
        """Scan a single node for variable mutations."""
        if isinstance(node, c_ast.Assignment):
            lvalue = node.lvalue
            if isinstance(lvalue, c_ast.ID):
                ctx.mutated_vars.add(lvalue.name)

        elif isinstance(node, c_ast.UnaryOp):
            if node.op in ("p++", "p--", "++", "--"):
                if isinstance(node.expr, c_ast.ID):
                    ctx.mutated_vars.add(node.expr.name)

        elif isinstance(node, c_ast.Compound):
            if node.block_items:
                self._scan_mutations_stmts(node.block_items, ctx)

        elif isinstance(node, c_ast.If):
            if node.iftrue:
                self._scan_mutations_node(node.iftrue, ctx)
            if node.iffalse:
                self._scan_mutations_node(node.iffalse, ctx)

        elif isinstance(node, c_ast.For):
            if node.init:
                self._scan_mutations_node(node.init, ctx)
            if node.stmt:
                self._scan_mutations_node(node.stmt, ctx)

        elif isinstance(node, c_ast.While):
            if node.stmt:
                self._scan_mutations_node(node.stmt, ctx)

        elif isinstance(node, c_ast.DoWhile):
            if node.stmt:
                self._scan_mutations_node(node.stmt, ctx)

        elif isinstance(node, c_ast.DeclList):
            for decl in node.decls or []:
                self._scan_mutations_node(decl, ctx)

        elif isinstance(node, c_ast.Switch):
            raise TranspileError("Switch statements not supported")

        elif isinstance(node, c_ast.Goto):
            raise TranspileError("Goto statements not supported")

    def _emit_body(
        self,
        compound: c_ast.Compound,
        ctx: "_FunctionContext",
        indent: int,
    ) -> str:
        """Emit the body of a compound statement."""
        if compound.block_items is None:
            return ""
        lines = []
        for stmt in compound.block_items:
            line = self._emit_stmt(stmt, ctx, indent)
            if line is not None:
                lines.append(line)
        return "\n".join(lines) + "\n"

    def _emit_stmt(
        self,
        node: c_ast.Node,
        ctx: "_FunctionContext",
        indent: int,
    ) -> Optional[str]:
        """Emit a single statement, returning the line or None to skip."""
        pad = " " * indent

        if isinstance(node, c_ast.Return):
            if node.expr is None:
                return f"{pad}<void"
            expr = self._emit_expr(node.expr, ctx)
            return f"{pad}<{expr}"

        if isinstance(node, c_ast.Break):
            return f"{pad}br;"

        if isinstance(node, c_ast.Continue):
            raise TranspileError("Continue statements not supported in toke")

        if isinstance(node, c_ast.Decl):
            return self._emit_decl(node, ctx, indent)

        if isinstance(node, c_ast.Assignment):
            return self._emit_assignment(node, ctx, indent)

        if isinstance(node, c_ast.If):
            return self._emit_if(node, ctx, indent)

        if isinstance(node, c_ast.For):
            return self._emit_for(node, ctx, indent)

        if isinstance(node, c_ast.While):
            return self._emit_while(node, ctx, indent)

        if isinstance(node, c_ast.DoWhile):
            return self._emit_while_do(node, ctx, indent)

        if isinstance(node, c_ast.Compound):
            return self._emit_compound_inline(node, ctx, indent)

        if isinstance(node, c_ast.FuncCall):
            fname = self._emit_expr(node.name, ctx)
            if fname in ("printf", "puts", "fprintf", "sprintf"):
                return None
            args = self._emit_func_args(node.args, ctx)
            return f"{pad}{fname}({args});"

        if isinstance(node, c_ast.UnaryOp):
            return self._emit_unary_stmt(node, ctx, indent)

        if isinstance(node, c_ast.EmptyStatement):
            return None

        if isinstance(node, c_ast.Switch):
            raise TranspileError("Switch statements not supported")
        if isinstance(node, c_ast.Goto):
            raise TranspileError("Goto statements not supported")

        logger.warning(
            "Unsupported statement type: %s", type(node).__name__
        )
        raise TranspileError(
            f"Unsupported statement: {type(node).__name__}"
        )

    def _emit_decl(
        self, node: c_ast.Decl, ctx: "_FunctionContext", indent: int
    ) -> Optional[str]:
        """Emit a variable declaration."""
        pad = " " * indent
        name = node.name
        if name is None:
            return None

        if node.init is None:
            if name in ctx.mutated_vars:
                toke_type = self._resolve_type(node.type)
                default = self._default_value(toke_type)
                return f"{pad}let {name}=mut.{default};"
            return None

        init_expr = self._emit_expr(node.init, ctx)

        if name in ctx.mutated_vars:
            return f"{pad}let {name}=mut.{init_expr};"

        return f"{pad}let {name}={init_expr};"

    def _default_value(self, toke_type: str) -> str:
        """Return a default value literal for a toke type."""
        if toke_type in ("i64", "u64"):
            return "0"
        if toke_type == "f64":
            return "0.0"
        if toke_type == "bool":
            return "false"
        if toke_type == "Str":
            return '""'
        return "0"

    def _emit_assignment(
        self,
        node: c_ast.Assignment,
        ctx: "_FunctionContext",
        indent: int,
    ) -> str:
        """Emit an assignment statement."""
        pad = " " * indent
        lhs = self._emit_expr(node.lvalue, ctx)
        rhs = self._emit_expr(node.rvalue, ctx)

        if node.op == "=":
            return f"{pad}{lhs}={rhs};"

        op_map = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%"}
        if node.op in op_map:
            op = op_map[node.op]
            return f"{pad}{lhs}={lhs}{op}{rhs};"

        raise TranspileError(f"Unsupported assignment operator: {node.op}")

    def _emit_condition(
        self, node: c_ast.Node, ctx: "_FunctionContext"
    ) -> tuple[str, bool]:
        """Emit a condition expression.

        Returns (expr_string, is_negated) where is_negated means the
        caller should swap if/else branches.
        """
        if isinstance(node, c_ast.BinaryOp):
            if node.op == "!=":
                left = self._emit_expr(node.left, ctx)
                right = self._emit_expr(node.right, ctx)
                return f"{left}={right}", True

            if node.op == "<=":
                left = self._emit_expr(node.left, ctx)
                right = self._emit_expr(node.right, ctx)
                return f"{left}>{right}", True

            if node.op == ">=":
                left = self._emit_expr(node.left, ctx)
                right = self._emit_expr(node.right, ctx)
                return f"{left}<{right}", True

            if node.op == "&&":
                return self._emit_and_condition(node, ctx)

            if node.op == "||":
                return self._emit_or_condition(node, ctx)

        if isinstance(node, c_ast.UnaryOp) and node.op == "!":
            inner = self._emit_expr(node.expr, ctx)
            return inner, True

        return self._emit_expr(node, ctx), False

    def _emit_and_condition(
        self, node: c_ast.BinaryOp, ctx: "_FunctionContext"
    ) -> tuple[str, bool]:
        """Handle && by returning a marker that _emit_if uses for nesting.

        Since && requires nested ifs, we handle it specially.
        We return a placeholder and let the caller deal with it.
        Actually, we'll handle this differently - we'll emit the whole
        if statement structure inline.
        """
        left = self._emit_expr(node.left, ctx)
        right = self._emit_expr(node.right, ctx)
        return f"{left}&&{right}", False

    def _emit_or_condition(
        self, node: c_ast.BinaryOp, ctx: "_FunctionContext"
    ) -> tuple[str, bool]:
        """Handle || similarly."""
        left = self._emit_expr(node.left, ctx)
        right = self._emit_expr(node.right, ctx)
        return f"{left}||{right}", False

    def _emit_if(
        self, node: c_ast.If, ctx: "_FunctionContext", indent: int
    ) -> str:
        """Emit an if/else statement, handling &&, ||, !=, <=, >=."""
        pad = " " * indent

        if isinstance(node.cond, c_ast.BinaryOp) and node.cond.op == "&&":
            return self._emit_if_and(node, ctx, indent)

        if isinstance(node.cond, c_ast.BinaryOp) and node.cond.op == "||":
            return self._emit_if_or(node, ctx, indent)

        cond_expr, negated = self._emit_condition(node.cond, ctx)

        if negated:
            true_branch = node.iffalse
            false_branch = node.iftrue
        else:
            true_branch = node.iftrue
            false_branch = node.iffalse

        if negated and true_branch is None:
            false_body = self._emit_branch_body(false_branch, ctx, indent)
            return f"{pad}if({cond_expr}){{}}el{{{false_body}}};"

        if negated:
            true_body = self._emit_branch_body(true_branch, ctx, indent)
            false_body = self._emit_branch_body(false_branch, ctx, indent)
            return f"{pad}if({cond_expr}){{{true_body}}}el{{{false_body}}};"

        true_body = self._emit_branch_body(true_branch, ctx, indent)
        if false_branch is None:
            return f"{pad}if({cond_expr}){{{true_body}}};"
        false_body = self._emit_branch_body(false_branch, ctx, indent)
        return f"{pad}if({cond_expr}){{{true_body}}}el{{{false_body}}};"

    def _emit_if_and(
        self, node: c_ast.If, ctx: "_FunctionContext", indent: int
    ) -> str:
        """Emit if(a && b){X} as if(a){if(b){X}}."""
        pad = " " * indent
        left_cond, left_neg = self._emit_condition(node.cond.left, ctx)
        right_cond, right_neg = self._emit_condition(node.cond.right, ctx)

        true_body = self._emit_branch_body(node.iftrue, ctx, indent)
        inner = f"if({right_cond}){{{true_body}}}"
        if right_neg:
            inner = f"if({right_cond}){{}}el{{{true_body}}}"

        if node.iffalse is not None:
            false_body = self._emit_branch_body(node.iffalse, ctx, indent)
            if left_neg:
                return f"{pad}if({left_cond}){{}}el{{{inner}el{{{false_body}}}}};"
            return f"{pad}if({left_cond}){{{inner}el{{{false_body}}}}};"

        if left_neg:
            return f"{pad}if({left_cond}){{}}el{{{inner}}};"
        return f"{pad}if({left_cond}){{{inner}}};"

    def _emit_if_or(
        self, node: c_ast.If, ctx: "_FunctionContext", indent: int
    ) -> str:
        """Emit if(a || b){X} as if(a){X}el{if(b){X}}."""
        pad = " " * indent
        left_cond, left_neg = self._emit_condition(node.cond.left, ctx)
        right_cond, right_neg = self._emit_condition(node.cond.right, ctx)

        true_body = self._emit_branch_body(node.iftrue, ctx, indent)

        right_if = f"if({right_cond}){{{true_body}}}"
        if right_neg:
            right_if = f"if({right_cond}){{}}el{{{true_body}}}"

        if node.iffalse is not None:
            false_body = self._emit_branch_body(node.iffalse, ctx, indent)
            right_if_with_else = f"if({right_cond}){{{true_body}}}el{{{false_body}}}"
            if right_neg:
                right_if_with_else = f"if({right_cond}){{{false_body}}}el{{{true_body}}}"
            if left_neg:
                return f"{pad}if({left_cond}){{{right_if_with_else}}}el{{{true_body}}};"
            return f"{pad}if({left_cond}){{{true_body}}}el{{{right_if_with_else}}};"

        if left_neg:
            return f"{pad}if({left_cond}){{{right_if}}}el{{{true_body}}};"
        return f"{pad}if({left_cond}){{{true_body}}}el{{{right_if}}};"

    def _emit_branch_body(
        self,
        node: Optional[c_ast.Node],
        ctx: "_FunctionContext",
        indent: int,
    ) -> str:
        """Emit the body of an if/else branch (may be compound or single stmt)."""
        if node is None:
            return ""
        if isinstance(node, c_ast.Compound):
            parts = []
            if node.block_items:
                for stmt in node.block_items:
                    line = self._emit_stmt(stmt, ctx, indent + 2)
                    if line is not None:
                        parts.append(line)
            if parts:
                return "\n" + "\n".join(parts) + "\n" + " " * indent
            return ""
        line = self._emit_stmt(node, ctx, indent + 2)
        if line is not None:
            return "\n" + line + "\n" + " " * indent
        return ""

    def _emit_for(
        self, node: c_ast.For, ctx: "_FunctionContext", indent: int
    ) -> str:
        """Emit a for loop as lp(init;cond;update){body};"""
        pad = " " * indent

        init_str = self._emit_for_init(node.init, ctx)
        cond_str = self._emit_expr(node.cond, ctx) if node.cond else "true"
        update_str = self._emit_for_update(node.next, ctx)

        body = self._emit_branch_body(node.stmt, ctx, indent)

        return f"{pad}lp({init_str};{cond_str};{update_str}){{{body}}};"

    def _emit_for_init(
        self, node: Optional[c_ast.Node], ctx: "_FunctionContext"
    ) -> str:
        """Emit the init part of a for loop."""
        if node is None:
            return "let _fi=0"

        if isinstance(node, c_ast.DeclList):
            parts = []
            for decl in node.decls:
                name = decl.name
                if name is None:
                    continue
                if decl.init is not None:
                    init_val = self._emit_expr(decl.init, ctx)
                    parts.append(f"let {name}={init_val}")
                else:
                    parts.append(f"let {name}=0")
            return ";".join(parts) if parts else "let _fi=0"

        if isinstance(node, c_ast.Assignment):
            lhs = self._emit_expr(node.lvalue, ctx)
            rhs = self._emit_expr(node.rvalue, ctx)
            if node.op == "=":
                return f"{lhs}={rhs}"
            op_map = {"+=": "+", "-=": "-", "*=": "*", "/=": "/"}
            if node.op in op_map:
                return f"{lhs}={lhs}{op_map[node.op]}{rhs}"
            return f"{lhs}={rhs}"

        return self._emit_expr(node, ctx)

    def _emit_for_update(
        self, node: Optional[c_ast.Node], ctx: "_FunctionContext"
    ) -> str:
        """Emit the update part of a for loop."""
        if node is None:
            return "_fi=0"

        if isinstance(node, c_ast.UnaryOp):
            var = self._emit_expr(node.expr, ctx)
            if node.op in ("p++", "++"):
                return f"{var}={var}+1"
            if node.op in ("p--", "--"):
                return f"{var}={var}-1"

        if isinstance(node, c_ast.Assignment):
            lhs = self._emit_expr(node.lvalue, ctx)
            rhs = self._emit_expr(node.rvalue, ctx)
            if node.op == "=":
                return f"{lhs}={rhs}"
            op_map = {"+=": "+", "-=": "-", "*=": "*", "/=": "/"}
            if node.op in op_map:
                return f"{lhs}={lhs}{op_map[node.op]}{rhs}"
            return f"{lhs}={rhs}"

        return self._emit_expr(node, ctx)

    def _emit_while(
        self, node: c_ast.While, ctx: "_FunctionContext", indent: int
    ) -> str:
        """Emit while(cond){body} as lp(let _w=0;cond;_w=0){body};"""
        pad = " " * indent
        cond = self._emit_expr(node.cond, ctx) if node.cond else "true"
        body = self._emit_branch_body(node.stmt, ctx, indent)
        return f"{pad}lp(let _w=0;{cond};_w=0){{{body}}};"

    def _emit_while_do(
        self, node: c_ast.DoWhile, ctx: "_FunctionContext", indent: int
    ) -> str:
        """Emit do{body}while(cond) as lp with break at end."""
        pad = " " * indent
        cond = self._emit_expr(node.cond, ctx) if node.cond else "true"
        body = self._emit_branch_body(node.stmt, ctx, indent)
        cond_neg, negated = self._emit_condition(node.cond, ctx)
        if negated:
            break_check = f"if({cond_neg}){{br;}};"
        else:
            break_check = f"if({cond_neg}){{}}el{{br;}};"
        inner_pad = " " * (indent + 2)
        body_with_check = body.rstrip()
        if body_with_check:
            body_with_check += "\n" + inner_pad + break_check + "\n" + pad
        else:
            body_with_check = "\n" + inner_pad + break_check + "\n" + pad
        return f"{pad}lp(let _dw=0;true;_dw=0){{{body_with_check}}};"

    def _emit_compound_inline(
        self,
        node: c_ast.Compound,
        ctx: "_FunctionContext",
        indent: int,
    ) -> str:
        """Emit an inline compound statement (bare block)."""
        parts = []
        if node.block_items:
            for stmt in node.block_items:
                line = self._emit_stmt(stmt, ctx, indent)
                if line is not None:
                    parts.append(line)
        return "\n".join(parts)

    def _emit_unary_stmt(
        self,
        node: c_ast.UnaryOp,
        ctx: "_FunctionContext",
        indent: int,
    ) -> str:
        """Emit a unary operation used as a statement (i++, i--)."""
        pad = " " * indent
        var = self._emit_expr(node.expr, ctx)
        if node.op in ("p++", "++"):
            return f"{pad}{var}={var}+1;"
        if node.op in ("p--", "--"):
            return f"{pad}{var}={var}-1;"
        raise TranspileError(
            f"Unsupported unary statement: {node.op}"
        )

    def _emit_expr(self, node: c_ast.Node, ctx: "_FunctionContext") -> str:
        """Emit an expression as a toke string."""
        if isinstance(node, c_ast.Constant):
            return self._emit_constant(node)

        if isinstance(node, c_ast.ID):
            name = node.name
            if name == "true":
                return "true"
            if name == "false":
                return "false"
            if name in ctx.dropped_len_params:
                arr_name = ctx.array_params.get(name, name)
                return f"{arr_name}.len"
            return name

        if isinstance(node, c_ast.BinaryOp):
            return self._emit_binary(node, ctx)

        if isinstance(node, c_ast.UnaryOp):
            return self._emit_unary_expr(node, ctx)

        if isinstance(node, c_ast.ArrayRef):
            arr = self._emit_expr(node.name, ctx)
            idx = self._emit_expr(node.subscript, ctx)
            return f"{arr}[{idx}]"

        if isinstance(node, c_ast.FuncCall):
            return self._emit_func_call(node, ctx)

        if isinstance(node, c_ast.Cast):
            expr = self._emit_expr(node.expr, ctx)
            target_type = self._resolve_type(node.to_type.type)
            return f"{expr} as {target_type}"

        if isinstance(node, c_ast.TernaryOp):
            return self._emit_ternary(node, ctx)

        if isinstance(node, c_ast.Assignment):
            lhs = self._emit_expr(node.lvalue, ctx)
            rhs = self._emit_expr(node.rvalue, ctx)
            if node.op == "=":
                return f"{lhs}={rhs}"
            op_map = {"+=": "+", "-=": "-", "*=": "*", "/=": "/"}
            if node.op in op_map:
                return f"{lhs}={lhs}{op_map[node.op]}{rhs}"
            return f"{lhs}={rhs}"

        if isinstance(node, c_ast.ExprList):
            parts = [self._emit_expr(e, ctx) for e in node.exprs]
            return ";".join(parts)

        if isinstance(node, c_ast.Typename):
            return self._resolve_type(node.type)

        if isinstance(node, c_ast.StructRef):
            raise TranspileError("Struct member access not supported")

        raise TranspileError(
            f"Unsupported expression: {type(node).__name__}"
        )

    def _emit_constant(self, node: c_ast.Constant) -> str:
        """Emit a constant literal."""
        if node.type == "string":
            return node.value
        if node.type == "char":
            return node.value
        val = node.value
        if val.endswith(("L", "l", "U", "u", "LL", "ll", "ULL", "ull")):
            val = val.rstrip("LlUu")
        if val.endswith("f") and node.type == "float":
            val = val.rstrip("f")
        return val

    def _emit_binary(
        self, node: c_ast.BinaryOp, ctx: "_FunctionContext"
    ) -> str:
        """Emit a binary operation."""
        left = self._emit_expr(node.left, ctx)
        right = self._emit_expr(node.right, ctx)

        if node.op == "==":
            return f"{left}={right}"

        if node.op == "!=":
            logger.debug(
                "!= in expression context -- emitting raw, "
                "caller should handle"
            )
            return f"{left}!={right}"

        if node.op == "<=":
            return f"{left}<={right}"

        if node.op == ">=":
            return f"{left}>={right}"

        if node.op == "&&":
            return f"{left}&&{right}"

        if node.op == "||":
            return f"{left}||{right}"

        if self._is_sizeof_len_pattern(node, ctx):
            return self._emit_sizeof_len(node, ctx)

        return f"{left}{node.op}{right}"

    def _is_sizeof_len_pattern(
        self, node: c_ast.BinaryOp, ctx: "_FunctionContext"
    ) -> bool:
        """Check if this is sizeof(arr)/sizeof(arr[0])."""
        if node.op != "/":
            return False
        if not isinstance(node.left, c_ast.UnaryOp):
            return False
        if node.left.op != "sizeof":
            return False
        if not isinstance(node.right, c_ast.UnaryOp):
            return False
        if node.right.op != "sizeof":
            return False
        return True

    def _emit_sizeof_len(
        self, node: c_ast.BinaryOp, ctx: "_FunctionContext"
    ) -> str:
        """Emit sizeof(arr)/sizeof(arr[0]) as arr.len."""
        left_expr = node.left.expr
        if isinstance(left_expr, c_ast.ID):
            return f"{left_expr.name}.len"
        return self._emit_expr(left_expr, ctx) + ".len"

    def _emit_unary_expr(
        self, node: c_ast.UnaryOp, ctx: "_FunctionContext"
    ) -> str:
        """Emit a unary expression."""
        if node.op == "-":
            inner = self._emit_expr(node.expr, ctx)
            return f"0-{inner}"

        if node.op == "+":
            return self._emit_expr(node.expr, ctx)

        if node.op == "!":
            inner = self._emit_expr(node.expr, ctx)
            return f"!{inner}"

        if node.op in ("p++", "++"):
            var = self._emit_expr(node.expr, ctx)
            return f"{var}+1"

        if node.op in ("p--", "--"):
            var = self._emit_expr(node.expr, ctx)
            return f"{var}-1"

        if node.op == "sizeof":
            return self._emit_expr(node.expr, ctx) + ".len"

        if node.op == "&":
            return self._emit_expr(node.expr, ctx)

        if node.op == "*":
            return self._emit_expr(node.expr, ctx)

        raise TranspileError(f"Unsupported unary operator: {node.op}")

    def _emit_func_call(
        self, node: c_ast.FuncCall, ctx: "_FunctionContext"
    ) -> str:
        """Emit a function call expression."""
        fname = self._emit_expr(node.name, ctx)

        if fname in ("printf", "puts", "fprintf", "sprintf"):
            return '""'

        if fname == "strlen":
            if node.args and node.args.exprs:
                arg = self._emit_expr(node.args.exprs[0], ctx)
                return f"{arg}.len"
            return "0"

        args = self._emit_func_args(node.args, ctx)
        return f"{fname}({args})"

    def _emit_func_args(
        self,
        args: Optional[c_ast.ExprList],
        ctx: "_FunctionContext",
    ) -> str:
        """Emit function arguments separated by semicolons."""
        if args is None:
            return ""
        parts = []
        for expr in args.exprs or []:
            part = self._emit_expr(expr, ctx)
            parts.append(part)
        return ";".join(parts)

    def _emit_ternary(
        self, node: c_ast.TernaryOp, ctx: "_FunctionContext"
    ) -> str:
        """Emit a ternary as a toke if/el expression.

        For simple cases we inline it. For the AST we produce a
        readable form, though the caller may need to assign to a temp.
        """
        cond, negated = self._emit_condition(node.cond, ctx)
        if negated:
            true_expr = self._emit_expr(node.iffalse, ctx)
            false_expr = self._emit_expr(node.iftrue, ctx)
        else:
            true_expr = self._emit_expr(node.iftrue, ctx)
            false_expr = self._emit_expr(node.iffalse, ctx)

        ctx.needs_ternary_temp = True
        return f"if({cond}){{{true_expr}}}el{{{false_expr}}}"


class _FunctionContext:
    """Per-function transpilation context."""

    def __init__(
        self,
        array_params: Optional[dict[str, str]] = None,
        dropped_len_params: Optional[set[str]] = None,
    ):
        self.array_params: dict[str, str] = array_params or {}
        self.dropped_len_params: set[str] = dropped_len_params or set()
        self.mutated_vars: set[str] = set()
        self.needs_ternary_temp: bool = False
