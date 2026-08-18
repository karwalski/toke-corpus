"""AST transpilation from harvested functions to toke.

Uses existing transpilers (CToTokeTranspiler, PyToTokeTranspiler)
to convert harvested functions into toke Phase 2 source code.
"""

import logging
import re

from ingest.ast_harvest import HarvestedFunction
from transpile.py_to_toke import PyToTokeTranspiler, TranspileError as PyTranspileError
from transpile.c_to_toke import CToTokeTranspiler, TranspileError as CTranspileError

logger = logging.getLogger(__name__)

# C type to toke type mapping
C_TYPE_MAP = {
    "int": "i64",
    "long": "i64",
    "unsigned int": "u64",
    "unsigned": "u64",
    "float": "f64",
    "double": "f64",
    "char*": "$str",
    "void": "$void",
}

# Python type to toke type mapping
PY_TYPE_MAP = {
    "int": "i64",
    "float": "f64",
    "bool": "bool",
    "str": "$str",
    "None": "$void",
}


class ASTTranspiler:
    """Transpiles harvested functions to toke using existing transpilers."""

    def __init__(self) -> None:
        self._py_transpiler = PyToTokeTranspiler()
        self._c_transpiler = CToTokeTranspiler()

    def transpile(self, func: HarvestedFunction) -> str | None:
        """Transpile a harvested function to toke.

        Dispatches to language-specific transpiler.

        Returns:
            toke source code string, or None if transpilation fails.
        """
        if func.language == "c":
            return self.transpile_c(func)
        elif func.language == "python":
            return self.transpile_python(func)
        else:
            logger.warning("Unsupported language: %s", func.language)
            return None

    def transpile_c(self, func: HarvestedFunction) -> str | None:
        """Transpile a harvested C function to toke.

        Wraps the function in a minimal C file and uses CToTokeTranspiler.

        Returns:
            toke source code string, or None if transpilation fails.
        """
        # Build a minimal C source with just this function
        module_name = _sanitize_module_name(func.name)
        c_source = _build_c_source(func)

        try:
            result = self._c_transpiler.transpile(c_source, module_name)
            return result
        except (CTranspileError, Exception) as exc:
            logger.debug(
                "C transpilation failed for %s: %s", func.name, exc
            )
            return None

    def transpile_python(self, func: HarvestedFunction) -> str | None:
        """Transpile a harvested Python function to toke.

        Wraps the function body and uses PyToTokeTranspiler.

        Returns:
            toke source code string, or None if transpilation fails.
        """
        module_name = _sanitize_module_name(func.name)

        try:
            result = self._py_transpiler.transpile(func.body, module_name)
            return result
        except (PyTranspileError, Exception) as exc:
            logger.debug(
                "Python transpilation failed for %s: %s", func.name, exc
            )
            return None


def _sanitize_module_name(name: str) -> str:
    """Convert a function name into a valid toke module name.

    No underscores, lowercase, alphanumeric only.
    """
    result = name.lower().replace("_", "")
    result = re.sub(r"[^a-z0-9]", "", result)
    if not result:
        result = "mod"
    if result[0].isdigit():
        result = "m" + result
    return result


def _build_c_source(func: HarvestedFunction) -> str:
    """Build a minimal C source file from a harvested C function."""
    # Build parameter list
    params = []
    for pname, ptype in func.params:
        params.append(f"{ptype} {pname}")
    param_str = ", ".join(params) if params else "void"

    return f"{func.return_type} {func.name}({param_str}) {func.body}"
