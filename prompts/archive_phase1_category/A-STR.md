## Strings — Key Syntax

- Type: `$str` (not `string`, `String`, or `Str`)
- Length: `s.len`
- Character access: `s.get(i)` returns `u8`
- Concatenation: `s1+s2` or `s+"literal"`
- Character to string: `c as $str` (where c is u8)
- Compare chars with ASCII values: `if(c>96){if(c<123){...}}`

## Working Examples

```
M=strlen;
F=strLen(s:$str):i64{
  <s.len
};
```

```
M=reverse;
F=reverse(s:$str):$str{
  let n=s.len;
  let m=mut.n-1;
  let r=mut."";
  lp(let i=0;i<n;i=i+1){
    r=r+s.get(m);
    m=m-1;
  };
  <r
};
```

```
M=repeat;
F=repeat(s:$str;n:i64):$str{
  if(n<0){<""};
  let result=mut."";
  lp(let i=0;i<n;i=i+1){
    result=result+s
  };
  <result
};
```
