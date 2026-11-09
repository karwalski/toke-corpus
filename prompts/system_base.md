# toke Language — Quick Reference

toke is a statically typed, compiled language. No implicit coercions. Every statement here is compiled by `regen/tests/test_phase_prompts.py` (131.113); `regen/syntax_card.md` is the full card.

## Syntax Essentials

- Module: `m=name;` is the first line of every file; then imports `i=io:std.io;`, types, functions.
- Function: `f=name(p1:type1;p2:type2):rettype{body};`
- Parameters, arguments and array elements use `;` not `,`
- Return: `<expr` (or `rt expr`), not `return`
- Variable (immutable): `let x=42;`
- Variable (mutable): `let n=mut.0;`
- Assignment (mutables only): `n=n+1;`
- Conditional: `if(c){a}el{b}` is an expression: `let g=if(n>90){1}el if(n>80){2}el{3};`
- Loop: `lp(let i=0;i<n;i=i+1){body};` is the only loop (exactly init;cond;step)
- Break: `br`
- Types: `i64`, `u64`, `f64`, `bool`, `str`, `void`; structs `t=$point{x:i64;y:i64};`
- Arrays: `@i64`, literal `@(1;2;3)`, read `arr.get(i)`, length `arr.len` (a `u64`)
- Comparison: `==` `!=` `<` `>` `<=` `>=`; logic `&&` `||` `!`; modulo `%`
- Cast: `expr as type`
- Strings: interpolation `"\(a)\(b)"`; `+` does not concatenate strings

## Type Casting Rules

- `arr.len` is a `u64`, so `arr.len-1` underflows on an empty array: loop forward and index from the end with `n-1-i`.
- An `i64` index works directly: `let v=arr.get(i);`
- Convert explicitly: `let f=n as f64;` and `let k=arr.len as i64;`
- Error union types: the return type `i64!$matherr` means the function returns EITHER an `i64` OR a `$matherr` error.

## Common Mistakes

- Loop keyword is `lp`, NOT `for` or `while`
- Else is `el`, NOT `else` or `elif`; chain with `el if`, never nest `el{if(...)}`
- Booleans are `true`/`false`; no uppercase anywhere in source
- String type is `str`, NOT `$str`, `String` or `string`
- Equality is `==`; a single `=` in a condition is a compile error (E2002)
- Mutable binding is `let n=mut.0;`; reassignment drops the prefix: `n=5;`
- Never simulate an `if` with a mut flag: bind it instead, as in `let x=if(c){1}el{2};`
- No `_` in identifiers: use `idx`, `tmp`
- No `++`, `--`, `+=`, `-=` operators: write `n=n+1;`
- `el` must directly follow `}`: write `}el{`, never `};el{` (a parse error)
- No nested function definitions inside function bodies
- No print/println/console unless the task asks for output: these programs are pure functions
- `set` and `append` return a new collection, so always reassign: `arr=arr.set(i;v);` and `arr=arr.append(v);`
- A map lookup is `V!$err`: use `let v=mt m.get(k) {$ok:x x;$err:e 0};` or `let v=m.getor(k;0);`
- Match arms are single expressions: `let v=mt s.toint(x) {$ok:n n;$err:e 0};` — no blocks inside arms
