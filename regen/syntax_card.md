# toke syntax card — tkc 3.1.0 / v0.4 idiom (every rule verified against the compiler)

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

## Character set — 59 characters, 14 keywords
26 lowercase letters a-z, 10 digits 0-9 and 23 symbols: `( ) { } = : . ; + - * / < > ! | & $ @ % ^ ~ "`
(whitespace separates tokens and is not one of the 59). NO uppercase letters (E2002).
NO underscores in identifiers (E1003). NO commas. NO square brackets, backtick, single
quote, or `\` outside a string literal. `^` and `~` are live operators (bitwise xor/not),
not reserved. Identifiers: lowercase alphanumeric only — `maxval`, `idx2`, `isvalid`.
The 14 keywords are: m i t f let if el lp br rt as mt sc mut.
The grammar is backtrack-free with bounded lookahead of up to 3 tokens — it is NOT
strict LL(1) (spec v0.4 §A keywords, §E grammar, §G character set).

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
`let x:i64=42;` compiles — the annotation is accepted and type-checked — but it is
never idiomatic: the checker infers every let, so an annotation is pure token cost.
Omit it. A constant index works in a binding: `let x=mut.arr.0;` compiles (127.13);
use `arr.get(i)` whenever the index is a variable.

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
Never simulate it with a mut flag — `let x=mut.0; if(c){x=a}el{x=b};` compiles but
wastes tokens; instead bind the if: `let x=if(c){a}el{b};`

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
let msg="count is \(n)";                   interpolation "\(expr)" — PREFERRED for all
                                           templating and multi-part strings
let w=s.split(text;" ");                   the s. module form is canonical for str ops
let n2=s.len(text);                        (i=s:std.str;) — it is what the pattern
let c=s.concat(a;b);                       catalogue measured
let u=text.upper();                        method-style calls also work again since
let k=text.len;                            2.8.0+ (127.6/127.7/127.8 fixed)
```
`s.fields(text)` splits on whitespace runs. `s.toint(x)` / `s.tofloat(x)` return error
unions — consume with mt. Builder for loops:
`let b=s.builder(); s.add(b;"x"); let r=s.build(b);`
String equality: `a=="b"` (works for variables too). Arrays keep `.len`/`.get`/`.set`.
Rules:
- never nest concat — interpolate: `"\(a)\(b)\(c)"`.
- separator FIRST: `s.join("-";parts)` is correct.
- a bool interpolates as `true`/`false` (127.15): `"\(b)"` — no helper needed.
- an interpolated literal nested inside another — `"\(tag("\(n)"))"` — is a compile
  error (E4073, 127.47): bind the inner string to a `let`, then interpolate that.
NO LONGER BROKEN, drop the old workarounds: `text.len` on a str (127.8), interpolating
a method-call result (127.7) or an `s.fields(text)` result (127.9), strings built by
interpolation then stored with `arr.append` (127.10), and an undeclared name inside an
interpolation, which `--check` now reports as E3011 (127.24).
Stdlib arity and argument order are now checked instead of misbehaving at run time:
`a.concat(x;y)` is a compile error (E4026; it used to drop y, 127.1).
`s.len()` with no argument is a compile error (E4026).
The swapped `s.join(parts;"-")` is a compile error (E4031; it used to SIGSEGV, 127.5).

## Arrays and maps — VALUE SEMANTICS: mutations return a NEW collection
```
arr.len               length (property, no parens)
arr.get(i)            read (never arr[i]); constant index may use arr.0
arr=arr.append(v)     append — PREFERRED accumulator idiom (in-place O(N) when linear)
arr=arr.set(i;v)      write — MUST reassign the result
arr=arr+@(v)          append via array concat
let b=a.map(&dbl);    map with a function reference &name (fn must be declared)
let c=a.filter(&p);   filter; a.fold(0;&fadd) runs now too (127.4)
let h=a.contains(v);  contains / find / indexof / slice all work (127.11 fixed)
let t=a.slice(0;n);   slice(start;len)
m2=m2.set(k;v)        map write — MUST reassign;  m2.keys key array
let x=mt m.get(k) {$ok:v v;$err:e 0};   map read — PREFERRED: m.get(k) is V!$err
let d=m.getor(k;0);   default lookup: the stored value, else the default
let g=m.contains(k);  map membership
let mi=mut.@(1:10);   int-keyed map literals work (127.20/127.35 fixed)
```
A map lookup can fail: `m.get(k)` is the error union `V!$err` (a missing key is `$err`;
a stored `0`, `false` or `""` is `$ok`). Consume it with `mt`, with `m.getor(k;0)`, or
with `!$err` inside a function returning `T!$err`. `m.get(k)+1` is a compile error (E4031),
and so is any use of a lookup as a plain value: through a let binding, or passed or
returned where the value type is expected.
Maps have value semantics like arrays: `let m2=m.set(k;v);` leaves `m` unchanged (127.55).
Rules:
- a bare `arr.set(i;v);` or `m2.set(k;v);` statement does NOT mutate: the new
  collection is discarded, and `tkc --lint` rejects it (discarded-value-result, 127.3).
  Always reassign: `arr=arr.set(i;v);`.
- expression-`if` is not accepted as an operand of `+` or a comparison (127.21):
  `let x=1+if(c){2}el{3};` is a compile error. Bind it first, then use the binding.
Swap inside a sort:
```
let r=mut.arr;
let tmp=r.get(j);
r=r.set(j;r.get(j+1));
r=r.set(j+1;tmp);
```

## Std modules (import as `i=alias:std.name;` — call names lowercase, NO underscores)
io (println, print, readln), args (count, get, all — `args.get` returns `str!$argserr`),
str, json (dec + typed accessors str i64 f64 bool arr; `json.i64`/`json.u64` read only a
whole JSON integer — `1.5`, `1e3` or a negative u64 is `$err`),
fmt (bool arr strs f64 pad — bool→"true"/"false", arr/strs join with sep, f64 fixed
decimals, pad to width),
csv (parse, reader, next), file (read write append list delete), env (get set),
time (now format since), log (info warn error debug), test (eq ok fail),
crypto (sha256 tohex randombytes ...), process (exec spawn wait), db (open exec one many
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

## Patterns — measured canonical forms (catalogue c42dcc38a16c; 46 entries, 46 provisional)
- cond: if: return per branch, never mut flag [cond-bind-if] · el if chain, never nested if/el [cond-elif-chain] · combine tests in one expr-if, no flags [cond-bool-combine]
- cond: clamp: one el if chain expression [cond-clamp] · bool text: expr-if picks the literal [cond-bool-render]
- acc: sum: lp into a mut, not reduce [acc-sum] · count-if: lp + if + counter [acc-count-if] · min: xs.sort(&cmp).get(0); big N: lp [acc-min-max]
- acc: x=x.append(v), never x=x+@(v) [acc-array] · small key set: parallel arrays + .set [acc-map-build] · dedupe: if(!out.contains(v)) append [acc-dedupe]
- str: loops: s.builder, not concat [str-build-loop] · assemble strings by interpolation [str-interp-vs-join] · int to text: n as str, not s.fromint [str-num-format]
- str: pad: lp prepending with s.concat [str-repeat-pad] · @str: build .append, render s.add [str-array-render]
- err: propagate errors with !$err, not mt [err-propagate] · error default: precondition if, not mt [err-default] · validate: sequential guard returns [err-validate-early]
- parse: json.dec + typed accessors [parse-json] · csv: s.split lines, then s.split "," [parse-csv-line] · delimited fields: s.split(line;",") [parse-delim-split]
- parse: whitespace fields: s.fields(line) [parse-fields] · parse int: mt s.toint(txt) with default [parse-int] · k=v lines: s.split twice, then m.set [parse-kv-lines]
- iter: transform: xs.map(&f), not a loop [iter-map] · select: xs.filter(&p), not a loop [iter-filter] · filter+sum: one reduce(0;&f) with if [iter-filter-sum]
- iter: find-first: lp with direct < return [iter-find-first] · count: lp counter, not filter.len [iter-count] · nested lp: return directly, no flag [iter-nested-early-exit]
- coll: map lookup default: mt m.get(k) [coll-lookup-default] · membership: map-as-set + mt get [coll-membership] · top-k: xs.sort(&cmp).slice(0;k) [coll-sort-take]
- coll: group-by: map of arrays, mt get + set [coll-group-by] · reverse: lp from the end, append [coll-reverse]
- coll: swap: chained .set(i;b).set(j;a) [coll-swap] · distinct: str-keyed map-as-set [coll-dedupe]
- cli: argv: mt args.get(1) with default [cli-argv] · drop flags: av.filter(&notflag) [cli-flag-filter] · print k=v by interpolation "\(k)=\(v)" [cli-print-results]
- fn: compound test: call a named helper [fn-helper-vs-inline] · chain postfix calls, skip interim let [fn-chain-vs-let] · recurse via expr-if; deep/hot: lp [fn-recursion-vs-loop]
- io: read lines: mt file.read, s.split "\n" [io-read-lines] · file write: builder + one file.write [io-write-accumulate]

## Task hygiene
- Task descriptions may contain LEGACY notation (uppercase names like `NotFound`,
  underscores, `$str`, `@(i64)` array types). Normalize everything you write to current
  syntax: identifiers lowercase without underscores, arrays `@i64`, strings `str`.
- The stated function SIGNATURE always wins: same name, same parameter count, same types.
  If a variant instruction (e.g. "use parameter names num1 and num2") conflicts with the
  signature's arity, keep the signature and ignore the extra names. NEVER add parameters.
- Do not print anything except the required results.
