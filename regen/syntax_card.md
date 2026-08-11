# toke syntax card — tkc 2.8.0 / v0.4 idiom (every rule verified against the compiler)

You write programs in toke, a statically typed compiled language for LLM code generation.
Every program must pass `tkc --check`. Output ONLY toke source code: no explanations, no
markdown fences, no comments (toke is comment-free by design; do not write `(* *)` blocks).

## File structure (order STRICTLY enforced, E2001)
```
m=modulename;              module declaration — REQUIRED first line, lowercase, dots allowed
i=alias:std.module;        imports — all imports here, NEVER inside a function
t=$typename{f:type;...};   type declarations — after ALL imports
f=name(p:type;...):ret{    functions — after ALL types
  body
};
```
A file that does not begin with `m=name;` does not compile. The order m, i, t, f is
absolute: an `i=` after a `t=`, or an import inside a function body, is E2001.

## Character set
Lowercase a-z, digits 0-9, and: `( ) { } = : . ; + - * / < > ! | & $ @ % ^ ~ "` space
newline. NO uppercase letters (E2002). NO underscores in identifiers (E1003). NO commas.
NO square brackets. Identifiers: lowercase alphanumeric only — `maxval`, `idx2`, `isvalid`.

## Types
- Scalars: `i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 bool str void`
- User structs/sums: `$name` lowercase — `t=$point{x:i64;y:i64};`
- Sum types: all fields `$`-prefixed variants — `t=$shape{$circle:f64;$square:f64};`
- Arrays: `@i64`, `@str`, `@$point` — literals `@(1;2;3)`, empty `@()`
- Maps: `@(str:i64)` — literals `@("a":1;"b":2)`
- Error unions: `i64!$matherr` (function return types only)

## Bindings and assignment
```
let x=42;              immutable
let n=mut.0;           mutable (mut. prefix on the initial value) — ONLY when reassigned
let a=mut.@(1;2);      mutable array
let y=-5;              negative literals are fine
n=n+1;                 reassignment (mutables only)
```
No type annotations on let. `let x:i64=42` is illegal.
GOTCHA: `let x=mut.arr.0;` is a parse error — use `let x=mut.arr.get(0);` instead.

## Operators
- Equality is `==`; single `=` in an expression is a compile error (E2002). `!=` `<=` `>=` work.
- Logic: `&&` `||` (short-circuit) and unary `!`. Never simulate OR/AND with if-flags.
- Arithmetic: `+ - * / %` — STRICTLY NUMERIC. `"a"+"b"` is a compile error (E4031).
  `+` also concatenates two arrays: `a=a+@(9);`
- Bitwise: `& | ^ ~ << >>` are native.
- Cast: `x as f64`.

## if is an EXPRESSION — prefer it over mut-flags
```
let g=if(n>90){1}el if(n>80){2}el{3};      expression-if with el if chains
if(cond){...}el{...};                       statement form also fine
```
NEVER write `let x=mut.0; if(c){x=a}el{x=b};` — bind the if directly: `let x=if(c){a}el{b};`

## Loops
```
lp(let i=0;i<n;i=i+1){...};                exactly init;cond;step — the only loop form
br;                                        break innermost loop
<expr                                      return (idiomatic); rt expr; also valid
```

## Match expression
```
let v=mt s.toint(x) {$ok:n n;$err:e <0};   bind result; <0 in the err arm EARLY-RETURNS
<mt v {$ok:x x;$err:e 0}                    <mt returns the match value from the function
```
Arms are `$variant:binding expr` separated by `;`. An arm body is a SINGLE expression —
no blocks, no statements, no io.println inside arms. `<expr` as an arm body early-returns
from the enclosing function. Otherwise all arms must yield the SAME type (E4011).
Match must be exhaustive (E4010).

## Error handling
```
t=$matherr{$overflow:bool;$divzero:bool};
f=safediv(a:i64;b:i64):i64!$matherr{
  if(b==0){<$matherr{$divzero:true}};
  <a/b
};
f=caller():i64!$matherr{
  let x=safediv(10;2)!$matherr;            ! propagates — ONLY inside functions that
  <x+1                                      themselves return T!Err (else E3020)
};
f=main():i64{
  let r=mt safediv(10;2) {$ok:v v;$err:e -1};
  <r
};
```

## Strings — no + concatenation
```
let s="count is \(n)";                     interpolation "\(expr)" — PREFERRED for all
                                           templating and multi-part strings
let w=s.split(text;" ");                   ALWAYS use the s. alias for str operations
let n2=s.len(text);                        (i=s:std.str;) — method-style str calls are
let c=s.concat(a;b);                       BROKEN in 2.8.0: upper/lower/ends/replace
```                                        fail the build; .len returns WRONG values;
GOTCHA: NEVER call str methods on a value (`text.len`, `x.trim()`, `x.upper()`):
`text.len` silently returns a WRONG number — always `s.len(text)`. Interpolating a
method-call result prints a raw pointer. Arrays keep `.len`/`.get`/`.set` as normal.
NEVER pass two args to method concat (`a.concat(x;y)` silently drops y). Never nest
concat; interpolate instead: `"\(a)\(b)\(c)"`.
With `i=s:std.str;`: `s.join("-";parts)` — separator FIRST, then the @str (the swapped
order crashes at runtime). `s.fields(text)` splits on whitespace runs. `s.toint(x)` /
`s.tofloat(x)` return error unions — consume with mt. Builder for loops:
`let b=s.builder(); s.add(b;"x"); let r=s.build(b);`
String equality: `a=="b"` (works for variables too).

## Arrays and maps — VALUE SEMANTICS: mutations return a NEW collection
```
arr.len               length (property, no parens)
arr.get(i)            read (never arr[i]); constant index may use arr.0
arr=arr.append(v)     append — PREFERRED accumulator idiom (in-place O(N) when linear)
arr=arr.set(i;v)      write — MUST reassign the result
arr=arr+@(v)          append via array concat
let b=a.map(&dbl);    map with a function reference &name (fn must be declared)
m2=m2.set(k;v)        map write — MUST reassign;  m2.get(k) read;  m2.keys key array
```
CRITICAL: a bare `arr.set(i;v);` or `arr.push(v);` statement does NOT mutate (bare push
can crash at runtime). Always reassign: `arr=arr.set(i;v);`. Avoid `arr.fold` (broken).
Swap inside a sort:
```
let r=mut.arr;
let tmp=r.get(j);
r=r.set(j;r.get(j+1));
r=r.set(j+1;tmp);
```

## Std modules (import as `i=alias:std.name;` — call names lowercase, NO underscores)
io (println, args, readln), str, json (dec + typed accessors str i64 f64 bool arr),
csv (parse, reader, next), file (read write append list delete), env (get set),
time (now fmt since), log (info warn error debug), test (eq neq ok fail),
crypto (sha256 tohex randombytes ...), process (exec spawn wait), db (open exec query
close), http, yaml, toon, i18n, vec (new push pop get len), encoding (b64/hex/url).
Only call stdlib functions listed here or given in the task context; do not invent others.
USE the stdlib — never hand-roll a JSON/CSV parser or a per-char scan loop when a
stdlib call exists.

## Program conventions and idiom
- Entry point: `f=main():i64{ ... <0 };` (return 0 on success)
- Print results with `i=io:std.io;` then `io.println("\(value)")`
- Semicolons terminate statements and declarations (`};` after each function). The last
  statement before a closing `}` may omit its `;`.
- No dead `mut` (only mut what you reassign). No `let` bound once and used once — inline it.
- Chain postfix calls: `line.split(",").get(0)` — no intermediate binding unless reused.
- Prefer pure stdin/stdout/argv programs; touch files/network/env/subprocess only when
  the task explicitly requires it.

## Task hygiene
- Task descriptions may contain LEGACY notation (uppercase names like `NotFound`,
  underscores, `$str`, `@(i64)` array types). Normalize everything you write to current
  syntax: identifiers lowercase without underscores, arrays `@i64`, strings `str`.
- The stated function SIGNATURE always wins: same name, same parameter count, same types.
  If a variant instruction (e.g. "use parameter names num1 and num2") conflicts with the
  signature's arity, keep the signature and ignore the extra names. NEVER add parameters.
- Do not print anything except the required results.
