# toke Phase 2 Specification Reference

toke is a statically typed, compiled language. File extension: `.tk`. No comments. No implicit coercions. One canonical form per construct.

## Character Set (56 chars)

Lowercase `a-z` (26), digits `0-9` (10), symbols `( ) { } = : . ; + - * / < > ! | $ @` (18), reserved `^ ~` (2). No uppercase letters in source. No `_` in identifiers. Whitespace is non-structural.

## Keywords (12)

| Keyword | Role |
|---------|------|
| `f` | function declaration (Phase 2 lowercase) |
| `t` | type declaration |
| `i` | import declaration |
| `m` | module declaration |
| `if` | conditional branch |
| `el` | else branch |
| `lp` | loop (only loop construct) |
| `br` | break from innermost loop |
| `let` | immutable binding |
| `mut` | mutable qualifier |
| `as` | type cast |
| `rt` | return (long form; `<` is short form) |

`true` and `false` are predefined identifiers, not keywords.

## Type Sigils (Phase 2)

Phase 2 prefixes all type names with `$` and lowercases them.

| Phase 1 | Phase 2 | Description |
|---------|---------|-------------|
| `i64` | `i64` | signed 64-bit integer |
| `u64` | `u64` | unsigned 64-bit integer |
| `f64` | `f64` | IEEE 754 64-bit float |
| `bool` | `bool` | boolean |
| `Str` | `$str` | UTF-8 string |
| `void` | `$void` | no return value |
| `[T]` | `@(T)` | array of T |
| `User` | `$user` | user-defined type |

Other numeric types: `i8`, `i16`, `i32`, `u8`, `u16`, `u32`, `f32`, `$byte`.

## Operators (precedence low to high)

1. Match: `expr\|{arms}`
2. Comparison: `<` `>` `=` (equality, NOT `==`)
3. Additive: `+` `-`
4. Multiplicative: `*` `/`
5. Unary: `-expr` `!expr`
6. Cast: `expr as type`
7. Error propagation: `expr!$errvariant`
8. Call: `func(args)`
9. Field access: `expr.field`

No `!=`, `<=`, `>=`, `&&`, `||`, `%`, `++`, `--`, `+=`. Use `!(a=b)` for not-equal, `!(a>b)` for less-or-equal, nested `if` for logical AND/OR, `a-a/b*b` for modulo.

## File Structure

Declarations must appear in this order: module, imports, types, constants, functions.

```
m=mymodule;
i=http:std.http;
t=$user{id:u64;name:$str};
pi=3.14159:f64;
f=main():i64{ <0 };
```

## Module Declaration

```
m=module.path;
```

Every file starts with exactly one module declaration.

## Import Declaration

```
i=alias:module.path;
```

Access via `alias.name`. All imports before types/functions.

## Type Declaration

Struct (lowercase fields):
```
t=$point{x:f64;y:f64};
```

Sum type / error type (uppercase-initial variant names, with `$` prefix in Phase 2):
```
t=$myerr{$notfound:bool;$badinput:$str};
```

## Function Declaration

```
f=name(p1:type1;p2:type2):$returntype{ body };
```

Fallible function with error type:
```
f=getuser(id:u64):$user!$usererr{ body };
```

No body = extern FFI: `f=puts(s:*u8):$void;`

Parameters and arguments separated by `;` (not `,`).

## Bindings and Assignment

```
let x=42;           immutable
let x=mut.0;        mutable (mut. prefix after =)
x=x+1;             reassign mutable
```

No type annotations on `let`. Types inferred from context.

## Return

```
<expr;              short form (preferred)
rt expr;            long form
```

## Conditionals

```
if(cond){ body };
if(cond){ body }el{ altbody };
```

No `elif`. Nest: `if(a){...}el{if(b){...}el{...}}`. Condition must be `bool`.

## Loop

```
lp(let i=0;i<n;i=i+1){ body };
```

Three parts: init; condition; step. `br;` breaks innermost loop. No `while`, `for`, `foreach`.

## Arrays (Phase 2)

```
let a=@(1;2;3);     array literal (Phase 2 uses @() not [])
a.len               length
a.get(i)            variable index access
```

## Error Handling

Declare error type:
```
t=$myerr{$divzero:bool;$overflow:$str};
```

Fallible return signature:
```
f=divide(a:f64;b:f64):f64!$myerr{ ... };
```

Propagate errors:
```
let val=fallible()!$myerr.$dberr;
```

Match on result:
```
result|{
  $ok:v v;
  $err:e defaultval
}
```

Match arms are expressions, not blocks. Exhaustive matching required.

## Arena Blocks

```
{arena
  ...allocations freed on scope exit...
}
```

## Common Mistakes

1. No comments (`//`, `#`, `/* */` are illegal)
2. `;` between params/args/array elements, NOT `,`
3. Return is `<`, not `return`
4. Equality is `=`, not `==`
5. Else is `el`, not `else`
6. Loop is `lp`, not `for`/`while`
7. String type is `$str`, not `string`/`String`
8. Mutable: `let x=mut.0`, not `let mut x=0`
9. No `_` in identifiers
10. `if` is NOT an expression; cannot assign from it
11. Phase 2: types prefixed with `$`, arrays use `@()`

## Complete Examples

### Factorial
```
m=fact;
f=factorial(n:i64):i64{
  if(n<2){<1};
  <n*factorial(n-1)
};
```

### Sum array
```
m=arrsum;
f=sum(arr:@(i64)):i64{
  let total=mut.0;
  lp(let i=0;i<arr.len;i=i+1){
    total=total+arr.get(i)
  };
  <total
};
```

### Error handling
```
m=safediv;
t=$matherr{$divzero:bool};
f=divide(a:f64;b:f64):f64!$matherr{
  if(b=0.0){<$matherr{$divzero:true}};
  <a/b
};
f=safe(a:f64;b:f64):f64{
  let r=divide(a;b);
  r|{$ok:v v;$err:e 0.0}
};
```

### Struct usage
```
m=geo;
t=$point{x:f64;y:f64};
f=dist(a:$point;b:$point):f64{
  let dx=b.x-a.x;
  let dy=b.y-a.y;
  <dx*dx+dy*dy
};
```
