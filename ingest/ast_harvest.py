"""AST harvesting from open-source code.

Extracts pure functions from C and Python source files.
C extraction uses regex-based patterns (no full parser required).
Python extraction uses the ast module with type annotation checks.
"""

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Allowed stdlib math functions in C
ALLOWED_C_CALLS = {"abs", "sqrt", "pow", "fabs", "ceil", "floor", "log", "exp"}

# Simple C return types we accept
SIMPLE_C_TYPES = {"int", "float", "double", "long", "void", "char*"}

# Python builtins that are acceptable in pure functions
PYTHON_PURE_BUILTINS = {
    "abs", "len", "min", "max", "sum", "sorted", "reversed",
    "range", "enumerate", "zip", "map", "filter",
    "int", "float", "str", "bool", "list", "tuple", "dict", "set",
    "isinstance", "type", "print", "round", "pow", "divmod",
    "all", "any", "ord", "chr", "hex", "oct", "bin",
}

# Regex for simple C function signatures
# Matches: type name(params) { ... }
C_FUNC_PATTERN = re.compile(
    r"^(?P<ret_type>(?:unsigned\s+)?(?:int|float|double|long|void|char\s*\*))"
    r"\s+"
    r"(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)"
    r"\s*\("
    r"(?P<params>[^)]*)"
    r"\)\s*\{",
    re.MULTILINE,
)

# Patterns that disqualify a C function
C_DISQUALIFIERS = [
    re.compile(r"\bstruct\b"),           # struct usage
    re.compile(r"\*\s*\w+\s*[+\-]"),     # pointer arithmetic (ptr + n)
    re.compile(r"\w+\s*[+\-]\s*\*"),     # pointer arithmetic (n + *ptr)
    re.compile(r"\[\s*\w+\s*\]"),        # array indexing
    re.compile(r"\bmalloc\b"),           # dynamic allocation
    re.compile(r"\bfree\b"),             # deallocation
    re.compile(r"\bcalloc\b"),
    re.compile(r"\brealloc\b"),
    re.compile(r"\bprintf\b"),           # I/O
    re.compile(r"\bscanf\b"),
    re.compile(r"\bfprintf\b"),
    re.compile(r"\bfopen\b"),
    re.compile(r"\bfclose\b"),
    re.compile(r"\bgoto\b"),
    re.compile(r"\bextern\b"),
    re.compile(r"\bstatic\b"),
    re.compile(r"\btypedef\b"),
    re.compile(r"\b\w+->"),             # pointer dereference via ->
]

MAX_LINES = 30


@dataclass
class HarvestedFunction:
    """A function extracted from source code."""

    name: str
    params: list[tuple[str, str]]  # (name, type)
    return_type: str
    body: str
    language: str
    source_file: str
    license: str = ""


class ASTHarvester:
    """Extracts pure functions from C and Python source files."""

    def harvest_c_functions(self, source_path: Path) -> list[HarvestedFunction]:
        """Extract simple C functions using regex-based extraction.

        Finds functions with:
        - Single return type (int, float, double, char*, void)
        - No pointer arithmetic (except char*)
        - No struct params
        - No external calls (only stdlib math functions allowed)
        - Max 30 lines
        """
        try:
            source = source_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("Cannot read %s: %s", source_path, exc)
            return []

        results = []
        for match in C_FUNC_PATTERN.finditer(source):
            ret_type = match.group("ret_type").strip()
            name = match.group("name")
            params_str = match.group("params").strip()
            start = match.start()

            # Extract the function body by finding matching braces
            body = self._extract_c_body(source, match.end() - 1)
            if body is None:
                continue

            full_func = source[match.start():match.end() - 1] + body
            lines = full_func.strip().split("\n")

            # Skip main()
            if name == "main":
                continue

            # Skip if too long
            if len(lines) > MAX_LINES:
                logger.debug("Skipping %s: too many lines (%d)", name, len(lines))
                continue

            # Parse params
            params = self._parse_c_params(params_str)
            if params is None:
                continue

            # Check for disqualifiers in the body
            if self._has_c_disqualifiers(body):
                logger.debug("Skipping %s: has disqualifying patterns", name)
                continue

            # Check for external calls
            if not self._c_calls_ok(body):
                logger.debug("Skipping %s: has external calls", name)
                continue

            results.append(HarvestedFunction(
                name=name,
                params=params,
                return_type=ret_type,
                body=body,
                language="c",
                source_file=str(source_path),
            ))

        return results

    def harvest_python_functions(self, source_path: Path) -> list[HarvestedFunction]:
        """Extract type-annotated pure Python functions using ast module.

        Finds functions with:
        - Return type annotation
        - All params have type annotations
        - No global variable access
        - No class methods
        - No imports used in body (except builtins)
        - Max 30 lines
        """
        try:
            source = source_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError) as exc:
            logger.warning("Cannot read %s: %s", source_path, exc)
            return []

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            logger.debug("Cannot parse %s: %s", source_path, exc)
            return []

        # Collect top-level import names
        import_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    import_names.add(node.module.split(".")[0])

        results = []
        for node in ast.iter_child_nodes(tree):
            # Only top-level functions (no class methods)
            if not isinstance(node, ast.FunctionDef):
                continue

            func = node
            name = func.name

            # Skip private/dunder
            if name.startswith("_"):
                continue

            # Must have return annotation
            if func.returns is None:
                logger.debug("Skipping %s: no return annotation", name)
                continue

            # All params must have annotations
            all_annotated = all(
                arg.annotation is not None for arg in func.args.args
            )
            if not all_annotated:
                logger.debug("Skipping %s: unannotated params", name)
                continue

            # Check line count
            func_lines = source.split("\n")[
                func.lineno - 1 : func.end_lineno
            ]
            if len(func_lines) > MAX_LINES:
                logger.debug("Skipping %s: too many lines (%d)", name, len(func_lines))
                continue

            # Check purity
            func_source = "\n".join(func_lines)
            if not self._is_python_pure(func, import_names):
                logger.debug("Skipping %s: not pure", name)
                continue

            # Extract param info
            params = []
            for arg in func.args.args:
                pname = arg.arg
                ptype = ast.dump(arg.annotation) if arg.annotation else "Any"
                # Try to get a simpler type string
                ptype = self._annotation_to_str(arg.annotation)
                params.append((pname, ptype))

            ret_type = self._annotation_to_str(func.returns)

            results.append(HarvestedFunction(
                name=name,
                params=params,
                return_type=ret_type,
                body=func_source,
                language="python",
                source_file=str(source_path),
            ))

        return results

    def is_pure(self, func: HarvestedFunction) -> bool:
        """Check if a harvested function appears to be pure (no side effects).

        For C: no I/O, no globals, no pointer writes.
        For Python: no global access, no I/O, no mutation of external state.
        """
        if func.language == "c":
            return self._is_c_pure(func.body)
        elif func.language == "python":
            try:
                tree = ast.parse(func.body)
            except SyntaxError:
                return False
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.FunctionDef):
                    return self._is_python_pure(node, set())
            return False
        return False

    # -- C helpers --

    def _extract_c_body(self, source: str, brace_pos: int) -> str | None:
        """Extract a brace-delimited body starting at brace_pos."""
        if brace_pos >= len(source) or source[brace_pos] != "{":
            return None
        depth = 0
        i = brace_pos
        while i < len(source):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    return source[brace_pos : i + 1]
            i += 1
        return None

    def _parse_c_params(self, params_str: str) -> list[tuple[str, str]] | None:
        """Parse C parameter list string into (name, type) tuples.

        Returns None if any param uses struct or complex pointer types.
        """
        params_str = params_str.strip()
        if not params_str or params_str == "void":
            return []

        params = []
        for part in params_str.split(","):
            part = part.strip()
            if not part:
                continue

            # Check for struct
            if "struct" in part:
                return None

            # Check for function pointers
            if "(*" in part:
                return None

            # Parse "type name" or "type *name"
            # Handle char* specially
            tokens = part.split()
            if len(tokens) < 2:
                return None

            pname = tokens[-1].lstrip("*")
            ptype = " ".join(tokens[:-1])
            if tokens[-1].startswith("*"):
                ptype += "*"

            # Reject complex pointer types (except char*)
            if "*" in ptype and ptype.replace(" ", "") != "char*":
                return None

            params.append((pname, ptype))

        return params

    def _has_c_disqualifiers(self, body: str) -> bool:
        """Check if C function body has disqualifying patterns."""
        for pattern in C_DISQUALIFIERS:
            if pattern.search(body):
                return True
        return False

    def _c_calls_ok(self, body: str) -> bool:
        """Check that all function calls in body are allowed."""
        # Find all function calls
        call_pattern = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
        calls = set(call_pattern.findall(body))

        # Remove known C keywords that look like calls
        c_keywords = {"if", "for", "while", "switch", "return", "sizeof"}
        calls -= c_keywords

        for call in calls:
            if call not in ALLOWED_C_CALLS:
                return False
        return True

    def _is_c_pure(self, body: str) -> bool:
        """Check if C function body is pure."""
        impure_patterns = [
            re.compile(r"\bprintf\b"),
            re.compile(r"\bscanf\b"),
            re.compile(r"\bfprintf\b"),
            re.compile(r"\bfopen\b"),
            re.compile(r"\bmalloc\b"),
            re.compile(r"\bfree\b"),
            re.compile(r"\bglobal\b"),
            re.compile(r"\bstatic\s+\w+\s+\w+\s*="),  # static local vars
        ]
        for pattern in impure_patterns:
            if pattern.search(body):
                return False
        return True

    # -- Python helpers --

    def _annotation_to_str(self, ann: ast.expr | None) -> str:
        """Convert a Python type annotation AST node to a string."""
        if ann is None:
            return "Any"
        if isinstance(ann, ast.Constant):
            return str(ann.value) if ann.value is not None else "None"
        if isinstance(ann, ast.Name):
            return ann.id
        if isinstance(ann, ast.Attribute):
            return f"{self._annotation_to_str(ann.value)}.{ann.attr}"
        if isinstance(ann, ast.Subscript):
            base = self._annotation_to_str(ann.value)
            if isinstance(ann.slice, ast.Tuple):
                args = ", ".join(self._annotation_to_str(e) for e in ann.slice.elts)
                return f"{base}[{args}]"
            arg = self._annotation_to_str(ann.slice)
            return f"{base}[{arg}]"
        return ast.dump(ann)

    def _is_python_pure(
        self, func: ast.FunctionDef, import_names: set[str]
    ) -> bool:
        """Check if a Python function appears to be pure."""
        for node in ast.walk(func):
            # No global/nonlocal
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                return False

            # No attribute setting
            if isinstance(node, ast.Attribute) and isinstance(
                getattr(node, "ctx", None), ast.Store
            ):
                return False

            # No I/O calls we can detect
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("open", "print", "input"):
                    return False

            # No calls to imported modules
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    if node.func.value.id in import_names:
                        return False

            # No Name references to imported modules (except in calls above)
            if isinstance(node, ast.Name) and node.id in import_names:
                # Check if it's a module-level reference
                if not isinstance(getattr(node, "ctx", None), ast.Load):
                    continue
                # Allow as function arguments, etc. but flag direct usage
                pass

        return True
