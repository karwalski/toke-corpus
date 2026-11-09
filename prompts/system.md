# toke Language Reference

toke is a statically typed, compiled language designed for LLM code generation. It compiles to native binaries. No implicit coercions, and one canonical form for each construct. This page is the phase-era generators' system prompt (main.py, trial/runner.py, scripts/regenerate.py); `regen/syntax_card.md` is the fuller, compiler-verified card. Every code block and statement here is compiled by `regen/check_card.py` in `regen/tests/test_phase_prompts.py` against the corpus compiler pin (131.113).

## Character Set

59 characters: lowercase `a-z`, digits `0-9` and 23 symbols. No uppercase letters anywhere in source (E2002). No `_` in identifiers (E1003). No commas outside string literals. Generated programs carry no comments.

## File Structure

Every file starts with exactly one `m=name;` line. Declarations appear in this order: module, imports, types, functions (E2001 otherwise).

```
m=geo;
i=io:std.io;
t=$point{x:i64;y:i64};
f=norm2(p:$point):i64{<p.x*p.x+p.y*p.y};
f=main():i64{
  io.println("\(norm2($point{x:3;y:4}))");
  <0
};
```

## Types

Scalars: `i64`, `u64`, `f64`, `bool`, `str`, `void` (also `i8`..`i32`, `u8`..`u32`, `f32`)
Arrays: `@i64` — literal: `@(1;2;3)` (semicolons, not commas), empty `@()`
Maps: `@(str:i64)` — literal: `@("a":1;"b":2)`
Error unions: `i64!$matherr` (function return types)
Structs: `t=$point{x:i64;y:i64};` — type names are `$`-prefixed and lowercase
Sum and error types: every variant `$`-prefixed: `t=$shape{$circle:f64;$square:f64};`

## Functions

`f=name(p1:type1;p2:type2):rettype{body};` — parameters and arguments are separated by `;`, never `,`: `add(a;b)`. A declaration with no body is an extern: `f=puts(s:*u8):void;`

## Statements

```
m=stmts;
f=main():i64{
  let x=42;
  let n=mut.0;
  n=n+x;
  lp(let i=0;i<10;i=i+1){
    if(i==3){br};
    n=n+1
  };
  <n-45
};
```

`let` is immutable; `let n=mut.0;` is mutable and is the only binding that can be reassigned. Return is `<expr` (or the long form `rt expr`). `br` breaks the innermost loop.

## Conditionals

`if` is an expression: bind or return it directly. Chain with `el if`.

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

An expression-`if` requires an `el` branch, and both branches yield the same type.

## Loops

`lp(init;cond;step){body};` is the only loop: there is no `while`, `for` or `foreach`.

## Match

```
m=matches;
i=s:std.str;
f=main():i64{
  let v=mt s.toint("12") {$ok:n n;$err:e 0};
  <v-12
};
```

`mt expr {$variant:binding expr;...}`. Each arm body is a single expression; all arms yield the same type unless an arm returns with `<`. Matching is exhaustive.

## Error Handling

```
m=errs;
t=$matherr{$divzero:bool};
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

`expr!$errtype` propagates the error and is valid only inside a function that itself returns `T!$errtype`.

## Operators

Arithmetic: `+`, `-`, `*`, `/`, `%` (numeric only; `+` also concatenates two arrays)
Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=`. A single `=` is binding and assignment only.
Logic: `&&`, `||` (short-circuit) and unary `!`
Cast: `expr as type`. Field access: `p.x`. Error propagation: `expr!$errtype`.
Strings are built by interpolation `"\(expr)"`; `+` does not concatenate strings (E4031).

## Examples

### Example 1 — Absolute value

```
m=absval;
f=abs(x:i64):i64{
  <if(x<0){-x}el{x}
};
f=main():i64{<abs(-3)-3};
```

### Example 2 — Sum an array

```
m=arrsum;
f=sum(arr:@i64):i64{
  let total=mut.0;
  lp(let i=0;i<arr.len;i=i+1){
    total=total+arr.get(i)
  };
  <total
};
f=main():i64{<sum(@(1;2;3))-6};
```

### Example 3 — Factorial

```
m=fact;
f=factorial(n:i64):i64{
  if(n<2){<1};
  <n*factorial(n-1)
};
f=main():i64{<factorial(5)-120};
```

### Example 4 — Fibonacci

```
m=fib;
f=fib(n:i64):i64{
  if(n<2){<n};
  <fib(n-1)+fib(n-2)
};
f=main():i64{<fib(10)-55};
```

### Example 5 — Safe division with error handling

```
m=safediv;
t=$matherr{$divzero:bool};
f=divide(a:f64;b:f64):f64!$matherr{
  if(b==0.0){<$matherr{$divzero:true}};
  <a/b
};
f=safemath(a:f64;b:f64):f64{
  <mt divide(a;b) {$ok:v v;$err:e 0.0}
};
f=main():i64{<if(safemath(1.0;0.0)==0.0){0}el{1}};
```

### Example 6 — Find maximum in array

```
m=findmax;
f=max(arr:@i64):i64{
  let m=mut.arr.get(0);
  lp(let i=1;i<arr.len;i=i+1){
    if(arr.get(i)>m){m=arr.get(i)}
  };
  <m
};
f=main():i64{<max(@(3;9;2))-9};
```

### Example 7 — Struct usage

```
m=geo;
t=$point{x:f64;y:f64};
f=dist2(a:$point;b:$point):f64{
  let dx=b.x-a.x;
  let dy=b.y-a.y;
  <dx*dx+dy*dy
};
f=main():i64{
  let d=dist2($point{x:0.0;y:0.0};$point{x:3.0;y:4.0});
  <if(d==25.0){0}el{1}
};
```

### Example 8 — String length check

```
m=strlen;
f=isempty(text:str):bool{
  <text.len==0
};
f=main():i64{<if(isempty("")){0}el{1}};
```

### Example 9 — Array reversal

```
m=rev;
f=reverse(arr:@i64):@i64{
  let out=mut.@();
  let n=arr.len;
  lp(let i=0;i<n;i=i+1){
    out=out.append(arr.get(n-1-i))
  };
  <out
};
f=main():i64{<reverse(@(1;2;3)).get(0)-3};
```

### Example 10 — Compound conditions

```
m=range;
f=inrange(x:i64;lo:i64;hi:i64):bool{
  <x>=lo&&x<=hi
};
f=main():i64{<if(inrange(5;1;9)&&!inrange(0;1;9)){0}el{1}};
```

### Example 11 — Modulo

```
m=modop;
f=iseven(n:i64):bool{
  <n%2==0
};
f=main():i64{<if(iseven(4)){0}el{1}};
```

### Example 12 — Sorting (bubble sort)

```
m=bsort;
f=sort(arr:@i64):@i64{
  let a=mut.arr;
  let n=a.len;
  lp(let i=0;i<n;i=i+1){
    lp(let j=0;j<n-1-i;j=j+1){
      if(a.get(j)>a.get(j+1)){
        let tmp=a.get(j);
        a=a.set(j;a.get(j+1));
        a=a.set(j+1;tmp)
      }
    }
  };
  <a
};
f=main():i64{<sort(@(3;1;2)).get(0)-1};
```

### Example 13 — String building

```
m=repeat;
f=repeatstr(text:str;n:i64):str{
  let out=mut."";
  lp(let i=0;i<n;i=i+1){
    out="\(out)\(text)"
  };
  <out
};
f=main():i64{<repeatstr("ab";3).len-6};
```

### Example 14 — Error handling with match

```
m=lookup;
t=$lookuperr{$notfound:bool};
f=findidx(arr:@i64;val:i64):i64!$lookuperr{
  lp(let i=0;i<arr.len;i=i+1){
    if(arr.get(i)==val){<i}
  };
  <$lookuperr{$notfound:true}
};
f=findordefault(arr:@i64;val:i64;dflt:i64):i64{
  <mt findidx(arr;val) {$ok:v v;$err:e dflt}
};
f=main():i64{<findordefault(@(4;5);5;-1)-1};
```

## Common Mistakes

1. **Equality is `==`.** A bare `=` in a condition is a compile error (E2002).
2. **Semicolons separate parameters, arguments and array elements**, never commas.
3. **Return is `<`**, not `return`.
4. **Else is `el`**, not `else`; chain with `el if`.
5. **No `while` or `for`.** The only loop is `lp(init;cond;step){body}`.
6. **Strings are `str`, arrays `@i64`**, not `$str`, `string` or `[i64]`.
7. **Mutable bindings put `mut.` on the value**: `let x=mut.0;`.
8. **No uppercase and no `_`** in identifiers or keywords. Type names are `$`-prefixed: `$point`.
9. **Collections have value semantics.** `set` and `append` return a new collection, so always reassign: `arr=arr.set(i;v);` is the correct form.
10. **A map lookup is `V!$err`**: read it with `let v=mt m.get(k) {$ok:x x;$err:e 0};` or with `let v=m.getor(k;0);`.
11. **No string `+`.** Build strings with interpolation `"\(a)\(b)"`.
