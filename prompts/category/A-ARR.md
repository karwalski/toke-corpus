## Arrays — Key Syntax

- Type: `[i64]`, `[Str]`, `[f64]`
- Literal: `[1;2;3]` (semicolons, not commas)
- Empty: `[]`
- Access: `arr[i]`
- Length: `arr.len`
- Concatenate: `arr+[newElem]`

## Working Examples

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

```
M=arrindexof;
F=arrIndexOf(arr:[u64];val:u64):i64{
  let i=mut.0;
  lp(let i=0;i<arr.len;i=i+1){
    if(arr[i]=val){<i}
  };
  <-1
};
```

```
M=arrrev;
F=arrReverse(arr:[f64]):[f64]{
  let n=arr.len;
  let result=mut.[];
  lp(let i=0;i<n;i=i+1){
    result=result+[arr[n-1-i]];
  };
  <result
};
```
