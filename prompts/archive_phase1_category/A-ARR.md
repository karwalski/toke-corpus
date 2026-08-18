## Arrays — Key Syntax

- Type: `@(i64)`, `@($str)`, `@(f64)`
- Literal: `@(1;2;3)` (semicolons, not commas)
- Empty: `@()`
- Access: `arr.get(i)`
- Length: `arr.len`
- Concatenate: `arr+@(newElem)`

## Working Examples

```
M=arrsum;
F=sum(arr:@(i64)):i64{
  let total=mut.0;
  lp(let i=0;i<arr.len;i=i+1){
    total=total+arr.get(i);
  };
  <total
};
```

```
M=arrindexof;
F=arrIndexOf(arr:@(u64);val:u64):i64{
  let i=mut.0;
  lp(let i=0;i<arr.len;i=i+1){
    if(arr.get(i)=val){<i}
  };
  <-1
};
```

```
M=arrrev;
F=arrReverse(arr:@(f64)):@(f64){
  let n=arr.len;
  let result=mut.@();
  lp(let i=0;i<n;i=i+1){
    result=result+@(arr.get(n-1-i));
  };
  <result
};
```
