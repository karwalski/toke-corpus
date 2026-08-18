"""
Extended task curriculum generator — Story 9.2.1.

Extends the Phase A curriculum (50K tasks, 6 categories) with 8 new domain
categories (D-WEB, D-DAT, D-CRY, D-FIO, D-CFG, D-TST, D-NET, D-CLI) to
produce 100K+ deterministic, reproducible task specifications for corpus
generation.

Each new domain task includes structured test cases and domain context
(relevant stdlib signatures) to give the LLM grounding.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from generator.curriculum import (
    CATEGORIES as PHASE_A_CATEGORIES,
    CurriculumGenerator as PhaseACurriculumGenerator,
    TaskSpec as PhaseATaskSpec,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# All categories (Phase A + Domain)
# ---------------------------------------------------------------------------

DOMAIN_CATEGORIES: list[str] = [
    "D-WEB", "D-DAT", "D-CRY", "D-FIO", "D-CFG", "D-TST", "D-NET", "D-CLI",
]

ALL_CATEGORIES: list[str] = list(PHASE_A_CATEGORIES) + DOMAIN_CATEGORIES

# ---------------------------------------------------------------------------
# Toke type vocabulary
# ---------------------------------------------------------------------------

NUMERIC_TYPES: list[str] = ["i64", "u64", "f64"]
ALL_TYPES: list[str] = ["i64", "u64", "f64", "$str", "bool"]
ARRAY_ELEM_TYPES: list[str] = ["i64", "u64", "f64", "$str"]

# Valid toke types for validation
VALID_TOKE_TYPES: set[str] = {
    "i64", "u64", "f64", "$str", "bool", "$void",
    "@(i64)", "@(u64)", "@(f64)", "@($str)", "@(bool)",
    "@($str:$str)", "@($str:i64)", "@($str:bool)", "@($str:f64)",
    "@($str:@($str))", "Opt<$str>", "Opt<i64>", "Opt<f64>",
    "Opt<bool>", "Res<$str>", "Res<i64>", "Res<@($str)>",
}

# ---------------------------------------------------------------------------
# TestCase dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestCase:
    """A single input/output test case for a task."""
    inputs: list[Any]
    expected: Any


# ---------------------------------------------------------------------------
# TaskSpecV2 dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskSpecV2:
    """An extended task specification with structured test cases.

    Attributes:
        task_id: Unique identifier (e.g. ``D-WEB-0001``).
        category: Category code (Phase A or Domain).
        description: Natural-language task description.
        input_types: Parameter types in toke notation.
        output_type: Return type in toke notation.
        test_cases: 3 input/output pairs for validation.
        difficulty: Difficulty tier 1-5.
        domain_context: Relevant stdlib signatures for LLM grounding.
    """
    task_id: str
    category: str
    description: str
    input_types: list[str] = field(default_factory=list)
    output_type: str = "$str"
    test_cases: list[TestCase] = field(default_factory=list)
    difficulty: int = 1
    domain_context: str = ""


# ---------------------------------------------------------------------------
# Sequencer (domain-aware)
# ---------------------------------------------------------------------------


class _Sequencer:
    """Generates sequential task IDs for a category."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._counter = 0

    def next(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:04d}"


# ---------------------------------------------------------------------------
# CurriculumGeneratorV2
# ---------------------------------------------------------------------------


class CurriculumGeneratorV2:
    """Deterministic generator of 100K+ task specifications.

    Combines the original Phase A generator (50K tasks across 6 categories)
    with new domain generators (50K+ tasks across 8 categories) for a total
    of 100K+ tasks.

    Uses ``random.Random(seed)`` for all random choices.
    """

    def __init__(self, seed: int = 42, phase_a_tasks: int = 50_000,
                 domain_tasks: int = 55_000) -> None:
        self._seed = seed
        self._phase_a_tasks = phase_a_tasks
        self._domain_tasks = domain_tasks
        self._rng = random.Random(seed)

    # -- public API ---------------------------------------------------------

    def generate(self) -> list[TaskSpecV2]:
        """Generate the full curriculum: Phase A + Domain tasks.

        Returns a deterministic list of 100K+ TaskSpecV2 objects.
        """
        # Phase A tasks (converted to V2 format)
        phase_a = self._generate_phase_a()

        # Domain tasks
        domain = self._generate_domain()

        # Interleave: all Phase A first, then domain round-robin
        all_tasks = phase_a + domain

        logger.info(
            "Generated %d total tasks (%d Phase A + %d Domain, seed=%d)",
            len(all_tasks), len(phase_a), len(domain), self._seed,
        )
        return all_tasks

    def generate_domain_category(self, category: str, count: int) -> list[TaskSpecV2]:
        """Generate *count* tasks for a single domain category."""
        if category not in DOMAIN_CATEGORIES:
            raise ValueError(f"Unknown domain category: {category!r}")

        pool = self._expand_domain_category(category)
        self._rng.shuffle(pool)

        if count <= len(pool):
            return pool[:count]

        result: list[TaskSpecV2] = list(pool)
        cycle = 1
        while len(result) < count:
            for spec in pool:
                if len(result) >= count:
                    break
                variant = self._mutate_variant(spec, cycle)
                result.append(variant)
            cycle += 1

        return result[:count]

    # -- Phase A conversion -------------------------------------------------

    def _generate_phase_a(self) -> list[TaskSpecV2]:
        """Generate Phase A tasks using the original generator, convert to V2."""
        gen = PhaseACurriculumGenerator(seed=self._seed,
                                        total_tasks=self._phase_a_tasks)
        phase_a_tasks = gen.generate()
        return [self._convert_phase_a(t) for t in phase_a_tasks]

    @staticmethod
    def _convert_phase_a(task: PhaseATaskSpec) -> TaskSpecV2:
        """Convert a Phase A TaskSpec to TaskSpecV2 format."""
        # Parse signature to extract input_types and output_type
        # Format: F=name(a:i64;b:i64):i64
        sig = task.expected_signature
        input_types: list[str] = []
        output_type = "$void"

        if "(" in sig and "):" in sig:
            params_str = sig.split("(", 1)[1].split(")", 1)[0]
            ret_str = sig.split("):", 1)[1]
            output_type = ret_str.strip()
            for part in params_str.split(";"):
                if ":" in part:
                    ty = part.split(":", 1)[1].strip()
                    if ty:
                        input_types.append(ty)

        return TaskSpecV2(
            task_id=task.task_id,
            category=task.category,
            description=task.description,
            input_types=input_types,
            output_type=output_type,
            test_cases=[],  # Phase A tasks don't have structured test cases
            difficulty=min(task.difficulty, 5),
            domain_context="",
        )

    # -- Domain generation --------------------------------------------------

    def _generate_domain(self) -> list[TaskSpecV2]:
        """Generate domain tasks, round-robin across domain categories."""
        per_category = self._domain_tasks // len(DOMAIN_CATEGORIES)
        remainder = self._domain_tasks % len(DOMAIN_CATEGORIES)

        per_cat_tasks: dict[str, list[TaskSpecV2]] = {}
        for idx, cat in enumerate(DOMAIN_CATEGORIES):
            count = per_category + (1 if idx < remainder else 0)
            per_cat_tasks[cat] = self.generate_domain_category(cat, count)

        # Round-robin interleave
        tasks: list[TaskSpecV2] = []
        max_len = max(len(v) for v in per_cat_tasks.values())
        for i in range(max_len):
            for cat in DOMAIN_CATEGORIES:
                cat_tasks = per_cat_tasks[cat]
                if i < len(cat_tasks):
                    tasks.append(cat_tasks[i])

        return tasks

    def _expand_domain_category(self, category: str) -> list[TaskSpecV2]:
        dispatch = {
            "D-WEB": self._expand_web,
            "D-DAT": self._expand_data,
            "D-CRY": self._expand_crypto,
            "D-FIO": self._expand_file_io,
            "D-CFG": self._expand_config,
            "D-TST": self._expand_testing,
            "D-NET": self._expand_network,
            "D-CLI": self._expand_cli,
        }
        return dispatch[category]()

    def _mutate_variant(self, spec: TaskSpecV2, cycle: int) -> TaskSpecV2:
        """Create a variant of *spec* for pool cycling."""
        param_names = [
            ("x", "y"), ("n", "m"), ("val", "other"),
            ("lhs", "rhs"), ("first", "second"), ("p", "q"),
            ("num1", "num2"), ("left", "right"),
        ]
        constraints = [
            "Use a helper variable to store the intermediate result.",
            "Handle the edge case where the input is empty by returning early.",
            "Use a conditional to handle invalid inputs specially.",
            "Accumulate the result in a mutable binding.",
            "Use nested if/el blocks for the control flow.",
            "Add explicit type annotations to all local bindings.",
            "Split the logic into two passes over the data.",
            "Use pattern matching where possible.",
        ]

        pair_idx = cycle % len(param_names)
        constraint_idx = cycle % len(constraints)
        p1, p2 = param_names[pair_idx]
        constraint = constraints[constraint_idx]

        mutated_desc = (
            f"{spec.description}. Variant {cycle}: use parameter names "
            f"{p1} and {p2}. {constraint}"
        )

        return TaskSpecV2(
            task_id=f"{spec.task_id}v{cycle}",
            category=spec.category,
            description=mutated_desc,
            input_types=list(spec.input_types),
            output_type=spec.output_type,
            test_cases=list(spec.test_cases),
            difficulty=spec.difficulty,
            domain_context=spec.domain_context,
        )

    # =====================================================================
    # D-WEB: Web handler patterns
    # =====================================================================

    def _expand_web(self) -> list[TaskSpecV2]:
        tasks: list[TaskSpecV2] = []
        seq = _Sequencer("D-WEB")

        # --- Query string parsing ---
        qs_ops = [
            ("parseQueryParam", "Extract a single query parameter value from a URL query string",
             ["$str", "$str"], "$str",
             [TestCase(["name=alice&age=30", "name"], "alice"),
              TestCase(["color=red&size=lg", "size"], "lg"),
              TestCase(["key=val", "missing"], "")]),
            ("parseAllQueryParams", "Parse a URL query string into a key=value representation, returning all pairs joined by newlines",
             ["$str"], "$str",
             [TestCase(["a=1&b=2"], "a=1\nb=2"),
              TestCase(["key=val"], "key=val"),
              TestCase([""], "")]),
            ("countQueryParams", "Count the number of query parameters in a URL query string",
             ["$str"], "i64",
             [TestCase(["a=1&b=2&c=3"], 3),
              TestCase(["key=val"], 1),
              TestCase([""], 0)]),
            ("hasQueryParam", "Check whether a specific query parameter exists in the query string",
             ["$str", "$str"], "bool",
             [TestCase(["a=1&b=2", "b"], True),
              TestCase(["x=10", "y"], False),
              TestCase(["", "k"], False)]),
        ]
        for fn, desc, in_types, out_type, cases in qs_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-WEB",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=contains(s:$str;sub:$str):bool",
            ))

        # --- Route matching ---
        route_ops = [
            ("matchRoute", "Given a URL path and a route pattern with :param placeholders, return whether the path matches the pattern structure",
             ["$str", "$str"], "bool",
             [TestCase(["/users/42", "/users/:id"], True),
              TestCase(["/users/42/posts", "/users/:id"], False),
              TestCase(["/api/v1", "/api/v1"], True)]),
            ("extractPathSegment", "Extract the Nth segment (0-indexed) from a URL path separated by /",
             ["$str", "i64"], "$str",
             [TestCase(["/api/users/42", 2], "42"),
              TestCase(["/home", 0], "home"),
              TestCase(["/a/b/c", 1], "b")]),
            ("countPathSegments", "Count the number of non-empty path segments in a URL path",
             ["$str"], "i64",
             [TestCase(["/api/users/42"], 3),
              TestCase(["/"], 0),
              TestCase(["/a/b/c/d"], 4)]),
            ("joinPath", "Join two URL path segments with a / separator, avoiding double slashes",
             ["$str", "$str"], "$str",
             [TestCase(["/api/", "/users"], "/api/users"),
              TestCase(["/api", "users"], "/api/users"),
              TestCase(["", "/home"], "/home")]),
        ]
        for fn, desc, in_types, out_type, cases in route_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-WEB",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=split(s:$str;delim:$str):@($str) F=len(a:@($str)):i64",
            ))

        # --- Response building ---
        resp_ops = [
            ("statusText", "Given an HTTP status code, return the standard reason phrase",
             ["i64"], "$str",
             [TestCase([200], "OK"),
              TestCase([404], "Not Found"),
              TestCase([500], "Internal Server Error")]),
            ("buildHeader", "Build an HTTP header line from a key and value",
             ["$str", "$str"], "$str",
             [TestCase(["Content-Type", "text/html"], "Content-Type: text/html"),
              TestCase(["X-Req-Id", "abc"], "X-Req-Id: abc"),
              TestCase(["Host", "example.com"], "Host: example.com")]),
            ("isSuccessStatus", "Return true if the HTTP status code is in the 2xx range",
             ["i64"], "bool",
             [TestCase([200], True),
              TestCase([301], False),
              TestCase([204], True)]),
            ("isRedirectStatus", "Return true if the HTTP status code is in the 3xx range",
             ["i64"], "bool",
             [TestCase([301], True),
              TestCase([200], False),
              TestCase([308], True)]),
            ("buildContentType", "Given a file extension, return the appropriate MIME content type",
             ["$str"], "$str",
             [TestCase(["html"], "text/html"),
              TestCase(["json"], "application/json"),
              TestCase(["css"], "text/css")]),
        ]
        for fn, desc, in_types, out_type, cases in resp_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-WEB",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=concat(a:$str;b:$str):$str F=eq(a:$str;b:$str):bool",
            ))

        # --- Request parsing ---
        req_ops = [
            ("extractMethod", "Extract the HTTP method from a raw request line",
             ["$str"], "$str",
             [TestCase(["GET /api/users HTTP/1.1"], "GET"),
              TestCase(["POST /login HTTP/1.1"], "POST"),
              TestCase(["DELETE /items/5 HTTP/1.1"], "DELETE")]),
            ("extractPath", "Extract the path from a raw HTTP request line",
             ["$str"], "$str",
             [TestCase(["GET /api/users HTTP/1.1"], "/api/users"),
              TestCase(["POST /login HTTP/1.1"], "/login"),
              TestCase(["GET / HTTP/1.1"], "/")]),
            ("extractHttpVersion", "Extract the HTTP version from a raw request line",
             ["$str"], "$str",
             [TestCase(["GET / HTTP/1.1"], "HTTP/1.1"),
              TestCase(["POST /x HTTP/2.0"], "HTTP/2.0"),
              TestCase(["GET /a HTTP/1.0"], "HTTP/1.0")]),
            ("isGetRequest", "Return true if the request line is a GET request",
             ["$str"], "bool",
             [TestCase(["GET / HTTP/1.1"], True),
              TestCase(["POST /x HTTP/1.1"], False),
              TestCase(["GET /api HTTP/2"], True)]),
            ("isPostRequest", "Return true if the request line is a POST request",
             ["$str"], "bool",
             [TestCase(["POST /login HTTP/1.1"], True),
              TestCase(["GET / HTTP/1.1"], False),
              TestCase(["POST /data HTTP/2"], True)]),
            ("normalizeMethod", "Convert an HTTP method string to uppercase",
             ["$str"], "$str",
             [TestCase(["get"], "GET"),
              TestCase(["Post"], "POST"),
              TestCase(["DELETE"], "DELETE")]),
        ]
        for fn, desc, in_types, out_type, cases in req_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-WEB",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=upper(s:$str):$str",
            ))

        # --- URL encoding/decoding ---
        enc_ops = [
            ("encodeSpace", "Replace all spaces in a string with %20 for URL encoding",
             ["$str"], "$str",
             [TestCase(["hello world"], "hello%20world"),
              TestCase(["no spaces"], "no spaces"),
              TestCase(["a b c"], "a%20b%20c")]),
            ("decodeSpace", "Replace all %20 sequences in a string with spaces",
             ["$str"], "$str",
             [TestCase(["hello%20world"], "hello world"),
              TestCase(["nospaces"], "nospaces"),
              TestCase(["a%20b%20c"], "a b c")]),
            ("extractFragment", "Extract the fragment (after #) from a URL, or return empty string if none",
             ["$str"], "$str",
             [TestCase(["http://x.com/page#section"], "section"),
              TestCase(["http://x.com/page"], ""),
              TestCase(["#top"], "top")]),
            ("extractHost", "Extract the host from a URL of the form scheme://host/path",
             ["$str"], "$str",
             [TestCase(["http://example.com/page"], "example.com"),
              TestCase(["https://api.test.io/v1"], "api.test.io"),
              TestCase(["http://localhost:8080/"], "localhost:8080")]),
            ("extractScheme", "Extract the scheme (http, https, etc.) from a URL",
             ["$str"], "$str",
             [TestCase(["http://example.com"], "http"),
              TestCase(["https://secure.io"], "https"),
              TestCase(["ftp://files.net"], "ftp")]),
        ]
        for fn, desc, in_types, out_type, cases in enc_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-WEB",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=replace(s:$str;old:$str;new:$str):$str F=split(s:$str;delim:$str):@($str)",
            ))

        # --- Cookie handling ---
        cookie_ops = [
            ("parseCookieValue", "Extract the value of a named cookie from a Cookie header string (key=val; key2=val2 format)",
             ["$str", "$str"], "$str",
             [TestCase(["session=abc; theme=dark", "theme"], "dark"),
              TestCase(["id=123", "id"], "123"),
              TestCase(["a=1; b=2", "c"], "")]),
            ("countCookies", "Count the number of cookies in a Cookie header string",
             ["$str"], "i64",
             [TestCase(["a=1; b=2; c=3"], 3),
              TestCase(["session=x"], 1),
              TestCase([""], 0)]),
            ("buildSetCookie", "Build a Set-Cookie header value from name, value, and max-age seconds",
             ["$str", "$str", "i64"], "$str",
             [TestCase(["session", "abc", 3600], "session=abc; Max-Age=3600"),
              TestCase(["id", "42", 0], "id=42; Max-Age=0"),
              TestCase(["theme", "dark", 86400], "theme=dark; Max-Age=86400")]),
        ]
        for fn, desc, in_types, out_type, cases in cookie_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-WEB",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=split(s:$str;delim:$str):@($str) F=trim(s:$str):$str F=concat(a:$str;b:$str):$str",
            ))

        return tasks

    # =====================================================================
    # D-DAT: Data processing
    # =====================================================================

    def _expand_data(self) -> list[TaskSpecV2]:
        tasks: list[TaskSpecV2] = []
        seq = _Sequencer("D-DAT")

        # --- CSV parsing ---
        csv_ops = [
            ("csvColumnCount", "Count the number of columns in a CSV header line",
             ["$str"], "i64",
             [TestCase(["name,age,city"], 3),
              TestCase(["id"], 1),
              TestCase(["a,b,c,d,e"], 5)]),
            ("csvGetField", "Extract the Nth field (0-indexed) from a CSV row",
             ["$str", "i64"], "$str",
             [TestCase(["alice,30,sydney", 0], "alice"),
              TestCase(["alice,30,sydney", 2], "sydney"),
              TestCase(["x,y", 1], "y")]),
            ("csvRowCount", "Count the number of data rows (excluding header) in CSV text (lines separated by newline)",
             ["$str"], "i64",
             [TestCase(["name,age\nalice,30\nbob,25"], 2),
              TestCase(["h\n"], 0),
              TestCase(["h\na\nb\nc"], 3)]),
            ("csvExtractColumn", "Extract all values from a specific column index across all rows (excluding header) of CSV text",
             ["$str", "i64"], "@($str)",
             [TestCase(["name,age\nalice,30\nbob,25", 0], ["alice", "bob"]),
              TestCase(["a,b\n1,2\n3,4", 1], ["2", "4"]),
              TestCase(["x\n1\n2\n3", 0], ["1", "2", "3"])]),
        ]
        for fn, desc, in_types, out_type, cases in csv_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-DAT",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=len(a:@($str)):i64",
            ))

        # --- Aggregation ---
        agg_ops = [
            ("sumArr", "Compute the sum of all elements in an integer array",
             ["@(i64)"], "i64",
             [TestCase([[1, 2, 3]], 6),
              TestCase([[10, -5]], 5),
              TestCase([[]], 0)]),
            ("avgArr", "Compute the average of elements in a float array, returning 0 if empty",
             ["@(f64)"], "f64",
             [TestCase([[1.0, 3.0]], 2.0),
              TestCase([[10.0]], 10.0),
              TestCase([[]], 0.0)]),
            ("maxArr", "Find the maximum value in an integer array, returning the minimum i64 value if empty",
             ["@(i64)"], "i64",
             [TestCase([[3, 1, 4, 1, 5]], 5),
              TestCase([[-1, -5]], -1),
              TestCase([[42]], 42)]),
            ("minArr", "Find the minimum value in an integer array, returning the maximum i64 value if empty",
             ["@(i64)"], "i64",
             [TestCase([[3, 1, 4, 1, 5]], 1),
              TestCase([[-1, -5]], -5),
              TestCase([[42]], 42)]),
            ("countMatching", "Count how many strings in an array equal the given target",
             ["@($str)", "$str"], "i64",
             [TestCase([["a", "b", "a", "c"], "a"], 2),
              TestCase([["x"], "y"], 0),
              TestCase([[], "z"], 0)]),
        ]
        for fn, desc, in_types, out_type, cases in agg_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-DAT",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=len(a:@(i64)):i64 F=get(a:@(i64);idx:i64):i64",
            ))

        # --- Filtering and mapping ---
        filter_ops = [
            ("filterPositive", "Return a new array containing only positive values from the input",
             ["@(i64)"], "@(i64)",
             [TestCase([[-1, 2, -3, 4]], [2, 4]),
              TestCase([[1, 2, 3]], [1, 2, 3]),
              TestCase([[-1, -2]], [])]),
            ("filterNonEmpty", "Return a new array containing only non-empty strings from the input",
             ["@($str)"], "@($str)",
             [TestCase([["a", "", "b", ""]], ["a", "b"]),
              TestCase([["", ""]], []),
              TestCase([["x"]], ["x"])]),
            ("mapToLengths", "Map an array of strings to their lengths",
             ["@($str)"], "@(i64)",
             [TestCase([["hi", "hello", "a"]], [2, 5, 1]),
              TestCase([[""]], [0]),
              TestCase([["abc"]], [3])]),
            ("mapDouble", "Double every element in an integer array",
             ["@(i64)"], "@(i64)",
             [TestCase([[1, 2, 3]], [2, 4, 6]),
              TestCase([[0, -1]], [0, -2]),
              TestCase([[]], [])]),
            ("filterByLength", "Return only strings longer than the given minimum length",
             ["@($str)", "i64"], "@($str)",
             [TestCase([["a", "abc", "ab", "abcd"], 2], ["abc", "abcd"]),
              TestCase([["x", "yy"], 0], ["x", "yy"]),
              TestCase([["a"], 5], [])]),
            ("uniqueStrings", "Return the unique strings from an array, preserving first occurrence order",
             ["@($str)"], "@($str)",
             [TestCase([["a", "b", "a", "c", "b"]], ["a", "b", "c"]),
              TestCase([["x"]], ["x"]),
              TestCase([[]], [])]),
        ]
        for fn, desc, in_types, out_type, cases in filter_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-DAT",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=push(a:@(i64);v:i64):@(i64) F=len(s:$str):i64",
            ))

        # --- Transformation ---
        transform_ops = [
            ("joinStrings", "Join an array of strings with a delimiter",
             ["@($str)", "$str"], "$str",
             [TestCase([["a", "b", "c"], ","], "a,b,c"),
              TestCase([["x"], "-"], "x"),
              TestCase([[], ","], "")]),
            ("flattenPairs", "Given parallel arrays of keys and values, produce lines of key=value",
             ["@($str)", "@($str)"], "$str",
             [TestCase([["a", "b"], ["1", "2"]], "a=1\nb=2"),
              TestCase([["k"], ["v"]], "k=v"),
              TestCase([[], []], "")]),
            ("reverseArr", "Reverse an integer array",
             ["@(i64)"], "@(i64)",
             [TestCase([[1, 2, 3]], [3, 2, 1]),
              TestCase([[42]], [42]),
              TestCase([[]], [])]),
            ("takeFirst", "Return the first N elements from an array (or the full array if shorter)",
             ["@($str)", "i64"], "@($str)",
             [TestCase([["a", "b", "c", "d"], 2], ["a", "b"]),
              TestCase([["x"], 5], ["x"]),
              TestCase([[], 3], [])]),
            ("dropFirst", "Return the array without its first N elements",
             ["@($str)", "i64"], "@($str)",
             [TestCase([["a", "b", "c", "d"], 2], ["c", "d"]),
              TestCase([["x"], 5], []),
              TestCase([[], 1], [])]),
            ("zipToString", "Interleave two string arrays into a single string separated by spaces",
             ["@($str)", "@($str)"], "$str",
             [TestCase([["a", "b"], ["1", "2"]], "a 1 b 2"),
              TestCase([["x"], ["y"]], "x y"),
              TestCase([[], []], "")]),
        ]
        for fn, desc, in_types, out_type, cases in transform_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-DAT",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=concat(a:$str;b:$str):$str F=push(a:@($str);v:$str):@($str)",
            ))

        # --- String-based data munging ---
        munge_ops = [
            ("trimAll", "Trim whitespace from every string in an array",
             ["@($str)"], "@($str)",
             [TestCase([[" a ", "b ", " c"]], ["a", "b", "c"]),
              TestCase([["x"]], ["x"]),
              TestCase([[]], [])]),
            ("lowerAll", "Convert every string in an array to lowercase",
             ["@($str)"], "@($str)",
             [TestCase([["Hello", "WORLD"]], ["hello", "world"]),
              TestCase([["abc"]], ["abc"]),
              TestCase([[]], [])]),
            ("upperAll", "Convert every string in an array to uppercase",
             ["@($str)"], "@($str)",
             [TestCase([["hello", "World"]], ["HELLO", "WORLD"]),
              TestCase([["ABC"]], ["ABC"]),
              TestCase([[]], [])]),
            ("prefixAll", "Prepend a given prefix to every string in an array",
             ["@($str)", "$str"], "@($str)",
             [TestCase([["a", "b"], "pre_"], ["pre_a", "pre_b"]),
              TestCase([["x"], ">>"], [">>x"]),
              TestCase([[], "z"], [])]),
            ("suffixAll", "Append a given suffix to every string in an array",
             ["@($str)", "$str"], "@($str)",
             [TestCase([["a", "b"], "!"], ["a!", "b!"]),
              TestCase([["x"], ".txt"], ["x.txt"]),
              TestCase([[], "z"], [])]),
        ]
        for fn, desc, in_types, out_type, cases in munge_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-DAT",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=trim(s:$str):$str F=lower(s:$str):$str F=upper(s:$str):$str",
            ))

        return tasks

    # =====================================================================
    # D-CRY: Cryptographic primitives
    # =====================================================================

    def _expand_crypto(self) -> list[TaskSpecV2]:
        tasks: list[TaskSpecV2] = []
        seq = _Sequencer("D-CRY")

        # --- Encoding ---
        enc_ops = [
            ("hexEncodeByte", "Convert a byte value (0-255) to its two-character hex representation",
             ["i64"], "$str",
             [TestCase([255], "ff"),
              TestCase([0], "00"),
              TestCase([171], "ab")]),
            ("hexDecodeByte", "Convert a two-character hex string to its integer value",
             ["$str"], "i64",
             [TestCase(["ff"], 255),
              TestCase(["00"], 0),
              TestCase(["ab"], 171)]),
            ("base64CharIndex", "Return the base64 index (0-63) of a base64 character (A-Z=0-25, a-z=26-51, 0-9=52-61, +=62, /=63)",
             ["$str"], "i64",
             [TestCase(["A"], 0),
              TestCase(["a"], 26),
              TestCase(["0"], 52)]),
            ("charToAscii", "Return the ASCII code of the first character of a string",
             ["$str"], "i64",
             [TestCase(["A"], 65),
              TestCase(["a"], 97),
              TestCase(["0"], 48)]),
            ("asciiToChar", "Return the character corresponding to an ASCII code",
             ["i64"], "$str",
             [TestCase([65], "A"),
              TestCase([97], "a"),
              TestCase([48], "0")]),
            ("isHexChar", "Return true if the character is a valid hex digit (0-9, a-f, A-F)",
             ["$str"], "bool",
             [TestCase(["a"], True),
              TestCase(["g"], False),
              TestCase(["5"], True)]),
        ]
        for fn, desc, in_types, out_type, cases in enc_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CRY",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=charCode(s:$str):i64 F=fromCharCode(n:i64):$str F=mod(a:i64;b:i64):i64",
            ))

        # --- Simple ciphers ---
        cipher_ops = [
            ("caesarShiftChar", "Apply a Caesar cipher shift to a single lowercase letter, wrapping around z",
             ["$str", "i64"], "$str",
             [TestCase(["a", 3], "d"),
              TestCase(["z", 1], "a"),
              TestCase(["m", 13], "z")]),
            ("rot13Char", "Apply ROT13 to a single lowercase letter",
             ["$str"], "$str",
             [TestCase(["a"], "n"),
              TestCase(["n"], "a"),
              TestCase(["z"], "m")]),
            ("xorByte", "XOR two byte values (0-255)",
             ["i64", "i64"], "i64",
             [TestCase([0xFF, 0x0F], 0xF0),
              TestCase([0, 255], 255),
              TestCase([42, 42], 0)]),
            ("onesComplement", "Compute the ones complement of a byte (0-255): 255 - value",
             ["i64"], "i64",
             [TestCase([0], 255),
              TestCase([255], 0),
              TestCase([128], 127)]),
            ("isAlphaLower", "Return true if the character is a lowercase ASCII letter",
             ["$str"], "bool",
             [TestCase(["a"], True),
              TestCase(["Z"], False),
              TestCase(["5"], False)]),
            ("isAlphaUpper", "Return true if the character is an uppercase ASCII letter",
             ["$str"], "bool",
             [TestCase(["A"], True),
              TestCase(["a"], False),
              TestCase(["5"], False)]),
            ("isDigitChar", "Return true if the character is an ASCII digit 0-9",
             ["$str"], "bool",
             [TestCase(["5"], True),
              TestCase(["a"], False),
              TestCase(["0"], True)]),
        ]
        for fn, desc, in_types, out_type, cases in cipher_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CRY",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=charCode(s:$str):i64 F=fromCharCode(n:i64):$str F=mod(a:i64;b:i64):i64",
            ))

        # --- Checksums and hashing ---
        hash_ops = [
            ("simpleChecksum", "Compute a simple checksum by summing all ASCII values of characters in a string",
             ["$str"], "i64",
             [TestCase(["abc"], 294),
              TestCase(["A"], 65),
              TestCase([""], 0)]),
            ("checksumMod256", "Compute the checksum of a string modulo 256",
             ["$str"], "i64",
             [TestCase(["abc"], 294 % 256),
              TestCase(["A"], 65),
              TestCase([""], 0)]),
            ("djb2Step", "Perform one step of the DJB2 hash: hash * 33 + charCode",
             ["i64", "i64"], "i64",
             [TestCase([5381, 97], 5381 * 33 + 97),
              TestCase([0, 65], 65),
              TestCase([100, 48], 100 * 33 + 48)]),
            ("pearsonStep", "Perform a Pearson hash step: XOR the accumulator with the byte value",
             ["i64", "i64"], "i64",
             [TestCase([0, 42], 42),
              TestCase([255, 255], 0),
              TestCase([100, 50], 100 ^ 50)]),
            ("rotateLeft", "Rotate an 8-bit value left by N positions",
             ["i64", "i64"], "i64",
             [TestCase([0b10000001, 1], 0b00000011),
              TestCase([0b11110000, 4], 0b00001111),
              TestCase([1, 0], 1)]),
            ("rotateRight", "Rotate an 8-bit value right by N positions",
             ["i64", "i64"], "i64",
             [TestCase([0b10000001, 1], 0b11000000),
              TestCase([0b00001111, 4], 0b11110000),
              TestCase([1, 0], 1)]),
        ]
        for fn, desc, in_types, out_type, cases in hash_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CRY",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=charCode(s:$str):i64 F=len(s:$str):i64 F=mod(a:i64;b:i64):i64 F=xor(a:i64;b:i64):i64",
            ))

        # --- Bit manipulation ---
        bit_ops = [
            ("getBit", "Get the Nth bit (0-indexed from LSB) of an integer",
             ["i64", "i64"], "i64",
             [TestCase([5, 0], 1),
              TestCase([5, 1], 0),
              TestCase([5, 2], 1)]),
            ("setBit", "Set the Nth bit (0-indexed from LSB) of an integer to 1",
             ["i64", "i64"], "i64",
             [TestCase([0, 3], 8),
              TestCase([5, 1], 7),
              TestCase([255, 0], 255)]),
            ("clearBit", "Clear the Nth bit (0-indexed from LSB) of an integer to 0",
             ["i64", "i64"], "i64",
             [TestCase([255, 0], 254),
              TestCase([5, 2], 1),
              TestCase([0, 3], 0)]),
            ("countSetBits", "Count the number of 1 bits in the binary representation of a non-negative integer",
             ["i64"], "i64",
             [TestCase([7], 3),
              TestCase([0], 0),
              TestCase([255], 8)]),
            ("isPowerOfTwo", "Return true if the positive integer is a power of two",
             ["i64"], "bool",
             [TestCase([8], True),
              TestCase([6], False),
              TestCase([1], True)]),
        ]
        for fn, desc, in_types, out_type, cases in bit_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CRY",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=shr(a:i64;b:i64):i64 F=shl(a:i64;b:i64):i64 F=and(a:i64;b:i64):i64",
            ))

        return tasks

    # =====================================================================
    # D-FIO: File I/O patterns
    # =====================================================================

    def _expand_file_io(self) -> list[TaskSpecV2]:
        tasks: list[TaskSpecV2] = []
        seq = _Sequencer("D-FIO")

        # --- Path manipulation ---
        path_ops = [
            ("fileExtension", "Extract the file extension from a path (without the dot), or empty string if none",
             ["$str"], "$str",
             [TestCase(["report.pdf"], "pdf"),
              TestCase(["archive.tar.gz"], "gz"),
              TestCase(["noext"], "")]),
            ("fileName", "Extract the file name (including extension) from a full path",
             ["$str"], "$str",
             [TestCase(["/home/user/doc.txt"], "doc.txt"),
              TestCase(["file.rs"], "file.rs"),
              TestCase(["/a/b/c/"], "")]),
            ("parentDir", "Extract the parent directory path from a full file path",
             ["$str"], "$str",
             [TestCase(["/home/user/doc.txt"], "/home/user"),
              TestCase(["/root"], "/"),
              TestCase(["file.txt"], "")]),
            ("joinPaths", "Join two path segments with a / separator, avoiding double slashes",
             ["$str", "$str"], "$str",
             [TestCase(["/home/user/", "doc.txt"], "/home/user/doc.txt"),
              TestCase(["/home", "user"], "/home/user"),
              TestCase(["", "file"], "file")]),
            ("fileBaseName", "Extract the file name without its extension",
             ["$str"], "$str",
             [TestCase(["report.pdf"], "report"),
              TestCase(["archive.tar.gz"], "archive.tar"),
              TestCase(["noext"], "noext")]),
            ("isAbsolutePath", "Return true if the path starts with /",
             ["$str"], "bool",
             [TestCase(["/home/user"], True),
              TestCase(["relative/path"], False),
              TestCase(["/"], True)]),
            ("pathDepth", "Count the number of path segments (non-empty parts between /)",
             ["$str"], "i64",
             [TestCase(["/home/user/docs"], 3),
              TestCase(["/"], 0),
              TestCase(["a/b"], 2)]),
            ("changeExtension", "Replace the extension of a filename with a new one",
             ["$str", "$str"], "$str",
             [TestCase(["file.txt", "md"], "file.md"),
              TestCase(["archive.tar.gz", "zip"], "archive.tar.zip"),
              TestCase(["noext", "txt"], "noext.txt")]),
        ]
        for fn, desc, in_types, out_type, cases in path_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-FIO",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=endsWith(s:$str;suffix:$str):bool",
            ))

        # --- Line processing ---
        line_ops = [
            ("countLines", "Count the number of lines in a text (split by newline)",
             ["$str"], "i64",
             [TestCase(["a\nb\nc"], 3),
              TestCase(["single"], 1),
              TestCase([""], 0)]),
            ("getLine", "Get the Nth line (0-indexed) from a text",
             ["$str", "i64"], "$str",
             [TestCase(["a\nb\nc", 1], "b"),
              TestCase(["hello", 0], "hello"),
              TestCase(["x\ny", 1], "y")]),
            ("filterBlankLines", "Remove all blank lines from a text, returning the cleaned text",
             ["$str"], "$str",
             [TestCase(["a\n\nb\n\nc"], "a\nb\nc"),
              TestCase(["no blanks"], "no blanks"),
              TestCase(["\n\n"], "")]),
            ("firstLine", "Return the first line of a text",
             ["$str"], "$str",
             [TestCase(["hello\nworld"], "hello"),
              TestCase(["single"], "single"),
              TestCase(["a\nb"], "a")]),
            ("lastLine", "Return the last non-empty line of a text",
             ["$str"], "$str",
             [TestCase(["a\nb\nc"], "c"),
              TestCase(["single"], "single"),
              TestCase(["x\ny\n"], "y")]),
            ("prependLineNumbers", "Prepend 1-based line numbers to each line: '1: first\\n2: second'",
             ["$str"], "$str",
             [TestCase(["a\nb\nc"], "1: a\n2: b\n3: c"),
              TestCase(["hello"], "1: hello"),
              TestCase(["x\ny"], "1: x\n2: y")]),
            ("indentLines", "Indent each line of text by N spaces",
             ["$str", "i64"], "$str",
             [TestCase(["a\nb", 2], "  a\n  b"),
              TestCase(["hello", 4], "    hello"),
              TestCase(["x\ny", 0], "x\ny")]),
        ]
        for fn, desc, in_types, out_type, cases in line_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-FIO",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=concat(a:$str;b:$str):$str",
            ))

        # --- File size / permissions helpers ---
        size_ops = [
            ("formatFileSize", "Format a byte count as a human-readable string (B, KB, MB)",
             ["i64"], "$str",
             [TestCase([500], "500B"),
              TestCase([1500], "1KB"),
              TestCase([2_500_000], "2MB")]),
            ("isHiddenFile", "Return true if the filename starts with a dot",
             ["$str"], "bool",
             [TestCase([".gitignore"], True),
              TestCase(["readme.md"], False),
              TestCase([".env"], True)]),
            ("hasExtension", "Return true if the filename has one of the given extensions (comma-separated)",
             ["$str", "$str"], "bool",
             [TestCase(["file.txt", "txt,md,rs"], True),
              TestCase(["file.py", "txt,md"], False),
              TestCase(["file.rs", "rs"], True)]),
        ]
        for fn, desc, in_types, out_type, cases in size_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-FIO",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=startsWith(s:$str;prefix:$str):bool F=endsWith(s:$str;suffix:$str):bool",
            ))

        return tasks

    # =====================================================================
    # D-CFG: Configuration parsing
    # =====================================================================

    def _expand_config(self) -> list[TaskSpecV2]:
        tasks: list[TaskSpecV2] = []
        seq = _Sequencer("D-CFG")

        # --- Key-value parsing ---
        kv_ops = [
            ("parseKV", "Parse a 'key=value' string and return the value",
             ["$str"], "$str",
             [TestCase(["host=localhost"], "localhost"),
              TestCase(["port=8080"], "8080"),
              TestCase(["name="], "")]),
            ("parseKVKey", "Parse a 'key=value' string and return the key",
             ["$str"], "$str",
             [TestCase(["host=localhost"], "host"),
              TestCase(["port=8080"], "port"),
              TestCase(["x=y"], "x")]),
            ("isComment", "Return true if a config line is a comment (starts with # after trimming)",
             ["$str"], "bool",
             [TestCase(["# this is a comment"], True),
              TestCase(["key=val"], False),
              TestCase(["  # indented comment"], True)]),
            ("isBlankOrComment", "Return true if a config line is blank or a comment",
             ["$str"], "bool",
             [TestCase([""], True),
              TestCase(["# comment"], True),
              TestCase(["key=val"], False)]),
            ("parseSection", "Extract the section name from an INI-style section header like [section]",
             ["$str"], "$str",
             [TestCase(["[database]"], "database"),
              TestCase(["[server]"], "server"),
              TestCase(["[app.settings]"], "app.settings")]),
            ("isSectionHeader", "Return true if a line is an INI-style section header",
             ["$str"], "bool",
             [TestCase(["[database]"], True),
              TestCase(["key=val"], False),
              TestCase(["[]"], True)]),
            ("countConfigKeys", "Count the number of non-comment, non-blank lines in a config text",
             ["$str"], "i64",
             [TestCase(["host=x\n# comment\nport=80"], 2),
              TestCase(["# only comment"], 0),
              TestCase(["a=1\nb=2\nc=3"], 3)]),
        ]
        for fn, desc, in_types, out_type, cases in kv_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CFG",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=trim(s:$str):$str F=startsWith(s:$str;prefix:$str):bool",
            ))

        # --- Environment variable handling ---
        env_ops = [
            ("envOrDefault", "Return the value if non-empty, otherwise return the default",
             ["$str", "$str"], "$str",
             [TestCase(["production", "development"], "production"),
              TestCase(["", "development"], "development"),
              TestCase(["", ""], "")]),
            ("envToBool", "Convert an env-style string to boolean: 'true', '1', 'yes' = true, everything else = false",
             ["$str"], "bool",
             [TestCase(["true"], True),
              TestCase(["0"], False),
              TestCase(["yes"], True)]),
            ("envToInt", "Parse an env-style string to i64, returning a default if not a valid number",
             ["$str", "i64"], "i64",
             [TestCase(["8080", 3000], 8080),
              TestCase(["invalid", 3000], 3000),
              TestCase(["", 5000], 5000)]),
            ("expandEnvRef", "Replace ${VAR} in a template with the provided value. Only one variable supported.",
             ["$str", "$str", "$str"], "$str",
             [TestCase(["Hello ${NAME}!", "NAME", "World"], "Hello World!"),
              TestCase(["${X}", "X", "42"], "42"),
              TestCase(["no vars", "X", "42"], "no vars")]),
        ]
        for fn, desc, in_types, out_type, cases in env_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CFG",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=eq(a:$str;b:$str):bool F=replace(s:$str;old:$str;new:$str):$str F=parseInt(s:$str):i64",
            ))

        # --- Config merging ---
        merge_ops = [
            ("mergeDefaults", "Given a config string and defaults string (both key=val lines), return merged config where config values override defaults",
             ["$str", "$str"], "$str",
             [TestCase(["host=prod", "host=dev\nport=80"], "host=prod\nport=80"),
              TestCase(["", "a=1\nb=2"], "a=1\nb=2"),
              TestCase(["x=1", ""], "x=1")]),
            ("configToQueryString", "Convert key=value config lines to a URL query string (key=val&key2=val2)",
             ["$str"], "$str",
             [TestCase(["host=localhost\nport=8080"], "host=localhost&port=8080"),
              TestCase(["key=val"], "key=val"),
              TestCase([""], "")]),
            ("configDiff", "Given two config strings, return only keys present in the first but not the second",
             ["$str", "$str"], "$str",
             [TestCase(["a=1\nb=2\nc=3", "b=2"], "a=1\nc=3"),
              TestCase(["x=1", "x=1"], ""),
              TestCase(["a=1", ""], "a=1")]),
            ("validateConfigLine", "Return true if a line matches key=value format (non-empty key, = present)",
             ["$str"], "bool",
             [TestCase(["host=localhost"], True),
              TestCase(["noequals"], False),
              TestCase(["=nokey"], False)]),
        ]
        for fn, desc, in_types, out_type, cases in merge_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CFG",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=split(s:$str;delim:$str):@($str) F=contains(s:$str;sub:$str):bool F=concat(a:$str;b:$str):$str",
            ))

        # --- Dotted key paths ---
        dot_ops = [
            ("dotKeyDepth", "Count the depth of a dotted config key (e.g. 'a.b.c' = 3)",
             ["$str"], "i64",
             [TestCase(["a.b.c"], 3),
              TestCase(["simple"], 1),
              TestCase(["a.b.c.d.e"], 5)]),
            ("dotKeyFirst", "Return the first segment of a dotted key",
             ["$str"], "$str",
             [TestCase(["app.db.host"], "app"),
              TestCase(["simple"], "simple"),
              TestCase(["a.b"], "a")]),
            ("dotKeyLast", "Return the last segment of a dotted key",
             ["$str"], "$str",
             [TestCase(["app.db.host"], "host"),
              TestCase(["simple"], "simple"),
              TestCase(["a.b"], "b")]),
            ("dotKeyParent", "Return the parent key path (all segments except the last)",
             ["$str"], "$str",
             [TestCase(["app.db.host"], "app.db"),
              TestCase(["simple"], ""),
              TestCase(["a.b.c"], "a.b")]),
        ]
        for fn, desc, in_types, out_type, cases in dot_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CFG",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=len(a:@($str)):i64",
            ))

        return tasks

    # =====================================================================
    # D-TST: Testing utilities
    # =====================================================================

    def _expand_testing(self) -> list[TaskSpecV2]:
        tasks: list[TaskSpecV2] = []
        seq = _Sequencer("D-TST")

        # --- Assertion helpers ---
        assert_ops = [
            ("assertEqual", "Return true if two integers are equal",
             ["i64", "i64"], "bool",
             [TestCase([42, 42], True),
              TestCase([1, 2], False),
              TestCase([0, 0], True)]),
            ("assertNotEqual", "Return true if two integers are not equal",
             ["i64", "i64"], "bool",
             [TestCase([1, 2], True),
              TestCase([42, 42], False),
              TestCase([0, 1], True)]),
            ("assertStrEqual", "Return true if two strings are equal",
             ["$str", "$str"], "bool",
             [TestCase(["hello", "hello"], True),
              TestCase(["a", "b"], False),
              TestCase(["", ""], True)]),
            ("assertGreater", "Return true if the first integer is greater than the second",
             ["i64", "i64"], "bool",
             [TestCase([5, 3], True),
              TestCase([3, 5], False),
              TestCase([5, 5], False)]),
            ("assertLess", "Return true if the first integer is less than the second",
             ["i64", "i64"], "bool",
             [TestCase([3, 5], True),
              TestCase([5, 3], False),
              TestCase([5, 5], False)]),
            ("assertBetween", "Return true if the value is between lo and hi (inclusive)",
             ["i64", "i64", "i64"], "bool",
             [TestCase([5, 1, 10], True),
              TestCase([0, 1, 10], False),
              TestCase([10, 1, 10], True)]),
            ("assertNonEmpty", "Return true if the string is non-empty",
             ["$str"], "bool",
             [TestCase(["hello"], True),
              TestCase([""], False),
              TestCase([" "], True)]),
            ("assertStartsWith", "Return true if the string starts with the given prefix",
             ["$str", "$str"], "bool",
             [TestCase(["hello world", "hello"], True),
              TestCase(["abc", "xyz"], False),
              TestCase(["", ""], True)]),
            ("assertContains", "Return true if the string contains the given substring",
             ["$str", "$str"], "bool",
             [TestCase(["hello world", "world"], True),
              TestCase(["abc", "xyz"], False),
              TestCase(["", ""], True)]),
            ("assertArrLength", "Return true if the array has the expected length",
             ["@($str)", "i64"], "bool",
             [TestCase([["a", "b", "c"], 3], True),
              TestCase([["x"], 2], False),
              TestCase([[], 0], True)]),
        ]
        for fn, desc, in_types, out_type, cases in assert_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-TST",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=1,
                domain_context="F=eq(a:i64;b:i64):bool F=len(s:$str):i64 F=startsWith(s:$str;prefix:$str):bool",
            ))

        # --- Test data generators ---
        gen_ops = [
            ("repeatStr", "Generate a string by repeating a character N times",
             ["$str", "i64"], "$str",
             [TestCase(["x", 3], "xxx"),
              TestCase(["ab", 2], "abab"),
              TestCase(["z", 0], "")]),
            ("rangeArr", "Generate an array of integers from start (inclusive) to end (exclusive)",
             ["i64", "i64"], "@(i64)",
             [TestCase([0, 3], [0, 1, 2]),
              TestCase([5, 8], [5, 6, 7]),
              TestCase([3, 3], [])]),
            ("fillArr", "Create an array of N copies of a given integer",
             ["i64", "i64"], "@(i64)",
             [TestCase([42, 3], [42, 42, 42]),
              TestCase([0, 2], [0, 0]),
              TestCase([1, 0], [])]),
            ("fillStrArr", "Create an array of N copies of a given string",
             ["$str", "i64"], "@($str)",
             [TestCase(["x", 3], ["x", "x", "x"]),
              TestCase(["hello", 1], ["hello"]),
              TestCase(["a", 0], [])]),
            ("alternatingArr", "Generate an array of N integers alternating between two values",
             ["i64", "i64", "i64"], "@(i64)",
             [TestCase([0, 1, 4], [0, 1, 0, 1]),
              TestCase([5, 10, 3], [5, 10, 5]),
              TestCase([1, 2, 0], [])]),
            ("sequenceStr", "Generate strings like 'item_0', 'item_1', ..., 'item_{n-1}' with the given prefix",
             ["$str", "i64"], "@($str)",
             [TestCase(["item_", 3], ["item_0", "item_1", "item_2"]),
              TestCase(["x", 2], ["x0", "x1"]),
              TestCase(["a", 0], [])]),
        ]
        for fn, desc, in_types, out_type, cases in gen_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-TST",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=concat(a:$str;b:$str):$str F=push(a:@(i64);v:i64):@(i64) F=toString(n:i64):$str",
            ))

        # --- Result formatters ---
        fmt_ops = [
            ("formatTestResult", "Format a test result as 'PASS: name' or 'FAIL: name' based on a boolean",
             ["$str", "bool"], "$str",
             [TestCase(["test_add", True], "PASS: test_add"),
              TestCase(["test_sub", False], "FAIL: test_sub"),
              TestCase(["edge", True], "PASS: edge")]),
            ("countPassed", "Count the number of true values in a boolean array",
             ["@(bool)"], "i64",
             [TestCase([[True, False, True]], 2),
              TestCase([[False, False]], 0),
              TestCase([[True, True, True]], 3)]),
            ("countFailed", "Count the number of false values in a boolean array",
             ["@(bool)"], "i64",
             [TestCase([[True, False, True]], 1),
              TestCase([[False, False]], 2),
              TestCase([[True, True]], 0)]),
            ("allPassed", "Return true if all values in a boolean array are true",
             ["@(bool)"], "bool",
             [TestCase([[True, True]], True),
              TestCase([[True, False]], False),
              TestCase([[]], True)]),
            ("anyFailed", "Return true if any value in a boolean array is false",
             ["@(bool)"], "bool",
             [TestCase([[True, False]], True),
              TestCase([[True, True]], False),
              TestCase([[]], False)]),
            ("summarize", "Return 'X/Y passed' given passed count and total count",
             ["i64", "i64"], "$str",
             [TestCase([3, 5], "3/5 passed"),
              TestCase([10, 10], "10/10 passed"),
              TestCase([0, 3], "0/3 passed")]),
        ]
        for fn, desc, in_types, out_type, cases in fmt_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-TST",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=concat(a:$str;b:$str):$str F=toString(n:i64):$str F=len(a:@(bool)):i64",
            ))

        # --- Mock helpers ---
        mock_ops = [
            ("stubReturn", "Given a function name and return value, build a stub string like 'fn_name() -> val'",
             ["$str", "$str"], "$str",
             [TestCase(["getUser", "alice"], "getUser() -> alice"),
              TestCase(["count", "42"], "count() -> 42"),
              TestCase(["check", "true"], "check() -> true")]),
            ("buildCallLog", "Format a call log entry: 'called fn_name with (arg1, arg2)'",
             ["$str", "$str", "$str"], "$str",
             [TestCase(["add", "3", "5"], "called add with (3, 5)"),
              TestCase(["get", "key", ""], "called get with (key, )"),
              TestCase(["f", "x", "y"], "called f with (x, y)")]),
            ("matchesPattern", "Return true if a string matches a simple glob pattern (only * supported, at start or end)",
             ["$str", "$str"], "bool",
             [TestCase(["test_add", "test_*"], True),
              TestCase(["test_add", "*_add"], True),
              TestCase(["other", "test_*"], False)]),
        ]
        for fn, desc, in_types, out_type, cases in mock_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-TST",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=concat(a:$str;b:$str):$str F=startsWith(s:$str;prefix:$str):bool F=endsWith(s:$str;suffix:$str):bool",
            ))

        return tasks

    # =====================================================================
    # D-NET: Network patterns
    # =====================================================================

    def _expand_network(self) -> list[TaskSpecV2]:
        tasks: list[TaskSpecV2] = []
        seq = _Sequencer("D-NET")

        # --- URL parsing ---
        url_ops = [
            ("urlScheme", "Extract the scheme from a URL (e.g. 'http' from 'http://example.com')",
             ["$str"], "$str",
             [TestCase(["http://example.com"], "http"),
              TestCase(["https://secure.io"], "https"),
              TestCase(["ftp://files.net/a"], "ftp")]),
            ("urlHost", "Extract the host from a URL (between :// and the next /)",
             ["$str"], "$str",
             [TestCase(["http://example.com/path"], "example.com"),
              TestCase(["https://api.io:8080/v1"], "api.io:8080"),
              TestCase(["http://localhost/"], "localhost")]),
            ("urlPath", "Extract the path from a URL (after the host, before ? or #)",
             ["$str"], "$str",
             [TestCase(["http://example.com/api/v1"], "/api/v1"),
              TestCase(["http://x.com/"], "/"),
              TestCase(["http://x.com/a?q=1"], "/a")]),
            ("urlPort", "Extract the port number from a URL, returning 0 if not specified",
             ["$str"], "i64",
             [TestCase(["http://localhost:8080/api"], 8080),
              TestCase(["http://example.com/page"], 0),
              TestCase(["https://api.io:443/"], 443)]),
            ("isHttps", "Return true if the URL uses the https scheme",
             ["$str"], "bool",
             [TestCase(["https://secure.io"], True),
              TestCase(["http://plain.com"], False),
              TestCase(["https://api.io/v1"], True)]),
        ]
        for fn, desc, in_types, out_type, cases in url_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-NET",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=startsWith(s:$str;prefix:$str):bool F=parseInt(s:$str):i64",
            ))

        # --- IP address handling ---
        ip_ops = [
            ("isIPv4Format", "Return true if the string looks like an IPv4 address (four dot-separated groups)",
             ["$str"], "bool",
             [TestCase(["192.168.1.1"], True),
              TestCase(["10.0.0"], False),
              TestCase(["1.2.3.4"], True)]),
            ("ipOctet", "Extract the Nth octet (0-indexed) from an IPv4 address string",
             ["$str", "i64"], "i64",
             [TestCase(["192.168.1.1", 0], 192),
              TestCase(["10.0.0.1", 3], 1),
              TestCase(["255.255.255.0", 2], 255)]),
            ("isPrivateIP", "Return true if the IPv4 address starts with 10., 172.16-31., or 192.168.",
             ["$str"], "bool",
             [TestCase(["192.168.1.1"], True),
              TestCase(["10.0.0.1"], True),
              TestCase(["8.8.8.8"], False)]),
            ("isLoopback", "Return true if the IPv4 address starts with 127.",
             ["$str"], "bool",
             [TestCase(["127.0.0.1"], True),
              TestCase(["127.1.2.3"], True),
              TestCase(["128.0.0.1"], False)]),
            ("formatIPPort", "Combine an IP address and port into 'ip:port' format",
             ["$str", "i64"], "$str",
             [TestCase(["192.168.1.1", 8080], "192.168.1.1:8080"),
              TestCase(["10.0.0.1", 443], "10.0.0.1:443"),
              TestCase(["localhost", 3000], "localhost:3000")]),
            ("parsePort", "Extract the port number from a host:port string, returning 0 if no port",
             ["$str"], "i64",
             [TestCase(["localhost:8080"], 8080),
              TestCase(["example.com"], 0),
              TestCase(["10.0.0.1:443"], 443)]),
        ]
        for fn, desc, in_types, out_type, cases in ip_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-NET",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=parseInt(s:$str):i64 F=startsWith(s:$str;prefix:$str):bool",
            ))

        # --- Protocol helpers ---
        proto_ops = [
            ("defaultPort", "Return the default port for a protocol: http=80, https=443, ftp=21, ssh=22, otherwise 0",
             ["$str"], "i64",
             [TestCase(["http"], 80),
              TestCase(["https"], 443),
              TestCase(["ftp"], 21)]),
            ("isSecureProtocol", "Return true if the protocol is considered secure (https, ssh, sftp)",
             ["$str"], "bool",
             [TestCase(["https"], True),
              TestCase(["http"], False),
              TestCase(["ssh"], True)]),
            ("buildEndpoint", "Build an endpoint URL from scheme, host, port, and path",
             ["$str", "$str", "i64", "$str"], "$str",
             [TestCase(["http", "localhost", 8080, "/api"], "http://localhost:8080/api"),
              TestCase(["https", "example.com", 443, "/"], "https://example.com:443/"),
              TestCase(["http", "api.io", 80, "/v1"], "http://api.io:80/v1")]),
            ("isWellKnownPort", "Return true if the port number is in the well-known range (1-1023)",
             ["i64"], "bool",
             [TestCase([80], True),
              TestCase([8080], False),
              TestCase([443], True)]),
            ("dnsLabelValid", "Return true if a string is a valid DNS label (1-63 chars, alphanumeric + hyphens, no leading/trailing hyphen)",
             ["$str"], "bool",
             [TestCase(["example"], True),
              TestCase(["-bad"], False),
              TestCase(["ok-label"], True)]),
        ]
        for fn, desc, in_types, out_type, cases in proto_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-NET",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=eq(a:$str;b:$str):bool F=concat(a:$str;b:$str):$str F=toString(n:i64):$str",
            ))

        # --- Header parsing ---
        header_ops = [
            ("parseHeaderName", "Extract the header name from 'Name: Value' format",
             ["$str"], "$str",
             [TestCase(["Content-Type: text/html"], "Content-Type"),
              TestCase(["Host: example.com"], "Host"),
              TestCase(["X-Custom: val"], "X-Custom")]),
            ("parseHeaderValue", "Extract the header value from 'Name: Value' format",
             ["$str"], "$str",
             [TestCase(["Content-Type: text/html"], "text/html"),
              TestCase(["Host: example.com"], "example.com"),
              TestCase(["X-Custom: val"], "val")]),
            ("isValidHeader", "Return true if the string matches 'Name: Value' format",
             ["$str"], "bool",
             [TestCase(["Content-Type: text/html"], True),
              TestCase(["NoColon"], False),
              TestCase(["X: Y"], True)]),
            ("contentLengthFromHeaders", "Given headers as newline-separated lines, find Content-Length value or return 0",
             ["$str"], "i64",
             [TestCase(["Host: x\nContent-Length: 42\nType: y"], 42),
              TestCase(["Host: x\nType: y"], 0),
              TestCase(["Content-Length: 100"], 100)]),
        ]
        for fn, desc, in_types, out_type, cases in header_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-NET",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=split(s:$str;delim:$str):@($str) F=trim(s:$str):$str F=parseInt(s:$str):i64",
            ))

        return tasks

    # =====================================================================
    # D-CLI: CLI patterns
    # =====================================================================

    def _expand_cli(self) -> list[TaskSpecV2]:
        tasks: list[TaskSpecV2] = []
        seq = _Sequencer("D-CLI")

        # --- Argument parsing ---
        arg_ops = [
            ("hasFlag", "Return true if a flag (e.g. '--verbose') exists in the args array",
             ["@($str)", "$str"], "bool",
             [TestCase([["--verbose", "--output", "file.txt"], "--verbose"], True),
              TestCase([["--quiet"], "--verbose"], False),
              TestCase([[], "--help"], False)]),
            ("getFlagValue", "Get the value following a flag in args, or return empty string if not found",
             ["@($str)", "$str"], "$str",
             [TestCase([["--output", "file.txt", "--verbose"], "--output"], "file.txt"),
              TestCase([["--quiet"], "--output"], ""),
              TestCase([["--port", "8080"], "--port"], "8080")]),
            ("countFlags", "Count the number of arguments that start with --",
             ["@($str)"], "i64",
             [TestCase([["--verbose", "file", "--output", "out.txt"]], 2),
              TestCase([["file1", "file2"]], 0),
              TestCase([["--a", "--b", "--c"]], 3)]),
            ("positionalArgs", "Return only the arguments that do not start with --",
             ["@($str)"], "@($str)",
             [TestCase([["--verbose", "input.txt", "--output", "out.txt"]], ["input.txt", "out.txt"]),
              TestCase([["file1", "file2"]], ["file1", "file2"]),
              TestCase([["--all"]], [])]),
            ("isShortFlag", "Return true if the argument is a short flag (starts with - but not --)",
             ["$str"], "bool",
             [TestCase(["-v"], True),
              TestCase(["--verbose"], False),
              TestCase(["file"], False)]),
            ("isLongFlag", "Return true if the argument is a long flag (starts with --)",
             ["$str"], "bool",
             [TestCase(["--verbose"], True),
              TestCase(["-v"], False),
              TestCase(["file"], False)]),
            ("parseKeyValueFlag", "Parse a --key=value flag and return the value, or empty string",
             ["$str"], "$str",
             [TestCase(["--output=file.txt"], "file.txt"),
              TestCase(["--port=8080"], "8080"),
              TestCase(["--verbose"], "")]),
            ("parseKeyValueFlagName", "Parse a --key=value flag and return the key name (without --)",
             ["$str"], "$str",
             [TestCase(["--output=file.txt"], "output"),
              TestCase(["--port=8080"], "port"),
              TestCase(["--verbose"], "verbose")]),
        ]
        for fn, desc, in_types, out_type, cases in arg_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CLI",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=startsWith(s:$str;prefix:$str):bool F=contains(arr:@($str);val:$str):bool F=split(s:$str;delim:$str):@($str)",
            ))

        # --- Output formatting ---
        fmt_ops = [
            ("padRight", "Pad a string to a minimum width by appending spaces",
             ["$str", "i64"], "$str",
             [TestCase(["hi", 5], "hi   "),
              TestCase(["hello", 3], "hello"),
              TestCase(["", 3], "   ")]),
            ("padLeft", "Pad a string to a minimum width by prepending spaces",
             ["$str", "i64"], "$str",
             [TestCase(["hi", 5], "   hi"),
              TestCase(["hello", 3], "hello"),
              TestCase(["", 3], "   ")]),
            ("formatTable2Col", "Format two parallel arrays as a two-column table with ' | ' separator",
             ["@($str)", "@($str)"], "$str",
             [TestCase([["name", "age"], ["alice", "30"]], "name | alice\nage | 30"),
              TestCase([["k"], ["v"]], "k | v"),
              TestCase([[], []], "")]),
            ("wrapText", "Wrap a text to a maximum line width by inserting newlines at the last space before the limit",
             ["$str", "i64"], "$str",
             [TestCase(["hello world foo", 11], "hello world\nfoo"),
              TestCase(["short", 20], "short"),
              TestCase(["a b c", 3], "a b\nc")]),
            ("progressBar", "Generate a simple progress bar: '[====    ] 50%' given current and total",
             ["i64", "i64"], "$str",
             [TestCase([5, 10], "[=====     ] 50%"),
              TestCase([10, 10], "[==========] 100%"),
              TestCase([0, 10], "[          ] 0%")]),
            ("formatDuration", "Format seconds into a human-readable string like '1h 30m 5s'",
             ["i64"], "$str",
             [TestCase([3665], "1h 1m 5s"),
              TestCase([60], "0h 1m 0s"),
              TestCase([0], "0h 0m 0s")]),
            ("formatCount", "Format a count with singular/plural: '1 item' vs '5 items'",
             ["i64", "$str"], "$str",
             [TestCase([1, "item"], "1 item"),
              TestCase([5, "item"], "5 items"),
              TestCase([0, "file"], "0 files")]),
        ]
        for fn, desc, in_types, out_type, cases in fmt_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CLI",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=3,
                domain_context="F=concat(a:$str;b:$str):$str F=len(s:$str):i64 F=toString(n:i64):$str F=repeat(s:$str;n:i64):$str",
            ))

        # --- Color/ANSI helpers ---
        color_ops = [
            ("ansiRed", "Wrap a string in ANSI red escape codes: \\033[31m ... \\033[0m",
             ["$str"], "$str",
             [TestCase(["error"], "\033[31merror\033[0m"),
              TestCase(["fail"], "\033[31mfail\033[0m"),
              TestCase(["x"], "\033[31mx\033[0m")]),
            ("ansiGreen", "Wrap a string in ANSI green escape codes: \\033[32m ... \\033[0m",
             ["$str"], "$str",
             [TestCase(["ok"], "\033[32mok\033[0m"),
              TestCase(["pass"], "\033[32mpass\033[0m"),
              TestCase(["x"], "\033[32mx\033[0m")]),
            ("ansiBold", "Wrap a string in ANSI bold escape codes: \\033[1m ... \\033[0m",
             ["$str"], "$str",
             [TestCase(["title"], "\033[1mtitle\033[0m"),
              TestCase(["x"], "\033[1mx\033[0m"),
              TestCase(["bold"], "\033[1mbold\033[0m")]),
            ("stripAnsi", "Remove ANSI escape sequences (\\033[...m patterns) from a string",
             ["$str"], "$str",
             [TestCase(["\033[31merror\033[0m"], "error"),
              TestCase(["plain"], "plain"),
              TestCase(["\033[1mbold\033[0m"], "bold")]),
        ]
        for fn, desc, in_types, out_type, cases in color_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CLI",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=2,
                domain_context="F=concat(a:$str;b:$str):$str F=replace(s:$str;old:$str;new:$str):$str",
            ))

        # --- Help text generation ---
        help_ops = [
            ("usageLine", "Build a usage line: 'Usage: program_name [options] args'",
             ["$str"], "$str",
             [TestCase(["myapp"], "Usage: myapp [options]"),
              TestCase(["cli"], "Usage: cli [options]"),
              TestCase(["tool"], "Usage: tool [options]")]),
            ("formatOption", "Format a CLI option help line: '  --flag    description'",
             ["$str", "$str"], "$str",
             [TestCase(["--verbose", "Enable verbose output"], "  --verbose    Enable verbose output"),
              TestCase(["--help", "Show help"], "  --help    Show help"),
              TestCase(["--port", "Set port"], "  --port    Set port")]),
            ("versionString", "Build a version string: 'name v1.2.3'",
             ["$str", "i64", "i64", "i64"], "$str",
             [TestCase(["myapp", 1, 2, 3], "myapp v1.2.3"),
              TestCase(["cli", 0, 1, 0], "cli v0.1.0"),
              TestCase(["tool", 2, 0, 0], "tool v2.0.0")]),
            ("errorMessage", "Format an error message: 'Error: message'",
             ["$str"], "$str",
             [TestCase(["file not found"], "Error: file not found"),
              TestCase(["invalid arg"], "Error: invalid arg"),
              TestCase([""], "Error: ")]),
            ("warningMessage", "Format a warning message: 'Warning: message'",
             ["$str"], "$str",
             [TestCase(["deprecated"], "Warning: deprecated"),
              TestCase(["slow"], "Warning: slow"),
              TestCase([""], "Warning: ")]),
        ]
        for fn, desc, in_types, out_type, cases in help_ops:
            tasks.append(TaskSpecV2(
                task_id=seq.next(), category="D-CLI",
                description=f"Write a function that: {desc}",
                input_types=in_types, output_type=out_type,
                test_cases=cases, difficulty=1,
                domain_context="F=concat(a:$str;b:$str):$str F=toString(n:i64):$str",
            ))

        return tasks
