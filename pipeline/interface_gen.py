"""Interface-first generation pipeline — Story 9.2.3.

Generates toke interface files (.tki) containing module declarations and
function signatures.  Bodies are generated separately (see body_gen.py)
so that type-correct signatures are locked in before body generation.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Categories (aligned with curriculum_v2)
# ---------------------------------------------------------------------------

PHASE_A_CATEGORIES: list[str] = [
    "A-MTH", "A-STR", "A-ARR", "A-CND", "A-ERR", "A-SRT",
]
DOMAIN_CATEGORIES: list[str] = [
    "D-WEB", "D-DAT", "D-CRY", "D-FIO", "D-CFG", "D-TST", "D-NET", "D-CLI",
]
ALL_CATEGORIES: list[str] = PHASE_A_CATEGORIES + DOMAIN_CATEGORIES

# ---------------------------------------------------------------------------
# Toke Phase 2 type vocabulary
# ---------------------------------------------------------------------------

PRIMITIVE_TYPES: list[str] = ["i64", "u64", "f64", "$str", "bool"]
NUMERIC_TYPES: list[str] = ["i64", "u64", "f64"]
SIMPLE_TYPES: list[str] = ["i64", "$str", "bool"]
ARRAY_TYPES: list[str] = ["@(i64)", "@(u64)", "@(f64)", "@($str)", "@(bool)"]
MAP_TYPES: list[str] = ["@($str:i64)", "@($str:$str)", "@($str:bool)", "@($str:f64)"]

# ---------------------------------------------------------------------------
# Category -> description templates
# ---------------------------------------------------------------------------

_CATEGORY_DESCRIPTIONS: dict[str, list[str]] = {
    "A-MTH": [
        "compute greatest common divisor",
        "check if number is prime",
        "calculate fibonacci at index",
        "sum digits of number",
        "compute factorial",
        "find absolute value",
        "calculate power",
        "count divisors",
    ],
    "A-STR": [
        "reverse a string",
        "count vowels in text",
        "check if palindrome",
        "convert to uppercase",
        "find substring index",
        "trim whitespace",
        "repeat string n times",
        "check if string contains char",
    ],
    "A-ARR": [
        "find maximum in array",
        "sum all elements",
        "reverse array in place",
        "count occurrences of value",
        "find minimum element",
        "compute average",
        "filter positive values",
        "merge two sorted arrays",
    ],
    "A-CND": [
        "classify temperature range",
        "determine grade from score",
        "check leap year",
        "return sign of number",
        "clamp value to range",
        "select larger of two",
        "map day number to name",
        "validate age range",
    ],
    "A-ERR": [
        "parse integer with error handling",
        "safe divide with zero check",
        "validate email format",
        "bounds check array access",
        "validate positive number",
        "check non empty string",
        "validate range input",
        "parse coordinate pair",
    ],
    "A-SRT": [
        "bubble sort array",
        "insertion sort array",
        "find kth smallest element",
        "check if sorted ascending",
        "sort by absolute value",
        "partition around pivot",
        "merge sorted halves",
        "selection sort array",
    ],
    "D-WEB": [
        "parse query string parameters",
        "build url from components",
        "extract path segments",
        "encode string for url",
        "validate http method",
        "parse header line",
    ],
    "D-DAT": [
        "parse date components",
        "days between two dates",
        "check valid date",
        "format date string",
        "day of week calculation",
        "is leap year check",
    ],
    "D-CRY": [
        "caesar cipher encrypt",
        "caesar cipher decrypt",
        "simple xor encode",
        "rot13 transform",
        "compute simple checksum",
        "vigenere encrypt",
    ],
    "D-FIO": [
        "count lines in content",
        "split into lines",
        "join lines with separator",
        "extract file extension",
        "build file path",
        "normalize path separators",
    ],
    "D-CFG": [
        "parse key value pair",
        "merge config maps",
        "lookup with default",
        "validate config keys",
        "count config entries",
        "filter by key prefix",
    ],
    "D-TST": [
        "assert values equal",
        "compare arrays element wise",
        "check within tolerance",
        "validate output format",
        "count passing checks",
        "summarize test results",
    ],
    "D-NET": [
        "parse host and port",
        "validate port range",
        "build address string",
        "extract protocol",
        "check ipv4 format",
        "split address parts",
    ],
    "D-CLI": [
        "parse flag argument",
        "build usage string",
        "validate argument count",
        "extract option value",
        "check flag present",
        "join arguments",
    ],
}

# ---------------------------------------------------------------------------
# Param/return type pools per category
# ---------------------------------------------------------------------------

_CATEGORY_PARAM_TYPES: dict[str, list[str]] = {
    "A-MTH": ["i64", "u64", "f64"],
    "A-STR": ["$str", "i64"],
    "A-ARR": ["@(i64)", "@($str)", "i64"],
    "A-CND": ["i64", "bool", "$str"],
    "A-ERR": ["$str", "i64"],
    "A-SRT": ["@(i64)", "@($str)", "i64"],
    "D-WEB": ["$str"],
    "D-DAT": ["i64", "$str"],
    "D-CRY": ["$str", "i64"],
    "D-FIO": ["$str"],
    "D-CFG": ["$str", "@($str:$str)", "@($str:i64)"],
    "D-TST": ["$str", "i64", "bool", "@(i64)"],
    "D-NET": ["$str", "i64"],
    "D-CLI": ["$str", "@($str)", "i64"],
}

_CATEGORY_RETURN_TYPES: dict[str, list[str]] = {
    "A-MTH": ["i64", "u64", "f64", "bool"],
    "A-STR": ["$str", "i64", "bool"],
    "A-ARR": ["i64", "@(i64)", "f64"],
    "A-CND": ["$str", "i64", "bool"],
    "A-ERR": ["bool", "$str", "i64"],
    "A-SRT": ["@(i64)", "bool"],
    "D-WEB": ["$str", "@($str:$str)"],
    "D-DAT": ["i64", "bool", "$str"],
    "D-CRY": ["$str"],
    "D-FIO": ["i64", "$str", "@($str)"],
    "D-CFG": ["$str", "i64", "bool", "@($str:$str)"],
    "D-TST": ["bool", "i64", "$str"],
    "D-NET": ["$str", "i64", "bool"],
    "D-CLI": ["$str", "bool", "i64"],
}

# ---------------------------------------------------------------------------
# Error type templates
# ---------------------------------------------------------------------------

_ERROR_TYPES: list[tuple[str, list[str]]] = [
    ("ParseErr", ["badformat", "empty", "overflow"]),
    ("ValidErr", ["outofrange", "invalid", "missing"]),
    ("LookupErr", ["notfound", "ambiguous"]),
    ("IoErr", ["notfound", "denied", "timeout"]),
]

# ---------------------------------------------------------------------------
# FuncSig dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FuncSig:
    """A single function signature in an interface."""
    name: str
    params: list[tuple[str, str]]  # (name, type)
    ret_type: str


@dataclass(frozen=True)
class InterfaceSpec:
    """A complete .tki interface specification."""
    module_name: str
    functions: list[FuncSig]
    error_types: list[tuple[str, list[str]]]  # (name, [variants])
    category: str
    difficulty: int
    seed: int


# ---------------------------------------------------------------------------
# InterfaceGenerator
# ---------------------------------------------------------------------------

class InterfaceGenerator:
    """Generates toke interface files (.tki) with module declarations
    and function signatures.

    Deterministic: same (category, difficulty, seed) -> same output.
    """

    def generate_interface(self, category: str, difficulty: int, seed: int) -> str:
        """Generate a complete .tki interface string.

        Args:
            category: One of ALL_CATEGORIES (e.g. "A-MTH", "D-WEB").
            difficulty: 1-5 difficulty tier.
            seed: Random seed for deterministic generation.

        Returns:
            A toke interface file as a string.
        """
        spec = self.generate_spec(category, difficulty, seed)
        return self.render(spec)

    def generate_spec(self, category: str, difficulty: int, seed: int) -> InterfaceSpec:
        """Generate the structured InterfaceSpec (useful for testing)."""
        if category not in ALL_CATEGORIES:
            raise ValueError(f"Unknown category: {category!r}")
        if not 1 <= difficulty <= 5:
            raise ValueError(f"Difficulty must be 1-5, got {difficulty}")

        rng = random.Random(seed)
        mod_name = self._module_name(category, seed, rng)
        num_funcs = self._num_functions(difficulty, rng)
        error_types = self._error_types(difficulty, rng)
        functions = self._generate_functions(category, difficulty, num_funcs, rng)

        return InterfaceSpec(
            module_name=mod_name,
            functions=functions,
            error_types=error_types,
            category=category,
            difficulty=difficulty,
            seed=seed,
        )

    def render(self, spec: InterfaceSpec) -> str:
        """Render an InterfaceSpec to a .tki interface string."""
        lines: list[str] = [f"m={spec.module_name};"]

        # Error type declarations
        for ename, variants in spec.error_types:
            variant_str = ";".join(variants)
            lines.append(f"e={ename}{{{variant_str};}}")

        # Function signatures (interface = semicolon instead of body)
        for func in spec.functions:
            param_str = ";".join(f"{p[0]}:{p[1]}" for p in func.params)
            lines.append(f"f={func.name}({param_str}):{func.ret_type};")

        return "\n".join(lines)

    # -- private helpers ----------------------------------------------------

    def _module_name(self, category: str, seed: int, rng: random.Random) -> str:
        """Generate a module name from category and seed."""
        prefix = category.lower().replace("-", "")
        suffix = rng.randint(100, 999)
        return f"{prefix}{suffix}"

    def _num_functions(self, difficulty: int, rng: random.Random) -> int:
        """Determine number of functions based on difficulty."""
        if difficulty <= 2:
            return 1
        if difficulty <= 4:
            return rng.randint(2, 3)
        # difficulty 5
        return rng.randint(3, 5)

    def _error_types(
        self, difficulty: int, rng: random.Random
    ) -> list[tuple[str, list[str]]]:
        """Generate error type declarations for higher difficulties."""
        if difficulty < 3:
            return []
        if difficulty <= 4:
            # Maybe one error type
            if rng.random() < 0.5:
                et = rng.choice(_ERROR_TYPES)
                return [et]
            return []
        # difficulty 5: always at least one
        count = rng.randint(1, 2)
        selected = rng.sample(_ERROR_TYPES, min(count, len(_ERROR_TYPES)))
        return selected

    def _generate_functions(
        self,
        category: str,
        difficulty: int,
        num_funcs: int,
        rng: random.Random,
    ) -> list[FuncSig]:
        """Generate function signatures for the interface."""
        descriptions = list(_CATEGORY_DESCRIPTIONS.get(category, []))
        rng.shuffle(descriptions)

        param_pool = _CATEGORY_PARAM_TYPES.get(category, SIMPLE_TYPES)
        ret_pool = _CATEGORY_RETURN_TYPES.get(category, SIMPLE_TYPES)
        name_counter = 0

        funcs: list[FuncSig] = []
        for i in range(num_funcs):
            # Pick a description-derived name
            if i < len(descriptions):
                fname = self._name_from_description(descriptions[i])
            else:
                name_counter += 1
                fname = f"helper{name_counter}"

            num_params = self._num_params(difficulty, rng)
            params = self._generate_params(num_params, param_pool, difficulty, rng)
            ret_type = self._pick_return_type(ret_pool, difficulty, rng)

            funcs.append(FuncSig(name=fname, params=params, ret_type=ret_type))

        return funcs

    def _name_from_description(self, desc: str) -> str:
        """Convert a description like 'compute greatest common divisor' to a
        valid toke function name like 'computegcd'."""
        words = desc.lower().split()
        # Take first word + abbreviation of remaining words
        if len(words) <= 2:
            return "".join(words)
        # first word full, rest abbreviated
        name = words[0] + "".join(w[:3] for w in words[1:])
        # Strip any non-alpha
        name = "".join(c for c in name if c.isalpha())
        return name[:20]  # keep it reasonable

    def _num_params(self, difficulty: int, rng: random.Random) -> int:
        """Number of parameters based on difficulty."""
        if difficulty <= 2:
            return rng.randint(1, 2)
        if difficulty <= 4:
            return rng.randint(1, 3)
        return rng.randint(2, 4)

    def _generate_params(
        self,
        count: int,
        pool: list[str],
        difficulty: int,
        rng: random.Random,
    ) -> list[tuple[str, str]]:
        """Generate named, typed parameters."""
        param_names = ["a", "b", "c", "d", "e", "f"]
        params: list[tuple[str, str]] = []
        for i in range(count):
            pname = param_names[i] if i < len(param_names) else f"p{i}"
            if difficulty >= 4 and rng.random() < 0.3:
                # Use a complex type at higher difficulty
                ptype = rng.choice(ARRAY_TYPES + MAP_TYPES)
            else:
                ptype = rng.choice(pool)
            params.append((pname, ptype))
        return params

    def _pick_return_type(
        self, pool: list[str], difficulty: int, rng: random.Random
    ) -> str:
        """Pick return type, allowing complex types at higher difficulty."""
        if difficulty >= 4 and rng.random() < 0.3:
            return rng.choice(ARRAY_TYPES + MAP_TYPES)
        return rng.choice(pool)
