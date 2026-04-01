## Working Examples

```
M=addmod;
F=add(a:i64;b:i64):i64{<a+b};
```

```
M=minfunc;
F=min(n:i64;m:i64):i64{
  let diff=n-m;
  if(diff<0){<n};
  <m
};
```

```
M=iseven;
F=isEven(x:i64):bool{
  let r=x/2;
  if(r*2=x){<true};
  <false
};
```
