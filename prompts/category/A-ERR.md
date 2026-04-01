## Error Handling — Key Syntax

- Error type: `T=MyErr{{Variant:bool;Other:Str}};`
- Error union return: `F=f():T!MyErr{{...}};`
- Return error: `<MyErr{{Variant:true}};`
- Propagate: `let val=call()!MyErr;`
- Match result: `result|{{Ok:v expr;Err:e expr}}`
- Match arms are EXPRESSIONS, not statements — no `<` inside arms
- Variant names start uppercase: `DivByZero`, `NotFound`

## Working Examples

```
M=safediv;
T=MathErr{{DivByZero:bool}};
F=divide(a:f64;b:f64):f64!MathErr{{
  if(b=0.0){{<MathErr{{DivByZero:true}}}};
  <a/b
}};
F=safeMath(a:f64;b:f64):f64{{
  let r=divide(a;b);
  r|{{
    Ok:v v;
    Err:e 0.0
  }}
}};
```

```
M=divOrDefault;
T=DivErr{{DivByZero:bool}};
F=divide(x:f64;y:f64):f64!DivErr{{
  if(y=0.0){{<DivErr{{DivByZero:true}}}};
  <x/y
}};
F=divOrDefault(x:f64;y:f64;default:f64):f64{{
  let result=divide(x;y);
  result|{{
    Ok:v v;
    Err:e default
  }}
}};
```
