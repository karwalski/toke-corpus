# toke Language Reference

toke is a statically typed, compiled language designed for LLM code generation. It compiles to native binaries. There are no comments, no implicit coercions, and exactly one syntactic form for each construct.

## Character Set

80 ASCII characters. No Unicode identifiers. No comments (`//`, `/* */`, `#` are all illegal).

## Module Structure

Every file starts with `M=name;`. Declarations must appear in order: module, imports, types, constants, functions.

```
M=name;
I=alias:path.to.mod;
T=TypeName{field1:Type1;field2:Type2};
PI=3.14159:f64;
F=funcName(p1:Type1;p2:Type2):RetType{body};
```

## Types

Primitives: `i64`, `u64`, `f64`, `bool`, `Str`, `void`
Arrays: `[T]` — literal: `[1;2;3]` (semicolons, not commas)
Maps: `[K:V]` — literal: `["a":1;"b":2]`
Error unions: `T!ErrType`
Structs: `T=Point{x:i64;y:i64};`
Pointers: `*T` (FFI extern declarations only)

## Functions

```
F=name(param1:Type1;param2:Type2):ReturnType{body};
```

Parameters separated by `;` (not `,`).
Function call arguments also separated by `;`: `add(a;b)`.
No body = extern FFI declaration: `F=puts(s:*u8):void;`

## Statements

Variable binding (immutable): `let x=42;`
Variable binding (mutable): `let x=mut.42;`
Assignment: `x=expr;`
Return: `<expr;` (the `<` character, not the word `return`)
Break: `br;`

## Conditionals

```
if(condition){body};
if(condition){body}el{altBody};
```

The keyword is `if`. The else keyword is `el` (two characters). There is no `elif` — nest `if` inside `el`.

## Loops

```
lp(init;condition;step){body};
```

One loop construct. No `while`, `for`, or `foreach`. Example:

```
lp(let i=0;i<n;i=i+1){body};
```

## Match

Pattern match on values using pipe-brace syntax:

```
expr|{
  Pattern1:binding1 body1;
  Pattern2:binding2 body2
}
```

For error results: `expr|{Ok:v handleV;Err:e handleE}`

## Error Handling

Error types are sum types: `T=MyErr{Variant1:bool;Variant2:Str};`
Return type declares failure: `F=f():T!MyErr{...};`
Propagate with `!`: `let val=fallibleCall()!MyErr;`
Return an error: `<MyErr{Variant1:true};`
Match for recovery: `result|{Ok:v v;Err:e defaultVal}`

## Operators

Arithmetic: `+`, `-`, `*`, `/`
Comparison: `<`, `>`, `=` (equality). There is NO `!=`, `<=`, or `>=` operator.
Logical: There are NO `&&` or `||` operators. Use nested `if` for compound conditions.
Cast: `expr as Type`
Field access: `expr.field`
Error propagation: `expr!ErrType`
Unary negation: `-expr`

Note: `=` is equality in expressions and binding in declarations (context-dependent).

## Examples

### Example 1 — Absolute value

```
M=abs;
F=abs(x:i64):i64{
  if(x<0){< -x};
  <x
};
```

### Example 2 — Sum an array

```
M=arrsum;
F=sum(arr:[i64]):i64{
  let total=mut.0;
  lp(let i=0;i<arr.len;i=i+1){
    total=total+arr[i];
  };
  <total
};
```

### Example 3 — Factorial

```
M=fact;
F=factorial(n:i64):i64{
  if(n<2){<1};
  <n*factorial(n-1)
};
```

### Example 4 — Fibonacci

```
M=fib;
F=fib(n:i64):i64{
  if(n<2){<n};
  <fib(n-1)+fib(n-2)
};
```

### Example 5 — Safe division with error handling

```
M=safediv;
T=MathErr{DivByZero:bool};
F=divide(a:f64;b:f64):f64!MathErr{
  if(b=0.0){<MathErr{DivByZero:true}};
  <a/b
};
F=safeMath(a:f64;b:f64):f64{
  let r=divide(a;b);
  r|{
    Ok:v v;
    Err:e 0.0
  }
};
```

### Example 6 — Find maximum in array

```
M=findmax;
F=max(arr:[i64]):i64{
  let m=mut.arr[0];
  lp(let i=1;i<arr.len;i=i+1){
    if(arr[i]>m){m=arr[i]};
  };
  <m
};
```

### Example 7 — Struct usage

```
M=geo;
T=Point{x:f64;y:f64};
F=dist(a:Point;b:Point):f64{
  let dx=b.x-a.x;
  let dy=b.y-a.y;
  <(dx*dx+dy*dy)
};
```



### Example 8 — String length check

```
M=strlen;
F=isEmpty(s:Str):bool{
  <s.len=0 as u64
};
```

### Example 9 — Array reversal

```
M=rev;
F=reverse(arr:[i64]):[i64]{
  let result=mut.[];
  let n=arr.len;
  lp(let i=0;i<n;i=i+1){
    let idx=n-1-i;
    result=result+[arr[idx]];
  };
  <result
};
```

### Example 10 — Nested conditionals (no && or ||)

```
M=range;
F=inRange(x:i64;lo:i64;hi:i64):bool{
  let r=mut.false;
  if(!(x<lo)){if(!(x>hi)){r=true}};
  <r
};
```

### Example 11 — Modulo without % operator

```
M=modop;
F=isEven(n:i64):bool{
  let rem=n-n/2*2;
  <rem=0
};
```

### Example 12 — Sorting (bubble sort)

```
M=bsort;
F=sort(arr:[i64]):[i64]{
  let a=mut.arr;
  let n=a.len;
  lp(let i=0;i<n;i=i+1){
    lp(let j=0;j<n-1-i;j=j+1){
      if(a[j]>a[j+1]){
        let tmp=a[j];
        a[j]=a[j+1];
        a[j+1]=tmp;
      };
    };
  };
  <a
};
```

### Example 13 — String building character by character

```
M=repeat;
F=repeatStr(s:Str;n:i64):Str{
  let result=mut."";
  lp(let i=0;i<n;i=i+1){
    result=result+s;
  };
  <result
};
```

### Example 14 — Error handling with match

```
M=lookup;
T=LookupErr{NotFound:bool};
F=findIdx(arr:[i64];val:i64):i64!LookupErr{
  lp(let i=0;i<arr.len;i=i+1){
    if(arr[i]=val){<i}
  };
  <LookupErr{NotFound:true}
};
F=findOrDefault(arr:[i64];val:i64;default:i64):i64{
  let r=findIdx(arr;val);
  r|{
    Ok:v v;
    Err:e default
  }
};
```

## Common Mistakes

1. **No comments.** Do not write `//`, `/* */`, or `#`. There is no comment syntax.
2. **Semicolons between parameters, not commas.** `F=f(a:i64;b:i64)` not `F=f(a:i64,b:i64)`.
3. **Semicolons between arguments, not commas.** `f(x;y)` not `f(x,y)`.
4. **Semicolons between array elements.** `[1;2;3]` not `[1,2,3]`.
5. **Return is `<`, not `return`.** Write `<42` not `return 42`.
6. **String type is `Str`**, not `string`, `String`, or `str`.
7. **Else is `el`**, not `else`. Write `if(c){a}el{b}`.
8. **No `while` or `for`.** Use `lp(init;cond;step){body}`.
9. **Module declaration is required.** Every file starts with `M=name;`.
10. **Equality is `=` in expressions.** Write `if(x=0)` not `if(x==0)`.
11. **No snake_case keywords.** All keywords are short: `if`, `el`, `lp`, `br`, `let`, `mut`, `as`, `rt`.
12. **Mutable bindings use `let x=mut.expr`**, not `mut x=expr`. The `mut.` prefix goes after `=`.
13. **No type annotations on let bindings.** Write `let x=42` not `let x:i64=42`. Types are inferred.
14. **Loop init uses `let`.** `lp(let i=0;i<n;i=i+1)` not `lp(mut i:i64=0;...)`.
15. **No `!=`, `<=`, `>=`, `&&`, or `||` operators.** Only `<`, `>`, `=` for comparison. Use nested `if` for compound conditions.
16. **Constants have no `C=` prefix.** Write `PI=3.14159:f64;` not `C=PI 3.14159;`.
