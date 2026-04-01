# toke Language — Quick Reference

toke is a statically typed, compiled language. No comments, no implicit coercions.

## Syntax Essentials

- Module: `M=name;`
- Function: `F=name(p1:Type1;p2:Type2):RetType{body};`
- Parameters/arguments use `;` not `,`
- Return: `<expr;` (not `return`)
- Variable (immutable): `let x=42;`
- Variable (mutable): `let x=mut.0;`
- Assignment: `x=expr;`
- Conditional: `if(cond){body};` or `if(cond){body}el{alt};`
- Loop: `lp(let i=0;i<n;i=i+1){body};` — MUST have 3 parts separated by `;` (init;cond;update)
- No `%` modulo operator. Use `a-a/b*b` instead of `a%b`.
- Break: `br;`
- Types: `i64`, `u64`, `f64`, `bool`, `Str`, `void`
- Arrays: `[T]` literal `[1;2;3]` access `arr[i]` length `arr.len` (returns `u64`)
- Array index must be `u64`: write `arr[i as u64]` if `i` is `i64`. Compare `.len` with `0 as u64`.
- Equality: `=` (not `==`). No `!=`, `<=`, `>=`, `&&`, `||`. Instead: `!(a>b)` for `a<=b`, `!(a<b)` for `a>=b`, `!(a=b)` for `a!=b`.
- For AND conditions: nest ifs — `if(a){if(b){...}}`. For OR: use a flag — `let zor=mut.false;if(a){zor=true};if(b){zor=true};if(zor){...}`
- Cast: `expr as Type`
- No comments (`//`, `#`, `/* */` all illegal)


## Type Casting Rules
- Array `.len` returns `u64`. When comparing with `i64`, cast: `i < arr.len` works because compiler auto-casts, BUT `arr.len - 1` can underflow if `u64`. Prefer: `let n=arr.len; lp(let i=0;i<n;i=i+1)`.
- When a function returns `u64` but you need `i64`: `expr as i64`.
- When indexing arrays with `i64`: `arr[i as u64]` or just `arr[i]` (auto-cast).
- `0` is `i64`, `0 as u64` is `u64`. Mutable init must match usage: if compared with `.len`, use `let i=mut.0 as u64;`.
- Error union types: the return type `T!ErrType` means the function can return EITHER a `T` value OR an `ErrType` error. The `!` separates success type from error type.

## Common Mistakes to AVOID
- Loop keyword is `lp`, NOT `for` or `while`
- Else keyword is `el`, NOT `else` or `elif`. For else-if: `el{if(cond){...}}`
- Boolean literals: `true`/`false` (lowercase), NOT `True`/`False`
- String type is `Str`, NOT `String` or `string`
- Return with `<`, NOT `return`
- No print/println/console — programs are pure functions
- No nested function definitions inside function bodies
- No `++`, `--`, `+=`, `-=` operators. Use `i=i+1`
- Mutable binding is `let x=mut.0;` NOT `let mut x=0;`
- `<=` is illegal — use `!(a>b)`. `>=` is illegal — use `!(a<b)`
- Use braces `{` `}`, NEVER double braces `{{` `}}`
- `el` must directly follow `}` — write `}el{` NOT `};el{` or `}; el{`
- Reassignment does NOT use `mut.` — write `x=5;` not `x=mut.5;` (only `let x=mut.5;` uses `mut.`)
- `if` is NOT an expression — you CANNOT write `let x=if(cond){a}el{b}`. Instead use: `let x=mut.0; if(cond){x=a}el{x=b};`
- No underscore `_` in identifiers — `_idx`, `_tmp`, `_` are all illegal. Use `idx`, `tmp`, `zz` instead.
- Map iteration: access keys/values through map indexing `m[i as u64]`, NOT tuple field syntax like `entry.0`
- Match arms are expressions, not blocks: `result|{Ok:v v;Err:e 0}` — no `{block}` inside arms
