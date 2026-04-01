## Strings — Key Syntax

- Type: `Str` (not `string` or `String`)
- Length: `s.len`
- Character access: `s[i]` returns `u8`
- Concatenation: `s1+s2` or `s+"literal"`
- Character to string: `c as Str` (where c is u8)
- Compare chars with ASCII values: `if(c>96){if(c<123){...}}`

## Working Examples

```
M=strlen;
F=strLen(s:Str):i64{
  <s.len
};
```

```
M=reverse;
F=reverse(s:Str):Str{
  let n=s.len;
  let m=mut.n-1;
  let r=mut."";
  lp(let i=0;i<n;i=i+1){
    r=r+s[m];
    m=m-1;
  };
  <r
};
```

```
M=repeat;
F=repeat(s:Str;n:i64):Str{
  if(n<0){<""};
  let result=mut."";
  lp(let i=0;i<n;i=i+1){
    result=result+s
  };
  <result
};
```
