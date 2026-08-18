"""Tests for the Python-to-toke transpiler."""

import pytest

from transpile.py_to_toke import PyToTokeTranspiler, TranspileError


@pytest.fixture
def t():
    return PyToTokeTranspiler()


class TestBasicFunctions:

    def test_simple_add(self, t):
        src = "def add(a: int, b: int) -> int:\n    return a + b\n"
        result = t.transpile(src, "add")
        assert result.startswith("m=add;")
        assert "f=add(a:i64;b:i64):i64" in result
        assert "<a+b" in result

    def test_simple_multiply(self, t):
        src = "def mul(x: int, y: int) -> int:\n    return x * y\n"
        result = t.transpile(src, "mul")
        assert "f=mul(x:i64;y:i64):i64" in result
        assert "<x*y" in result

    def test_void_return(self, t):
        src = "def noop() -> None:\n    pass\n"
        result = t.transpile(src, "noop")
        assert ":$void{" in result

    def test_no_return_annotation(self, t):
        src = "def foo() -> None:\n    pass\n"
        result = t.transpile(src, "foo")
        assert ":$void{" in result

    def test_multiple_functions(self, t):
        src = (
            "def f1(x: int) -> int:\n    return x\n\n"
            "def f2(x: int) -> int:\n    return x + 1\n"
        )
        result = t.transpile(src, "multi")
        assert "f=f1(" in result
        assert "f=f2(" in result

    def test_skips_private_functions(self, t):
        src = (
            "def _helper(x: int) -> int:\n    return x\n\n"
            "def public(x: int) -> int:\n    return x\n"
        )
        result = t.transpile(src, "mod")
        assert "_helper" not in result
        assert "f=public(" in result


class TestTypeMapping:

    def test_int_type(self, t):
        src = "def f(x: int) -> int:\n    return x\n"
        result = t.transpile(src, "m")
        assert "x:i64" in result
        assert "):i64{" in result

    def test_float_type(self, t):
        src = "def f(x: float) -> float:\n    return x\n"
        result = t.transpile(src, "m")
        assert "x:f64" in result
        assert "):f64{" in result

    def test_bool_type(self, t):
        src = "def f(x: bool) -> bool:\n    return x\n"
        result = t.transpile(src, "m")
        assert "x:bool" in result
        assert "):bool{" in result

    def test_str_type(self, t):
        src = "def f(x: str) -> str:\n    return x\n"
        result = t.transpile(src, "m")
        assert "x:$str" in result
        assert "):$str{" in result

    def test_list_int_type(self, t):
        src = "def f(arr: list[int]) -> int:\n    return 0\n"
        result = t.transpile(src, "m")
        assert "arr:@(i64)" in result

    def test_list_float_type(self, t):
        src = "def f(arr: list[float]) -> float:\n    return 0.0\n"
        result = t.transpile(src, "m")
        assert "arr:@(f64)" in result

    def test_dict_type(self, t):
        src = "def f(d: dict[str, int]) -> int:\n    return 0\n"
        result = t.transpile(src, "m")
        assert "d:@($str:i64)" in result

    def test_missing_param_annotation_raises(self, t):
        src = "def f(x) -> int:\n    return x\n"
        with pytest.raises(TranspileError, match="no type annotation"):
            t.transpile(src, "m")

    def test_unsupported_type_raises(self, t):
        src = "def f(x: bytes) -> int:\n    return 0\n"
        with pytest.raises(TranspileError, match="Unsupported type"):
            t.transpile(src, "m")


class TestNameSanitization:

    def test_underscore_removal(self, t):
        src = "def my_func(my_var: int) -> int:\n    return my_var\n"
        result = t.transpile(src, "m")
        assert "f=myfunc(" in result
        assert "myvar:i64" in result

    def test_lowercase_conversion(self, t):
        src = "def MyFunc(X: int) -> int:\n    return X\n"
        result = t.transpile(src, "m")
        assert "f=myfunc(" in result


class TestBindings:

    def test_immutable_binding(self, t):
        src = "def f(x: int) -> int:\n    y = x + 1\n    return y\n"
        result = t.transpile(src, "m")
        assert "let y=x+1;" in result

    def test_mutable_binding(self, t):
        src = (
            "def f(x: int) -> int:\n"
            "    total = 0\n"
            "    total = total + x\n"
            "    return total\n"
        )
        result = t.transpile(src, "m")
        assert "let total=mut.0;" in result
        assert "total=total+x;" in result

    def test_augmented_assign(self, t):
        src = (
            "def f(x: int) -> int:\n"
            "    total = 0\n"
            "    total += x\n"
            "    return total\n"
        )
        result = t.transpile(src, "m")
        assert "total=total+x;" in result


class TestOperators:

    def test_add(self, t):
        src = "def f(a: int, b: int) -> int:\n    return a + b\n"
        assert "<a+b" in t.transpile(src, "m")

    def test_sub(self, t):
        src = "def f(a: int, b: int) -> int:\n    return a - b\n"
        assert "<a-b" in t.transpile(src, "m")

    def test_mul(self, t):
        src = "def f(a: int, b: int) -> int:\n    return a * b\n"
        assert "<a*b" in t.transpile(src, "m")

    def test_div(self, t):
        src = "def f(a: int, b: int) -> int:\n    return a // b\n"
        assert "<a/b" in t.transpile(src, "m")

    def test_modulo(self, t):
        src = "def f(a: int, b: int) -> int:\n    return a % b\n"
        result = t.transpile(src, "m")
        assert "a-a/b*b" in result

    def test_equality(self, t):
        src = "def f(a: int, b: int) -> bool:\n    return a == b\n"
        result = t.transpile(src, "m")
        assert "<a=b" in result

    def test_not_equal(self, t):
        src = "def f(a: int, b: int) -> bool:\n    return a != b\n"
        result = t.transpile(src, "m")
        assert "!(a=b)" in result

    def test_less_than(self, t):
        src = "def f(a: int, b: int) -> bool:\n    return a < b\n"
        result = t.transpile(src, "m")
        assert "<a<b" in result

    def test_less_equal(self, t):
        src = "def f(a: int, b: int) -> bool:\n    return a <= b\n"
        result = t.transpile(src, "m")
        assert "!(a>b)" in result

    def test_greater_equal(self, t):
        src = "def f(a: int, b: int) -> bool:\n    return a >= b\n"
        result = t.transpile(src, "m")
        assert "!(a<b)" in result

    def test_unary_neg(self, t):
        src = "def f(x: int) -> int:\n    return -x\n"
        result = t.transpile(src, "m")
        assert "0-x" in result

    def test_not(self, t):
        src = "def f(x: bool) -> bool:\n    return not x\n"
        result = t.transpile(src, "m")
        assert "!(x)" in result

    def test_power_raises(self, t):
        src = "def f(x: int) -> int:\n    return x ** 2\n"
        with pytest.raises(TranspileError, match="Power"):
            t.transpile(src, "m")


class TestControlFlow:

    def test_simple_if(self, t):
        src = (
            "def f(x: int) -> int:\n"
            "    if x > 0:\n"
            "        return x\n"
            "    return 0\n"
        )
        result = t.transpile(src, "m")
        assert "if(x>0){" in result
        assert "<x" in result

    def test_if_else(self, t):
        src = (
            "def f(x: int) -> int:\n"
            "    if x > 0:\n"
            "        return x\n"
            "    else:\n"
            "        return 0\n"
        )
        result = t.transpile(src, "m")
        assert "if(x>0){" in result
        assert "}el{" in result

    def test_if_not_equal_swaps_branches(self, t):
        src = (
            "def f(x: int) -> int:\n"
            "    if x != 0:\n"
            "        return x\n"
            "    else:\n"
            "        return 0\n"
        )
        result = t.transpile(src, "m")
        assert "if(x=0){" in result

    def test_elif_nesting(self, t):
        src = (
            "def f(x: int) -> int:\n"
            "    if x > 0:\n"
            "        return 1\n"
            "    elif x < 0:\n"
            "        return 0-1\n"
            "    else:\n"
            "        return 0\n"
        )
        result = t.transpile(src, "m")
        assert "if(x>0){" in result
        assert "}el{" in result

    def test_while_loop(self, t):
        src = (
            "def f(n: int) -> int:\n"
            "    total = 0\n"
            "    i = 0\n"
            "    while i < n:\n"
            "        total = total + i\n"
            "        i = i + 1\n"
            "    return total\n"
        )
        result = t.transpile(src, "m")
        assert "lp(let _w=0;i<n;_w=0){" in result

    def test_for_range_single(self, t):
        src = (
            "def f(n: int) -> int:\n"
            "    total = 0\n"
            "    for i in range(n):\n"
            "        total = total + i\n"
            "    return total\n"
        )
        result = t.transpile(src, "m")
        assert "lp(let i=0;i<n;i=i+1){" in result

    def test_for_range_two_args(self, t):
        src = (
            "def f(a: int, b: int) -> int:\n"
            "    total = 0\n"
            "    for i in range(a, b):\n"
            "        total = total + i\n"
            "    return total\n"
        )
        result = t.transpile(src, "m")
        assert "lp(let i=a;i<b;i=i+1){" in result

    def test_for_range_three_args(self, t):
        src = (
            "def f(n: int) -> int:\n"
            "    total = 0\n"
            "    for i in range(0, n, 2):\n"
            "        total = total + i\n"
            "    return total\n"
        )
        result = t.transpile(src, "m")
        assert "lp(let i=0;i<n;i=i+2){" in result

    def test_for_range_negative_step(self, t):
        src = (
            "def f(n: int) -> int:\n"
            "    total = 0\n"
            "    for i in range(n, 0, -1):\n"
            "        total = total + i\n"
            "    return total\n"
        )
        result = t.transpile(src, "m")
        assert "i>0" in result
        assert "i=i-1" in result

    def test_for_over_array(self, t):
        src = (
            "def f(arr: list[int]) -> int:\n"
            "    total = 0\n"
            "    for x in arr:\n"
            "        total = total + x\n"
            "    return total\n"
        )
        result = t.transpile(src, "m")
        assert "arr.len" in result
        assert "arr.get(" in result

    def test_break(self, t):
        src = (
            "def f(n: int) -> int:\n"
            "    i = 0\n"
            "    while True:\n"
            "        if i > n:\n"
            "            break\n"
            "        i = i + 1\n"
            "    return i\n"
        )
        result = t.transpile(src, "m")
        assert "br;" in result

    def test_continue_raises(self, t):
        src = (
            "def f(n: int) -> int:\n"
            "    total = 0\n"
            "    for i in range(n):\n"
            "        if i == 0:\n"
            "            continue\n"
            "        total = total + i\n"
            "    return total\n"
        )
        with pytest.raises(TranspileError, match="Continue"):
            t.transpile(src, "m")


class TestBuiltinMapping:

    def test_len(self, t):
        src = "def f(arr: list[int]) -> int:\n    return len(arr)\n"
        result = t.transpile(src, "m")
        assert "arr.len" in result

    def test_abs(self, t):
        src = "def f(x: int) -> int:\n    return abs(x)\n"
        result = t.transpile(src, "m")
        assert "if(x<0){0-x}el{x}" in result

    def test_min_two_args(self, t):
        src = "def f(a: int, b: int) -> int:\n    return min(a, b)\n"
        result = t.transpile(src, "m")
        assert "if(a<b){a}el{b}" in result

    def test_max_two_args(self, t):
        src = "def f(a: int, b: int) -> int:\n    return max(a, b)\n"
        result = t.transpile(src, "m")
        assert "if(a>b){a}el{b}" in result

    def test_int_cast(self, t):
        src = "def f(x: float) -> int:\n    return int(x)\n"
        result = t.transpile(src, "m")
        assert "x as i64" in result

    def test_float_cast(self, t):
        src = "def f(x: int) -> float:\n    return float(x)\n"
        result = t.transpile(src, "m")
        assert "x as f64" in result

    def test_print_skipped(self, t):
        src = (
            "def f(x: int) -> int:\n"
            "    print(x)\n"
            "    return x\n"
        )
        result = t.transpile(src, "m")
        assert "print" not in result


class TestConstants:

    def test_int_constant(self, t):
        src = "def f() -> int:\n    return 42\n"
        assert "<42" in t.transpile(src, "m")

    def test_float_constant(self, t):
        src = "def f() -> float:\n    return 3.14\n"
        assert "<3.14" in t.transpile(src, "m")

    def test_string_constant(self, t):
        src = 'def f() -> str:\n    return "hello"\n'
        result = t.transpile(src, "m")
        assert '"hello"' in result

    def test_bool_true(self, t):
        src = "def f() -> bool:\n    return True\n"
        assert "<true" in t.transpile(src, "m")

    def test_bool_false(self, t):
        src = "def f() -> bool:\n    return False\n"
        assert "<false" in t.transpile(src, "m")

    def test_negative_int(self, t):
        src = "def f() -> int:\n    return -1\n"
        result = t.transpile(src, "m")
        assert "0-1" in result


class TestArrayOperations:

    def test_array_literal(self, t):
        src = "def f() -> list[int]:\n    return [1, 2, 3]\n"
        result = t.transpile(src, "m")
        assert "@(1;2;3)" in result

    def test_array_indexing(self, t):
        src = "def f(arr: list[int]) -> int:\n    return arr[0]\n"
        result = t.transpile(src, "m")
        assert "arr.get(0)" in result

    def test_array_variable_indexing(self, t):
        src = "def f(arr: list[int], i: int) -> int:\n    return arr[i]\n"
        result = t.transpile(src, "m")
        assert "arr.get(i)" in result


class TestTernary:

    def test_ternary(self, t):
        src = "def f(x: int) -> int:\n    return x if x > 0 else 0\n"
        result = t.transpile(src, "m")
        assert "if(x>0){x}el{0}" in result

    def test_ternary_not_equal(self, t):
        src = "def f(x: int) -> int:\n    return 1 if x != 0 else 0\n"
        result = t.transpile(src, "m")
        assert "if(x=0){0}el{1}" in result


class TestErrors:

    def test_no_functions_raises(self, t):
        src = "x = 42\n"
        with pytest.raises(TranspileError, match="No functions found"):
            t.transpile(src, "m")

    def test_parse_error_raises(self, t):
        src = "def f(:\n"
        with pytest.raises(TranspileError, match="Failed to parse"):
            t.transpile(src, "m")

    def test_multiple_targets_raises(self, t):
        src = "def f(x: int) -> int:\n    a = b = x\n    return a\n"
        with pytest.raises(TranspileError, match="Multiple assignment"):
            t.transpile(src, "m")


class TestAlgorithms:
    """End-to-end tests with real algorithm implementations."""

    def test_factorial(self, t):
        src = (
            "def factorial(n: int) -> int:\n"
            "    if n < 2:\n"
            "        return 1\n"
            "    return n * factorial(n - 1)\n"
        )
        result = t.transpile(src, "fact")
        assert "m=fact;" in result
        assert "f=factorial(n:i64):i64{" in result
        assert "if(n<2){" in result
        assert "<1" in result
        assert "<n*factorial(n-1)" in result

    def test_fibonacci(self, t):
        src = (
            "def fib(n: int) -> int:\n"
            "    if n < 2:\n"
            "        return n\n"
            "    return fib(n - 1) + fib(n - 2)\n"
        )
        result = t.transpile(src, "fib")
        assert "f=fib(n:i64):i64{" in result
        assert "fib(n-1)+fib(n-2)" in result

    def test_sum_array(self, t):
        src = (
            "def sum_arr(arr: list[int]) -> int:\n"
            "    total = 0\n"
            "    for i in range(len(arr)):\n"
            "        total = total + arr[i]\n"
            "    return total\n"
        )
        result = t.transpile(src, "arrsum")
        assert "f=sumarr(arr:@(i64)):i64{" in result
        assert "let total=mut.0;" in result
        assert "arr.len" in result

    def test_binary_search(self, t):
        src = (
            "def binary_search(arr: list[int], target: int) -> int:\n"
            "    lo = 0\n"
            "    hi = len(arr) - 1\n"
            "    while lo <= hi:\n"
            "        mid = lo + (hi - lo) // 2\n"
            "        if arr[mid] == target:\n"
            "            return mid\n"
            "        elif arr[mid] < target:\n"
            "            lo = mid + 1\n"
            "        else:\n"
            "            hi = mid - 1\n"
            "    return -1\n"
        )
        result = t.transpile(src, "bsearch")
        assert "f=binarysearch(" in result
        assert "arr:@(i64)" in result
        assert "target:i64" in result
        assert "let lo=mut.0;" in result
        assert "lp(" in result

    def test_gcd(self, t):
        src = (
            "def gcd(a: int, b: int) -> int:\n"
            "    while b != 0:\n"
            "        temp = b\n"
            "        b = a % b\n"
            "        a = temp\n"
            "    return a\n"
        )
        result = t.transpile(src, "gcd")
        assert "f=gcd(a:i64;b:i64):i64{" in result
        assert "lp(" in result
        assert "a-a/b*b" in result  # modulo expansion

    def test_is_palindrome(self, t):
        src = (
            "def is_palindrome(s: str) -> bool:\n"
            "    n = len(s)\n"
            "    for i in range(n // 2):\n"
            "        if s[i] != s[n - 1 - i]:\n"
            "            return False\n"
            "    return True\n"
        )
        result = t.transpile(src, "pal")
        assert "f=ispalindrome(s:$str):bool{" in result
        assert "s.len" in result
