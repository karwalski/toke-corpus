"""
Task curriculum generator for Phase A corpus generation.

Generates 50,000 deterministic, reproducible task specifications across
six Phase A categories. Each task is a single-function primitive (5-50 lines)
targeting the toke language.

Story 8.1.3 — Task curriculum generator.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase A categories
# ---------------------------------------------------------------------------

CATEGORIES: list[str] = ["A-MTH", "A-CND", "A-STR", "A-ARR", "A-SRT", "A-ERR"]

# ---------------------------------------------------------------------------
# Toke type vocabulary used in template expansion
# ---------------------------------------------------------------------------

NUMERIC_TYPES: list[str] = ["i64", "u64", "f64"]
ALL_TYPES: list[str] = ["i64", "u64", "f64", "Str", "bool"]
ARRAY_ELEM_TYPES: list[str] = ["i64", "u64", "f64", "Str"]

# ---------------------------------------------------------------------------
# TaskSpec dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskSpec:
    """A single Phase A task specification.

    Attributes:
        task_id: Unique identifier in the format ``A-{CAT}-{NNNN}``.
        category: Phase A category code (e.g. ``A-MTH``).
        description: Self-contained natural-language task description.
            An LLM reading only this field plus the toke spec should be
            able to generate correct toke code.
        expected_signature: The expected toke function signature
            (e.g. ``F=add(a:i64;b:i64):i64``).
        difficulty: Difficulty tier 1-3.
            1 = basic single operation, 2 = moderate composition,
            3 = complex multi-concept.
        type_hints: Relevant toke types the solution will use.
        test_input_hint: A human-readable hint describing representative
            test inputs for validating the generated code.
    """

    task_id: str
    category: str
    description: str
    expected_signature: str
    difficulty: int
    type_hints: list[str] = field(default_factory=list)
    test_input_hint: str = ""


# ---------------------------------------------------------------------------
# CurriculumGenerator
# ---------------------------------------------------------------------------


class CurriculumGenerator:
    """Deterministic generator of Phase A task specifications.

    Uses ``random.Random(seed)`` for all random choices, ensuring
    reproducible output across runs.
    """

    def __init__(self, seed: int = 42, total_tasks: int = 50_000) -> None:
        self._seed = seed
        self._total_tasks = total_tasks
        self._rng = random.Random(seed)

    # -- public API ---------------------------------------------------------

    def generate(self) -> list[TaskSpec]:
        """Generate the full curriculum, round-robin across categories.

        Returns a deterministic list of ``total_tasks`` TaskSpec objects,
        interleaved across categories so that the pipeline processes a mix
        of categories from the start rather than exhausting one at a time.
        """
        per_category = self._total_tasks // len(CATEGORIES)
        remainder = self._total_tasks % len(CATEGORIES)

        per_cat_tasks: dict[str, list[TaskSpec]] = {}
        for idx, cat in enumerate(CATEGORIES):
            count = per_category + (1 if idx < remainder else 0)
            per_cat_tasks[cat] = self.generate_category(cat, count)

        # Round-robin interleave: take one from each category in turn
        tasks: list[TaskSpec] = []
        max_len = max(len(v) for v in per_cat_tasks.values())
        for i in range(max_len):
            for cat in CATEGORIES:
                cat_tasks = per_cat_tasks[cat]
                if i < len(cat_tasks):
                    tasks.append(cat_tasks[i])

        logger.info(
            "Generated %d tasks across %d categories (seed=%d, round-robin)",
            len(tasks),
            len(CATEGORIES),
            self._seed,
        )
        return tasks

    def generate_category(self, category: str, count: int) -> list[TaskSpec]:
        """Generate *count* tasks for a single category.

        The internal template pool is expanded deterministically; if
        *count* exceeds the pool size the pool is cycled with variant
        mutations that alter parameter names and add constraints to
        produce distinct LLM outputs.
        """
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category: {category!r}")

        pool = self._expand_category(category)
        # Shuffle deterministically
        self._rng.shuffle(pool)

        if count <= len(pool):
            return pool[:count]

        result: list[TaskSpec] = list(pool)
        cycle = 1
        while len(result) < count:
            for spec in pool:
                if len(result) >= count:
                    break
                variant = self._mutate_variant(spec, cycle)
                result.append(variant)
            cycle += 1

        return result[:count]

    def _mutate_variant(self, spec: TaskSpec, cycle: int) -> TaskSpec:
        """Create a variant of *spec* with mutated description for diversity.

        Each cycle applies deterministic mutations: renamed parameters,
        added constraints, or rephrased descriptions so LLMs generate
        structurally different code rather than identical copies.
        """
        # Deterministic parameter name pools
        param_names = [
            ("x", "y"), ("n", "m"), ("val", "other"),
            ("lhs", "rhs"), ("first", "second"), ("p", "q"),
            ("num1", "num2"), ("left", "right"),
        ]
        # Extra constraints to add diversity
        constraints = [
            "Use a helper variable to store the intermediate result.",
            "Compute the result using only addition and subtraction.",
            "Handle the edge case where the inputs are equal by returning early.",
            "Use a loop to compute the result iteratively.",
            "Use a conditional to handle negative inputs specially.",
            "Accumulate the result in a mutable binding.",
            "Return the result through a match expression if possible.",
            "Use nested if/el blocks for the control flow.",
        ]

        pair_idx = cycle % len(param_names)
        constraint_idx = cycle % len(constraints)

        p1, p2 = param_names[pair_idx]
        constraint = constraints[constraint_idx]

        # Build mutated description
        desc = spec.description
        # Swap 'a' and 'b' parameter names in description if they appear
        if "(a:" in spec.expected_signature and "(b:" not in desc:
            desc = desc  # keep as-is if no simple substitution
        mutated_desc = (
            f"{desc}. Variant {cycle}: use parameter names {p1} and {p2}. "
            f"{constraint}"
        )

        # Mutate the signature to use different param names
        sig = spec.expected_signature
        sig = sig.replace("(a:", f"({p1}:").replace(";b:", f";{p2}:")

        return TaskSpec(
            task_id=f"{spec.task_id}v{cycle}",
            category=spec.category,
            description=mutated_desc,
            expected_signature=sig,
            difficulty=spec.difficulty,
            type_hints=list(spec.type_hints),
            test_input_hint=spec.test_input_hint,
        )

    # -- private expansion per category ------------------------------------

    def _expand_category(self, category: str) -> list[TaskSpec]:
        dispatch = {
            "A-MTH": self._expand_math,
            "A-CND": self._expand_conditional,
            "A-STR": self._expand_string,
            "A-ARR": self._expand_array,
            "A-SRT": self._expand_sort_search,
            "A-ERR": self._expand_error,
        }
        return dispatch[category]()

    # -- A-MTH: Mathematical computation -----------------------------------

    def _expand_math(self) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seq = _Sequencer("A-MTH")

        # Tier 1 — basic arithmetic across type combinations
        binary_ops = [
            ("add", "+", "the sum of"),
            ("subtract", "-", "the difference of"),
            ("multiply", "*", "the product of"),
            ("divide", "/", "the quotient of"),
        ]
        for op_name, op_sym, op_desc in binary_ops:
            for ty in NUMERIC_TYPES:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-MTH",
                    description=(
                        f"Write a function F={op_name}(a:{ty};b:{ty}):{ty} "
                        f"that returns {op_desc} a and b"
                    ),
                    expected_signature=f"F={op_name}(a:{ty};b:{ty}):{ty}",
                    difficulty=1,
                    type_hints=[ty],
                    test_input_hint=f"{op_name}(3, 7) with type {ty}",
                ))

        # Tier 1 — unary operations
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=negate(x:{ty}):{ty} "
                    f"that returns the negation of x"
                ),
                expected_signature=f"F=negate(x:{ty}):{ty}",
                difficulty=1,
                type_hints=[ty],
                test_input_hint=f"negate(5) with type {ty}",
            ))

        # Tier 1 — modulo for integer types
        for ty in ["i64", "u64"]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=modulo(a:{ty};b:{ty}):{ty} "
                    f"that returns the remainder when a is divided by b"
                ),
                expected_signature=f"F=modulo(a:{ty};b:{ty}):{ty}",
                difficulty=1,
                type_hints=[ty],
                test_input_hint=f"modulo(10, 3) = 1 with type {ty}",
            ))

        # Tier 1 — absolute value
        for ty in ["i64", "f64"]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=abs(x:{ty}):{ty} "
                    f"that returns the absolute value of x"
                ),
                expected_signature=f"F=abs(x:{ty}):{ty}",
                difficulty=1,
                type_hints=[ty],
                test_input_hint=f"abs(-5) = 5 with type {ty}",
            ))

        # Tier 1 — min and max of two values
        for fn, desc in [("min", "the smaller"), ("max", "the larger")]:
            for ty in NUMERIC_TYPES:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-MTH",
                    description=(
                        f"Write a function F={fn}(a:{ty};b:{ty}):{ty} "
                        f"that returns {desc} of a and b"
                    ),
                    expected_signature=f"F={fn}(a:{ty};b:{ty}):{ty}",
                    difficulty=1,
                    type_hints=[ty],
                    test_input_hint=f"{fn}(3, 7) with type {ty}",
                ))

        # Tier 1 — type casting operations
        cast_pairs = [
            ("i64", "f64"), ("f64", "i64"), ("i64", "u64"),
            ("u64", "i64"), ("u64", "f64"), ("f64", "u64"),
        ]
        for src, dst in cast_pairs:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=toType(x:{src}):{dst} "
                    f"that converts x from {src} to {dst} using the as keyword"
                ),
                expected_signature=f"F=toType(x:{src}):{dst}",
                difficulty=1,
                type_hints=[src, dst],
                test_input_hint=f"toType(42) casting {src} to {dst}",
            ))

        # Tier 1 — clamp
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=clamp(x:{ty};lo:{ty};hi:{ty}):{ty} "
                    f"that returns lo if x < lo, hi if x > hi, and x otherwise"
                ),
                expected_signature=f"F=clamp(x:{ty};lo:{ty};hi:{ty}):{ty}",
                difficulty=1,
                type_hints=[ty],
                test_input_hint=f"clamp(5, 1, 10) = 5; clamp(-3, 0, 10) = 0 with type {ty}",
            ))

        # Tier 1 — isEven, isOdd, isPositive, isNegative, isZero
        predicates = [
            ("isEven", "true when x is even and false otherwise", "i64"),
            ("isOdd", "true when x is odd and false otherwise", "i64"),
            ("isPositive", "true when x is strictly greater than zero", "i64"),
            ("isNegative", "true when x is strictly less than zero", "i64"),
            ("isZero", "true when x equals zero", "i64"),
        ]
        for fn, desc, ty in predicates:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F={fn}(x:{ty}):bool "
                    f"that returns {desc}"
                ),
                expected_signature=f"F={fn}(x:{ty}):bool",
                difficulty=1,
                type_hints=[ty, "bool"],
                test_input_hint=f"{fn}(4) or {fn}(-3) with type {ty}",
            ))

        # Tier 2 — factorial
        for ty in ["i64", "u64"]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=factorial(n:{ty}):{ty} "
                    f"that returns the factorial of n using a loop. "
                    f"Return 1 when n is 0"
                ),
                expected_signature=f"F=factorial(n:{ty}):{ty}",
                difficulty=2,
                type_hints=[ty],
                test_input_hint=f"factorial(0)=1, factorial(5)=120 with type {ty}",
            ))

        # Tier 2 — recursive factorial
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=factorialRec(n:i64):i64 "
                "that returns the factorial of n using recursion. "
                "Return 1 when n <= 1"
            ),
            expected_signature="F=factorialRec(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="factorialRec(0)=1, factorialRec(6)=720",
        ))

        # Tier 2 — fibonacci (loop)
        for ty in ["i64", "u64"]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=fibonacci(n:{ty}):{ty} "
                    f"that returns the n-th Fibonacci number (0-indexed) "
                    f"using a loop. fib(0)=0, fib(1)=1"
                ),
                expected_signature=f"F=fibonacci(n:{ty}):{ty}",
                difficulty=2,
                type_hints=[ty],
                test_input_hint=f"fibonacci(0)=0, fibonacci(10)=55 with type {ty}",
            ))

        # Tier 2 — recursive fibonacci
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=fibonacciRec(n:i64):i64 "
                "that returns the n-th Fibonacci number using recursion. "
                "fib(0)=0, fib(1)=1"
            ),
            expected_signature="F=fibonacciRec(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="fibonacciRec(0)=0, fibonacciRec(7)=13",
        ))

        # Tier 2 — GCD (Euclidean)
        for ty in ["i64", "u64"]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=gcd(a:{ty};b:{ty}):{ty} "
                    f"that returns the greatest common divisor of a and b "
                    f"using the Euclidean algorithm"
                ),
                expected_signature=f"F=gcd(a:{ty};b:{ty}):{ty}",
                difficulty=2,
                type_hints=[ty],
                test_input_hint=f"gcd(12, 8) = 4 with type {ty}",
            ))

        # Tier 2 — LCM
        for ty in ["i64", "u64"]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=lcm(a:{ty};b:{ty}):{ty} "
                    f"that returns the least common multiple of a and b. "
                    f"Use the formula lcm(a,b) = a / gcd(a,b) * b"
                ),
                expected_signature=f"F=lcm(a:{ty};b:{ty}):{ty}",
                difficulty=2,
                type_hints=[ty],
                test_input_hint=f"lcm(4, 6) = 12 with type {ty}",
            ))

        # Tier 2 — isPrime
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=isPrime(n:i64):bool "
                "that returns true if n is a prime number and false otherwise. "
                "Numbers less than 2 are not prime"
            ),
            expected_signature="F=isPrime(n:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isPrime(1)=false, isPrime(2)=true, isPrime(17)=true, isPrime(15)=false",
        ))

        # Tier 2 — power (exponentiation)
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=power(base:{ty};exp:i64):{ty} "
                    f"that returns base raised to the power exp using a loop. "
                    f"Assume exp >= 0"
                ),
                expected_signature=f"F=power(base:{ty};exp:i64):{ty}",
                difficulty=2,
                type_hints=[ty, "i64"],
                test_input_hint=f"power(2, 10) = 1024 with base type {ty}",
            ))

        # Tier 2 — sum of digits
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=digitSum(n:i64):i64 "
                "that returns the sum of the digits of n. "
                "Handle negative numbers by using the absolute value"
            ),
            expected_signature="F=digitSum(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="digitSum(123)=6, digitSum(-45)=9",
        ))

        # Tier 2 — count digits
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=countDigits(n:i64):i64 "
                "that returns the number of digits in n. "
                "Handle negative numbers and treat 0 as having 1 digit"
            ),
            expected_signature="F=countDigits(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="countDigits(0)=1, countDigits(123)=3, countDigits(-99)=2",
        ))

        # Tier 2 — reverse integer
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=reverseInt(n:i64):i64 "
                "that returns n with its digits reversed. "
                "reverseInt(123) returns 321. "
                "Preserve the sign: reverseInt(-45) returns -54"
            ),
            expected_signature="F=reverseInt(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="reverseInt(123)=321, reverseInt(-45)=-54, reverseInt(100)=1",
        ))

        # Tier 2 — isPalindrome (number)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=isPalindromeNum(n:i64):bool "
                "that returns true if n reads the same forwards and backwards. "
                "Negative numbers are not palindromes"
            ),
            expected_signature="F=isPalindromeNum(n:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isPalindromeNum(121)=true, isPalindromeNum(-121)=false, isPalindromeNum(10)=false",
        ))

        # Tier 3 — fast exponentiation (binary)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=fastPow(base:i64;exp:i64):i64 "
                "that computes base raised to exp using binary exponentiation "
                "(repeated squaring). Assume exp >= 0. "
                "The algorithm checks if exp is odd (multiply by base), "
                "then squares base and halves exp"
            ),
            expected_signature="F=fastPow(base:i64;exp:i64):i64",
            difficulty=3,
            type_hints=["i64"],
            test_input_hint="fastPow(2, 10)=1024, fastPow(3, 5)=243",
        ))

        # Tier 3 — modular exponentiation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=modPow(base:i64;exp:i64;m:i64):i64 "
                "that computes (base^exp) mod m using binary exponentiation. "
                "Assume exp >= 0, m > 0. At each step take mod m to avoid overflow"
            ),
            expected_signature="F=modPow(base:i64;exp:i64;m:i64):i64",
            difficulty=3,
            type_hints=["i64"],
            test_input_hint="modPow(2, 10, 1000)=24, modPow(3, 13, 7)=3",
        ))

        # Tier 3 — Sieve of Eratosthenes: count primes up to n
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=countPrimes(n:i64):i64 "
                "that returns the count of prime numbers less than or equal to n. "
                "Use the Sieve of Eratosthenes algorithm. Return 0 when n < 2"
            ),
            expected_signature="F=countPrimes(n:i64):i64",
            difficulty=3,
            type_hints=["i64", "[bool]"],
            test_input_hint="countPrimes(10)=4, countPrimes(1)=0, countPrimes(30)=10",
        ))

        # Tier 3 — extended GCD
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=extGcd(a:i64;b:i64):[i64] "
                "that returns an array [g; x; y] where g = gcd(a, b) "
                "and a*x + b*y = g. Use the extended Euclidean algorithm"
            ),
            expected_signature="F=extGcd(a:i64;b:i64):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="extGcd(35, 15) = [5; ...] where 35*x+15*y=5",
        ))

        # Tier 3 — integer square root
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=isqrt(n:i64):i64 "
                "that returns the integer square root of n, "
                "i.e. the largest integer r such that r*r <= n. "
                "Use binary search. Assume n >= 0"
            ),
            expected_signature="F=isqrt(n:i64):i64",
            difficulty=3,
            type_hints=["i64"],
            test_input_hint="isqrt(0)=0, isqrt(8)=2, isqrt(9)=3, isqrt(26)=5",
        ))

        # Tier 3 — collatz steps
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=collatzSteps(n:i64):i64 "
                "that returns the number of steps to reach 1 in the "
                "Collatz sequence starting from n. "
                "If n is even, next = n/2. If n is odd, next = 3*n+1. "
                "Assume n >= 1. collatzSteps(1) = 0"
            ),
            expected_signature="F=collatzSteps(n:i64):i64",
            difficulty=3,
            type_hints=["i64"],
            test_input_hint="collatzSteps(1)=0, collatzSteps(6)=8, collatzSteps(27)=111",
        ))

        # Tier 2 — average of array
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=average(arr:[{ty}]):f64 "
                    f"that returns the arithmetic mean of the elements in arr. "
                    f"Cast the sum to f64 before dividing by the count"
                ),
                expected_signature=f"F=average(arr:[{ty}]):f64",
                difficulty=2,
                type_hints=[ty, f"[{ty}]", "f64"],
                test_input_hint=f"average([1;2;3;4;5]) = 3.0 with element type {ty}",
            ))

        # Tier 2 — distance between two 2D points
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=distance(x1:f64;y1:f64;x2:f64;y2:f64):f64 "
                "that returns the Euclidean distance between points (x1,y1) and (x2,y2). "
                "Compute sqrt((x2-x1)^2 + (y2-y1)^2) using a power and sqrt helper, "
                "or by multiplying differences and iterating for the square root"
            ),
            expected_signature="F=distance(x1:f64;y1:f64;x2:f64;y2:f64):f64",
            difficulty=2,
            type_hints=["f64"],
            test_input_hint="distance(0.0, 0.0, 3.0, 4.0) = 5.0",
        ))

        # Tier 3 — nth prime
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=nthPrime(n:i64):i64 "
                "that returns the n-th prime number (1-indexed). "
                "nthPrime(1) = 2, nthPrime(4) = 7. "
                "Iterate candidate numbers and test each for primality"
            ),
            expected_signature="F=nthPrime(n:i64):i64",
            difficulty=3,
            type_hints=["i64", "bool"],
            test_input_hint="nthPrime(1)=2, nthPrime(6)=13, nthPrime(10)=29",
        ))

        # Tier 3 — evaluate polynomial (Horner's method)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=evalPoly(coeffs:[f64];x:f64):f64 "
                "that evaluates a polynomial at x using Horner's method. "
                "coeffs[0] is the highest-degree coefficient. "
                "For coeffs=[2.0;3.0;1.0] and x=5.0, compute 2*25 + 3*5 + 1 = 66.0"
            ),
            expected_signature="F=evalPoly(coeffs:[f64];x:f64):f64",
            difficulty=3,
            type_hints=["f64", "[f64]"],
            test_input_hint="evalPoly([2.0;3.0;1.0], 5.0) = 66.0",
        ))

        # Tier 1 — sign function
        for ty in ["i64", "f64"]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=sign(x:{ty}):i64 "
                    f"that returns -1 if x is negative, 0 if x is zero, "
                    f"and 1 if x is positive"
                ),
                expected_signature=f"F=sign(x:{ty}):i64",
                difficulty=1,
                type_hints=[ty, "i64"],
                test_input_hint=f"sign(-5)=-1, sign(0)=0, sign(3)=1 with type {ty}",
            ))

        # Tier 2 — sum of range
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=sumRange(a:i64;b:i64):i64 "
                "that returns the sum of all integers from a to b inclusive. "
                "If a > b, return 0. Use the formula n*(n+1)/2 or a loop"
            ),
            expected_signature="F=sumRange(a:i64;b:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="sumRange(1, 100)=5050, sumRange(5, 3)=0",
        ))

        # Tier 2 — combinations (n choose k)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=choose(n:i64;k:i64):i64 "
                "that returns the binomial coefficient n choose k. "
                "Use the multiplicative formula: product of (n-i)/(i+1) for i from 0 to k-1. "
                "Return 0 if k < 0 or k > n"
            ),
            expected_signature="F=choose(n:i64;k:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="choose(5, 2)=10, choose(10, 0)=1, choose(3, 5)=0",
        ))

        # Tier 3 — digital root
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=digitalRoot(n:i64):i64 "
                "that repeatedly sums the digits of n until the result "
                "is a single digit, then returns that digit. "
                "Handle negative numbers by using the absolute value. "
                "digitalRoot(0)=0"
            ),
            expected_signature="F=digitalRoot(n:i64):i64",
            difficulty=3,
            type_hints=["i64"],
            test_input_hint="digitalRoot(0)=0, digitalRoot(493)=7, digitalRoot(-99)=9",
        ))


        # ---------------------------------------------------------------
        # NEW TEMPLATES: A-MTH additions (~46 new tasks)
        # ---------------------------------------------------------------

        # Tier 1 — arithmetic mean of 2 values
        for ty in ["i64", "f64"]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-MTH",
                description=(
                    f"Write a function F=mean2(a:{ty};b:{ty}):f64 "
                    f"that returns the arithmetic mean of a and b as f64. "
                    f"Cast to f64 before dividing by 2.0"
                ),
                expected_signature=f"F=mean2(a:{ty};b:{ty}):f64",
                difficulty=1,
                type_hints=[ty, "f64"],
                test_input_hint=f"mean2(3, 7) = 5.0 with type {ty}",
            ))

        # Tier 1 — ceiling division
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=ceilDiv(a:i64;b:i64):i64 "
                "that returns the ceiling of a divided by b. "
                "Compute as (a + b - 1) / b. Assume a >= 0 and b > 0"
            ),
            expected_signature="F=ceilDiv(a:i64;b:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="ceilDiv(7, 3) = 3, ceilDiv(6, 3) = 2",
        ))

        # Tier 1 — difference of squares
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=diffSquares(a:i64;b:i64):i64 "
                "that returns a*a - b*b"
            ),
            expected_signature="F=diffSquares(a:i64;b:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="diffSquares(5, 3) = 16",
        ))

        # Tier 1 — sum of squares
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=sumOfSquares(a:i64;b:i64):i64 "
                "that returns a*a + b*b"
            ),
            expected_signature="F=sumOfSquares(a:i64;b:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="sumOfSquares(3, 4) = 25",
        ))

        # Tier 1 — hypotenuse squared
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=hypotSq(a:f64;b:f64):f64 "
                "that returns a*a + b*b (the hypotenuse squared)"
            ),
            expected_signature="F=hypotSq(a:f64;b:f64):f64",
            difficulty=1,
            type_hints=["f64"],
            test_input_hint="hypotSq(3.0, 4.0) = 25.0",
        ))

        # Tier 2 — linear interpolation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=lerp(a:f64;b:f64;t:f64):f64 "
                "that returns the linear interpolation between a and b "
                "at parameter t. Compute a + (b - a) * t"
            ),
            expected_signature="F=lerp(a:f64;b:f64;t:f64):f64",
            difficulty=2,
            type_hints=["f64"],
            test_input_hint="lerp(0.0, 10.0, 0.5) = 5.0",
        ))

        # Tier 1 — manhattan distance
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=manhattan(x1:i64;y1:i64;x2:i64;y2:i64):i64 "
                "that returns the Manhattan distance between (x1,y1) and (x2,y2). "
                "Compute abs(x1-x2) + abs(y1-y2) where abs returns the absolute value"
            ),
            expected_signature="F=manhattan(x1:i64;y1:i64;x2:i64;y2:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="manhattan(1, 2, 4, 6) = 7",
        ))

        # Tier 1 — euclidean distance squared
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=distSq(x1:i64;y1:i64;x2:i64;y2:i64):i64 "
                "that returns the squared Euclidean distance between (x1,y1) "
                "and (x2,y2). Compute (x1-x2)*(x1-x2) + (y1-y2)*(y1-y2)"
            ),
            expected_signature="F=distSq(x1:i64;y1:i64;x2:i64;y2:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="distSq(0, 0, 3, 4) = 25",
        ))

        # Tier 2 — dot product of two 3-element vectors
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=dot3(a:[f64];b:[f64]):f64 "
                "that returns the dot product of two 3-element vectors. "
                "Compute a[0]*b[0] + a[1]*b[1] + a[2]*b[2]. "
                "Assume both arrays have exactly 3 elements"
            ),
            expected_signature="F=dot3(a:[f64];b:[f64]):f64",
            difficulty=2,
            type_hints=["f64", "[f64]"],
            test_input_hint="dot3([1.0;2.0;3.0],[4.0;5.0;6.0]) = 32.0",
        ))

        # Tier 2 — cross product magnitude of 2D vectors
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=cross2d(ax:f64;ay:f64;bx:f64;by:f64):f64 "
                "that returns the magnitude of the cross product of two 2D "
                "vectors (ax,ay) and (bx,by). Compute ax*by - ay*bx"
            ),
            expected_signature="F=cross2d(ax:f64;ay:f64;bx:f64;by:f64):f64",
            difficulty=2,
            type_hints=["f64"],
            test_input_hint="cross2d(1.0, 0.0, 0.0, 1.0) = 1.0",
        ))

        # Tier 1 — triangular number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=triangular(n:i64):i64 "
                "that returns the n-th triangular number: n*(n+1)/2. "
                "Assume n >= 0"
            ),
            expected_signature="F=triangular(n:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="triangular(0)=0, triangular(4)=10, triangular(10)=55",
        ))

        # Tier 2 — pentagonal number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=pentagonal(n:i64):i64 "
                "that returns the n-th pentagonal number: n*(3*n-1)/2. "
                "Assume n >= 1"
            ),
            expected_signature="F=pentagonal(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="pentagonal(1)=1, pentagonal(5)=35",
        ))

        # Tier 2 — is perfect square
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=isPerfectSquare(n:i64):bool "
                "that returns true if n is a perfect square. "
                "Find the integer square root r and check if r*r equals n. "
                "Return false for negative numbers"
            ),
            expected_signature="F=isPerfectSquare(n:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isPerfectSquare(16)=true, isPerfectSquare(15)=false",
        ))

        # Tier 2 — is power of two
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=isPowerOfTwo(n:i64):bool "
                "that returns true if n is a positive power of two. "
                "Repeatedly divide by 2 while even; result should be 1"
            ),
            expected_signature="F=isPowerOfTwo(n:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isPowerOfTwo(8)=true, isPowerOfTwo(6)=false, isPowerOfTwo(0)=false",
        ))

        # Tier 2 — count trailing zeros in binary
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=trailingZeros(n:i64):i64 "
                "that returns the number of trailing zeros in the binary "
                "representation of n. Count how many times n is divisible by 2. "
                "Return 0 if n is 0"
            ),
            expected_signature="F=trailingZeros(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="trailingZeros(8)=3, trailingZeros(12)=2, trailingZeros(0)=0",
        ))

        # Tier 2 — bit length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=bitLen(n:i64):i64 "
                "that returns the number of bits needed to represent n "
                "(position of the highest set bit plus one). "
                "Return 0 if n is 0. Use a loop dividing by 2"
            ),
            expected_signature="F=bitLen(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="bitLen(0)=0, bitLen(1)=1, bitLen(8)=4",
        ))

        # Tier 2 — hamming weight (popcount)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=popcount(n:i64):i64 "
                "that returns the number of 1-bits in the binary "
                "representation of n (Hamming weight). "
                "Use a loop: count n modulo 2 and divide by 2"
            ),
            expected_signature="F=popcount(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="popcount(7)=3, popcount(0)=0, popcount(255)=8",
        ))

        # Tier 2 — geometric series sum iterative
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=geoSum(a:f64;r:f64;n:i64):f64 "
                "that returns the sum of the first n terms of a geometric "
                "series with initial term a and common ratio r. "
                "Compute iteratively: sum = a + a*r + a*r*r + ..."
            ),
            expected_signature="F=geoSum(a:f64;r:f64;n:i64):f64",
            difficulty=2,
            type_hints=["f64", "i64"],
            test_input_hint="geoSum(1.0, 2.0, 4) = 15.0",
        ))

        # Tier 2 — harmonic sum
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=harmonicSum(n:i64):f64 "
                "that returns the sum 1.0 + 1.0/2.0 + 1.0/3.0 + ... + 1.0/n. "
                "Use a loop. Return 0.0 if n < 1"
            ),
            expected_signature="F=harmonicSum(n:i64):f64",
            difficulty=2,
            type_hints=["i64", "f64"],
            test_input_hint="harmonicSum(1) = 1.0, harmonicSum(4) approximately 2.083",
        ))

        # Tier 2 — sum of first n cubes
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=sumCubes(n:i64):i64 "
                "that returns the sum of cubes 1*1*1 + 2*2*2 + ... + n*n*n. "
                "Use a loop. Return 0 if n < 1"
            ),
            expected_signature="F=sumCubes(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="sumCubes(3) = 36, sumCubes(0) = 0",
        ))

        # Tier 2 — sum of first n squares
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=sumSquaresN(n:i64):i64 "
                "that returns 1*1 + 2*2 + ... + n*n. "
                "Use the formula n*(n+1)*(2*n+1)/6 or a loop. Return 0 if n < 1"
            ),
            expected_signature="F=sumSquaresN(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="sumSquaresN(3) = 14, sumSquaresN(10) = 385",
        ))

        # Tier 2 — product of range
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=productRange(a:i64;b:i64):i64 "
                "that returns the product of all integers from a to b inclusive. "
                "If a > b, return 1"
            ),
            expected_signature="F=productRange(a:i64;b:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="productRange(1, 5) = 120, productRange(3, 3) = 3",
        ))

        # Tier 2 — binomial coefficient iterative
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=binomial(n:i64;k:i64):i64 "
                "that returns n choose k using the iterative multiplicative "
                "formula. Optimize by using min(k, n-k). Return 0 if k < 0 or k > n"
            ),
            expected_signature="F=binomial(n:i64;k:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="binomial(10, 3) = 120, binomial(5, 0) = 1",
        ))

        # Tier 3 — catalan number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=catalan(n:i64):i64 "
                "that returns the n-th Catalan number. "
                "Use the formula: catalan(n) = binomial(2*n, n) / (n+1). "
                "Compute the binomial coefficient iteratively"
            ),
            expected_signature="F=catalan(n:i64):i64",
            difficulty=3,
            type_hints=["i64"],
            test_input_hint="catalan(0)=1, catalan(3)=5, catalan(5)=42",
        ))

        # Tier 2 — stirling approximation (iterative factorial as f64)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=factorialF(n:i64):f64 "
                "that returns n factorial as an f64. "
                "Use a loop multiplying from 1.0 to n as f64. "
                "Return 1.0 if n < 1"
            ),
            expected_signature="F=factorialF(n:i64):f64",
            difficulty=2,
            type_hints=["i64", "f64"],
            test_input_hint="factorialF(5) = 120.0, factorialF(0) = 1.0",
        ))

        # Tier 2 — is armstrong number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=isArmstrong(n:i64):bool "
                "that returns true if n is an Armstrong number. "
                "An Armstrong number of d digits equals the sum of each "
                "digit raised to the power d. First count digits, then "
                "compute the sum of each digit to the power of d using a loop"
            ),
            expected_signature="F=isArmstrong(n:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isArmstrong(153)=true, isArmstrong(9474)=true, isArmstrong(10)=false",
        ))

        # Tier 2 — is harshad number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=isHarshad(n:i64):bool "
                "that returns true if n is a Harshad number "
                "(divisible by the sum of its digits). "
                "Return false if n < 1"
            ),
            expected_signature="F=isHarshad(n:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isHarshad(18)=true, isHarshad(19)=false",
        ))

        # Tier 3 — luhn checksum
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=luhnCheck(n:i64):bool "
                "that returns true if the number n passes the Luhn check. "
                "From the rightmost digit, double every second digit. "
                "If doubling produces a value > 9, subtract 9. "
                "Sum all digits; result is valid if sum modulo 10 equals 0"
            ),
            expected_signature="F=luhnCheck(n:i64):bool",
            difficulty=3,
            type_hints=["i64", "bool"],
            test_input_hint="luhnCheck(79927398713)=true, luhnCheck(12345)=false",
        ))

        # Tier 2 — digital sum (repeated until single digit)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=digitalSum(n:i64):i64 "
                "that repeatedly sums the digits of n until a single "
                "digit remains. Use the absolute value of n. "
                "digitalSum(0)=0"
            ),
            expected_signature="F=digitalSum(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="digitalSum(493)=7, digitalSum(0)=0",
        ))

        # Tier 2 — nth fibonacci modulo m
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=fibMod(n:i64;m:i64):i64 "
                "that returns the n-th Fibonacci number modulo m. "
                "Compute iteratively, taking modulo at each step to "
                "avoid overflow. fib(0)=0, fib(1)=1"
            ),
            expected_signature="F=fibMod(n:i64;m:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="fibMod(10, 7) = 6",
        ))

        # Tier 2 — count divisors
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=countDivisors(n:i64):i64 "
                "that returns the number of positive divisors of n. "
                "Iterate from 1 to n, counting numbers that divide n evenly. "
                "Assume n >= 1"
            ),
            expected_signature="F=countDivisors(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="countDivisors(12)=6, countDivisors(1)=1",
        ))

        # Tier 2 — sum of divisors
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=sumDivisors(n:i64):i64 "
                "that returns the sum of all positive divisors of n. "
                "Iterate from 1 to n. Assume n >= 1"
            ),
            expected_signature="F=sumDivisors(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="sumDivisors(12)=28, sumDivisors(1)=1",
        ))

        # Tier 3 — euler totient function
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=totient(n:i64):i64 "
                "that returns Euler's totient of n: the count of integers "
                "from 1 to n that are coprime with n. "
                "Two numbers are coprime if their GCD is 1"
            ),
            expected_signature="F=totient(n:i64):i64",
            difficulty=3,
            type_hints=["i64"],
            test_input_hint="totient(10)=4, totient(1)=1",
        ))

        # Tier 2 — is coprime
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=isCoprime(a:i64;b:i64):bool "
                "that returns true if gcd(a, b) equals 1"
            ),
            expected_signature="F=isCoprime(a:i64;b:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isCoprime(14, 15)=true, isCoprime(14, 21)=false",
        ))

        # Tier 3 — modular inverse
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=modInverse(a:i64;m:i64):i64 "
                "that returns the modular inverse of a modulo m. "
                "Use the extended Euclidean algorithm. "
                "Return -1 if the inverse does not exist (gcd(a,m) is not 1)"
            ),
            expected_signature="F=modInverse(a:i64;m:i64):i64",
            difficulty=3,
            type_hints=["i64"],
            test_input_hint="modInverse(3, 11) = 4, modInverse(2, 4) = -1",
        ))

        # Tier 2 — next power of two
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=nextPow2(n:i64):i64 "
                "that returns the smallest power of 2 that is greater "
                "than or equal to n. Use a loop doubling from 1. "
                "Assume n >= 1"
            ),
            expected_signature="F=nextPow2(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="nextPow2(5)=8, nextPow2(8)=8, nextPow2(1)=1",
        ))

        # Tier 2 — previous power of two
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=prevPow2(n:i64):i64 "
                "that returns the largest power of 2 that is less than "
                "or equal to n. Use a loop. Assume n >= 1"
            ),
            expected_signature="F=prevPow2(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="prevPow2(5)=4, prevPow2(8)=8, prevPow2(1)=1",
        ))

        # Tier 1 — round to nearest multiple
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=roundToMultiple(n:i64;m:i64):i64 "
                "that rounds n to the nearest multiple of m. "
                "If exactly halfway, round up. Assume m > 0"
            ),
            expected_signature="F=roundToMultiple(n:i64;m:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="roundToMultiple(7, 5)=5, roundToMultiple(8, 5)=10",
        ))

        # Tier 2 — sigmoid approximation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=sigmoid(x:f64):f64 "
                "that returns an approximation of the sigmoid function "
                "1.0 / (1.0 + exp(-x)). Approximate exp using a Taylor "
                "series with at least 10 terms iteratively"
            ),
            expected_signature="F=sigmoid(x:f64):f64",
            difficulty=2,
            type_hints=["f64"],
            test_input_hint="sigmoid(0.0) approximately 0.5",
        ))

        # Tier 2 — tanh approximation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=tanhApprox(x:f64):f64 "
                "that returns an approximation of tanh(x). "
                "Clamp x to [-5.0, 5.0], then compute "
                "(exp(2*x) - 1) / (exp(2*x) + 1) using iterative exp approximation"
            ),
            expected_signature="F=tanhApprox(x:f64):f64",
            difficulty=2,
            type_hints=["f64"],
            test_input_hint="tanhApprox(0.0) approximately 0.0",
        ))

        # Tier 1 — relu for i64
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=reluI(x:i64):i64 "
                "that returns x if x > 0, otherwise returns 0"
            ),
            expected_signature="F=reluI(x:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="reluI(5)=5, reluI(-3)=0, reluI(0)=0",
        ))

        # Tier 1 — relu for f64
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=reluF(x:f64):f64 "
                "that returns x if x > 0.0, otherwise returns 0.0"
            ),
            expected_signature="F=reluF(x:f64):f64",
            difficulty=1,
            type_hints=["f64"],
            test_input_hint="reluF(3.14)=3.14, reluF(-1.5)=0.0",
        ))

        # Tier 1 — wrap around (circular index)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=wrapIndex(idx:i64;len:i64):i64 "
                "that wraps idx to be within [0, len). "
                "Handle negative idx by adding len until positive, then "
                "take modulo. Assume len > 0"
            ),
            expected_signature="F=wrapIndex(idx:i64;len:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="wrapIndex(5, 3)=2, wrapIndex(-1, 5)=4",
        ))

        # Tier 2 — map range (linear rescale)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-MTH",
            description=(
                "Write a function F=mapRange(x:f64;a:f64;b:f64;c:f64;d:f64):f64 "
                "that linearly maps x from range [a,b] to range [c,d]. "
                "Compute c + (x - a) * (d - c) / (b - a). "
                "Assume b is not equal to a"
            ),
            expected_signature="F=mapRange(x:f64;a:f64;b:f64;c:f64;d:f64):f64",
            difficulty=2,
            type_hints=["f64"],
            test_input_hint="mapRange(5.0, 0.0, 10.0, 0.0, 100.0) = 50.0",
        ))

        return tasks

    # -- A-CND: Conditional logic ------------------------------------------

    def _expand_conditional(self) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seq = _Sequencer("A-CND")

        # Tier 1 — max/min of 2
        for fn, desc in [("max2", "the larger"), ("min2", "the smaller")]:
            for ty in NUMERIC_TYPES:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-CND",
                    description=(
                        f"Write a function F={fn}(a:{ty};b:{ty}):{ty} "
                        f"that returns {desc} of a and b using an if expression"
                    ),
                    expected_signature=f"F={fn}(a:{ty};b:{ty}):{ty}",
                    difficulty=1,
                    type_hints=[ty],
                    test_input_hint=f"{fn}(3, 7) with type {ty}",
                ))

        # Tier 1 — max/min of 3
        for fn, desc in [("max3", "the largest"), ("min3", "the smallest")]:
            for ty in NUMERIC_TYPES:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-CND",
                    description=(
                        f"Write a function F={fn}(a:{ty};b:{ty};c:{ty}):{ty} "
                        f"that returns {desc} of three values using if expressions"
                    ),
                    expected_signature=f"F={fn}(a:{ty};b:{ty};c:{ty}):{ty}",
                    difficulty=1,
                    type_hints=[ty],
                    test_input_hint=f"{fn}(3, 7, 5) with type {ty}",
                ))

        # Tier 1 — boolean logic gates
        gates = [
            ("boolAnd", "a && b", "the logical AND of a and b"),
            ("boolOr", "a || b", "the logical OR of a and b"),
            ("boolNot", None, "the logical NOT of a"),
            ("boolXor", None, "true when exactly one of a and b is true"),
            ("boolNand", None, "true unless both a and b are true"),
            ("boolNor", None, "true only when both a and b are false"),
        ]
        for fn, _expr, desc in gates:
            if fn == "boolNot":
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-CND",
                    description=(
                        f"Write a function F={fn}(a:bool):bool "
                        f"that returns {desc}"
                    ),
                    expected_signature=f"F={fn}(a:bool):bool",
                    difficulty=1,
                    type_hints=["bool"],
                    test_input_hint=f"{fn}(true)=false, {fn}(false)=true",
                ))
            else:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-CND",
                    description=(
                        f"Write a function F={fn}(a:bool;b:bool):bool "
                        f"that returns {desc}"
                    ),
                    expected_signature=f"F={fn}(a:bool;b:bool):bool",
                    difficulty=1,
                    type_hints=["bool"],
                    test_input_hint=f"{fn}(true, false) with boolean inputs",
                ))

        # Tier 1 — comparison helpers
        comparisons = [
            ("isEqual", "true when a equals b"),
            ("isNotEqual", "true when a does not equal b"),
            ("isLessThan", "true when a is strictly less than b"),
            ("isGreaterThan", "true when a is strictly greater than b"),
            ("isLessOrEqual", "true when a is less than or equal to b"),
            ("isGreaterOrEqual", "true when a is greater than or equal to b"),
        ]
        for fn, desc in comparisons:
            for ty in NUMERIC_TYPES:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-CND",
                    description=(
                        f"Write a function F={fn}(a:{ty};b:{ty}):bool "
                        f"that returns {desc}"
                    ),
                    expected_signature=f"F={fn}(a:{ty};b:{ty}):bool",
                    difficulty=1,
                    type_hints=[ty, "bool"],
                    test_input_hint=f"{fn}(3, 7) with type {ty}",
                ))

        # Tier 1 — isBetween
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-CND",
                description=(
                    f"Write a function F=isBetween(x:{ty};lo:{ty};hi:{ty}):bool "
                    f"that returns true when lo <= x && x <= hi"
                ),
                expected_signature=f"F=isBetween(x:{ty};lo:{ty};hi:{ty}):bool",
                difficulty=1,
                type_hints=[ty, "bool"],
                test_input_hint=f"isBetween(5, 1, 10)=true, isBetween(0, 1, 10)=false with {ty}",
            ))

        # Tier 2 — grade classifier
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=grade(score:i64):Str "
                "that returns a letter grade: "
                "\"A\" for score >= 90, \"B\" for >= 80, \"C\" for >= 70, "
                "\"D\" for >= 60, \"F\" otherwise"
            ),
            expected_signature="F=grade(score:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="grade(95)=\"A\", grade(73)=\"C\", grade(45)=\"F\"",
        ))

        # Tier 2 — leap year
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isLeapYear(year:i64):bool "
                "that returns true if year is a leap year. "
                "A year is a leap year if divisible by 4, "
                "except years divisible by 100 unless also divisible by 400"
            ),
            expected_signature="F=isLeapYear(year:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isLeapYear(2000)=true, isLeapYear(1900)=false, isLeapYear(2024)=true",
        ))

        # Tier 2 — fizzbuzz for single number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=fizzBuzz(n:i64):Str "
                "that returns \"FizzBuzz\" if n is divisible by both 3 and 5, "
                "\"Fizz\" if divisible by 3 only, \"Buzz\" if divisible by 5 only, "
                "and the number as a string otherwise"
            ),
            expected_signature="F=fizzBuzz(n:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="fizzBuzz(15)=\"FizzBuzz\", fizzBuzz(9)=\"Fizz\", fizzBuzz(7)=\"7\"",
        ))

        # Tier 2 — day of week name
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=dayName(n:i64):Str "
                "that returns the name of the day of the week for n where "
                "1=\"Monday\", 2=\"Tuesday\", ..., 7=\"Sunday\". "
                "Return \"Invalid\" for values outside 1-7"
            ),
            expected_signature="F=dayName(n:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="dayName(1)=\"Monday\", dayName(7)=\"Sunday\", dayName(0)=\"Invalid\"",
        ))

        # Tier 2 — triangle type classifier
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=triangleType(a:i64;b:i64;c:i64):Str "
                "that classifies a triangle by its sides. "
                "Return \"equilateral\" if all sides equal, \"isosceles\" if "
                "exactly two sides equal, \"scalene\" if all different, "
                "and \"invalid\" if the sides cannot form a triangle"
            ),
            expected_signature="F=triangleType(a:i64;b:i64;c:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="triangleType(3,3,3)=\"equilateral\", triangleType(1,1,10)=\"invalid\"",
        ))

        # Tier 2 — number sign description
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=describeSign(x:f64):Str "
                "that returns \"positive\" if x > 0.0, \"negative\" if x < 0.0, "
                "and \"zero\" if x equals 0.0"
            ),
            expected_signature="F=describeSign(x:f64):Str",
            difficulty=1,
            type_hints=["f64", "Str"],
            test_input_hint="describeSign(3.14)=\"positive\", describeSign(-1.0)=\"negative\"",
        ))

        # Tier 2 — nested conditions: classify BMI
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=bmiCategory(bmi:f64):Str "
                "that returns \"underweight\" for bmi < 18.5, "
                "\"normal\" for 18.5 <= bmi < 25.0, "
                "\"overweight\" for 25.0 <= bmi < 30.0, "
                "\"obese\" for bmi >= 30.0"
            ),
            expected_signature="F=bmiCategory(bmi:f64):Str",
            difficulty=2,
            type_hints=["f64", "Str"],
            test_input_hint="bmiCategory(17.0)=\"underweight\", bmiCategory(22.5)=\"normal\"",
        ))

        # Tier 2 — median of three
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-CND",
                description=(
                    f"Write a function F=median3(a:{ty};b:{ty};c:{ty}):{ty} "
                    f"that returns the median (middle value) of a, b, and c "
                    f"using conditional expressions"
                ),
                expected_signature=f"F=median3(a:{ty};b:{ty};c:{ty}):{ty}",
                difficulty=2,
                type_hints=[ty],
                test_input_hint=f"median3(1, 3, 2) = 2 with type {ty}",
            ))

        # Tier 3 — nested boolean: date validity
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isValidDate(y:i64;m:i64;d:i64):bool "
                "that returns true if the date y/m/d is valid. "
                "Handle months 1-12, day ranges per month, "
                "and February 29 in leap years. "
                "A year is a leap year if divisible by 4, except centuries "
                "unless divisible by 400"
            ),
            expected_signature="F=isValidDate(y:i64;m:i64;d:i64):bool",
            difficulty=3,
            type_hints=["i64", "bool"],
            test_input_hint="isValidDate(2024,2,29)=true, isValidDate(2023,2,29)=false",
        ))

        # Tier 3 — classify quadrant
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=quadrant(x:f64;y:f64):Str "
                "that returns \"Q1\" for x>0,y>0; \"Q2\" for x<0,y>0; "
                "\"Q3\" for x<0,y<0; \"Q4\" for x>0,y<0; "
                "\"origin\" for x==0,y==0; \"x-axis\" when y==0; "
                "\"y-axis\" when x==0"
            ),
            expected_signature="F=quadrant(x:f64;y:f64):Str",
            difficulty=3,
            type_hints=["f64", "Str"],
            test_input_hint="quadrant(1.0,1.0)=\"Q1\", quadrant(0.0,0.0)=\"origin\"",
        ))

        # Tier 3 — Roman numeral (1-3999)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=toRoman(n:i64):Str "
                "that converts n (1-3999) to a Roman numeral string. "
                "Use the standard symbols: M=1000, CM=900, D=500, CD=400, "
                "C=100, XC=90, L=50, XL=40, X=10, IX=9, V=5, IV=4, I=1. "
                "Process from largest to smallest, appending symbols"
            ),
            expected_signature="F=toRoman(n:i64):Str",
            difficulty=3,
            type_hints=["i64", "Str"],
            test_input_hint="toRoman(1994)=\"MCMXCIV\", toRoman(58)=\"LVIII\"",
        ))

        # Tier 3 — match expression: HTTP status code category
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=httpCategory(code:i64):Str "
                "that returns \"informational\" for 100-199, "
                "\"success\" for 200-299, \"redirect\" for 300-399, "
                "\"client_error\" for 400-499, \"server_error\" for 500-599, "
                "and \"unknown\" otherwise"
            ),
            expected_signature="F=httpCategory(code:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="httpCategory(200)=\"success\", httpCategory(404)=\"client_error\"",
        ))

        # Tier 3 — multi-condition: password strength
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=passwordStrength(len:i64;hasUpper:bool;"
                "hasDigit:bool;hasSpecial:bool):Str "
                "that returns \"weak\" if len < 8, "
                "\"strong\" if len >= 12 and all three booleans are true, "
                "\"medium\" if len >= 8 and at least two of the three booleans are true, "
                "and \"weak\" otherwise"
            ),
            expected_signature=(
                "F=passwordStrength(len:i64;hasUpper:bool;"
                "hasDigit:bool;hasSpecial:bool):Str"
            ),
            difficulty=3,
            type_hints=["i64", "bool", "Str"],
            test_input_hint="passwordStrength(14,true,true,true)=\"strong\"",
        ))


        # ---------------------------------------------------------------
        # NEW TEMPLATES: A-CND additions (~46 new tasks)
        # ---------------------------------------------------------------

        # Tier 1 — classify angle
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=classifyAngle(deg:i64):Str "
                "that returns \"acute\" if 0 < deg < 90, \"right\" if deg equals 90, "
                "\"obtuse\" if 90 < deg < 180, \"straight\" if deg equals 180, "
                "and \"invalid\" otherwise"
            ),
            expected_signature="F=classifyAngle(deg:i64):Str",
            difficulty=1,
            type_hints=["i64", "Str"],
            test_input_hint="classifyAngle(45)=\"acute\", classifyAngle(90)=\"right\"",
        ))

        # Tier 1 — is valid RGB component
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isValidRgb(r:i64;g:i64;b:i64):bool "
                "that returns true if all three components are between 0 and 255 inclusive"
            ),
            expected_signature="F=isValidRgb(r:i64;g:i64;b:i64):bool",
            difficulty=1,
            type_hints=["i64", "bool"],
            test_input_hint="isValidRgb(0, 128, 255)=true, isValidRgb(-1, 0, 0)=false",
        ))

        # Tier 1 — is valid hex digit
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isHexDigit(c:Str):bool "
                "that returns true if c is a single character that is a valid "
                "hexadecimal digit (0-9, a-f, A-F)"
            ),
            expected_signature="F=isHexDigit(c:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isHexDigit(\"a\")=true, isHexDigit(\"g\")=false",
        ))

        # Tier 1 — is vowel
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isVowel(c:Str):bool "
                "that returns true if c is a vowel (a, e, i, o, u) "
                "case-insensitive. Assume c is a single character"
            ),
            expected_signature="F=isVowel(c:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isVowel(\"a\")=true, isVowel(\"A\")=true, isVowel(\"b\")=false",
        ))

        # Tier 1 — is consonant
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isConsonant(c:Str):bool "
                "that returns true if c is a letter that is not a vowel. "
                "Return false for non-letter characters"
            ),
            expected_signature="F=isConsonant(c:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isConsonant(\"b\")=true, isConsonant(\"a\")=false",
        ))

        # Tier 1 — is uppercase
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isUpperCase(c:Str):bool "
                "that returns true if c is an uppercase ASCII letter (A-Z). "
                "Assume c is a single character"
            ),
            expected_signature="F=isUpperCase(c:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isUpperCase(\"A\")=true, isUpperCase(\"a\")=false",
        ))

        # Tier 1 — is lowercase
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isLowerCase(c:Str):bool "
                "that returns true if c is a lowercase ASCII letter (a-z). "
                "Assume c is a single character"
            ),
            expected_signature="F=isLowerCase(c:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isLowerCase(\"a\")=true, isLowerCase(\"A\")=false",
        ))

        # Tier 1 — is alphanumeric
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isAlphaNum(c:Str):bool "
                "that returns true if c is a letter (a-z, A-Z) or digit (0-9). "
                "Assume c is a single character"
            ),
            expected_signature="F=isAlphaNum(c:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isAlphaNum(\"a\")=true, isAlphaNum(\"5\")=true, isAlphaNum(\"!\")=false",
        ))

        # Tier 1 — is printable ASCII
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isPrintable(code:i64):bool "
                "that returns true if the ASCII code represents a printable "
                "character (codes 32 through 126 inclusive)"
            ),
            expected_signature="F=isPrintable(code:i64):bool",
            difficulty=1,
            type_hints=["i64", "bool"],
            test_input_hint="isPrintable(65)=true, isPrintable(10)=false",
        ))

        # Tier 2 — season from month
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=season(month:i64):Str "
                "that returns the season for the given month (1-12). "
                "Dec/Jan/Feb=\"winter\", Mar/Apr/May=\"spring\", "
                "Jun/Jul/Aug=\"summer\", Sep/Oct/Nov=\"autumn\". "
                "Return \"invalid\" for values outside 1-12"
            ),
            expected_signature="F=season(month:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="season(1)=\"winter\", season(7)=\"summer\"",
        ))

        # Tier 2 — time of day from hour
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=timeOfDay(hour:i64):Str "
                "that returns \"morning\" for 6-11, \"afternoon\" for 12-17, "
                "\"evening\" for 18-21, \"night\" for 22-23 or 0-5. "
                "Return \"invalid\" if hour is outside 0-23"
            ),
            expected_signature="F=timeOfDay(hour:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="timeOfDay(9)=\"morning\", timeOfDay(14)=\"afternoon\"",
        ))

        # Tier 2 — wind direction from degrees
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=windDir(deg:i64):Str "
                "that returns the compass direction for the given degrees (0-359). "
                "0-44=\"N\", 45-89=\"NE\", 90-134=\"E\", 135-179=\"SE\", "
                "180-224=\"S\", 225-269=\"SW\", 270-314=\"W\", 315-359=\"NW\""
            ),
            expected_signature="F=windDir(deg:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="windDir(0)=\"N\", windDir(90)=\"E\", windDir(200)=\"S\"",
        ))

        # Tier 2 — temperature category
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=tempCategory(celsius:f64):Str "
                "that returns \"freezing\" for < 0, \"cold\" for 0 to < 10, "
                "\"mild\" for 10 to < 20, \"warm\" for 20 to < 30, "
                "\"hot\" for >= 30"
            ),
            expected_signature="F=tempCategory(celsius:f64):Str",
            difficulty=2,
            type_hints=["f64", "Str"],
            test_input_hint="tempCategory(-5.0)=\"freezing\", tempCategory(25.0)=\"warm\"",
        ))

        # Tier 2 — coin denomination
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=largestCoin(cents:i64):i64 "
                "that returns the largest US coin denomination (in cents) "
                "that fits into the given amount. "
                "Denominations: 100, 50, 25, 10, 5, 1. Return 0 if cents < 1"
            ),
            expected_signature="F=largestCoin(cents:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="largestCoin(75)=50, largestCoin(30)=25, largestCoin(3)=1",
        ))

        # Tier 2 — shipping cost tier
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=shippingCost(weight:f64):f64 "
                "that returns the shipping cost based on weight in kg. "
                "0-1kg: 5.0, 1-5kg: 10.0, 5-10kg: 20.0, over 10kg: 50.0. "
                "Return 0.0 if weight < 0.0"
            ),
            expected_signature="F=shippingCost(weight:f64):f64",
            difficulty=2,
            type_hints=["f64"],
            test_input_hint="shippingCost(0.5)=5.0, shippingCost(7.0)=20.0",
        ))

        # Tier 1 — age group classifier
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=ageGroup(age:i64):Str "
                "that returns \"infant\" for 0-1, \"child\" for 2-12, "
                "\"teen\" for 13-17, \"adult\" for 18-64, "
                "\"senior\" for 65+. Return \"invalid\" if age < 0"
            ),
            expected_signature="F=ageGroup(age:i64):Str",
            difficulty=1,
            type_hints=["i64", "Str"],
            test_input_hint="ageGroup(5)=\"child\", ageGroup(25)=\"adult\"",
        ))

        # Tier 1 — is valid month
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isValidMonth(m:i64):bool "
                "that returns true if m is between 1 and 12 inclusive"
            ),
            expected_signature="F=isValidMonth(m:i64):bool",
            difficulty=1,
            type_hints=["i64", "bool"],
            test_input_hint="isValidMonth(1)=true, isValidMonth(13)=false",
        ))

        # Tier 2 — days in month
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=daysInMonth(month:i64;year:i64):i64 "
                "that returns the number of days in the given month. "
                "Handle February with leap years. "
                "Return 0 if month is outside 1-12"
            ),
            expected_signature="F=daysInMonth(month:i64;year:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="daysInMonth(2, 2024)=29, daysInMonth(1, 2023)=31",
        ))

        # Tier 1 — is valid time
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isValidTime(h:i64;m:i64):bool "
                "that returns true if h is 0-23 and m is 0-59"
            ),
            expected_signature="F=isValidTime(h:i64;m:i64):bool",
            difficulty=1,
            type_hints=["i64", "bool"],
            test_input_hint="isValidTime(12, 30)=true, isValidTime(25, 0)=false",
        ))

        # Tier 2 — compare version numbers
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=compareVersion(maj1:i64;min1:i64;maj2:i64;min2:i64):i64 "
                "that compares two version numbers (major.minor). "
                "Return -1 if v1 < v2, 0 if equal, 1 if v1 > v2. "
                "Compare major first, then minor"
            ),
            expected_signature="F=compareVersion(maj1:i64;min1:i64;maj2:i64;min2:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="compareVersion(1,2,1,3)=-1, compareVersion(2,0,1,9)=1",
        ))

        # Tier 2 — priority level from score
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=priority(score:i64):Str "
                "that returns \"low\" for score 0-25, \"medium\" for 26-50, "
                "\"high\" for 51-75, \"critical\" for 76-100, "
                "and \"invalid\" otherwise"
            ),
            expected_signature="F=priority(score:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="priority(30)=\"medium\", priority(80)=\"critical\"",
        ))

        # Tier 1 — traffic light state
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=trafficLight(state:i64):Str "
                "that returns \"red\" for 1, \"yellow\" for 2, \"green\" for 3, "
                "and \"unknown\" for any other value"
            ),
            expected_signature="F=trafficLight(state:i64):Str",
            difficulty=1,
            type_hints=["i64", "Str"],
            test_input_hint="trafficLight(1)=\"red\", trafficLight(3)=\"green\"",
        ))

        # Tier 2 — letter grade from percentage
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=letterGrade(pct:f64):Str "
                "that returns \"A+\" for >= 97, \"A\" for >= 93, \"A-\" for >= 90, "
                "\"B+\" for >= 87, \"B\" for >= 83, \"B-\" for >= 80, "
                "\"C+\" for >= 77, \"C\" for >= 73, \"C-\" for >= 70, "
                "\"D\" for >= 60, \"F\" otherwise"
            ),
            expected_signature="F=letterGrade(pct:f64):Str",
            difficulty=2,
            type_hints=["f64", "Str"],
            test_input_hint="letterGrade(95.0)=\"A\", letterGrade(72.0)=\"C-\"",
        ))

        # Tier 2 — parity check
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=parityBit(n:i64):bool "
                "that returns true if n has an even number of 1-bits "
                "in its binary representation (even parity)"
            ),
            expected_signature="F=parityBit(n:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="parityBit(7)=false, parityBit(3)=true",
        ))

        # Tier 1 — is palindrome number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isPalindromeN(n:i64):bool "
                "that returns true if n is a palindrome number. "
                "Negative numbers are not palindromes"
            ),
            expected_signature="F=isPalindromeN(n:i64):bool",
            difficulty=1,
            type_hints=["i64", "bool"],
            test_input_hint="isPalindromeN(121)=true, isPalindromeN(-121)=false",
        ))

        # Tier 1 — is between inclusive
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isBetweenInc(x:f64;lo:f64;hi:f64):bool "
                "that returns true when x is between lo and hi inclusive. "
                "Use !(x < lo) and !(x > hi) since toke has no >= or <="
            ),
            expected_signature="F=isBetweenInc(x:f64;lo:f64;hi:f64):bool",
            difficulty=1,
            type_hints=["f64", "bool"],
            test_input_hint="isBetweenInc(5.0, 1.0, 10.0)=true",
        ))

        # Tier 1 — is between exclusive
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isBetweenExc(x:f64;lo:f64;hi:f64):bool "
                "that returns true when x is strictly between lo and hi (exclusive)"
            ),
            expected_signature="F=isBetweenExc(x:f64;lo:f64;hi:f64):bool",
            difficulty=1,
            type_hints=["f64", "bool"],
            test_input_hint="isBetweenExc(5.0, 5.0, 10.0)=false",
        ))

        # Tier 1 — three-way comparison
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=cmp(a:i64;b:i64):i64 "
                "that returns -1 if a < b, 0 if a equals b, 1 if a > b"
            ),
            expected_signature="F=cmp(a:i64;b:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="cmp(3, 5)=-1, cmp(5, 5)=0, cmp(7, 2)=1",
        ))

        # Tier 1 — ternary-style if-value
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-CND",
                description=(
                    f"Write a function F=ifVal(cond:bool;then:{ty};otherwise:{ty}):{ty} "
                    f"that returns then if cond is true, otherwise returns otherwise"
                ),
                expected_signature=f"F=ifVal(cond:bool;then:{ty};otherwise:{ty}):{ty}",
                difficulty=1,
                type_hints=["bool", ty],
                test_input_hint=f"ifVal(true, 10, 20) = 10 with type {ty}",
            ))

        # Tier 1 — null coalesce: first non-zero of two
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=firstNonZero2(a:i64;b:i64):i64 "
                "that returns a if a is not zero, otherwise returns b"
            ),
            expected_signature="F=firstNonZero2(a:i64;b:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="firstNonZero2(0, 5)=5, firstNonZero2(3, 5)=3",
        ))

        # Tier 1 — first non-zero of three
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=firstNonZero3(a:i64;b:i64;c:i64):i64 "
                "that returns the first of a, b, c that is not zero. "
                "If all are zero, return 0"
            ),
            expected_signature="F=firstNonZero3(a:i64;b:i64;c:i64):i64",
            difficulty=1,
            type_hints=["i64"],
            test_input_hint="firstNonZero3(0, 0, 7)=7, firstNonZero3(3, 0, 0)=3",
        ))

        # Tier 1 — is sorted triple
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isSortedTriple(a:i64;b:i64;c:i64):bool "
                "that returns true if a, b, c are in non-decreasing order. "
                "Use !(a > b) and !(b > c) since toke has no <="
            ),
            expected_signature="F=isSortedTriple(a:i64;b:i64;c:i64):bool",
            difficulty=1,
            type_hints=["i64", "bool"],
            test_input_hint="isSortedTriple(1, 2, 3)=true, isSortedTriple(3, 2, 1)=false",
        ))

        # Tier 2 — classify triangle by sides
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=triangleSides(a:f64;b:f64;c:f64):Str "
                "that returns \"equilateral\" if all sides equal, "
                "\"isosceles\" if exactly two sides equal, "
                "\"scalene\" if all different. "
                "Return \"invalid\" if the sides cannot form a triangle "
                "(each side must be less than the sum of the other two)"
            ),
            expected_signature="F=triangleSides(a:f64;b:f64;c:f64):Str",
            difficulty=2,
            type_hints=["f64", "Str"],
            test_input_hint="triangleSides(3.0,3.0,3.0)=\"equilateral\"",
        ))

        # Tier 1 — can form triangle
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=canFormTriangle(a:i64;b:i64;c:i64):bool "
                "that returns true if three lengths can form a valid triangle. "
                "Check a+b > c, b+c > a, and a+c > b. All must be positive"
            ),
            expected_signature="F=canFormTriangle(a:i64;b:i64;c:i64):bool",
            difficulty=1,
            type_hints=["i64", "bool"],
            test_input_hint="canFormTriangle(3, 4, 5)=true, canFormTriangle(1, 1, 10)=false",
        ))

        # Tier 2 — classify number (prime/composite/neither)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=classifyNum(n:i64):Str "
                "that returns \"neither\" for n < 2, \"prime\" if n is prime, "
                "and \"composite\" otherwise"
            ),
            expected_signature="F=classifyNum(n:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="classifyNum(1)=\"neither\", classifyNum(7)=\"prime\", classifyNum(9)=\"composite\"",
        ))

        # Tier 2 — is perfect number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isPerfectNum(n:i64):bool "
                "that returns true if n is a perfect number "
                "(equals the sum of its proper divisors). "
                "Return false for n < 2"
            ),
            expected_signature="F=isPerfectNum(n:i64):bool",
            difficulty=2,
            type_hints=["i64", "bool"],
            test_input_hint="isPerfectNum(6)=true, isPerfectNum(28)=true, isPerfectNum(12)=false",
        ))

        # Tier 2 — quadratic discriminant sign
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=discriminant(a:f64;b:f64;c:f64):Str "
                "that computes b*b - 4.0*a*c and returns "
                "\"two_real\" if positive, \"one_real\" if zero, "
                "\"complex\" if negative"
            ),
            expected_signature="F=discriminant(a:f64;b:f64;c:f64):Str",
            difficulty=2,
            type_hints=["f64", "Str"],
            test_input_hint="discriminant(1.0, -3.0, 2.0)=\"two_real\"",
        ))

        # Tier 2 — classify year (century)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=century(year:i64):i64 "
                "that returns the century for the given year. "
                "Year 1-100 is century 1, 101-200 is century 2, etc. "
                "Compute as (year - 1) / 100 + 1. Assume year >= 1"
            ),
            expected_signature="F=century(year:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="century(2000)=20, century(2001)=21",
        ))

        # Tier 2 — HTTP method classifier
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=httpMethod(code:i64):Str "
                "that returns a description: 1=\"GET\", 2=\"POST\", "
                "3=\"PUT\", 4=\"DELETE\", 5=\"PATCH\", "
                "and \"UNKNOWN\" for any other value"
            ),
            expected_signature="F=httpMethod(code:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="httpMethod(1)=\"GET\", httpMethod(9)=\"UNKNOWN\"",
        ))

        # Tier 2 — tax bracket simplified
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=taxRate(income:f64):f64 "
                "that returns a simplified tax rate based on income. "
                "0-10000: 0.0, 10000-50000: 0.1, 50000-100000: 0.2, "
                "over 100000: 0.3. Return 0.0 if income < 0.0"
            ),
            expected_signature="F=taxRate(income:f64):f64",
            difficulty=2,
            type_hints=["f64"],
            test_input_hint="taxRate(25000.0)=0.1, taxRate(75000.0)=0.2",
        ))

        # Tier 2 — encode simple cipher value
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=simpleEncode(n:i64):i64 "
                "that encodes n by swapping pairs: 0<->1, 2<->3, 4<->5, etc. "
                "If n is even, return n+1. If n is odd, return n-1. "
                "Return n if n < 0"
            ),
            expected_signature="F=simpleEncode(n:i64):i64",
            difficulty=2,
            type_hints=["i64"],
            test_input_hint="simpleEncode(0)=1, simpleEncode(1)=0, simpleEncode(4)=5",
        ))

        # Tier 2 — zodiac sign from day/month (simplified)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=zodiac(month:i64;day:i64):Str "
                "that returns the zodiac sign for the given date. "
                "Use simplified boundaries: Aries (Mar 21-Apr 19), "
                "Taurus (Apr 20-May 20), Gemini (May 21-Jun 20), "
                "Cancer (Jun 21-Jul 22), Leo (Jul 23-Aug 22), "
                "Virgo (Aug 23-Sep 22), Libra (Sep 23-Oct 22), "
                "Scorpio (Oct 23-Nov 21), Sagittarius (Nov 22-Dec 21), "
                "Capricorn (Dec 22-Jan 19), Aquarius (Jan 20-Feb 18), "
                "Pisces (Feb 19-Mar 20)"
            ),
            expected_signature="F=zodiac(month:i64;day:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="zodiac(3, 25)=\"Aries\", zodiac(7, 1)=\"Cancer\"",
        ))

        # Tier 1 — is valid day for month (simplified)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-CND",
            description=(
                "Write a function F=isValidDay(month:i64;day:i64):bool "
                "that returns true if day is valid for the given month. "
                "Use simplified rules: months 1,3,5,7,8,10,12 have 31 days, "
                "months 4,6,9,11 have 30 days, month 2 has 28 days. "
                "Ignore leap years"
            ),
            expected_signature="F=isValidDay(month:i64;day:i64):bool",
            difficulty=1,
            type_hints=["i64", "bool"],
            test_input_hint="isValidDay(1, 31)=true, isValidDay(2, 30)=false",
        ))

        return tasks

    # -- A-STR: String manipulation ----------------------------------------

    def _expand_string(self) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seq = _Sequencer("A-STR")

        # Tier 1 — basic operations
        tier1_ops = [
            ("reverse", "s:Str", "Str",
             "returns the string s reversed", "reverse(\"hello\")=\"olleh\""),
            ("toUpper", "s:Str", "Str",
             "returns s converted to uppercase", "toUpper(\"hello\")=\"HELLO\""),
            ("toLower", "s:Str", "Str",
             "returns s converted to lowercase", "toLower(\"HELLO\")=\"hello\""),
            ("strLen", "s:Str", "i64",
             "returns the length of the string s", "strLen(\"abc\")=3"),
            ("isEmpty", "s:Str", "bool",
             "returns true if s is the empty string", "isEmpty(\"\")=true"),
            ("concat", "a:Str;b:Str", "Str",
             "returns a concatenated with b", "concat(\"ab\",\"cd\")=\"abcd\""),
            ("repeat", "s:Str;n:i64", "Str",
             "returns s repeated n times", "repeat(\"ab\",3)=\"ababab\""),
            ("charAt", "s:Str;i:i64", "Str",
             "returns the character at index i as a single-character string",
             "charAt(\"abc\",1)=\"b\""),
            ("startsWith", "s:Str;prefix:Str", "bool",
             "returns true if s starts with prefix",
             "startsWith(\"hello\",\"he\")=true"),
            ("endsWith", "s:Str;suffix:Str", "bool",
             "returns true if s ends with suffix",
             "endsWith(\"hello\",\"lo\")=true"),
            ("contains", "s:Str;sub:Str", "bool",
             "returns true if s contains the substring sub",
             "contains(\"hello\",\"ell\")=true"),
        ]
        for fn, params, ret, desc, hint in tier1_ops:
            types = _extract_types(params, ret)
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-STR",
                description=f"Write a function F={fn}({params}):{ret} that {desc}",
                expected_signature=f"F={fn}({params}):{ret}",
                difficulty=1,
                type_hints=types,
                test_input_hint=hint,
            ))

        # Tier 1 — trim whitespace
        for variant, desc in [
            ("trim", "leading and trailing whitespace removed"),
            ("trimLeft", "leading whitespace removed"),
            ("trimRight", "trailing whitespace removed"),
        ]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-STR",
                description=(
                    f"Write a function F={variant}(s:Str):Str "
                    f"that returns s with {desc}"
                ),
                expected_signature=f"F={variant}(s:Str):Str",
                difficulty=1,
                type_hints=["Str"],
                test_input_hint=f"{variant}(\"  hello  \")",
            ))

        # Tier 2 — substring extraction
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=substring(s:Str;start:i64;end:i64):Str "
                "that returns the substring of s from index start (inclusive) "
                "to end (exclusive)"
            ),
            expected_signature="F=substring(s:Str;start:i64;end:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="substring(\"hello\", 1, 4)=\"ell\"",
        ))

        # Tier 2 — replace first occurrence
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=replaceFirst(s:Str;old:Str;new:Str):Str "
                "that returns s with the first occurrence of old replaced by new. "
                "If old is not found, return s unchanged"
            ),
            expected_signature="F=replaceFirst(s:Str;old:Str;new:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="replaceFirst(\"aabaa\",\"a\",\"x\")=\"xabaa\"",
        ))

        # Tier 2 — replace all occurrences
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=replaceAll(s:Str;old:Str;new:Str):Str "
                "that returns s with all occurrences of old replaced by new"
            ),
            expected_signature="F=replaceAll(s:Str;old:Str;new:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="replaceAll(\"aabaa\",\"a\",\"x\")=\"xxbxx\"",
        ))

        # Tier 2 — split by delimiter
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=split(s:Str;delim:Str):[Str] "
                "that splits s by the delimiter delim and returns an array of parts"
            ),
            expected_signature="F=split(s:Str;delim:Str):[Str]",
            difficulty=2,
            type_hints=["Str", "[Str]"],
            test_input_hint="split(\"a,b,c\",\",\")=[\"a\";\"b\";\"c\"]",
        ))

        # Tier 2 — join array of strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=join(parts:[Str];delim:Str):Str "
                "that joins the strings in parts with delim between each pair"
            ),
            expected_signature="F=join(parts:[Str];delim:Str):Str",
            difficulty=2,
            type_hints=["Str", "[Str]"],
            test_input_hint="join([\"a\";\"b\";\"c\"],\"-\")=\"a-b-c\"",
        ))

        # Tier 2 — pad left / pad right
        for variant, desc in [
            ("padLeft", "on the left"),
            ("padRight", "on the right"),
        ]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-STR",
                description=(
                    f"Write a function F={variant}(s:Str;width:i64;fill:Str):Str "
                    f"that pads s {desc} with the fill character until the total "
                    f"length reaches width. If s is already at least width long, "
                    f"return s unchanged"
                ),
                expected_signature=f"F={variant}(s:Str;width:i64;fill:Str):Str",
                difficulty=2,
                type_hints=["Str", "i64"],
                test_input_hint=f"{variant}(\"hi\", 5, \"*\")=\"***hi\" or \"hi***\"",
            ))

        # Tier 2 — count occurrences of substring
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countOccurrences(s:Str;sub:Str):i64 "
                "that returns the number of non-overlapping occurrences of sub in s"
            ),
            expected_signature="F=countOccurrences(s:Str;sub:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countOccurrences(\"aabaa\",\"a\")=4",
        ))

        # Tier 2 — indexOf
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=indexOf(s:Str;sub:Str):i64 "
                "that returns the index of the first occurrence of sub in s, "
                "or -1 if not found"
            ),
            expected_signature="F=indexOf(s:Str;sub:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="indexOf(\"hello\",\"ll\")=2, indexOf(\"hello\",\"xyz\")=-1",
        ))

        # Tier 2 — isPalindrome (string)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isPalindrome(s:Str):bool "
                "that returns true if s reads the same forwards and backwards"
            ),
            expected_signature="F=isPalindrome(s:Str):bool",
            difficulty=2,
            type_hints=["Str", "bool"],
            test_input_hint="isPalindrome(\"racecar\")=true, isPalindrome(\"hello\")=false",
        ))

        # Tier 2 — capitalize first letter
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=capitalize(s:Str):Str "
                "that returns s with its first character converted to uppercase "
                "and the rest unchanged. Return the empty string unchanged"
            ),
            expected_signature="F=capitalize(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="capitalize(\"hello\")=\"Hello\", capitalize(\"\")=\"\"",
        ))

        # Tier 2 — title case
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=titleCase(s:Str):Str "
                "that capitalizes the first letter of each word in s. "
                "Words are separated by spaces"
            ),
            expected_signature="F=titleCase(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="titleCase(\"hello world\")=\"Hello World\"",
        ))

        # Tier 2 — camelCase to snake_case
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=toSnakeCase(s:Str):Str "
                "that converts a camelCase string to snake_case. "
                "Insert an underscore before each uppercase letter and "
                "convert the entire string to lowercase"
            ),
            expected_signature="F=toSnakeCase(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="toSnakeCase(\"helloWorld\")=\"hello_world\"",
        ))

        # Tier 2 — snake_case to camelCase
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=toCamelCase(s:Str):Str "
                "that converts a snake_case string to camelCase. "
                "The first word stays lowercase; each subsequent word "
                "after an underscore has its first letter capitalized"
            ),
            expected_signature="F=toCamelCase(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="toCamelCase(\"hello_world\")=\"helloWorld\"",
        ))

        # Tier 2 — truncate with ellipsis
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=truncate(s:Str;maxLen:i64):Str "
                "that returns s if its length is <= maxLen, otherwise "
                "returns the first (maxLen - 3) characters followed by \"...\""
            ),
            expected_signature="F=truncate(s:Str;maxLen:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="truncate(\"hello world\", 8)=\"hello...\"",
        ))

        # Tier 2 — number to string
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-STR",
                description=(
                    f"Write a function F=numToStr(x:{ty}):Str "
                    f"that converts the numeric value x to its string representation"
                ),
                expected_signature=f"F=numToStr(x:{ty}):Str",
                difficulty=2,
                type_hints=[ty, "Str"],
                test_input_hint=f"numToStr(42) = \"42\" with type {ty}",
            ))

        # Tier 3 — word count
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=wordCount(s:Str):i64 "
                "that returns the number of words in s. "
                "Words are separated by one or more spaces. "
                "Leading and trailing spaces should be ignored"
            ),
            expected_signature="F=wordCount(s:Str):i64",
            difficulty=3,
            type_hints=["Str", "i64"],
            test_input_hint="wordCount(\"  hello  world  \")=2, wordCount(\"\")=0",
        ))

        # Tier 3 — character frequency map
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=charFreq(s:Str):[Str:i64] "
                "that returns a map from each character in s to its "
                "frequency count"
            ),
            expected_signature="F=charFreq(s:Str):[Str:i64]",
            difficulty=3,
            type_hints=["Str", "[Str:i64]"],
            test_input_hint="charFreq(\"aab\")=[\"a\":2;\"b\":1]",
        ))

        # Tier 3 — longest common prefix
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=longestCommonPrefix(strs:[Str]):Str "
                "that returns the longest common prefix shared by all strings "
                "in the array. Return the empty string if there is no common prefix"
            ),
            expected_signature="F=longestCommonPrefix(strs:[Str]):Str",
            difficulty=3,
            type_hints=["Str", "[Str]"],
            test_input_hint=(
                "longestCommonPrefix([\"flower\";\"flow\";\"flight\"])=\"fl\""
            ),
        ))

        # Tier 3 — run-length encoding
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=rle(s:Str):Str "
                "that returns the run-length encoding of s. "
                "Consecutive identical characters are replaced by the character "
                "followed by its count. Single characters have no count suffix. "
                "rle(\"aaabbc\") returns \"a3b2c\""
            ),
            expected_signature="F=rle(s:Str):Str",
            difficulty=3,
            type_hints=["Str"],
            test_input_hint="rle(\"aaabbc\")=\"a3b2c\", rle(\"abc\")=\"abc\"",
        ))

        # Tier 3 — is anagram
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isAnagram(a:Str;b:Str):bool "
                "that returns true if a and b are anagrams of each other "
                "(same characters in different order, case-insensitive)"
            ),
            expected_signature="F=isAnagram(a:Str;b:Str):bool",
            difficulty=3,
            type_hints=["Str", "bool"],
            test_input_hint="isAnagram(\"listen\",\"silent\")=true",
        ))

        # Tier 3 — Caesar cipher
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=caesarEncrypt(s:Str;shift:i64):Str "
                "that applies a Caesar cipher to s, shifting each lowercase "
                "letter by shift positions in the alphabet. "
                "Non-lowercase letters are unchanged. Wrap around from z to a"
            ),
            expected_signature="F=caesarEncrypt(s:Str;shift:i64):Str",
            difficulty=3,
            type_hints=["Str", "i64"],
            test_input_hint="caesarEncrypt(\"abc\",3)=\"def\"",
        ))


        # ---------------------------------------------------------------
        # NEW TEMPLATES: A-STR additions (~62 new tasks)
        # ---------------------------------------------------------------

        # Tier 1 — first char
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=firstChar(s:Str):Str "
                "that returns the first character of s as a single-character string. "
                "Return the empty string if s is empty"
            ),
            expected_signature="F=firstChar(s:Str):Str",
            difficulty=1,
            type_hints=["Str"],
            test_input_hint="firstChar(\"hello\")=\"h\", firstChar(\"\")=\"\"",
        ))

        # Tier 1 — last char
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=lastChar(s:Str):Str "
                "that returns the last character of s as a single-character string. "
                "Return the empty string if s is empty"
            ),
            expected_signature="F=lastChar(s:Str):Str",
            difficulty=1,
            type_hints=["Str"],
            test_input_hint="lastChar(\"hello\")=\"o\", lastChar(\"\")=\"\"",
        ))

        # Tier 2 — reverse words in sentence
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=reverseWords(s:Str):Str "
                "that reverses the order of words in s. "
                "Words are separated by single spaces. "
                "reverseWords(\"hello world\") returns \"world hello\""
            ),
            expected_signature="F=reverseWords(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="reverseWords(\"hello world\")=\"world hello\"",
        ))

        # Tier 1 — remove all spaces
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=removeSpaces(s:Str):Str "
                "that returns s with all space characters removed"
            ),
            expected_signature="F=removeSpaces(s:Str):Str",
            difficulty=1,
            type_hints=["Str"],
            test_input_hint="removeSpaces(\"a b c\")=\"abc\"",
        ))

        # Tier 2 — remove leading zeros
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=removeLeadingZeros(s:Str):Str "
                "that removes leading zero characters from a numeric string. "
                "If the string is all zeros, return \"0\""
            ),
            expected_signature="F=removeLeadingZeros(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="removeLeadingZeros(\"00123\")=\"123\", removeLeadingZeros(\"000\")=\"0\"",
        ))

        # Tier 2 — left pad with zeros
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=zeroPad(s:Str;width:i64):Str "
                "that pads s on the left with zeros until total length "
                "reaches width. If s is already at least width long, return s"
            ),
            expected_signature="F=zeroPad(s:Str;width:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="zeroPad(\"42\", 5)=\"00042\"",
        ))

        # Tier 2 — right pad with spaces
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=rightPadSpace(s:Str;width:i64):Str "
                "that pads s on the right with spaces until total length "
                "reaches width. If s is already at least width long, return s"
            ),
            expected_signature="F=rightPadSpace(s:Str;width:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="rightPadSpace(\"hi\", 5)=\"hi   \"",
        ))

        # Tier 2 — center pad
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=centerPad(s:Str;width:i64;fill:Str):Str "
                "that centers s within width characters, padding both sides "
                "with the fill character. If extra padding is odd, add the "
                "extra character on the right"
            ),
            expected_signature="F=centerPad(s:Str;width:i64;fill:Str):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="centerPad(\"hi\", 6, \"*\")=\"**hi**\"",
        ))

        # Tier 2 — extract digits from string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=extractDigits(s:Str):Str "
                "that returns a new string containing only the digit "
                "characters (0-9) from s, in order"
            ),
            expected_signature="F=extractDigits(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="extractDigits(\"a1b2c3\")=\"123\"",
        ))

        # Tier 2 — extract letters from string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=extractLetters(s:Str):Str "
                "that returns a new string containing only the letter "
                "characters (a-z, A-Z) from s, in order"
            ),
            expected_signature="F=extractLetters(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="extractLetters(\"h3ll0 w0rld\")=\"hllwrld\"",
        ))

        # Tier 2 — remove punctuation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=removePunct(s:Str):Str "
                "that removes all punctuation characters from s. "
                "Keep only letters, digits, and spaces"
            ),
            expected_signature="F=removePunct(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="removePunct(\"hello, world!\")=\"hello world\"",
        ))

        # Tier 2 — collapse whitespace
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=collapseSpaces(s:Str):Str "
                "that replaces runs of multiple consecutive spaces with "
                "a single space. Also trim leading and trailing spaces"
            ),
            expected_signature="F=collapseSpaces(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="collapseSpaces(\"  a  b  c  \")=\"a b c\"",
        ))

        # Tier 2 — interleave two strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=interleave(a:Str;b:Str):Str "
                "that interleaves characters from a and b. "
                "If one is longer, append its remaining characters"
            ),
            expected_signature="F=interleave(a:Str;b:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="interleave(\"abc\",\"12\")=\"a1b2c\"",
        ))

        # Tier 2 — alternate case
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=alternateCase(s:Str):Str "
                "that converts s to alternating case: lowercase at even "
                "indices (0-based), uppercase at odd indices"
            ),
            expected_signature="F=alternateCase(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="alternateCase(\"hello\")=\"hElLo\"",
        ))

        # Tier 2 — swap case
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=swapCase(s:Str):Str "
                "that swaps the case of every letter in s. "
                "Uppercase becomes lowercase and vice versa. "
                "Non-letter characters remain unchanged"
            ),
            expected_signature="F=swapCase(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="swapCase(\"Hello\")=\"hELLO\"",
        ))

        # Tier 1 — is numeric string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isNumeric(s:Str):bool "
                "that returns true if every character in s is a digit (0-9). "
                "Return false for empty strings"
            ),
            expected_signature="F=isNumeric(s:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isNumeric(\"123\")=true, isNumeric(\"12a\")=false",
        ))

        # Tier 1 — is alpha string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isAlpha(s:Str):bool "
                "that returns true if every character in s is a letter (a-z, A-Z). "
                "Return false for empty strings"
            ),
            expected_signature="F=isAlpha(s:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isAlpha(\"hello\")=true, isAlpha(\"he1lo\")=false",
        ))

        # Tier 1 — is alphanumeric string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isAlphaNumStr(s:Str):bool "
                "that returns true if every character in s is a letter or digit. "
                "Return false for empty strings"
            ),
            expected_signature="F=isAlphaNumStr(s:Str):bool",
            difficulty=1,
            type_hints=["Str", "bool"],
            test_input_hint="isAlphaNumStr(\"abc123\")=true, isAlphaNumStr(\"abc 123\")=false",
        ))

        # Tier 2 — count vowels
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countVowels(s:Str):i64 "
                "that returns the number of vowels (a, e, i, o, u) in s, "
                "case-insensitive"
            ),
            expected_signature="F=countVowels(s:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countVowels(\"hello\")=2",
        ))

        # Tier 2 — count consonants
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countConsonants(s:Str):i64 "
                "that returns the number of consonant letters in s, "
                "case-insensitive"
            ),
            expected_signature="F=countConsonants(s:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countConsonants(\"hello\")=3",
        ))

        # Tier 2 — count words
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countWords(s:Str):i64 "
                "that returns the number of space-separated words in s. "
                "Multiple spaces count as one separator. "
                "Leading and trailing spaces are ignored"
            ),
            expected_signature="F=countWords(s:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countWords(\"hello world\")=2, countWords(\"\")=0",
        ))

        # Tier 2 — count sentences
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countSentences(s:Str):i64 "
                "that returns the number of sentences in s. "
                "Count the number of period (.) characters"
            ),
            expected_signature="F=countSentences(s:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countSentences(\"Hi. Hello. Bye.\")=3",
        ))

        # Tier 2 — longest word
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=longestWord(s:Str):Str "
                "that returns the longest word in s (space-separated). "
                "If there is a tie, return the first one. "
                "Return empty string if s is empty"
            ),
            expected_signature="F=longestWord(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="longestWord(\"the quick brown fox\")=\"quick\"",
        ))

        # Tier 2 — shortest word
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=shortestWord(s:Str):Str "
                "that returns the shortest word in s (space-separated). "
                "If there is a tie, return the first one. "
                "Return empty string if s is empty"
            ),
            expected_signature="F=shortestWord(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="shortestWord(\"the quick brown fox\")=\"the\"",
        ))

        # Tier 2 — first word
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=firstWord(s:Str):Str "
                "that returns the first space-separated word in s. "
                "Return empty string if s is empty or all spaces"
            ),
            expected_signature="F=firstWord(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="firstWord(\"hello world\")=\"hello\"",
        ))

        # Tier 2 — last word
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=lastWord(s:Str):Str "
                "that returns the last space-separated word in s. "
                "Return empty string if s is empty or all spaces"
            ),
            expected_signature="F=lastWord(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="lastWord(\"hello world\")=\"world\"",
        ))

        # Tier 2 — remove duplicate chars
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=removeDupChars(s:Str):Str "
                "that returns s with duplicate characters removed, "
                "keeping only the first occurrence of each character"
            ),
            expected_signature="F=removeDupChars(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="removeDupChars(\"aabbcc\")=\"abc\"",
        ))

        # Tier 2 — sort chars in string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=sortChars(s:Str):Str "
                "that returns a new string with the characters of s "
                "sorted in ascending ASCII order"
            ),
            expected_signature="F=sortChars(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="sortChars(\"cba\")=\"abc\"",
        ))

        # Tier 2 — insert char at position
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=insertAt(s:Str;c:Str;pos:i64):Str "
                "that inserts character c at position pos in s. "
                "If pos is beyond the string length, append c at the end"
            ),
            expected_signature="F=insertAt(s:Str;c:Str;pos:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="insertAt(\"hllo\", \"e\", 1)=\"hello\"",
        ))

        # Tier 2 — delete char at position
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=deleteAt(s:Str;pos:i64):Str "
                "that deletes the character at position pos in s. "
                "If pos is out of bounds, return s unchanged"
            ),
            expected_signature="F=deleteAt(s:Str;pos:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="deleteAt(\"hello\", 1)=\"hllo\"",
        ))

        # Tier 2 — replace char at position
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=replaceAt(s:Str;c:Str;pos:i64):Str "
                "that replaces the character at position pos with c. "
                "If pos is out of bounds, return s unchanged"
            ),
            expected_signature="F=replaceAt(s:Str;c:Str;pos:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="replaceAt(\"hello\", \"a\", 1)=\"hallo\"",
        ))

        # Tier 3 — ROT13 cipher
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=rot13(s:Str):Str "
                "that applies ROT13 encoding to s. "
                "Shift each letter by 13 positions in the alphabet, "
                "wrapping around. Preserve case. Non-letters unchanged"
            ),
            expected_signature="F=rot13(s:Str):Str",
            difficulty=3,
            type_hints=["Str"],
            test_input_hint="rot13(\"Hello\")=\"Uryyb\"",
        ))

        # Tier 2 — binary representation of integer as string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=toBinary(n:i64):Str "
                "that converts a non-negative integer n to its binary "
                "string representation. Return \"0\" if n is 0"
            ),
            expected_signature="F=toBinary(n:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="toBinary(10)=\"1010\", toBinary(0)=\"0\"",
        ))

        # Tier 2 — hex representation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=toHex(n:i64):Str "
                "that converts a non-negative integer n to its lowercase "
                "hexadecimal string representation. Return \"0\" if n is 0"
            ),
            expected_signature="F=toHex(n:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="toHex(255)=\"ff\", toHex(0)=\"0\"",
        ))

        # Tier 2 — octal representation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=toOctal(n:i64):Str "
                "that converts a non-negative integer n to its octal "
                "string representation. Return \"0\" if n is 0"
            ),
            expected_signature="F=toOctal(n:i64):Str",
            difficulty=2,
            type_hints=["i64", "Str"],
            test_input_hint="toOctal(8)=\"10\", toOctal(0)=\"0\"",
        ))

        # Tier 2 — manual toLowerCase
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=manualToLower(s:Str):Str "
                "that converts s to lowercase by checking if each character "
                "is in the ASCII range A-Z (65-90) and shifting to a-z (97-122). "
                "Non-uppercase characters remain unchanged"
            ),
            expected_signature="F=manualToLower(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="manualToLower(\"HeLLO\")=\"hello\"",
        ))

        # Tier 2 — manual toUpperCase
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=manualToUpper(s:Str):Str "
                "that converts s to uppercase by checking if each character "
                "is in the ASCII range a-z (97-122) and shifting to A-Z (65-90). "
                "Non-lowercase characters remain unchanged"
            ),
            expected_signature="F=manualToUpper(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="manualToUpper(\"hElLo\")=\"HELLO\"",
        ))

        # Tier 2 — is valid identifier
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isValidIdent(s:Str):bool "
                "that returns true if s is a valid identifier: "
                "starts with a letter, rest are letters or digits, "
                "and length is at least 1"
            ),
            expected_signature="F=isValidIdent(s:Str):bool",
            difficulty=2,
            type_hints=["Str", "bool"],
            test_input_hint="isValidIdent(\"hello2\")=true, isValidIdent(\"2abc\")=false",
        ))

        # Tier 2 — common prefix of two strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=commonPrefix(a:Str;b:Str):Str "
                "that returns the longest common prefix of strings a and b"
            ),
            expected_signature="F=commonPrefix(a:Str;b:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="commonPrefix(\"flower\",\"flow\")=\"flow\"",
        ))

        # Tier 2 — common suffix of two strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=commonSuffix(a:Str;b:Str):Str "
                "that returns the longest common suffix of strings a and b"
            ),
            expected_signature="F=commonSuffix(a:Str;b:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="commonSuffix(\"testing\",\"running\")=\"ning\"",
        ))

        # Tier 2 — hamming distance of two strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=hammingDist(a:Str;b:Str):i64 "
                "that returns the number of positions where a and b differ. "
                "Both strings must have equal length; return -1 if they do not"
            ),
            expected_signature="F=hammingDist(a:Str;b:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="hammingDist(\"abc\",\"axc\")=1",
        ))

        # Tier 2 — remove nth char
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=removeNth(s:Str;n:i64):Str "
                "that removes every n-th character from s (1-indexed). "
                "removeNth(\"abcdef\", 2) removes chars at positions 2,4,6 "
                "and returns \"ace\""
            ),
            expected_signature="F=removeNth(s:Str;n:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="removeNth(\"abcdef\", 2)=\"ace\"",
        ))

        # Tier 2 — insert string at position
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=insertStr(s:Str;sub:Str;pos:i64):Str "
                "that inserts sub into s at position pos. "
                "If pos > length of s, append sub at the end"
            ),
            expected_signature="F=insertStr(s:Str;sub:Str;pos:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="insertStr(\"helo\", \"l\", 3)=\"hello\"",
        ))

        # Tier 2 — substring between two indices
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=substringBetween(s:Str;start:i64;end:i64):Str "
                "that returns the substring from index start to end (exclusive). "
                "Clamp indices to valid bounds"
            ),
            expected_signature="F=substringBetween(s:Str;start:i64;end:i64):Str",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="substringBetween(\"hello\", 1, 4)=\"ell\"",
        ))

        # Tier 2 — count char frequency
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=charCount(s:Str;c:Str):i64 "
                "that returns the number of times the single character c "
                "appears in s"
            ),
            expected_signature="F=charCount(s:Str;c:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="charCount(\"hello\", \"l\")=2",
        ))

        # Tier 3 — most frequent char
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=mostFreqChar(s:Str):Str "
                "that returns the most frequently occurring character in s. "
                "If there is a tie, return the one that appears first. "
                "Return empty string if s is empty"
            ),
            expected_signature="F=mostFreqChar(s:Str):Str",
            difficulty=3,
            type_hints=["Str"],
            test_input_hint="mostFreqChar(\"aabbc\")=\"a\"",
        ))

        # Tier 3 — least frequent char
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=leastFreqChar(s:Str):Str "
                "that returns the least frequently occurring character in s. "
                "If there is a tie, return the one that appears first. "
                "Return empty string if s is empty"
            ),
            expected_signature="F=leastFreqChar(s:Str):Str",
            difficulty=3,
            type_hints=["Str"],
            test_input_hint="leastFreqChar(\"aabbc\")=\"c\"",
        ))

        # Tier 3 — is rotation of
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isRotation(a:Str;b:Str):bool "
                "that returns true if b is a rotation of a. "
                "Check if a and b have the same length and b appears "
                "in a concatenated with itself"
            ),
            expected_signature="F=isRotation(a:Str;b:Str):bool",
            difficulty=3,
            type_hints=["Str", "bool"],
            test_input_hint="isRotation(\"abcde\", \"cdeab\")=true",
        ))

        # Tier 3 — is subsequence
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isSubsequence(a:Str;b:Str):bool "
                "that returns true if a is a subsequence of b. "
                "Use two pointers: advance the pointer in a only when "
                "characters match"
            ),
            expected_signature="F=isSubsequence(a:Str;b:Str):bool",
            difficulty=3,
            type_hints=["Str", "bool"],
            test_input_hint="isSubsequence(\"ace\", \"abcde\")=true",
        ))

        # Tier 3 — wrap text at width
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=wrapText(s:Str;width:i64):Str "
                "that inserts newline characters to wrap text so no line "
                "exceeds width characters. Break at the last space before "
                "the width limit. If no space exists, break at width"
            ),
            expected_signature="F=wrapText(s:Str;width:i64):Str",
            difficulty=3,
            type_hints=["Str", "i64"],
            test_input_hint="wrapText(\"hello world foo\", 10)=\"hello\\nworld foo\"",
        ))

        # Tier 2 — is valid email simplified
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=isValidEmail(s:Str):bool "
                "that returns true if s contains exactly one @ character "
                "and at least one . character after the @"
            ),
            expected_signature="F=isValidEmail(s:Str):bool",
            difficulty=2,
            type_hints=["Str", "bool"],
            test_input_hint="isValidEmail(\"a@b.c\")=true, isValidEmail(\"abc\")=false",
        ))


        # Tier 3 — Vigenere-style shift (simplified single key)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=vigenere(s:Str;key:i64):Str "
                "that shifts each lowercase letter in s by key positions "
                "in the alphabet (wrapping from z to a). "
                "Uppercase and non-letter characters remain unchanged"
            ),
            expected_signature="F=vigenere(s:Str;key:i64):Str",
            difficulty=3,
            type_hints=["Str", "i64"],
            test_input_hint="vigenere(\"abc\", 3)=\"def\"",
        ))

        # Tier 2 — morse code for single char
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=morseChar(c:Str):Str "
                "that returns the Morse code for a single uppercase letter. "
                "A=\".-\", B=\"-...\", C=\"-.-.\", D=\"-..\", E=\".\", "
                "F=\"..-.\", G=\"--.\", H=\"....\", I=\"..\", J=\".---\", "
                "K=\"-.-\", L=\".-..\", M=\"--\", N=\"-.\", O=\"---\", "
                "P=\".--.\", Q=\"--.-\", R=\".-.\", S=\"...\", T=\"-\", "
                "U=\"..-\", V=\"...-\", W=\".--\", X=\"-..-\", Y=\"-.--\", "
                "Z=\"--..\". Return \"?\" for unknown characters"
            ),
            expected_signature="F=morseChar(c:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="morseChar(\"A\")=\".-\", morseChar(\"S\")=\"...\"",
        ))

        # Tier 2 — NATO phonetic for single char
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=natoPhonetic(c:Str):Str "
                "that returns the NATO phonetic alphabet word for a "
                "single uppercase letter. A=\"Alpha\", B=\"Bravo\", C=\"Charlie\", "
                "D=\"Delta\", E=\"Echo\", F=\"Foxtrot\", etc. "
                "Return \"Unknown\" for non-letter input"
            ),
            expected_signature="F=natoPhonetic(c:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="natoPhonetic(\"A\")=\"Alpha\"",
        ))

        # Tier 3 — edit distance (simple cases)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=editDist(a:Str;b:Str):i64 "
                "that returns the Levenshtein edit distance between a and b. "
                "Use a dynamic programming approach with a 2D array of size "
                "(len(a)+1) by (len(b)+1)"
            ),
            expected_signature="F=editDist(a:Str;b:Str):i64",
            difficulty=3,
            type_hints=["Str", "i64"],
            test_input_hint="editDist(\"kitten\",\"sitting\")=3",
        ))

        # Tier 2 — zip two strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=zipStrs(a:Str;b:Str):Str "
                "that interleaves characters from a and b alternately. "
                "If one string is shorter, append remaining chars of the longer one"
            ),
            expected_signature="F=zipStrs(a:Str;b:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="zipStrs(\"abc\",\"123\")=\"a1b2c3\"",
        ))

        # Tier 2 — count uppercase letters
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countUpper(s:Str):i64 "
                "that returns the number of uppercase letters in s"
            ),
            expected_signature="F=countUpper(s:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countUpper(\"Hello World\")=2",
        ))

        # Tier 2 — count lowercase letters
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countLower(s:Str):i64 "
                "that returns the number of lowercase letters in s"
            ),
            expected_signature="F=countLower(s:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countLower(\"Hello World\")=8",
        ))

        # Tier 2 — count digits in string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countDigitsInStr(s:Str):i64 "
                "that returns the number of digit characters in s"
            ),
            expected_signature="F=countDigitsInStr(s:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countDigitsInStr(\"abc123\")=3",
        ))

        # Tier 2 — count spaces in string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=countSpaces(s:Str):i64 "
                "that returns the number of space characters in s"
            ),
            expected_signature="F=countSpaces(s:Str):i64",
            difficulty=2,
            type_hints=["Str", "i64"],
            test_input_hint="countSpaces(\"hello world test\")=2",
        ))

        # Tier 2 — replace spaces with dashes
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=spaceToDash(s:Str):Str "
                "that replaces all space characters in s with dashes"
            ),
            expected_signature="F=spaceToDash(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="spaceToDash(\"hello world\")=\"hello-world\"",
        ))

        # Tier 2 — reverse each word
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-STR",
            description=(
                "Write a function F=reverseEachWord(s:Str):Str "
                "that reverses each word individually but keeps the words "
                "in their original order. Words are separated by spaces"
            ),
            expected_signature="F=reverseEachWord(s:Str):Str",
            difficulty=2,
            type_hints=["Str"],
            test_input_hint="reverseEachWord(\"hello world\")=\"olleh dlrow\"",
        ))

        return tasks

    # -- A-ARR: Array / sequence operations --------------------------------

    def _expand_array(self) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seq = _Sequencer("A-ARR")

        # Tier 1 — basic operations across element types
        basic_ops: list[tuple[str, str, str, str, str, int]] = [
            ("sum", "arr:[{ty}]", "{ty}",
             "returns the sum of all elements in arr",
             "sum([1;2;3])=6 with type {ty}", 1),
            ("product", "arr:[{ty}]", "{ty}",
             "returns the product of all elements in arr",
             "product([1;2;3])=6 with type {ty}", 1),
            ("arrLen", "arr:[{ty}]", "i64",
             "returns the number of elements in arr",
             "arrLen([1;2;3])=3 with type {ty}", 1),
            ("first", "arr:[{ty}]", "{ty}",
             "returns the first element of arr",
             "first([10;20;30])=10 with type {ty}", 1),
            ("last", "arr:[{ty}]", "{ty}",
             "returns the last element of arr",
             "last([10;20;30])=30 with type {ty}", 1),
        ]
        for fn, params_tmpl, ret_tmpl, desc, hint_tmpl, diff in basic_ops:
            for ty in ARRAY_ELEM_TYPES:
                if ty == "Str" and fn in ("sum", "product"):
                    continue  # sum/product don't apply to strings
                params = params_tmpl.format(ty=ty)
                ret = ret_tmpl.format(ty=ty)
                types = _extract_types(params, ret)
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-ARR",
                    description=(
                        f"Write a function F={fn}({params}):{ret} that {desc}"
                    ),
                    expected_signature=f"F={fn}({params}):{ret}",
                    difficulty=diff,
                    type_hints=types,
                    test_input_hint=hint_tmpl.format(ty=ty),
                ))

        # Tier 1 — contains
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=arrContains(arr:[{ty}];val:{ty}):bool "
                    f"that returns true if val is present in arr"
                ),
                expected_signature=f"F=arrContains(arr:[{ty}];val:{ty}):bool",
                difficulty=1,
                type_hints=[ty, f"[{ty}]", "bool"],
                test_input_hint=f"arrContains([1;2;3], 2)=true with type {ty}",
            ))

        # Tier 1 — indexOf in array
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=arrIndexOf(arr:[{ty}];val:{ty}):i64 "
                    f"that returns the index of the first occurrence of val "
                    f"in arr, or -1 if not found"
                ),
                expected_signature=f"F=arrIndexOf(arr:[{ty}];val:{ty}):i64",
                difficulty=1,
                type_hints=[ty, f"[{ty}]", "i64"],
                test_input_hint=f"arrIndexOf([10;20;30], 20)=1 with type {ty}",
            ))

        # Tier 1 — reverse array
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=arrReverse(arr:[{ty}]):[{ty}] "
                    f"that returns a new array with the elements of arr "
                    f"in reverse order"
                ),
                expected_signature=f"F=arrReverse(arr:[{ty}]):[{ty}]",
                difficulty=1,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=f"arrReverse([1;2;3])=[3;2;1] with type {ty}",
            ))

        # Tier 1 — concatenate two arrays
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=arrConcat(a:[{ty}];b:[{ty}]):[{ty}] "
                    f"that returns a new array containing all elements of a "
                    f"followed by all elements of b"
                ),
                expected_signature=f"F=arrConcat(a:[{ty}];b:[{ty}]):[{ty}]",
                difficulty=1,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=f"arrConcat([1;2],[3;4])=[1;2;3;4] with type {ty}",
            ))

        # Tier 2 — map (apply function): double and square
        for op, op_desc in [("mapDouble", "doubled"), ("mapSquare", "squared")]:
            for ty in NUMERIC_TYPES:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-ARR",
                    description=(
                        f"Write a function F={op}(arr:[{ty}]):[{ty}] "
                        f"that returns a new array where each element is {op_desc}"
                    ),
                    expected_signature=f"F={op}(arr:[{ty}]):[{ty}]",
                    difficulty=2,
                    type_hints=[ty, f"[{ty}]"],
                    test_input_hint=(
                        f"{op}([1;2;3])=[2;4;6] or [1;4;9] with type {ty}"
                    ),
                ))

        # Tier 2 — filter positive / negative
        for ty in ["i64", "f64"]:
            for fn, desc in [
                ("filterPositive", "that are strictly greater than zero"),
                ("filterNegative", "that are strictly less than zero"),
            ]:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-ARR",
                    description=(
                        f"Write a function F={fn}(arr:[{ty}]):[{ty}] "
                        f"that returns a new array containing only the "
                        f"elements {desc}"
                    ),
                    expected_signature=f"F={fn}(arr:[{ty}]):[{ty}]",
                    difficulty=2,
                    type_hints=[ty, f"[{ty}]"],
                    test_input_hint=(
                        f"{fn}([-1;0;2;-3;4])=[2;4] or [-1;-3] with type {ty}"
                    ),
                ))

        # Tier 2 — filter even / odd
        for fn, desc in [
            ("filterEven", "that are even"),
            ("filterOdd", "that are odd"),
        ]:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F={fn}(arr:[i64]):[i64] "
                    f"that returns a new array containing only the elements {desc}"
                ),
                expected_signature=f"F={fn}(arr:[i64]):[i64]",
                difficulty=2,
                type_hints=["i64", "[i64]"],
                test_input_hint=f"{fn}([1;2;3;4;5;6])=[2;4;6] or [1;3;5]",
            ))

        # Tier 2 — fold / reduce
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=foldSum(arr:[{ty}];init:{ty}):{ty} "
                    f"that folds the array from left to right using addition, "
                    f"starting with init as the accumulator"
                ),
                expected_signature=f"F=foldSum(arr:[{ty}];init:{ty}):{ty}",
                difficulty=2,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=f"foldSum([1;2;3], 10)=16 with type {ty}",
            ))

        # Tier 2 — unique (deduplicate)
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=unique(arr:[{ty}]):[{ty}] "
                    f"that returns a new array with duplicate elements removed, "
                    f"preserving the order of first occurrence"
                ),
                expected_signature=f"F=unique(arr:[{ty}]):[{ty}]",
                difficulty=2,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=f"unique([1;2;2;3;1])=[1;2;3] with type {ty}",
            ))

        # Tier 2 — slice
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=arrSlice(arr:[{ty}];start:i64;"
                    f"end:i64):[{ty}] "
                    f"that returns a new array containing elements from index "
                    f"start (inclusive) to end (exclusive)"
                ),
                expected_signature=(
                    f"F=arrSlice(arr:[{ty}];start:i64;end:i64):[{ty}]"
                ),
                difficulty=2,
                type_hints=[ty, f"[{ty}]", "i64"],
                test_input_hint=(
                    f"arrSlice([10;20;30;40], 1, 3)=[20;30] with type {ty}"
                ),
            ))

        # Tier 2 — max and min of array
        for ty in NUMERIC_TYPES:
            for fn, desc in [
                ("arrMax", "the maximum element"),
                ("arrMin", "the minimum element"),
            ]:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-ARR",
                    description=(
                        f"Write a function F={fn}(arr:[{ty}]):{ty} "
                        f"that returns {desc} of the array. "
                        f"Assume the array is non-empty"
                    ),
                    expected_signature=f"F={fn}(arr:[{ty}]):{ty}",
                    difficulty=2,
                    type_hints=[ty, f"[{ty}]"],
                    test_input_hint=(
                        f"{fn}([3;1;4;1;5])=5 or 1 with type {ty}"
                    ),
                ))

        # Tier 2 — count occurrences in array
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=arrCount(arr:[{ty}];val:{ty}):i64 "
                    f"that returns the number of times val appears in arr"
                ),
                expected_signature=f"F=arrCount(arr:[{ty}];val:{ty}):i64",
                difficulty=2,
                type_hints=[ty, f"[{ty}]", "i64"],
                test_input_hint=(
                    f"arrCount([1;2;2;3;2], 2)=3 with type {ty}"
                ),
            ))

        # Tier 2 — take and drop
        for ty in ARRAY_ELEM_TYPES:
            for fn, desc in [
                ("take", "the first n elements"),
                ("drop", "all elements except the first n"),
            ]:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-ARR",
                    description=(
                        f"Write a function F={fn}(arr:[{ty}];n:i64):[{ty}] "
                        f"that returns {desc} of arr. "
                        f"If n exceeds the array length, return the "
                        f"appropriate result"
                    ),
                    expected_signature=f"F={fn}(arr:[{ty}];n:i64):[{ty}]",
                    difficulty=2,
                    type_hints=[ty, f"[{ty}]", "i64"],
                    test_input_hint=f"{fn}([1;2;3;4;5], 3) with type {ty}",
                ))

        # Tier 3 — flatten 2D array
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=flatten(arrs:[[{ty}]]):[{ty}] "
                    f"that flattens a two-dimensional array into a "
                    f"one-dimensional array"
                ),
                expected_signature=f"F=flatten(arrs:[[{ty}]]):[{ty}]",
                difficulty=3,
                type_hints=[ty, f"[{ty}]", f"[[{ty}]]"],
                test_input_hint=(
                    f"flatten([[1;2];[3;4]])=[1;2;3;4] with type {ty}"
                ),
            ))

        # Tier 3 — zip two arrays
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=zip(a:[i64];b:[i64]):[[i64]] "
                "that returns an array of pairs (2-element arrays) from a and b. "
                "Truncate to the length of the shorter array"
            ),
            expected_signature="F=zip(a:[i64];b:[i64]):[[i64]]",
            difficulty=3,
            type_hints=["i64", "[i64]", "[[i64]]"],
            test_input_hint="zip([1;2;3],[4;5])=[[1;4];[2;5]]",
        ))

        # Tier 3 — partition by even/odd
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=partitionEven(arr:[i64]):[[i64]] "
                "that returns a 2-element array where the first element "
                "is an array of even numbers and the second is an array of "
                "odd numbers, both preserving original order"
            ),
            expected_signature="F=partitionEven(arr:[i64]):[[i64]]",
            difficulty=3,
            type_hints=["i64", "[i64]", "[[i64]]"],
            test_input_hint="partitionEven([1;2;3;4])=[[2;4];[1;3]]",
        ))

        # Tier 3 — rotate array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=rotateLeft(arr:[i64];k:i64):[i64] "
                "that rotates arr to the left by k positions. "
                "rotateLeft([1;2;3;4;5], 2) returns [3;4;5;1;2]. "
                "Handle k larger than array length using modulo"
            ),
            expected_signature="F=rotateLeft(arr:[i64];k:i64):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="rotateLeft([1;2;3;4;5], 2)=[3;4;5;1;2]",
        ))

        # Tier 3 — group consecutive equal elements
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=groupConsecutive(arr:[i64]):[[i64]] "
                "that groups consecutive equal elements into sub-arrays. "
                "groupConsecutive([1;1;2;3;3;3;2]) returns "
                "[[1;1];[2];[3;3;3];[2]]"
            ),
            expected_signature="F=groupConsecutive(arr:[i64]):[[i64]]",
            difficulty=3,
            type_hints=["i64", "[i64]", "[[i64]]"],
            test_input_hint=(
                "groupConsecutive([1;1;2;3;3;3;2])=[[1;1];[2];[3;3;3];[2]]"
            ),
        ))

        # Tier 3 — intersection of two arrays
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=intersect(a:[{ty}];b:[{ty}]):[{ty}] "
                    f"that returns an array of elements present in both a and b, "
                    f"with no duplicates in the result"
                ),
                expected_signature=f"F=intersect(a:[{ty}];b:[{ty}]):[{ty}]",
                difficulty=3,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=(
                    f"intersect([1;2;3],[2;3;4])=[2;3] with type {ty}"
                ),
            ))

        # Tier 3 — difference of two arrays
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=difference(a:[{ty}];b:[{ty}]):[{ty}] "
                    f"that returns elements in a that are not in b, "
                    f"preserving order"
                ),
                expected_signature=f"F=difference(a:[{ty}];b:[{ty}]):[{ty}]",
                difficulty=3,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=(
                    f"difference([1;2;3;4],[2;4])=[1;3] with type {ty}"
                ),
            ))


        # ---------------------------------------------------------------
        # NEW TEMPLATES: A-ARR additions (~30 new tasks)
        # ---------------------------------------------------------------

        # Tier 2 — second largest element
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=secondLargest(arr:[{ty}]):{ty} "
                    f"that returns the second largest distinct element in arr. "
                    f"Assume arr has at least two distinct elements"
                ),
                expected_signature=f"F=secondLargest(arr:[{ty}]):{ty}",
                difficulty=2,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=f"secondLargest([3;1;4;1;5])=4 with type {ty}",
            ))

        # Tier 2 — second smallest element
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ARR",
                description=(
                    f"Write a function F=secondSmallest(arr:[{ty}]):{ty} "
                    f"that returns the second smallest distinct element in arr. "
                    f"Assume arr has at least two distinct elements"
                ),
                expected_signature=f"F=secondSmallest(arr:[{ty}]):{ty}",
                difficulty=2,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=f"secondSmallest([3;1;4;1;5])=3 with type {ty}",
            ))

        # Tier 2 — array average as f64
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=arrAverage(arr:[i64]):f64 "
                "that returns the average of all elements as f64. "
                "Cast the sum to f64 before dividing by the length. "
                "Assume arr is non-empty"
            ),
            expected_signature="F=arrAverage(arr:[i64]):f64",
            difficulty=2,
            type_hints=["i64", "[i64]", "f64"],
            test_input_hint="arrAverage([1;2;3;4;5])=3.0",
        ))

        # Tier 2 — running sum (prefix sums)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=prefixSum(arr:[i64]):[i64] "
                "that returns the prefix sum array. "
                "prefixSum([1;2;3]) returns [1;3;6]"
            ),
            expected_signature="F=prefixSum(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="prefixSum([1;2;3;4])=[1;3;6;10]",
        ))

        # Tier 2 — running product
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=prefixProduct(arr:[i64]):[i64] "
                "that returns the prefix product array. "
                "prefixProduct([1;2;3]) returns [1;2;6]"
            ),
            expected_signature="F=prefixProduct(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="prefixProduct([1;2;3;4])=[1;2;6;24]",
        ))

        # Tier 2 — pairwise differences
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=pairDiffs(arr:[i64]):[i64] "
                "that returns an array of differences between consecutive "
                "elements. pairDiffs([1;4;2;7]) returns [3;-2;5]. "
                "The result array has length one less than the input"
            ),
            expected_signature="F=pairDiffs(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="pairDiffs([1;4;2;7])=[3;-2;5]",
        ))

        # Tier 2 — pairwise sums
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=pairSums(arr:[i64]):[i64] "
                "that returns an array of sums of consecutive element pairs. "
                "pairSums([1;2;3;4]) returns [3;5;7]"
            ),
            expected_signature="F=pairSums(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="pairSums([1;2;3;4])=[3;5;7]",
        ))

        # Tier 2 — dot product of two arrays
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=dotProduct(a:[i64];b:[i64]):i64 "
                "that returns the dot product of two arrays of equal length. "
                "Sum of a[i]*b[i] for all i"
            ),
            expected_signature="F=dotProduct(a:[i64];b:[i64]):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="dotProduct([1;2;3],[4;5;6])=32",
        ))

        # Tier 2 — elementwise multiply
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=elemMul(a:[i64];b:[i64]):[i64] "
                "that returns a new array where each element is a[i]*b[i]. "
                "Assume both arrays have the same length"
            ),
            expected_signature="F=elemMul(a:[i64];b:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="elemMul([1;2;3],[4;5;6])=[4;10;18]",
        ))

        # Tier 2 — elementwise add
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=elemAdd(a:[i64];b:[i64]):[i64] "
                "that returns a new array where each element is a[i]+b[i]. "
                "Assume both arrays have the same length"
            ),
            expected_signature="F=elemAdd(a:[i64];b:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="elemAdd([1;2;3],[4;5;6])=[5;7;9]",
        ))

        # Tier 2 — array equals
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=arrEquals(a:[i64];b:[i64]):bool "
                "that returns true if a and b have the same length and "
                "all corresponding elements are equal"
            ),
            expected_signature="F=arrEquals(a:[i64];b:[i64]):bool",
            difficulty=2,
            type_hints=["i64", "[i64]", "bool"],
            test_input_hint="arrEquals([1;2;3],[1;2;3])=true",
        ))

        # Tier 2 — is prefix of
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=isPrefix(prefix:[i64];arr:[i64]):bool "
                "that returns true if prefix is a prefix of arr"
            ),
            expected_signature="F=isPrefix(prefix:[i64];arr:[i64]):bool",
            difficulty=2,
            type_hints=["i64", "[i64]", "bool"],
            test_input_hint="isPrefix([1;2],[1;2;3])=true, isPrefix([1;3],[1;2;3])=false",
        ))

        # Tier 2 — is suffix of
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=isSuffix(suffix:[i64];arr:[i64]):bool "
                "that returns true if suffix is a suffix of arr"
            ),
            expected_signature="F=isSuffix(suffix:[i64];arr:[i64]):bool",
            difficulty=2,
            type_hints=["i64", "[i64]", "bool"],
            test_input_hint="isSuffix([2;3],[1;2;3])=true",
        ))

        # Tier 2 — remove element at index
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=removeAt(arr:[i64];idx:i64):[i64] "
                "that returns a new array with the element at idx removed. "
                "If idx is out of bounds, return arr unchanged"
            ),
            expected_signature="F=removeAt(arr:[i64];idx:i64):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="removeAt([1;2;3], 1)=[1;3]",
        ))

        # Tier 2 — insert element at index
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=insertAtArr(arr:[i64];idx:i64;val:i64):[i64] "
                "that returns a new array with val inserted at index idx. "
                "If idx is beyond the length, append at the end"
            ),
            expected_signature="F=insertAtArr(arr:[i64];idx:i64;val:i64):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="insertAtArr([1;3;4], 1, 2)=[1;2;3;4]",
        ))

        # Tier 2 — swap elements at two indices
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=swapAt(arr:[i64];i:i64;j:i64):[i64] "
                "that returns a new array with the elements at indices i "
                "and j swapped. Assume both indices are valid"
            ),
            expected_signature="F=swapAt(arr:[i64];i:i64;j:i64):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="swapAt([1;2;3;4], 0, 3)=[4;2;3;1]",
        ))

        # Tier 2 — cumulative max
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=cumulativeMax(arr:[i64]):[i64] "
                "that returns an array where each element is the maximum "
                "of all elements up to and including that index"
            ),
            expected_signature="F=cumulativeMax(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="cumulativeMax([3;1;4;1;5])=[3;3;4;4;5]",
        ))

        # Tier 2 — cumulative min
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=cumulativeMin(arr:[i64]):[i64] "
                "that returns an array where each element is the minimum "
                "of all elements up to and including that index"
            ),
            expected_signature="F=cumulativeMin(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="cumulativeMin([3;1;4;1;5])=[3;1;1;1;1]",
        ))

        # Tier 3 — find first duplicate
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=firstDuplicate(arr:[i64]):i64 "
                "that returns the first element that appears more than once "
                "in arr. Return -1 if no duplicates exist"
            ),
            expected_signature="F=firstDuplicate(arr:[i64]):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="firstDuplicate([2;1;3;2;4])=2, firstDuplicate([1;2;3])=-1",
        ))

        # Tier 3 — find missing number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=findMissing(arr:[i64];n:i64):i64 "
                "that finds the missing number in a sequence from 0 to n "
                "where arr contains all numbers except one. "
                "Use the sum formula n*(n+1)/2 minus the array sum"
            ),
            expected_signature="F=findMissing(arr:[i64];n:i64):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="findMissing([0;1;3;4], 4)=2",
        ))

        # Tier 2 — array is palindrome
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=arrIsPalindrome(arr:[i64]):bool "
                "that returns true if the array reads the same forwards "
                "and backwards"
            ),
            expected_signature="F=arrIsPalindrome(arr:[i64]):bool",
            difficulty=2,
            type_hints=["i64", "[i64]", "bool"],
            test_input_hint="arrIsPalindrome([1;2;3;2;1])=true",
        ))

        # Tier 2 — count elements greater than threshold
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=countAbove(arr:[i64];threshold:i64):i64 "
                "that returns the count of elements strictly greater than threshold"
            ),
            expected_signature="F=countAbove(arr:[i64];threshold:i64):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="countAbove([1;5;3;7;2], 3)=2",
        ))

        # Tier 2 — count elements less than threshold
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=countBelow(arr:[i64];threshold:i64):i64 "
                "that returns the count of elements strictly less than threshold"
            ),
            expected_signature="F=countBelow(arr:[i64];threshold:i64):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="countBelow([1;5;3;7;2], 3)=2",
        ))

        # Tier 2 — sum of elements at even indices
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=sumEvenIdx(arr:[i64]):i64 "
                "that returns the sum of elements at even indices (0, 2, 4, ...)"
            ),
            expected_signature="F=sumEvenIdx(arr:[i64]):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="sumEvenIdx([10;20;30;40;50])=90",
        ))

        # Tier 3 — sliding window sums
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ARR",
            description=(
                "Write a function F=windowSums(arr:[i64];k:i64):[i64] "
                "that returns an array of sums for each sliding window "
                "of size k. The result has length len(arr)-k+1"
            ),
            expected_signature="F=windowSums(arr:[i64];k:i64):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="windowSums([1;2;3;4;5], 3)=[6;9;12]",
        ))

        return tasks

    # -- A-SRT: Sorting and searching algorithms ---------------------------

    def _expand_sort_search(self) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seq = _Sequencer("A-SRT")

        # Sorting algorithms across type combinations
        sort_algos: list[tuple[str, str, str, int]] = [
            ("bubbleSort", "bubble sort",
             "Compare adjacent pairs and swap if out of order; "
             "repeat until no swaps needed", 1),
            ("selectionSort", "selection sort",
             "Find the minimum of the unsorted portion and swap it "
             "into position", 1),
            ("insertionSort", "insertion sort",
             "Insert each element into its correct position in the "
             "sorted prefix", 1),
            ("mergeSort", "merge sort",
             "Recursively split the array in half, sort each half, "
             "then merge", 2),
            ("quickSort", "quicksort",
             "Choose a pivot, partition elements around it, then "
             "recursively sort partitions", 2),
            ("countingSort", "counting sort",
             "Count occurrences of each value and rebuild the array "
             "from the counts. Assume non-negative integers", 2),
        ]
        for fn, algo_name, algo_desc, diff in sort_algos:
            for ty in NUMERIC_TYPES:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-SRT",
                    description=(
                        f"Write a function F={fn}(arr:[{ty}]):[{ty}] "
                        f"that sorts the array in ascending order using "
                        f"{algo_name}. {algo_desc}"
                    ),
                    expected_signature=f"F={fn}(arr:[{ty}]):[{ty}]",
                    difficulty=diff,
                    type_hints=[ty, f"[{ty}]"],
                    test_input_hint=(
                        f"{fn}([3;1;4;1;5])=[1;1;3;4;5] with type {ty}"
                    ),
                ))

        # Sorting strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortStrings(arr:[Str]):[Str] "
                "that sorts an array of strings in lexicographic ascending "
                "order using any comparison-based sorting algorithm"
            ),
            expected_signature="F=sortStrings(arr:[Str]):[Str]",
            difficulty=2,
            type_hints=["Str", "[Str]"],
            test_input_hint=(
                "sortStrings([\"banana\";\"apple\";\"cherry\"])="
                "[\"apple\";\"banana\";\"cherry\"]"
            ),
        ))

        # Descending sort variants
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=sortDesc(arr:[{ty}]):[{ty}] "
                    f"that sorts the array in descending order"
                ),
                expected_signature=f"F=sortDesc(arr:[{ty}]):[{ty}]",
                difficulty=2,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=(
                    f"sortDesc([3;1;4;1;5])=[5;4;3;1;1] with type {ty}"
                ),
            ))

        # Linear search
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=linearSearch(arr:[{ty}];val:{ty}):i64 "
                    f"that returns the index of val in arr using linear search, "
                    f"or -1 if not found"
                ),
                expected_signature=f"F=linearSearch(arr:[{ty}];val:{ty}):i64",
                difficulty=1,
                type_hints=[ty, f"[{ty}]", "i64"],
                test_input_hint=(
                    f"linearSearch([10;20;30], 20)=1 with type {ty}"
                ),
            ))

        # Binary search (sorted array)
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=binarySearch(arr:[{ty}];val:{ty}):i64 "
                    f"that returns the index of val in the sorted array arr "
                    f"using binary search, or -1 if not found"
                ),
                expected_signature=f"F=binarySearch(arr:[{ty}];val:{ty}):i64",
                difficulty=2,
                type_hints=[ty, f"[{ty}]", "i64"],
                test_input_hint=(
                    f"binarySearch([1;3;5;7;9], 5)=2 with type {ty}"
                ),
            ))

        # Tier 2 — isSorted check
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=isSorted(arr:[{ty}]):bool "
                    f"that returns true if arr is sorted in non-decreasing order"
                ),
                expected_signature=f"F=isSorted(arr:[{ty}]):bool",
                difficulty=2,
                type_hints=[ty, f"[{ty}]", "bool"],
                test_input_hint=(
                    f"isSorted([1;2;3])=true, isSorted([3;1;2])=false "
                    f"with type {ty}"
                ),
            ))

        # Tier 2 — find min/max index
        for ty in NUMERIC_TYPES:
            for fn, desc in [
                ("findMinIndex", "the index of the minimum element"),
                ("findMaxIndex", "the index of the maximum element"),
            ]:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-SRT",
                    description=(
                        f"Write a function F={fn}(arr:[{ty}]):i64 "
                        f"that returns {desc} in arr. "
                        f"If multiple elements have the same value, "
                        f"return the first index"
                    ),
                    expected_signature=f"F={fn}(arr:[{ty}]):i64",
                    difficulty=2,
                    type_hints=[ty, f"[{ty}]", "i64"],
                    test_input_hint=(
                        f"{fn}([3;1;4;1;5])=1 or 4 with type {ty}"
                    ),
                ))

        # Tier 2 — kth smallest element
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=kthSmallest(arr:[{ty}];k:i64):{ty} "
                    f"that returns the k-th smallest element (1-indexed) in "
                    f"arr. You may sort the array first"
                ),
                expected_signature=f"F=kthSmallest(arr:[{ty}];k:i64):{ty}",
                difficulty=2,
                type_hints=[ty, f"[{ty}]", "i64"],
                test_input_hint=(
                    f"kthSmallest([7;2;5;1;3], 3)=3 with type {ty}"
                ),
            ))

        # Tier 3 — merge two sorted arrays
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=mergeSorted(a:[{ty}];b:[{ty}]):[{ty}] "
                    f"that merges two sorted arrays into a single sorted array. "
                    f"Both a and b are already sorted in ascending order"
                ),
                expected_signature=f"F=mergeSorted(a:[{ty}];b:[{ty}]):[{ty}]",
                difficulty=3,
                type_hints=[ty, f"[{ty}]"],
                test_input_hint=(
                    f"mergeSorted([1;3;5],[2;4;6])=[1;2;3;4;5;6] "
                    f"with type {ty}"
                ),
            ))

        # Tier 3 — lower bound
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=lowerBound(arr:[{ty}];val:{ty}):i64 "
                    f"that returns the index of the first element in the sorted "
                    f"array arr that is not less than val. Use binary search. "
                    f"If all elements are less than val, return the array length"
                ),
                expected_signature=f"F=lowerBound(arr:[{ty}];val:{ty}):i64",
                difficulty=3,
                type_hints=[ty, f"[{ty}]", "i64"],
                test_input_hint=(
                    f"lowerBound([1;2;4;4;5], 4)=2 with type {ty}"
                ),
            ))

        # Tier 3 — upper bound
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=upperBound(arr:[{ty}];val:{ty}):i64 "
                    f"that returns the index of the first element in the sorted "
                    f"array arr that is strictly greater than val. Use binary "
                    f"search. If no element is greater, return the array length"
                ),
                expected_signature=f"F=upperBound(arr:[{ty}];val:{ty}):i64",
                difficulty=3,
                type_hints=[ty, f"[{ty}]", "i64"],
                test_input_hint=(
                    f"upperBound([1;2;4;4;5], 4)=4 with type {ty}"
                ),
            ))

        # Tier 3 — sort by absolute value
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortByAbs(arr:[i64]):[i64] "
                "that sorts the array by absolute value in ascending order. "
                "When two elements have the same absolute value, "
                "preserve their relative order (stable sort)"
            ),
            expected_signature="F=sortByAbs(arr:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortByAbs([-3;1;-1;2])=[1;-1;2;-3]",
        ))

        # Tier 3 — Dutch national flag (3-way partition)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=threeWayPartition(arr:[i64];"
                "pivot:i64):[i64] "
                "that rearranges arr so that all elements less than pivot "
                "come first, then elements equal to pivot, then elements "
                "greater than pivot"
            ),
            expected_signature=(
                "F=threeWayPartition(arr:[i64];pivot:i64):[i64]"
            ),
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint=(
                "threeWayPartition([3;1;2;3;0;3], 2)=[1;0;2;3;3;3]"
            ),
        ))


        # ---------------------------------------------------------------
        # NEW TEMPLATES: A-SRT additions (~48 new tasks)
        # ---------------------------------------------------------------

        # Tier 1 — is sorted descending
        for ty in NUMERIC_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-SRT",
                description=(
                    f"Write a function F=isSortedDesc(arr:[{ty}]):bool "
                    f"that returns true if arr is sorted in non-increasing order"
                ),
                expected_signature=f"F=isSortedDesc(arr:[{ty}]):bool",
                difficulty=1,
                type_hints=[ty, f"[{ty}]", "bool"],
                test_input_hint=f"isSortedDesc([5;3;1])=true with type {ty}",
            ))

        # Tier 2 — sort by digit sum
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortByDigitSum(arr:[i64]):[i64] "
                "that sorts the array by the sum of digits of each element "
                "in ascending order. Use absolute value for negative numbers. "
                "For equal digit sums, preserve original order (stable sort)"
            ),
            expected_signature="F=sortByDigitSum(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortByDigitSum([21;3;12;100])=[100;3;12;21]",
        ))

        # Tier 2 — sort by last digit
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortByLastDigit(arr:[i64]):[i64] "
                "that sorts the array by the last digit of each element. "
                "Use absolute value for negative numbers. "
                "For equal last digits, preserve original order"
            ),
            expected_signature="F=sortByLastDigit(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortByLastDigit([13;21;32;24])=[21;32;13;24]",
        ))

        # Tier 2 — sort negatives before positives
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=negBeforePos(arr:[i64]):[i64] "
                "that rearranges arr so all negative numbers come before "
                "all non-negative numbers. Preserve relative order within "
                "each group (stable partition)"
            ),
            expected_signature="F=negBeforePos(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="negBeforePos([3;-1;2;-5;0])=[-1;-5;3;2;0]",
        ))

        # Tier 2 — sort even before odd
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=evenBeforeOdd(arr:[i64]):[i64] "
                "that rearranges arr so all even numbers come before "
                "all odd numbers. Preserve relative order within each group"
            ),
            expected_signature="F=evenBeforeOdd(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="evenBeforeOdd([3;2;1;4;5])=[2;4;3;1;5]",
        ))

        # Tier 2 — binary search first occurrence
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=bsearchFirst(arr:[i64];val:i64):i64 "
                "that returns the index of the first occurrence of val "
                "in the sorted array arr using binary search. "
                "Return -1 if not found"
            ),
            expected_signature="F=bsearchFirst(arr:[i64];val:i64):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="bsearchFirst([1;2;2;2;3], 2)=1",
        ))

        # Tier 2 — binary search last occurrence
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=bsearchLast(arr:[i64];val:i64):i64 "
                "that returns the index of the last occurrence of val "
                "in the sorted array arr using binary search. "
                "Return -1 if not found"
            ),
            expected_signature="F=bsearchLast(arr:[i64];val:i64):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="bsearchLast([1;2;2;2;3], 2)=3",
        ))

        # Tier 1 — linear search from end
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=searchFromEnd(arr:[i64];val:i64):i64 "
                "that returns the index of the last occurrence of val "
                "in arr using linear search from the end. Return -1 if not found"
            ),
            expected_signature="F=searchFromEnd(arr:[i64];val:i64):i64",
            difficulty=1,
            type_hints=["i64", "[i64]"],
            test_input_hint="searchFromEnd([1;2;3;2;1], 2)=3",
        ))

        # Tier 2 — find peak element
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=findPeak(arr:[i64]):i64 "
                "that returns the index of a peak element in arr. "
                "A peak is an element greater than its neighbors. "
                "For edge elements, only check one neighbor. "
                "Return the first peak found"
            ),
            expected_signature="F=findPeak(arr:[i64]):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="findPeak([1;3;2;4;1])=1",
        ))

        # Tier 2 — find valley element
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=findValley(arr:[i64]):i64 "
                "that returns the index of a valley element in arr. "
                "A valley is an element smaller than its neighbors. "
                "For edge elements, only check one neighbor. "
                "Return the first valley found"
            ),
            expected_signature="F=findValley(arr:[i64]):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="findValley([3;1;2;0;4])=1",
        ))

        # Tier 2 — rank elements
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=rank(arr:[i64]):[i64] "
                "that returns an array of ranks (1-based position in sorted "
                "order). For ties, assign the same (lower) rank"
            ),
            expected_signature="F=rank(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="rank([40;10;30;20])=[4;1;3;2]",
        ))

        # Tier 2 — median of array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=median(arr:[i64]):f64 "
                "that sorts the array and returns the median. "
                "If the array has even length, return the average of the "
                "two middle elements as f64"
            ),
            expected_signature="F=median(arr:[i64]):f64",
            difficulty=2,
            type_hints=["i64", "[i64]", "f64"],
            test_input_hint="median([3;1;2])=2.0, median([1;2;3;4])=2.5",
        ))

        # Tier 2 — mode of array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=mode(arr:[i64]):i64 "
                "that returns the most frequently occurring element. "
                "If there are ties, return the smallest such element. "
                "Assume arr is non-empty"
            ),
            expected_signature="F=mode(arr:[i64]):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="mode([1;2;2;3;3;3])=3",
        ))

        # Tier 2 — is permutation of
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=isPermutation(a:[i64];b:[i64]):bool "
                "that returns true if b is a permutation of a "
                "(same elements, possibly different order). "
                "Sort both and compare"
            ),
            expected_signature="F=isPermutation(a:[i64];b:[i64]):bool",
            difficulty=2,
            type_hints=["i64", "[i64]", "bool"],
            test_input_hint="isPermutation([3;1;2],[1;2;3])=true",
        ))

        # Tier 2 — sort strings by length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortByLen(arr:[Str]):[Str] "
                "that sorts an array of strings by their length in "
                "ascending order. For equal lengths, preserve original order"
            ),
            expected_signature="F=sortByLen(arr:[Str]):[Str]",
            difficulty=2,
            type_hints=["Str", "[Str]"],
            test_input_hint="sortByLen([\"cat\",\"a\",\"elephant\"])=[\"a\",\"cat\",\"elephant\"]",
        ))

        # Tier 2 — count elements in range
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=countInRange(arr:[i64];lo:i64;hi:i64):i64 "
                "that returns the number of elements in arr that are "
                "between lo and hi inclusive"
            ),
            expected_signature="F=countInRange(arr:[i64];lo:i64;hi:i64):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="countInRange([1;5;3;7;2], 2, 5)=3",
        ))

        # Tier 3 — find rotation point
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=rotationPoint(arr:[i64]):i64 "
                "that finds the index of the minimum element in a rotated "
                "sorted array. This is the rotation point. "
                "Use binary search for efficiency"
            ),
            expected_signature="F=rotationPoint(arr:[i64]):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="rotationPoint([4;5;6;1;2;3])=3",
        ))

        # Tier 3 — two sum
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=twoSum(arr:[i64];target:i64):[i64] "
                "that returns a 2-element array [i;j] where arr[i]+arr[j]=target. "
                "Return [-1;-1] if no such pair exists. "
                "Use nested loops; return the first valid pair found"
            ),
            expected_signature="F=twoSum(arr:[i64];target:i64):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="twoSum([2;7;11;15], 9)=[0;1]",
        ))

        # Tier 3 — three sum
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=threeSum(arr:[i64];target:i64):[i64] "
                "that returns a 3-element array [i;j;k] where "
                "arr[i]+arr[j]+arr[k]=target with i<j<k. "
                "Return [-1;-1;-1] if no such triple exists"
            ),
            expected_signature="F=threeSum(arr:[i64];target:i64):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="threeSum([1;2;3;4;5], 6)=[0;1;2]",
        ))

        # Tier 3 — closest pair (smallest abs difference)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=closestPair(arr:[i64]):[i64] "
                "that returns a 2-element array containing the two elements "
                "with the smallest absolute difference. Sort first, then "
                "check adjacent pairs. Return the first such pair found"
            ),
            expected_signature="F=closestPair(arr:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="closestPair([1;5;3;19;18])=[18;19]",
        ))

        # Tier 3 — longest increasing subsequence length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=lisLength(arr:[i64]):i64 "
                "that returns the length of the longest strictly increasing "
                "subsequence. Use a simple O(n*n) dynamic programming approach"
            ),
            expected_signature="F=lisLength(arr:[i64]):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="lisLength([10;9;2;5;3;7;101;18])=4",
        ))

        # Tier 3 — longest decreasing subsequence length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=ldsLength(arr:[i64]):i64 "
                "that returns the length of the longest strictly decreasing "
                "subsequence. Use dynamic programming"
            ),
            expected_signature="F=ldsLength(arr:[i64]):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="ldsLength([10;9;2;5;3;7])=3",
        ))

        # Tier 2 — number of distinct elements
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=countDistinct(arr:[i64]):i64 "
                "that returns the number of distinct elements in arr. "
                "Sort the array and count transitions"
            ),
            expected_signature="F=countDistinct(arr:[i64]):i64",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="countDistinct([1;2;2;3;3;3])=3",
        ))

        # Tier 2 — remove duplicates from sorted array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=removeDupsSorted(arr:[i64]):[i64] "
                "that removes duplicate elements from a sorted array "
                "and returns a new array with unique elements"
            ),
            expected_signature="F=removeDupsSorted(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="removeDupsSorted([1;1;2;3;3])=[1;2;3]",
        ))

        # Tier 3 — union of two sorted arrays
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortedUnion(a:[i64];b:[i64]):[i64] "
                "that returns the sorted union of two sorted arrays "
                "with no duplicates in the result. Use merge-like logic"
            ),
            expected_signature="F=sortedUnion(a:[i64];b:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortedUnion([1;3;5],[2;3;6])=[1;2;3;5;6]",
        ))

        # Tier 3 — intersection of two sorted arrays
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortedIntersect(a:[i64];b:[i64]):[i64] "
                "that returns the sorted intersection of two sorted arrays. "
                "Each element appears at most once in the result"
            ),
            expected_signature="F=sortedIntersect(a:[i64];b:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortedIntersect([1;2;3;4],[2;4;6])=[2;4]",
        ))

        # Tier 3 — difference of two sorted arrays
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortedDiff(a:[i64];b:[i64]):[i64] "
                "that returns elements in sorted array a that are not in "
                "sorted array b"
            ),
            expected_signature="F=sortedDiff(a:[i64];b:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortedDiff([1;2;3;4],[2;4])=[1;3]",
        ))

        # Tier 3 — symmetric difference
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=symDiff(a:[i64];b:[i64]):[i64] "
                "that returns the symmetric difference of two sorted arrays: "
                "elements in a but not b, plus elements in b but not a, "
                "in sorted order"
            ),
            expected_signature="F=symDiff(a:[i64];b:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="symDiff([1;2;3],[2;3;4])=[1;4]",
        ))

        # Tier 2 — is subset
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=isSubset(a:[i64];b:[i64]):bool "
                "that returns true if every element of a is present in b. "
                "Sort both arrays first for efficient comparison"
            ),
            expected_signature="F=isSubset(a:[i64];b:[i64]):bool",
            difficulty=2,
            type_hints=["i64", "[i64]", "bool"],
            test_input_hint="isSubset([1;2],[1;2;3;4])=true",
        ))

        # Tier 2 — partial sort (k largest)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=kLargest(arr:[i64];k:i64):[i64] "
                "that returns the k largest elements in descending order. "
                "Sort the array, then take the last k elements reversed"
            ),
            expected_signature="F=kLargest(arr:[i64];k:i64):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="kLargest([3;1;5;2;4], 3)=[5;4;3]",
        ))

        # Tier 3 — search in rotated sorted array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=searchRotated(arr:[i64];target:i64):i64 "
                "that searches for target in a rotated sorted array. "
                "Return the index of target or -1 if not found. "
                "First find the rotation point, then binary search the "
                "appropriate half"
            ),
            expected_signature="F=searchRotated(arr:[i64];target:i64):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="searchRotated([4;5;6;1;2;3], 1)=3",
        ))

        # Tier 3 — count inversions
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=countInversions(arr:[i64]):i64 "
                "that counts the number of inversions in arr. "
                "An inversion is a pair (i,j) where i < j and arr[i] > arr[j]. "
                "Use nested loops for simplicity"
            ),
            expected_signature="F=countInversions(arr:[i64]):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="countInversions([2;4;1;3;5])=3",
        ))

        # Tier 3 — sort by custom key (absolute value)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortByAbsVal(arr:[i64]):[i64] "
                "that sorts the array by absolute value in ascending order. "
                "For equal absolute values, negative comes first"
            ),
            expected_signature="F=sortByAbsVal(arr:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortByAbsVal([-3;1;-1;2])=[-1;1;2;-3]",
        ))

        # Tier 3 — merge two sorted arrays into one
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=merge3Sorted(a:[i64];b:[i64];c:[i64]):[i64] "
                "that merges three sorted arrays into one sorted array. "
                "Merge a and b first, then merge the result with c"
            ),
            expected_signature="F=merge3Sorted(a:[i64];b:[i64];c:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="merge3Sorted([1;4],[2;5],[3;6])=[1;2;3;4;5;6]",
        ))

        # Tier 3 — sort matrix rows
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortRows(mat:[[i64]]):[[i64]] "
                "that sorts each row of the matrix independently "
                "in ascending order"
            ),
            expected_signature="F=sortRows(mat:[[i64]]):[[i64]]",
            difficulty=3,
            type_hints=["i64", "[i64]", "[[i64]]"],
            test_input_hint="sortRows([[3;1;2];[6;4;5]])=[[1;2;3];[4;5;6]]",
        ))

        # Tier 3 — percentile
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=percentile(arr:[i64];p:i64):i64 "
                "that returns the p-th percentile value. "
                "Sort the array, compute index as p * (len-1) / 100, "
                "return the element at that index (integer index)"
            ),
            expected_signature="F=percentile(arr:[i64];p:i64):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="percentile([1;2;3;4;5], 50)=3",
        ))

        # Tier 3 — topK frequent elements
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=topKFrequent(arr:[i64];k:i64):[i64] "
                "that returns the k most frequent elements. "
                "Count frequencies, sort by frequency descending, "
                "return the top k elements. For ties, return smaller elements first"
            ),
            expected_signature="F=topKFrequent(arr:[i64];k:i64):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="topKFrequent([1;1;1;2;2;3], 2)=[1;2]",
        ))


        # Tier 2 — sort by absolute value stable
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortAbsStable(arr:[i64]):[i64] "
                "that sorts the array by absolute value, preserving "
                "relative order of elements with the same absolute value"
            ),
            expected_signature="F=sortAbsStable(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortAbsStable([-3;1;-1;2])=[1;-1;2;-3]",
        ))

        # Tier 3 — next permutation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=nextPerm(arr:[i64]):[i64] "
                "that returns the next lexicographic permutation. "
                "Find the rightmost element that is smaller than its successor, "
                "swap with the smallest element to its right that is larger, "
                "then reverse the suffix. Return sorted array if at last permutation"
            ),
            expected_signature="F=nextPerm(arr:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="nextPerm([1;2;3])=[1;3;2], nextPerm([3;2;1])=[1;2;3]",
        ))

        # Tier 2 — insertion sort for strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=insertionSortStr(arr:[Str]):[Str] "
                "that sorts an array of strings in lexicographic order "
                "using insertion sort"
            ),
            expected_signature="F=insertionSortStr(arr:[Str]):[Str]",
            difficulty=2,
            type_hints=["Str", "[Str]"],
            test_input_hint="insertionSortStr([\"c\",\"a\",\"b\"])=[\"a\",\"b\",\"c\"]",
        ))

        # Tier 2 — bubble sort for strings
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=bubbleSortStr(arr:[Str]):[Str] "
                "that sorts an array of strings in lexicographic order "
                "using bubble sort"
            ),
            expected_signature="F=bubbleSortStr(arr:[Str]):[Str]",
            difficulty=2,
            type_hints=["Str", "[Str]"],
            test_input_hint="bubbleSortStr([\"c\",\"a\",\"b\"])=[\"a\",\"b\",\"c\"]",
        ))

        # Tier 2 — sort by frequency (most frequent first)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortByFreq(arr:[i64]):[i64] "
                "that sorts elements by their frequency in descending order. "
                "Elements with the same frequency are sorted by value ascending"
            ),
            expected_signature="F=sortByFreq(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortByFreq([1;1;2;2;2;3])=[2;2;2;1;1;3]",
        ))

        # Tier 3 — selection sort descending
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=selSortDesc(arr:[i64]):[i64] "
                "that sorts the array in descending order using selection sort. "
                "Find the maximum of the unsorted portion and swap it into position"
            ),
            expected_signature="F=selSortDesc(arr:[i64]):[i64]",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="selSortDesc([3;1;4;1;5])=[5;4;3;1;1]",
        ))

        # Tier 3 — nth smallest without full sort
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=nthSmallest(arr:[i64];n:i64):i64 "
                "that returns the n-th smallest element (1-indexed) "
                "using partial selection: run selection sort for only n iterations"
            ),
            expected_signature="F=nthSmallest(arr:[i64];n:i64):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="nthSmallest([7;2;5;1;3], 3)=3",
        ))

        # Tier 2 — sort even and odd separately
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortEvenOdd(arr:[i64]):[i64] "
                "that sorts even numbers ascending and odd numbers ascending "
                "separately, then interleaves them: even first, then odd"
            ),
            expected_signature="F=sortEvenOdd(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortEvenOdd([5;2;3;4;1;6])=[2;4;6;1;3;5]",
        ))

        # Tier 2 — sort positive then negative
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-SRT",
            description=(
                "Write a function F=sortPosNeg(arr:[i64]):[i64] "
                "that puts all positive numbers first (sorted ascending), "
                "then all negative numbers (sorted ascending). "
                "Zeros go with positives"
            ),
            expected_signature="F=sortPosNeg(arr:[i64]):[i64]",
            difficulty=2,
            type_hints=["i64", "[i64]"],
            test_input_hint="sortPosNeg([3;-1;-2;5;0])=[0;3;5;-2;-1]",
        ))

        return tasks

    # -- A-ERR: Error propagation ------------------------------------------

    def _expand_error(self) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seq = _Sequencer("A-ERR")

        # Tier 1 — basic error return: safe division
        for ty in ["i64", "f64"]:
            zero = "0" if ty == "i64" else "0.0"
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ERR",
                description=(
                    f"Write a function F=safeDiv(a:{ty};b:{ty}):{ty}!MathErr "
                    f"that returns DivByZero error when b is {zero}, "
                    f"otherwise returns a / b. "
                    f"Define T=MathErr{{DivByZero:bool;Overflow:Str}}"
                ),
                expected_signature=f"F=safeDiv(a:{ty};b:{ty}):{ty}!MathErr",
                difficulty=1,
                type_hints=[ty, f"{ty}!MathErr"],
                test_input_hint=(
                    f"safeDiv(10, 0) returns error, safeDiv(10, 2) = 5 "
                    f"with type {ty}"
                ),
            ))

        # Tier 1 — safe modulo
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeMod(a:i64;b:i64):i64!MathErr "
                "that returns DivByZero error when b is 0, "
                "otherwise returns a modulo b. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeMod(a:i64;b:i64):i64!MathErr",
            difficulty=1,
            type_hints=["i64", "i64!MathErr"],
            test_input_hint="safeMod(10, 3)=1, safeMod(10, 0) returns error",
        ))

        # Tier 1 — safe array access
        for ty in ARRAY_ELEM_TYPES:
            tasks.append(TaskSpec(
                task_id=seq.next(),
                category="A-ERR",
                description=(
                    f"Write a function F=safeGet(arr:[{ty}];idx:i64):"
                    f"{ty}!LookupErr "
                    f"that returns the element at index idx, "
                    f"or NotFound error if idx is out of bounds. "
                    f"Define T=LookupErr{{NotFound:Str;EmptyCollection:bool}}"
                ),
                expected_signature=(
                    f"F=safeGet(arr:[{ty}];idx:i64):{ty}!LookupErr"
                ),
                difficulty=1,
                type_hints=[ty, f"[{ty}]", f"{ty}!LookupErr"],
                test_input_hint=(
                    f"safeGet([1;2;3], 5) returns NotFound with type {ty}"
                ),
            ))

        # Tier 1 — safe first / last
        for fn, desc in [
            ("safeFirst", "the first element, or EmptyCollection error "
             "if empty"),
            ("safeLast", "the last element, or EmptyCollection error "
             "if empty"),
        ]:
            for ty in ARRAY_ELEM_TYPES:
                tasks.append(TaskSpec(
                    task_id=seq.next(),
                    category="A-ERR",
                    description=(
                        f"Write a function F={fn}(arr:[{ty}]):"
                        f"{ty}!LookupErr "
                        f"that returns {desc}. "
                        f"Define T=LookupErr"
                        f"{{NotFound:Str;EmptyCollection:bool}}"
                    ),
                    expected_signature=(
                        f"F={fn}(arr:[{ty}]):{ty}!LookupErr"
                    ),
                    difficulty=1,
                    type_hints=[ty, f"[{ty}]", f"{ty}!LookupErr"],
                    test_input_hint=(
                        f"{fn}([]) returns EmptyCollection error "
                        f"with type {ty}"
                    ),
                ))

        # Tier 1 — safe string to integer parse
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseInt(s:Str):i64!ParseErr "
                "that parses s as an integer, returning InvalidFormat error "
                "if s is not a valid integer string, or EmptyInput error "
                "if s is empty. "
                "Define T=ParseErr{InvalidFormat:Str;EmptyInput:bool;"
                "OutOfRange:Str}"
            ),
            expected_signature="F=parseInt(s:Str):i64!ParseErr",
            difficulty=1,
            type_hints=["Str", "i64", "i64!ParseErr"],
            test_input_hint=(
                "parseInt(\"42\")=42, parseInt(\"abc\") returns InvalidFormat"
            ),
        ))

        # Tier 2 — error propagation chain: parse then compute
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseAndDouble(s:Str):i64!ParseErr "
                "that parses s as an integer using parseInt and returns "
                "the result doubled. Propagate any parse error using the "
                "! operator. "
                "Define T=ParseErr{InvalidFormat:Str;EmptyInput:bool;"
                "OutOfRange:Str}"
            ),
            expected_signature="F=parseAndDouble(s:Str):i64!ParseErr",
            difficulty=2,
            type_hints=["Str", "i64", "i64!ParseErr"],
            test_input_hint=(
                "parseAndDouble(\"5\")=10, parseAndDouble(\"x\") "
                "returns InvalidFormat"
            ),
        ))

        # Tier 2 — error propagation: two fallible calls
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseAndDivide(a:Str;b:Str):"
                "f64!CalcErr "
                "that parses both a and b as f64, then divides a by b. "
                "Return ParseFailed error if either parse fails. "
                "Return DivByZero error if the parsed b equals 0.0. "
                "Propagate errors using the ! operator. "
                "Define T=CalcErr{ParseFailed:Str;DivByZero:bool}"
            ),
            expected_signature=(
                "F=parseAndDivide(a:Str;b:Str):f64!CalcErr"
            ),
            difficulty=2,
            type_hints=["Str", "f64", "f64!CalcErr"],
            test_input_hint="parseAndDivide(\"10.0\",\"2.0\")=5.0",
        ))

        # Tier 2 — error mapping: safe sqrt
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeSqrt(x:f64):f64!MathErr "
                "that returns the square root of x. "
                "Return Overflow error with message \"negative input\" "
                "if x is negative. Otherwise compute sqrt via Newton's "
                "method or iteration. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeSqrt(x:f64):f64!MathErr",
            difficulty=2,
            type_hints=["f64", "f64!MathErr"],
            test_input_hint=(
                "safeSqrt(9.0)=3.0, safeSqrt(-1.0) returns Overflow error"
            ),
        ))

        # Tier 2 — match on error for recovery
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=divOrDefault(a:f64;b:f64;"
                "default:f64):f64 "
                "that attempts to divide a by b, and if b is 0.0, "
                "returns the default value instead of an error. "
                "Use match on the error result to recover. "
                "This function is total (no error in return type)"
            ),
            expected_signature=(
                "F=divOrDefault(a:f64;b:f64;default:f64):f64"
            ),
            difficulty=2,
            type_hints=["f64"],
            test_input_hint=(
                "divOrDefault(10.0, 0.0, -1.0)=-1.0, "
                "divOrDefault(10.0, 2.0, -1.0)=5.0"
            ),
        ))

        # Tier 2 — validate string length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateLength(s:Str;minLen:i64;"
                "maxLen:i64):Str!ValidationErr "
                "that returns s if its length is between minLen and "
                "maxLen inclusive. "
                "Return TooShort error if too short, TooLong error if "
                "too long. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;"
                "InvalidChar:Str}"
            ),
            expected_signature=(
                "F=validateLength(s:Str;minLen:i64;maxLen:i64):"
                "Str!ValidationErr"
            ),
            difficulty=2,
            type_hints=["Str", "i64", "Str!ValidationErr"],
            test_input_hint=(
                "validateLength(\"hi\", 3, 10) returns TooShort"
            ),
        ))

        # Tier 2 — safe map lookup
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeLookup(m:[Str:i64];key:Str):"
                "i64!LookupErr "
                "that returns the value for key in the map m. "
                "Return NotFound error with the key as message if the "
                "key is not present. "
                "Return EmptyCollection error if the map is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature=(
                "F=safeLookup(m:[Str:i64];key:Str):i64!LookupErr"
            ),
            difficulty=2,
            type_hints=["Str", "i64", "[Str:i64]", "i64!LookupErr"],
            test_input_hint=(
                "safeLookup([\"a\":1], \"b\") returns NotFound"
            ),
        ))

        # Tier 2 — chain of two fallible lookups
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=lookupAndNegate(m:[Str:i64];"
                "key:Str):i64!LookupErr "
                "that looks up key in m using safeLookup and returns "
                "the negated value. "
                "Propagate any error from safeLookup using the ! operator. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature=(
                "F=lookupAndNegate(m:[Str:i64];key:Str):i64!LookupErr"
            ),
            difficulty=2,
            type_hints=["Str", "i64", "[Str:i64]", "i64!LookupErr"],
            test_input_hint="lookupAndNegate([\"a\":5], \"a\")=-5",
        ))

        # Tier 2 — checked addition with overflow detection
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=checkedAdd(a:i64;b:i64):i64!MathErr "
                "that adds a and b, returning Overflow error with "
                "\"positive overflow\" if both are positive and the result "
                "would overflow i64 max, or \"negative overflow\" if both "
                "negative and result would underflow. "
                "For simplicity, check if a > 0 && b > 0 && "
                "a > 4611686018427387903 - b. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=checkedAdd(a:i64;b:i64):i64!MathErr",
            difficulty=2,
            type_hints=["i64", "i64!MathErr"],
            test_input_hint=(
                "checkedAdd(1, 2)=3, large values return Overflow"
            ),
        ))

        # Tier 3 — chain of three fallible operations
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseAddDivide(a:Str;b:Str;c:Str):"
                "f64!CalcErr "
                "that parses a, b, and c as f64, computes (a + b) / c. "
                "Return ParseFailed if any parse fails, DivByZero if "
                "c is 0.0. "
                "Propagate errors with the ! operator at each step. "
                "Define T=CalcErr{ParseFailed:Str;DivByZero:bool}"
            ),
            expected_signature=(
                "F=parseAddDivide(a:Str;b:Str;c:Str):f64!CalcErr"
            ),
            difficulty=3,
            type_hints=["Str", "f64", "f64!CalcErr"],
            test_input_hint=(
                "parseAddDivide(\"3.0\",\"7.0\",\"2.0\")=5.0"
            ),
        ))

        # Tier 3 — error recovery with fallback chain
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=firstValid(values:[Str]):"
                "i64!ParseErr "
                "that iterates through an array of strings, attempting "
                "to parse each one as an integer. Return the first "
                "successfully parsed value. "
                "If no value parses successfully, return EmptyInput error. "
                "Define T=ParseErr{InvalidFormat:Str;EmptyInput:bool;"
                "OutOfRange:Str}"
            ),
            expected_signature="F=firstValid(values:[Str]):i64!ParseErr",
            difficulty=3,
            type_hints=["Str", "i64", "[Str]", "i64!ParseErr"],
            test_input_hint="firstValid([\"abc\";\"42\";\"99\"])=42",
        ))

        # Tier 3 — accumulate: sum parsed values
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=sumParsed(values:[Str]):"
                "i64!ParseErr "
                "that parses every string in values as an integer and "
                "returns the sum. If any string fails to parse, return "
                "InvalidFormat error immediately using the ! operator. "
                "Define T=ParseErr{InvalidFormat:Str;EmptyInput:bool;"
                "OutOfRange:Str}"
            ),
            expected_signature="F=sumParsed(values:[Str]):i64!ParseErr",
            difficulty=3,
            type_hints=["Str", "i64", "[Str]", "i64!ParseErr"],
            test_input_hint=(
                "sumParsed([\"1\";\"2\";\"3\"])=6, "
                "sumParsed([\"1\";\"x\"])=error"
            ),
        ))

        # Tier 3 — validate and transform pipeline
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateAge(input:Str):"
                "i64!ValidationErr "
                "that parses input as an integer, then validates that "
                "it is between 0 and 150 inclusive. "
                "Return InvalidChar error if not a number. "
                "Return TooShort error with \"too young\" if < 0. "
                "Return TooLong error with \"too old\" if > 150. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;"
                "InvalidChar:Str}"
            ),
            expected_signature=(
                "F=validateAge(input:Str):i64!ValidationErr"
            ),
            difficulty=3,
            type_hints=["Str", "i64", "i64!ValidationErr"],
            test_input_hint=(
                "validateAge(\"25\")=25, validateAge(\"200\")=TooLong, "
                "validateAge(\"abc\")=InvalidChar"
            ),
        ))

        # Tier 3 — multi-field validation
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateUsername(s:Str):"
                "Str!ValidationErr "
                "that validates a username string. "
                "Return TooShort if length < 3. "
                "Return TooLong if length > 20. "
                "Return InvalidChar if s contains a space. "
                "Return s if all checks pass. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;"
                "InvalidChar:Str}"
            ),
            expected_signature=(
                "F=validateUsername(s:Str):Str!ValidationErr"
            ),
            difficulty=3,
            type_hints=["Str", "Str!ValidationErr"],
            test_input_hint=(
                "validateUsername(\"ab\")=TooShort, "
                "validateUsername(\"hello world\")=InvalidChar"
            ),
        ))

        # Tier 3 — match on specific error variants for recovery
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=resilientLookup(m:[Str:i64];"
                "key:Str;fallback:i64):i64 "
                "that looks up key in m. If found, return the value. "
                "If NotFound, return fallback. If EmptyCollection, "
                "return 0. "
                "This function is total (no error in return type). "
                "Use match on the error result to handle each variant. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature=(
                "F=resilientLookup(m:[Str:i64];key:Str;fallback:i64):i64"
            ),
            difficulty=3,
            type_hints=["Str", "i64", "[Str:i64]"],
            test_input_hint=(
                "resilientLookup([\"a\":1], \"b\", -1)=-1"
            ),
        ))

        # Tier 3 — pipeline with error mapping
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeDivFromStrs(a:Str;b:Str):"
                "f64!CalcErr "
                "that parses both strings as f64, divides a by b. "
                "Map any parse failure to CalcErr ParseFailed variant. "
                "Return DivByZero if denominator is 0.0. "
                "Use the ! operator for propagation. "
                "Define T=CalcErr{ParseFailed:Str;DivByZero:bool}"
            ),
            expected_signature=(
                "F=safeDivFromStrs(a:Str;b:Str):f64!CalcErr"
            ),
            difficulty=3,
            type_hints=["Str", "f64", "f64!CalcErr"],
            test_input_hint=(
                "safeDivFromStrs(\"10.0\",\"0.0\")=DivByZero"
            ),
        ))


        # ---------------------------------------------------------------
        # NEW TEMPLATES: A-ERR additions (~69 new tasks)
        # ---------------------------------------------------------------

        # Tier 1 — safe subtraction (underflow for u64)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeSub(a:u64;b:u64):u64!MathErr "
                "that returns a - b, or DivByZero error (used as underflow indicator) "
                "if b > a. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeSub(a:u64;b:u64):u64!MathErr",
            difficulty=1,
            type_hints=["u64", "u64!MathErr"],
            test_input_hint="safeSub(5, 3) = 2, safeSub(3, 5) returns error",
        ))

        # Tier 1 — safe multiplication (overflow)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeMul(a:i64;b:i64):i64!MathErr "
                "that multiplies a and b. Return Overflow error if the result "
                "would overflow. For simplicity, check if b is not 0 and "
                "abs(a) > 4611686018427387903 / abs(b). "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeMul(a:i64;b:i64):i64!MathErr",
            difficulty=1,
            type_hints=["i64", "i64!MathErr"],
            test_input_hint="safeMul(3, 4) = 12",
        ))

        # Tier 1 — safe conversion i64 to u64
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeToU64(n:i64):u64!ConvErr "
                "that converts n to u64. Return NegativeValue error if n < 0. "
                "Define T=ConvErr{NegativeValue:bool;Overflow:Str}"
            ),
            expected_signature="F=safeToU64(n:i64):u64!ConvErr",
            difficulty=1,
            type_hints=["i64", "u64", "u64!ConvErr"],
            test_input_hint="safeToU64(5) = 5, safeToU64(-1) returns error",
        ))

        # Tier 1 — safe percentage
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safePercent(part:f64;total:f64):f64!MathErr "
                "that returns (part / total) * 100.0. "
                "Return DivByZero error if total is 0.0. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safePercent(part:f64;total:f64):f64!MathErr",
            difficulty=1,
            type_hints=["f64", "f64!MathErr"],
            test_input_hint="safePercent(25.0, 100.0) = 25.0",
        ))

        # Tier 2 — safe average (empty array)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeAvg(arr:[f64]):f64!LookupErr "
                "that returns the average of arr elements. "
                "Return EmptyCollection error if arr is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeAvg(arr:[f64]):f64!LookupErr",
            difficulty=2,
            type_hints=["f64", "[f64]", "f64!LookupErr"],
            test_input_hint="safeAvg([1.0;2.0;3.0]) = 2.0, safeAvg([]) returns error",
        ))

        # Tier 2 — safe min of array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeMin(arr:[i64]):i64!LookupErr "
                "that returns the minimum element. "
                "Return EmptyCollection error if arr is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeMin(arr:[i64]):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safeMin([3;1;2]) = 1, safeMin([]) returns error",
        ))

        # Tier 2 — safe max of array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeMax(arr:[i64]):i64!LookupErr "
                "that returns the maximum element. "
                "Return EmptyCollection error if arr is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeMax(arr:[i64]):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safeMax([3;1;2]) = 3, safeMax([]) returns error",
        ))

        # Tier 2 — safe array sum (empty check)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeSum(arr:[i64]):i64!LookupErr "
                "that returns the sum of elements. "
                "Return EmptyCollection error if arr is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeSum(arr:[i64]):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safeSum([1;2;3]) = 6, safeSum([]) returns error",
        ))

        # Tier 2 — safe head of array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeHead(arr:[i64]):i64!LookupErr "
                "that returns the first element. "
                "Return EmptyCollection error if arr is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeHead(arr:[i64]):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safeHead([1;2;3]) = 1",
        ))

        # Tier 2 — safe tail of array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeTail(arr:[i64]):[i64]!LookupErr "
                "that returns all elements except the first. "
                "Return EmptyCollection error if arr is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeTail(arr:[i64]):[i64]!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "[i64]!LookupErr"],
            test_input_hint="safeTail([1;2;3]) = [2;3]",
        ))

        # Tier 2 — safe pop (remove last)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safePop(arr:[i64]):i64!LookupErr "
                "that returns the last element of arr. "
                "Return EmptyCollection error if arr is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safePop(arr:[i64]):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safePop([1;2;3]) = 3, safePop([]) returns error",
        ))

        # Tier 2 — safe nth element
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeNth(arr:[i64];n:i64):i64!LookupErr "
                "that returns the n-th element (0-indexed). "
                "Return NotFound error if n is out of bounds. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeNth(arr:[i64];n:i64):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safeNth([10;20;30], 1) = 20",
        ))

        # Tier 2 — safe binary search
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeBsearch(arr:[i64];val:i64):i64!LookupErr "
                "that binary searches for val in sorted arr. "
                "Return NotFound error if val is not present. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeBsearch(arr:[i64];val:i64):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safeBsearch([1;3;5;7], 3) = 1",
        ))

        # Tier 2 — safe index of
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeIndexOf(arr:[i64];val:i64):i64!LookupErr "
                "that returns the index of val in arr. "
                "Return NotFound error if val is not found. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeIndexOf(arr:[i64];val:i64):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safeIndexOf([10;20;30], 20) = 1",
        ))

        # Tier 1 — safe string to bool parse
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseBool(s:Str):bool!ParseErr "
                "that returns true for \"true\", false for \"false\". "
                "Return InvalidFormat error for any other input. "
                "Define T=ParseErr{InvalidFormat:Str;EmptyInput:bool;OutOfRange:Str}"
            ),
            expected_signature="F=parseBool(s:Str):bool!ParseErr",
            difficulty=1,
            type_hints=["Str", "bool", "bool!ParseErr"],
            test_input_hint="parseBool(\"true\") = true, parseBool(\"yes\") returns error",
        ))

        # Tier 2 — safe char at index
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeCharAt(s:Str;idx:i64):Str!LookupErr "
                "that returns the character at index idx. "
                "Return NotFound error if idx is out of bounds. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeCharAt(s:Str;idx:i64):Str!LookupErr",
            difficulty=2,
            type_hints=["Str", "i64", "Str!LookupErr"],
            test_input_hint="safeCharAt(\"hello\", 1) = \"e\"",
        ))

        # Tier 2 — safe substring
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeSubstr(s:Str;start:i64;end:i64):Str!LookupErr "
                "that returns the substring from start to end (exclusive). "
                "Return NotFound error if start or end is out of bounds or start > end. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeSubstr(s:Str;start:i64;end:i64):Str!LookupErr",
            difficulty=2,
            type_hints=["Str", "i64", "Str!LookupErr"],
            test_input_hint="safeSubstr(\"hello\", 1, 4) = \"ell\"",
        ))

        # Tier 2 — safe division chain
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeDivChain(a:f64;b:f64;c:f64):f64!MathErr "
                "that computes a / b / c. Return DivByZero error if b or c is 0.0. "
                "Propagate errors using the ! operator. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeDivChain(a:f64;b:f64;c:f64):f64!MathErr",
            difficulty=2,
            type_hints=["f64", "f64!MathErr"],
            test_input_hint="safeDivChain(100.0, 5.0, 2.0) = 10.0",
        ))

        # Tier 1 — validate positive integer
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validatePos(n:i64):i64!ValidationErr "
                "that returns n if n > 0, otherwise returns TooShort error. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validatePos(n:i64):i64!ValidationErr",
            difficulty=1,
            type_hints=["i64", "i64!ValidationErr"],
            test_input_hint="validatePos(5) = 5, validatePos(-1) returns error",
        ))

        # Tier 1 — validate non-negative
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateNonNeg(n:i64):i64!ValidationErr "
                "that returns n if n >= 0, otherwise returns TooShort error. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateNonNeg(n:i64):i64!ValidationErr",
            difficulty=1,
            type_hints=["i64", "i64!ValidationErr"],
            test_input_hint="validateNonNeg(0) = 0, validateNonNeg(-1) returns error",
        ))

        # Tier 1 — validate in range
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateRange(n:i64;lo:i64;hi:i64):i64!ValidationErr "
                "that returns n if lo <= n and n <= hi. "
                "Return TooShort error if n < lo, TooLong error if n > hi. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateRange(n:i64;lo:i64;hi:i64):i64!ValidationErr",
            difficulty=1,
            type_hints=["i64", "i64!ValidationErr"],
            test_input_hint="validateRange(5, 1, 10) = 5",
        ))

        # Tier 1 — validate non-empty string
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateNonEmpty(s:Str):Str!ValidationErr "
                "that returns s if its length > 0. "
                "Return TooShort error if empty. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateNonEmpty(s:Str):Str!ValidationErr",
            difficulty=1,
            type_hints=["Str", "Str!ValidationErr"],
            test_input_hint="validateNonEmpty(\"hi\") = \"hi\", validateNonEmpty(\"\") returns error",
        ))

        # Tier 1 — validate string max length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateMaxLen(s:Str;max:i64):Str!ValidationErr "
                "that returns s if its length <= max. "
                "Return TooLong error otherwise. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateMaxLen(s:Str;max:i64):Str!ValidationErr",
            difficulty=1,
            type_hints=["Str", "i64", "Str!ValidationErr"],
            test_input_hint="validateMaxLen(\"hi\", 5) = \"hi\"",
        ))

        # Tier 1 — validate string min length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateMinLen(s:Str;min:i64):Str!ValidationErr "
                "that returns s if its length >= min. "
                "Return TooShort error otherwise. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateMinLen(s:Str;min:i64):Str!ValidationErr",
            difficulty=1,
            type_hints=["Str", "i64", "Str!ValidationErr"],
            test_input_hint="validateMinLen(\"hi\", 3) returns TooShort",
        ))

        # Tier 1 — validate even number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateEven(n:i64):i64!ValidationErr "
                "that returns n if n is even. "
                "Return InvalidChar error with \"not even\" otherwise. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateEven(n:i64):i64!ValidationErr",
            difficulty=1,
            type_hints=["i64", "i64!ValidationErr"],
            test_input_hint="validateEven(4) = 4, validateEven(3) returns error",
        ))

        # Tier 1 — validate odd number
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateOdd(n:i64):i64!ValidationErr "
                "that returns n if n is odd. "
                "Return InvalidChar error with \"not odd\" otherwise. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateOdd(n:i64):i64!ValidationErr",
            difficulty=1,
            type_hints=["i64", "i64!ValidationErr"],
            test_input_hint="validateOdd(3) = 3, validateOdd(4) returns error",
        ))

        # Tier 1 — validate multiple of n
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateMultiple(x:i64;n:i64):i64!ValidationErr "
                "that returns x if x is a multiple of n. "
                "Return InvalidChar error otherwise. Return DivByZero-like error if n is 0. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateMultiple(x:i64;n:i64):i64!ValidationErr",
            difficulty=1,
            type_hints=["i64", "i64!ValidationErr"],
            test_input_hint="validateMultiple(10, 5) = 10",
        ))

        # Tier 2 — safe gcd (zero check)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeGcd(a:i64;b:i64):i64!MathErr "
                "that returns the GCD of a and b. "
                "Return DivByZero error if both a and b are 0. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeGcd(a:i64;b:i64):i64!MathErr",
            difficulty=2,
            type_hints=["i64", "i64!MathErr"],
            test_input_hint="safeGcd(12, 8) = 4, safeGcd(0, 0) returns error",
        ))

        # Tier 2 — safe modulo (zero check)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeModulo(a:i64;b:i64):i64!MathErr "
                "that returns a modulo b. Return DivByZero error if b is 0. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeModulo(a:i64;b:i64):i64!MathErr",
            difficulty=2,
            type_hints=["i64", "i64!MathErr"],
            test_input_hint="safeModulo(10, 3) = 1",
        ))

        # Tier 2 — safe sqrt (negative check)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeSqrtI(n:i64):i64!MathErr "
                "that returns the integer square root of n. "
                "Return Overflow error with \"negative input\" if n < 0. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeSqrtI(n:i64):i64!MathErr",
            difficulty=2,
            type_hints=["i64", "i64!MathErr"],
            test_input_hint="safeSqrtI(9) = 3, safeSqrtI(-1) returns error",
        ))

        # Tier 2 — safe factorial
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeFactorial(n:i64):i64!MathErr "
                "that returns n factorial. Return Overflow error with "
                "\"negative input\" if n < 0. Return Overflow error with "
                "\"too large\" if n > 20. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeFactorial(n:i64):i64!MathErr",
            difficulty=2,
            type_hints=["i64", "i64!MathErr"],
            test_input_hint="safeFactorial(5) = 120, safeFactorial(-1) returns error",
        ))

        # Tier 3 — error chain: parse then validate then compute
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseValidateSquare(s:Str):i64!CalcErr "
                "that parses s as i64, validates it is positive, then returns "
                "the square of the value. Return ParseFailed if parse fails, "
                "DivByZero if the parsed value is not positive. "
                "Propagate errors with the ! operator. "
                "Define T=CalcErr{ParseFailed:Str;DivByZero:bool}"
            ),
            expected_signature="F=parseValidateSquare(s:Str):i64!CalcErr",
            difficulty=3,
            type_hints=["Str", "i64", "i64!CalcErr"],
            test_input_hint="parseValidateSquare(\"5\") = 25",
        ))

        # Tier 3 — error recovery: try primary, fallback
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=divWithFallback(a:f64;b:f64;c:f64):f64 "
                "that tries to compute a / b. If b is 0.0, try a / c instead. "
                "If c is also 0.0, return 0.0. This function is total. "
                "Use match on the error result to recover"
            ),
            expected_signature="F=divWithFallback(a:f64;b:f64;c:f64):f64",
            difficulty=3,
            type_hints=["f64"],
            test_input_hint="divWithFallback(10.0, 0.0, 5.0) = 2.0",
        ))

        # Tier 3 — error recovery: try with default
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=getOrDefault(arr:[i64];idx:i64;def:i64):i64 "
                "that returns arr[idx] if idx is valid, otherwise returns def. "
                "This function is total (no error in return type). "
                "Internally call a safe access function and handle the error"
            ),
            expected_signature="F=getOrDefault(arr:[i64];idx:i64;def:i64):i64",
            difficulty=3,
            type_hints=["i64", "[i64]"],
            test_input_hint="getOrDefault([1;2;3], 5, -1) = -1",
        ))

        # Tier 3 — error accumulator: validate all, first error
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateAllPositive(arr:[i64]):bool!ValidationErr "
                "that checks every element is positive. "
                "Return TooShort error on the first non-positive element found. "
                "Return true if all are positive. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateAllPositive(arr:[i64]):bool!ValidationErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "bool", "bool!ValidationErr"],
            test_input_hint="validateAllPositive([1;2;3]) = true",
        ))

        # Tier 2 — multi-field validation (2 fields)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validatePoint(x:i64;y:i64):bool!ValidationErr "
                "that validates both x and y are between -1000 and 1000 inclusive. "
                "Return TooShort if x is out of range, TooLong if y is out of range. "
                "Check x first. Return true if both valid. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validatePoint(x:i64;y:i64):bool!ValidationErr",
            difficulty=2,
            type_hints=["i64", "bool", "bool!ValidationErr"],
            test_input_hint="validatePoint(5, 10) = true",
        ))

        # Tier 3 — multi-field validation (3 fields)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateRgb(r:i64;g:i64;b:i64):bool!ValidationErr "
                "that validates r, g, b are each 0-255 inclusive. "
                "Return InvalidChar error with the name of the first invalid field. "
                "Return true if all valid. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateRgb(r:i64;g:i64;b:i64):bool!ValidationErr",
            difficulty=3,
            type_hints=["i64", "bool", "bool!ValidationErr"],
            test_input_hint="validateRgb(0, 128, 255) = true",
        ))

        # Tier 2 — safe pop front
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safePopFront(arr:[i64]):i64!LookupErr "
                "that returns the first element of arr. "
                "Return EmptyCollection error if arr is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safePopFront(arr:[i64]):i64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "i64!LookupErr"],
            test_input_hint="safePopFront([1;2;3]) = 1",
        ))

        # Tier 2 — safe remove at index
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeRemoveAt(arr:[i64];idx:i64):[i64]!LookupErr "
                "that returns arr with element at idx removed. "
                "Return NotFound error if idx is out of bounds. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeRemoveAt(arr:[i64];idx:i64):[i64]!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "[i64]!LookupErr"],
            test_input_hint="safeRemoveAt([1;2;3], 1) = [1;3]",
        ))

        # Tier 2 — safe insert at index
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeInsertAt(arr:[i64];idx:i64;val:i64):[i64]!LookupErr "
                "that inserts val at index idx. "
                "Return NotFound error if idx > length of arr. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeInsertAt(arr:[i64];idx:i64;val:i64):[i64]!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "[i64]!LookupErr"],
            test_input_hint="safeInsertAt([1;3], 1, 2) = [1;2;3]",
        ))

        # Tier 2 — safe swap in array
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeSwap(arr:[i64];i:i64;j:i64):[i64]!LookupErr "
                "that returns arr with elements at i and j swapped. "
                "Return NotFound error if either index is out of bounds. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeSwap(arr:[i64];i:i64;j:i64):[i64]!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "[i64]!LookupErr"],
            test_input_hint="safeSwap([1;2;3], 0, 2) = [3;2;1]",
        ))

        # Tier 2 — safe find and remove
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeFindRemove(arr:[i64];val:i64):[i64]!LookupErr "
                "that finds val in arr and removes the first occurrence. "
                "Return NotFound error if val is not in arr. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeFindRemove(arr:[i64];val:i64):[i64]!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "[i64]!LookupErr"],
            test_input_hint="safeFindRemove([1;2;3], 2) = [1;3]",
        ))

        # Tier 3 — safe zip (length mismatch)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeZip(a:[i64];b:[i64]):[[i64]]!LookupErr "
                "that zips a and b into pairs. "
                "Return NotFound error with \"length mismatch\" if lengths differ. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeZip(a:[i64];b:[i64]):[[i64]]!LookupErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "[[i64]]", "[[i64]]!LookupErr"],
            test_input_hint="safeZip([1;2],[3;4]) = [[1;3];[2;4]]",
        ))

        # Tier 2 — safe string concat with max length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeConcat(a:Str;b:Str;maxLen:i64):Str!ValidationErr "
                "that concatenates a and b. Return TooLong error if the "
                "combined length exceeds maxLen. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=safeConcat(a:Str;b:Str;maxLen:i64):Str!ValidationErr",
            difficulty=2,
            type_hints=["Str", "i64", "Str!ValidationErr"],
            test_input_hint="safeConcat(\"hi\", \"lo\", 5) = \"hilo\"",
        ))

        # Tier 2 — safe string repeat with max length
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeRepeat(s:Str;n:i64;maxLen:i64):Str!ValidationErr "
                "that repeats s n times. Return TooLong error if the "
                "result length would exceed maxLen. "
                "Return TooShort error if n < 0. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=safeRepeat(s:Str;n:i64;maxLen:i64):Str!ValidationErr",
            difficulty=2,
            type_hints=["Str", "i64", "Str!ValidationErr"],
            test_input_hint="safeRepeat(\"ab\", 3, 10) = \"ababab\"",
        ))

        # Tier 2 — safe left pad
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safePadLeft(s:Str;width:i64;fill:Str):Str!ValidationErr "
                "that pads s on the left. Return TooShort error if width < 0. "
                "Return InvalidChar error if fill is not exactly 1 character. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=safePadLeft(s:Str;width:i64;fill:Str):Str!ValidationErr",
            difficulty=2,
            type_hints=["Str", "i64", "Str!ValidationErr"],
            test_input_hint="safePadLeft(\"hi\", 5, \"0\") = \"000hi\"",
        ))

        # Tier 2 — safe trim (empty result)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeTrim(s:Str):Str!ValidationErr "
                "that trims whitespace from both ends of s. "
                "Return TooShort error if the result is empty. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=safeTrim(s:Str):Str!ValidationErr",
            difficulty=2,
            type_hints=["Str", "Str!ValidationErr"],
            test_input_hint="safeTrim(\"  hi  \") = \"hi\", safeTrim(\"   \") returns error",
        ))

        # Tier 2 — safe replace (pattern not found)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeReplace(s:Str;old:Str;new:Str):Str!LookupErr "
                "that replaces the first occurrence of old with new. "
                "Return NotFound error if old is not in s. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeReplace(s:Str;old:Str;new:Str):Str!LookupErr",
            difficulty=2,
            type_hints=["Str", "Str!LookupErr"],
            test_input_hint="safeReplace(\"hello\", \"ll\", \"r\") = \"hero\"",
        ))

        # Tier 3 — safe base conversion
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeToBase(n:i64;base:i64):Str!MathErr "
                "that converts n to a string in the given base (2-16). "
                "Return DivByZero error if base < 2 or base > 16. "
                "Use digits 0-9 and a-f. Assume n >= 0. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeToBase(n:i64;base:i64):Str!MathErr",
            difficulty=3,
            type_hints=["i64", "Str", "Str!MathErr"],
            test_input_hint="safeToBase(255, 16) = \"ff\"",
        ))

        # Tier 3 — error mapping: convert one error type to another
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=lookupAsCalcErr(m:[Str:i64];key:Str):i64!CalcErr "
                "that looks up key in m. If found return the value. "
                "If not found, return ParseFailed error (mapping LookupErr to CalcErr). "
                "Use match on the lookup result to convert the error type. "
                "Define T=CalcErr{ParseFailed:Str;DivByZero:bool}"
            ),
            expected_signature="F=lookupAsCalcErr(m:[Str:i64];key:Str):i64!CalcErr",
            difficulty=3,
            type_hints=["Str", "i64", "[Str:i64]", "i64!CalcErr"],
            test_input_hint="lookupAsCalcErr([\"a\":1], \"b\") returns ParseFailed",
        ))

        # Tier 3 — error chaining: three sequential operations
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseValidateNegate(s:Str):i64!CalcErr "
                "that parses s as i64, validates it is positive, "
                "then negates it. Return ParseFailed if parse fails, "
                "DivByZero if not positive. Propagate with ! operator. "
                "Define T=CalcErr{ParseFailed:Str;DivByZero:bool}"
            ),
            expected_signature="F=parseValidateNegate(s:Str):i64!CalcErr",
            difficulty=3,
            type_hints=["Str", "i64", "i64!CalcErr"],
            test_input_hint="parseValidateNegate(\"5\") = -5",
        ))

        # Tier 3 — error chaining: four sequential operations
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseValidateDoubleNegate(s:Str):i64!CalcErr "
                "that parses s as i64, validates positive, doubles it, "
                "then negates. Return ParseFailed if parse fails, "
                "DivByZero if not positive. "
                "Define T=CalcErr{ParseFailed:Str;DivByZero:bool}"
            ),
            expected_signature="F=parseValidateDoubleNegate(s:Str):i64!CalcErr",
            difficulty=3,
            type_hints=["Str", "i64", "i64!CalcErr"],
            test_input_hint="parseValidateDoubleNegate(\"5\") = -10",
        ))

        # Tier 3 — try-catch: attempt, log variant, return default
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=divOrZero(a:f64;b:f64):f64 "
                "that divides a by b. If b is 0.0, return 0.0 instead "
                "of an error. This function is total. "
                "Internally compute using a fallible division and match "
                "on the result to recover"
            ),
            expected_signature="F=divOrZero(a:f64;b:f64):f64",
            difficulty=3,
            type_hints=["f64"],
            test_input_hint="divOrZero(10.0, 0.0) = 0.0, divOrZero(10.0, 2.0) = 5.0",
        ))

        # Tier 3 — validate and transform pipeline
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=parseAndClamp(s:Str;lo:i64;hi:i64):i64!CalcErr "
                "that parses s as i64, then clamps the result to [lo, hi]. "
                "Return ParseFailed if parse fails. "
                "Define T=CalcErr{ParseFailed:Str;DivByZero:bool}"
            ),
            expected_signature="F=parseAndClamp(s:Str;lo:i64;hi:i64):i64!CalcErr",
            difficulty=3,
            type_hints=["Str", "i64", "i64!CalcErr"],
            test_input_hint="parseAndClamp(\"50\", 0, 100) = 50",
        ))

        # Tier 3 — batch validation: check array, return first invalid
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=validateAllInRange(arr:[i64];lo:i64;hi:i64):bool!ValidationErr "
                "that checks every element is in [lo, hi]. "
                "Return TooShort error if any element < lo (first found). "
                "Return TooLong error if any element > hi (first found). "
                "Return true if all valid. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=validateAllInRange(arr:[i64];lo:i64;hi:i64):bool!ValidationErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "bool", "bool!ValidationErr"],
            test_input_hint="validateAllInRange([1;5;3], 0, 10) = true",
        ))

        # Tier 3 — safe matrix access
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeMatGet(mat:[[i64]];row:i64;col:i64):i64!LookupErr "
                "that returns mat[row][col]. Return NotFound error if row or "
                "col is out of bounds. Return EmptyCollection if mat is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeMatGet(mat:[[i64]];row:i64;col:i64):i64!LookupErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "[[i64]]", "i64!LookupErr"],
            test_input_hint="safeMatGet([[1;2];[3;4]], 0, 1) = 2",
        ))

        # Tier 3 — safe stack push with capacity
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safePush(stack:[i64];val:i64;cap:i64):[i64]!ValidationErr "
                "that appends val to stack. Return TooLong error if the "
                "stack length already equals cap. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=safePush(stack:[i64];val:i64;cap:i64):[i64]!ValidationErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "[i64]!ValidationErr"],
            test_input_hint="safePush([1;2], 3, 5) = [1;2;3]",
        ))

        # Tier 3 — safe stack pop from empty
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeStackPop(stack:[i64]):[i64]!LookupErr "
                "that returns the stack with the last element removed. "
                "Return EmptyCollection error if the stack is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeStackPop(stack:[i64]):[i64]!LookupErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "[i64]!LookupErr"],
            test_input_hint="safeStackPop([1;2;3]) = [1;2]",
        ))

        # Tier 3 — safe queue enqueue with capacity
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeEnqueue(queue:[i64];val:i64;cap:i64):[i64]!ValidationErr "
                "that appends val to queue. Return TooLong error if the "
                "queue length already equals cap. "
                "Define T=ValidationErr{TooShort:Str;TooLong:Str;InvalidChar:Str}"
            ),
            expected_signature="F=safeEnqueue(queue:[i64];val:i64;cap:i64):[i64]!ValidationErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "[i64]!ValidationErr"],
            test_input_hint="safeEnqueue([1;2], 3, 5) = [1;2;3]",
        ))

        # Tier 3 — safe queue dequeue from empty
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeDequeue(queue:[i64]):[i64]!LookupErr "
                "that returns the queue with the first element removed. "
                "Return EmptyCollection error if the queue is empty. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeDequeue(queue:[i64]):[i64]!LookupErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "[i64]!LookupErr"],
            test_input_hint="safeDequeue([1;2;3]) = [2;3]",
        ))

        # Tier 2 — safe median (empty check)
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeMedian(arr:[i64]):f64!LookupErr "
                "that returns the median of arr. "
                "Return EmptyCollection error if arr is empty. "
                "Sort the array, then return the middle element as f64, "
                "or average of the two middle elements. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeMedian(arr:[i64]):f64!LookupErr",
            difficulty=2,
            type_hints=["i64", "[i64]", "f64", "f64!LookupErr"],
            test_input_hint="safeMedian([3;1;2]) = 2.0",
        ))

        # Tier 3 — safe map with fallible function
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeDoubleAll(arr:[i64]):[i64]!MathErr "
                "that doubles each element. Return Overflow error if any "
                "doubled value would overflow (check if element > 4611686018427387903). "
                "Use a loop, propagating errors with !. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeDoubleAll(arr:[i64]):[i64]!MathErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "[i64]!MathErr"],
            test_input_hint="safeDoubleAll([1;2;3]) = [2;4;6]",
        ))

        # Tier 3 — safe fold with fallible accumulator
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeSumChecked(arr:[i64]):i64!MathErr "
                "that sums all elements with overflow checking. "
                "At each step, check if adding the next element would overflow. "
                "Return Overflow error if it would. "
                "Define T=MathErr{DivByZero:bool;Overflow:Str}"
            ),
            expected_signature="F=safeSumChecked(arr:[i64]):i64!MathErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "i64!MathErr"],
            test_input_hint="safeSumChecked([1;2;3]) = 6",
        ))

        # Tier 3 — safe nested array access
        tasks.append(TaskSpec(
            task_id=seq.next(),
            category="A-ERR",
            description=(
                "Write a function F=safeGet2d(arr:[[i64]];i:i64;j:i64):i64!LookupErr "
                "that returns arr[i][j]. Return NotFound error if i is out of "
                "bounds for the outer array, or j is out of bounds for the inner array. "
                "Define T=LookupErr{NotFound:Str;EmptyCollection:bool}"
            ),
            expected_signature="F=safeGet2d(arr:[[i64]];i:i64;j:i64):i64!LookupErr",
            difficulty=3,
            type_hints=["i64", "[i64]", "[[i64]]", "i64!LookupErr"],
            test_input_hint="safeGet2d([[1;2];[3;4]], 1, 0) = 3",
        ))

        return tasks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Sequencer:
    """Generates sequential task IDs for a category."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._counter = 0

    def next(self) -> str:
        self._counter += 1
        return f"A-{self._prefix[2:]}-{self._counter:04d}"


def _extract_types(params: str, ret: str) -> list[str]:
    """Pull type names from a parameter list and return type."""
    types: list[str] = []
    # Parse parameters like "a:i64;b:f64"
    for part in params.split(";"):
        if ":" in part:
            ty = part.split(":", 1)[1].strip()
            if ty and ty not in types:
                types.append(ty)
    if ret and ret not in types:
        types.append(ret)
    return types
