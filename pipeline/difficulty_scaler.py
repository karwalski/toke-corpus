"""Difficulty scaler for toke programs (Story 9.3.4).

Takes simple valid toke programs and applies progressive complexity layers:
  Level 1 -> 2: Add input validation (if param<0 checks)
  Level 2 -> 3: Extract helper function
  Level 3 -> 4: Add array accumulation
  Level 4 -> 5: Add error handling with error types
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Regex patterns for parsing toke source
# ---------------------------------------------------------------------------

# Module declaration: m=name;
_MODULE_RE = re.compile(r"m=(?P<name>\w+);")

# Function: f=name(params):rettype{body}
# Handles nested braces by matching the outermost pair.
_FUNC_RE = re.compile(
    r"f=(?P<name>\w+)"
    r"\((?P<params>[^)]*)\)"
    r":(?P<ret>[a-zA-Z0-9$@():\[\]]+)"
    r"\{(?P<body>[^}]*(?:\{[^}]*\}[^}]*)*)\}"
)

# Individual param: name:type
_PARAM_RE = re.compile(r"(?P<name>\w+):(?P<type>[a-zA-Z0-9$@()]+)")

# Return expression: <expr
_RETURN_RE = re.compile(r"<(?P<expr>[^}]+?)(?=\}|$)")

# Numeric types that support <0 checks
_NUMERIC_TYPES = {"i64", "u64", "f64"}

# String type
_STRING_TYPE = "$str"


class DifficultyScaler:
    """Apply progressive complexity layers to toke source programs."""

    def scale_up(self, source: str, level: int) -> str | None:
        """Apply complexity layer to move source from *level* to *level+1*.

        Args:
            source: toke source code at the given level
            level: current difficulty level (1-4)

        Returns:
            Transformed source at level+1, or None if transform cannot apply.
        """
        if level == 1:
            return self._add_validation(source)
        if level == 2:
            return self._add_helper(source)
        if level == 3:
            return self._add_array_accumulation(source)
        if level == 4:
            return self._add_error_handling(source)
        return None

    # ------------------------------------------------------------------
    # Level 1 -> 2: Add input validation
    # ------------------------------------------------------------------

    def _add_validation(self, source: str) -> str | None:
        """Add if param<0 or param="" guard to the first function."""
        func_match = _FUNC_RE.search(source)
        if not func_match:
            return None

        params_str = func_match.group("params")
        if not params_str:
            return None

        params = _PARAM_RE.findall(params_str)
        if not params:
            return None

        # Find a parameter suitable for validation
        guard_param = None
        guard_type = None
        for pname, ptype in params:
            if ptype in _NUMERIC_TYPES:
                guard_param = pname
                guard_type = ptype
                break
            if ptype == _STRING_TYPE:
                guard_param = pname
                guard_type = ptype
                break

        if guard_param is None:
            return None

        body = func_match.group("body")
        ret_type = func_match.group("ret")

        # Build the default return value for the guard
        default_val = self._default_value(ret_type)

        # Build guard condition
        if guard_type in _NUMERIC_TYPES:
            guard = f"if {guard_param}<0{{<{default_val}}}el{{{body}}}"
        else:
            # String emptiness check
            guard = f'if {guard_param}=""{{<{default_val}}}el{{{body}}}'

        # Reconstruct the function with the guard wrapping the body
        fname = func_match.group("name")
        new_func = f"f={fname}({params_str}):{ret_type}{{{guard}}}"

        result = source[: func_match.start()] + new_func + source[func_match.end() :]
        return result

    # ------------------------------------------------------------------
    # Level 2 -> 3: Extract helper function
    # ------------------------------------------------------------------

    def _add_helper(self, source: str) -> str | None:
        """Extract the core logic of the main function into a helper."""
        mod_match = _MODULE_RE.search(source)
        if not mod_match:
            return None

        func_match = _FUNC_RE.search(source)
        if not func_match:
            return None

        fname = func_match.group("name")
        params_str = func_match.group("params")
        ret_type = func_match.group("ret")
        body = func_match.group("body")

        params = _PARAM_RE.findall(params_str)
        if not params:
            return None

        # Find the innermost return expression to extract
        # Look for the core computation (the el{...} branch or the body itself)
        el_match = re.search(r"el\{([^}]+)\}", body)
        if el_match:
            core_body = el_match.group(1)
        else:
            core_body = body

        # The core body should have a return expression
        ret_match = _RETURN_RE.search(core_body)
        if not ret_match:
            return None

        ret_expr = ret_match.group("expr").strip()

        # Generate helper function name
        helper_name = self._helper_name(fname)

        # Build the helper function with the same params and return type
        helper_func = f"f={helper_name}({params_str}):{ret_type}{{<{ret_expr}}}"

        # Build a call to the helper using the params
        call_args = ";".join(pname for pname, _ in params)
        call_expr = f"{helper_name}({call_args})"

        # Replace the core return with a let binding + return of the helper call
        new_core = f"let r={call_expr};<r"

        if el_match:
            new_body = body[: el_match.start()] + f"el{{" + new_core + "}}"
            if el_match.end() < len(body):
                new_body += body[el_match.end() :]
        else:
            new_body = new_core

        new_main = f"f={fname}({params_str}):{ret_type}{{{new_body}}}"

        # Insert helper before the main function
        result = source[: func_match.start()] + helper_func + new_main + source[func_match.end() :]
        return result

    # ------------------------------------------------------------------
    # Level 3 -> 4: Add array accumulation
    # ------------------------------------------------------------------

    def _add_array_accumulation(self, source: str) -> str | None:
        """Convert scalar return to array-based accumulation pattern."""
        mod_match = _MODULE_RE.search(source)
        if not mod_match:
            return None

        # Find all functions
        funcs = list(_FUNC_RE.finditer(source))
        if not funcs:
            return None

        # Find the last function (the main entry point)
        main_func = funcs[-1]
        fname = main_func.group("name")
        params_str = main_func.group("params")
        ret_type = main_func.group("ret")
        body = main_func.group("body")

        # Only works on numeric return types
        if ret_type not in _NUMERIC_TYPES:
            return None

        params = _PARAM_RE.findall(params_str)

        # Build array accumulation version
        arr_type = f"@({ret_type})"

        # Find the return expression in the el{} branch if present,
        # otherwise search the whole body
        el_match = re.search(r"el\{([^}]*(?:\{[^}]*\}[^}]*)*)}", body)
        if el_match:
            el_body = el_match.group(1)
            ret_match = _RETURN_RE.search(el_body)
            if not ret_match:
                return None
            ret_expr = ret_match.group("expr").strip()
        else:
            ret_match = _RETURN_RE.search(body)
            if not ret_match:
                return None
            ret_expr = ret_match.group("expr").strip()

        # Build new body: let arr=mut.@(rettype); let val=expr; arr.push(val); <arr.get(0)
        new_body = (
            f"let arr=mut.{arr_type};"
            f"let val={ret_expr};"
            f"arr.push(val);"
            f"<arr.get(0)"
        )

        # Replace the body in the el{} block if it exists, or the whole body
        if el_match:
            new_full_body = body[: el_match.start()] + "el{" + new_body + "}"
            if el_match.end() < len(body):
                new_full_body += body[el_match.end() :]
        else:
            new_full_body = new_body

        new_main = f"f={fname}({params_str}):{ret_type}{{{new_full_body}}}"

        # Replace just the main function
        result = source[: main_func.start()] + new_main + source[main_func.end() :]
        return result

    # ------------------------------------------------------------------
    # Level 4 -> 5: Add error handling
    # ------------------------------------------------------------------

    def _add_error_handling(self, source: str) -> str | None:
        """Wrap function logic with error type returns."""
        mod_match = _MODULE_RE.search(source)
        if not mod_match:
            return None

        mod_name = mod_match.group("name")

        funcs = list(_FUNC_RE.finditer(source))
        if not funcs:
            return None

        # Find the last function (main entry)
        main_func = funcs[-1]
        fname = main_func.group("name")
        params_str = main_func.group("params")
        body = main_func.group("body")

        params = _PARAM_RE.findall(params_str)

        # Define an error type
        err_name = f"{fname}err"
        err_decl = f"e={err_name}{{invalid;overflow;}}"

        # Find the validation guard (if ... <default ... el { ... })
        guard_match = re.search(
            r"if\s+(\w+[<>=!][^{]*)\{<([^}]*)\}el\{([^}]*(?:\{[^}]*\}[^}]*)*)}", body
        )

        if guard_match:
            cond = guard_match.group(1)
            el_body = guard_match.group(3)
            # Replace the guard's default return with an error return
            # and wrap the el body in success logic
            new_body = (
                f"if {cond}{{<0}}el{{{el_body}}}"
            )
        else:
            # No guard found -- wrap the whole body with a simple error check
            if params:
                first_param = params[0][0]
                first_type = params[0][1]
                if first_type in _NUMERIC_TYPES:
                    new_body = f"if {first_param}<0{{<0}}el{{{body}}}"
                else:
                    new_body = body
            else:
                new_body = body

        new_main = f"f={fname}({params_str}):{main_func.group('ret')}{{{new_body}}}"

        # Insert error type declaration after module declaration
        mod_end = mod_match.end()
        result = source[:mod_end] + err_decl + source[mod_end : main_func.start()] + new_main + source[main_func.end() :]
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _default_value(ret_type: str) -> str:
        """Return a sensible default value for a given toke type."""
        if ret_type in ("i64", "u64"):
            return "0"
        if ret_type == "f64":
            return "0.0"
        if ret_type == "bool":
            return "false"
        if ret_type == "$str":
            return '""'
        if ret_type == "$void":
            return ""
        return "0"

    @staticmethod
    def _helper_name(fname: str) -> str:
        """Generate a helper function name from the main function name."""
        # Avoid name collisions by prepending or appending
        if fname.startswith("fn"):
            return fname.replace("fn", "h", 1)
        return f"{fname}h"
