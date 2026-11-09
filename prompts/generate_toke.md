Generate a toke program for the following task.

**Category:** {category}

**Task:** {task_description}

**Expected function signature:** {expected_signature}

**Rules:**
- Output ONLY the toke source code. No explanations, no markdown fences, no comments.
- The program must start with the `m=name;` module declaration, then imports, types, functions in that order.
- Parameters, arguments and array elements use `;` as separator, NOT `,`: `@(1;2;3)`.
- Return with `<`, not `return`.
- `if` is an expression: `let g=if(n>90){{1}}el if(n>80){{2}}el{{3}};`. Chain with `el if`.
- Mutable bindings: `let n=mut.0;` (NOT `let mut n=0`); reassign with `n=n+1;`.
- No type annotations on let bindings: types are inferred.
- Loops: `lp(let i=0;i<n;i=i+1){{...}}` — exactly init;condition;update. There is no `for` or `while`.
- Comparison: `==` `!=` `<` `>` `<=` `>=`. A single `=` in a condition is a compile error.
- Logic: `&&` `||` `!`. Modulo: `%`.
- Types: `i64` `u64` `f64` `bool` `str`; arrays `@i64`; type names are `$`-prefixed: `t=$point{{x:i64;y:i64}};`.
- No uppercase letters and no `_` anywhere in identifiers. Booleans are `true`/`false`.
- No `++`, `--`, `+=`, `-=` operators.
- Strings are built by interpolation `"\(a)\(b)"`; `+` does not concatenate strings.
- `set` and `append` return a new collection, so always reassign: `arr=arr.append(v);`.
- Error handling: error types are sum types with `$`-prefixed variants; a fallible function returns `T!$errtype`; `!$errtype` propagates only inside a function that itself returns `T!$errtype`; match a result with `mt`. Match arm bodies are single EXPRESSIONS (no blocks or statements); an arm may early-return with `<`:

```
m=errs;
i=s:std.str;
t=$myerr{{$badinput:bool}};
f=parse(x:str):i64!$myerr{{
  <mt s.toint(x) {{$ok:n n;$err:e <$myerr{{$badinput:true}}}}
}};
f=twice(x:str):i64!$myerr{{
  let v=parse(x)!$myerr;
  <v*2
}};
f=main():i64{{
  <mt twice("21") {{$ok:v v-42;$err:e 1}}
}};
```
- The program must be complete and compilable.
