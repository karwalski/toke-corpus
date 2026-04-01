## Conditionals — Key Syntax

- `if(cond){body};` — no parentheses around body, semicolon after closing brace
- `if(cond){body}el{alt};` — else is `el`, not `else`
- No `elif` — nest `if` inside `el`: `if(a){x}el{if(b){y}el{z}};`
- No `&&` or `||` — use nested `if` for compound conditions

## Working Examples

```
M=sign;
F=describeSign(x:f64):Str{
  if(x>0.0){<"positive"};
  if(x<0.0){<"negative"};
  <"zero"
};
```

```
M=isgreq;
F=isGreaterOrEqual(a:u64;b:u64):bool{
  if(a>b){<true};
  if(a=b){<true};
  <false
};
```

```
M=clamp;
F=clamp(x:i64;lo:i64;hi:i64):i64{
  if(x<lo){<lo};
  if(x>hi){<hi};
  <x
};
```
