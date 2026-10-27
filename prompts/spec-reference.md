# toke specification reference (v0.4)

toke is a statically typed, compiled language designed for LLM code generation.
Compiler: `tkc`. File extension: `.tk`. No implicit coercions. One canonical form
per construct. This page condenses the normative spec
(`toke/docs/spec/toke-spec-v0.4.md`, which amends `toke-spec-v0.3.md`) for use as
generation context; `regen/syntax_card.md` is the fuller, compiler-verified card.
Every code block here is compiled by `regen/check_card.py` in
`regen/tests/test_spec_reference.py`, so it cannot drift from the compiler again.

## Character set (59 chars)

Lowercase `a-z` (26), digits `0-9` (10) and 23 symbols: `( ) { } = : . ; + - * / < > ! | " $ @ % & ^ ~`.
Whitespace separates tokens and is not one of the 59. No uppercase letters in source
(E2002). No `_` in identifiers (E1003). No comma, `?`, square brackets, backtick,
apostrophe, or `\` outside a string literal. `^` and `~` are live operators (bitwise
XOR and NOT, story 114.8), not reserved; `%` is modulo; `&` is bitwise-and and `&&`
short-circuit-and. Identifiers are lowercase alphanumeric: `maxval`, `idx2`, `isvalid`.
Derived from `src/lexer.c` — see `toke/docs/metrics-baseline.md` (spec v0.4 §G).

## Keywords (14)

| Keyword | Role |
|---------|------|
| `m` | module declaration |
| `i` | import declaration |
| `t` | type declaration |
| `f` | function declaration |
| `let` | binding |
| `mut` | mutable binding (`let x=mut.0`) |
| `if` | conditional (statement or expression) |
| `el` | else branch; `el if` chains |
| `lp` | loop (the only loop construct) |
| `br` | break from the innermost loop |
| `rt` | return (long form; `<` is the short form) |
| `as` | cast |
| `mt` | match |
| `sc` | structured-concurrency scope |

`true` and `false` are predefined identifiers, not keywords. The grammar is
backtrack-free with bounded lookahead of up to 3 tokens (spec v0.4 §E).

## Comments

The comment form is `(* ... *)`; comments nest (spec v0.4 §H). `//` and `#` are
not comments. Generated programs carry no comments at all: write none.

## Types

- Scalars: `i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 bool str void`
- User structs: `t=$point{x:f64;y:f64};` (type names `$`-prefixed, lowercase)
- Sum and error types: every variant `$`-prefixed and lowercase: `t=$shape{$circle:f64;$square:f64};`
- Arrays: `@i64`, `@str`, `@$point`; literals `@(1;2;3)`, empty `@()`
- Maps: `@(str:i64)`; literals `@("a":1;"b":2)`
- Error unions: `i64!$matherr` (function return types)

Task text may show legacy spellings (`$str`, `@(i64)`, `NotFound`); always write
`str`, `@i64`, `$notfound`.

## Operators

- `=` is binding and assignment only, never comparison (spec v0.4 §B).
- Comparison: `==` `!=` `<` `>` `<=` `>=`.
- Logic: `&&` `||` (short-circuit, `||` binds looser than `&&`, both looser than comparison) and unary `!`.
- Arithmetic: `+ - * / %` (numeric only; `+` also concatenates two arrays). Unary `-`.
- Bitwise: `& | ^ ~ << >>`.
- Cast: `x as f64`. Error propagation: `expr!$errtype`.
- Postfix: call `f(a;b)`, field `p.x`, method `xs.len`; index with `.get`, never brackets.

## File structure

Declarations appear in this order: module, imports, types, functions (E2001
otherwise). Every file starts with exactly one `m=` line.

```
m=geom;
i=io:std.io;
t=$point{x:f64;y:f64};
f=norm2(p:$point):f64{<p.x*p.x+p.y*p.y};
f=main():i64{
  io.println("\(norm2($point{x:3.0;y:4.0}))");
  <0
};
```

The grammar also has module constants (`name=literal:type;`, after the types),
but such a declaration passes `--check` and then fails to build in tkc 3.1.0
(story 127.310): do not write module constants; use a literal or a function.

Imports are `i=alias:std.module;` and are used as `alias.name(...)`.

## Functions

`f=name(p1:type1;p2:type2):rettype{ body };` — parameters and arguments are
separated by `;`, never `,`. A fallible function returns `T!$errtype`. A declaration
with no body is an extern: `f=puts(s:*u8):void;`

```
m=fns;
f=add(a:i64;b:i64):i64{<a+b};
f=main():i64{<add(1;2)-3};
```

## Bindings and assignment

```
m=binds;
f=main():i64{
  let x=42;
  let n=mut.0;
  n=n+x;
  let xs=mut.@(1;2);
  xs=xs.append(3);
  <n-42+xs.len-3
};
```

`let` is immutable; `let n=mut.0;` is mutable and is the only form that can be
reassigned. Bindings are block-scoped (spec v0.4 §I). Types are inferred; omit
annotations.

## Return

`<expr` is the return (preferred); `rt expr` is the long form. The last statement
before a closing `}` may omit its `;`.

## Conditionals — `if` is an expression

```
m=conds;
f=sign(n:i64):i64{
  <if(n<0){-1}el if(n>0){1}el{0}
};
f=main():i64{
  let g=if(sign(5)==1){10}el{20};
  if(g!=10){<1};
  <0
};
```

An expression-`if` requires an `el` branch; both branches yield the same type.
Chain with `el if`; never nest `el{if(...)}`. Never simulate an `if` with a mut
flag: bind the expression-`if` instead.

## Loops

```
m=loops;
f=main():i64{
  let total=mut.0;
  lp(let i=0;i<10;i=i+1){
    if(i==5){br};
    total=total+i
  };
  <total-10
};
```

`lp(init;cond;step){...}` is the only loop: no `while`, `for` or `foreach`. `br`
breaks the innermost loop.

## Match

```
m=matches;
i=s:std.str;
f=main():i64{
  let v=mt s.toint("12") {$ok:n n;$err:e 0};
  <v-12
};
```

`mt expr {$variant:binding expr;...}`. Each arm body is a single expression; all
arms yield the same type unless an arm returns with `<`. Matching is exhaustive.

## Error handling

```
m=safediv;
t=$matherr{$divzero:bool;$overflow:bool};
f=divide(a:i64;b:i64):i64!$matherr{
  if(b==0){<$matherr{$divzero:true}};
  <a/b
};
f=twice(a:i64;b:i64):i64!$matherr{
  let q=divide(a;b)!$matherr;
  <q*2
};
f=main():i64{
  <mt twice(10;5) {$ok:v v-4;$err:e 1}
};
```

`expr!$errtype` propagates the error and is valid only inside a function that
itself returns `T!$errtype`.

## Arrays and maps

```
m=colls;
f=main():i64{
  let a=mut.@(3;1;2);
  a=a.set(0;9);
  let m=mut.@("x":1);
  m=m.set("y";2);
  <a.get(0)+a.len+m.get("y")-14
};
```

Collections have value semantics: `set` and `append` return a new collection, so
always reassign the result: `arr=arr.set(i;v)` is correct. Read with `arr.get(i)`, never
square brackets; the length is the property `arr.len`.

## Strings

```
m=strs;
i=io:std.io;
i=s:std.str;
f=main():i64{
  let n=7;
  let msg="count is \(n)";
  io.println(msg);
  <s.len(msg)-10
};
```

Build strings by interpolation `"\(expr)"`; `+` does not concatenate strings
(E4031). String equality is `==`.

## Arena blocks

```
{arena
  let tmp=@(1;2;3);
}
```

Allocations inside `{arena ...}` are freed when the block exits; every function
body is an implicit arena (spec v0.3 §11.14).

## Common mistakes

1. Equality is `==`; a bare `=` in a condition is E2002.
2. `;` separates parameters, arguments and elements, never `,`.
3. Return is `<` (or `rt`), not `return`.
4. Else is `el`, not `else`; chain with `el if`.
5. Loop is `lp`, not `for` / `while`.
6. Strings are `str`, arrays `@i64`; not `$str`, `string` or `[i64]`.
7. A mutable binding puts the `mut.` prefix on the value, as above.
   `let mut x=0` is WRONG: it draws W2021 and the binding stays immutable.
8. No `_` in identifiers and no uppercase anywhere.
9. Match is `mt expr {...}`.
10. A bare `arr.set(i;v);` statement silently discards the new collection;
    always reassign: `arr=arr.set(i;v)`.
