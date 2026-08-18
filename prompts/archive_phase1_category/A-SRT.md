## Sorting/Searching — Key Patterns

- No slice syntax — copy elements one at a time with loop
- Swap pattern: `let tmp=arr.get(i); arr.set(i;arr.get(j)); arr.set(j;tmp);`
- Build new array: `let result=mut.@(); lp(...){result=result+@(elem)};`

## Working Examples

```
M=sorted;
F=isSorted(arr:@(i64)):bool{
  lp(let i=0;i+1<arr.len;i=i+1){
    if(arr.get(i)>arr.get(i+1)){<false};
  };
  <true
};
```

```
M=findmax;
F=findMaxIndex(arr:@(f64)):i64{
  let maxIdx=mut.0;
  let maxVal=mut.arr.get(0);
  lp(let i=0;i<arr.len;i=i+1){
    let curr=arr.get(i);
    if(curr>maxVal){
      maxVal=curr;
      maxIdx=i;
    };
  };
  <maxIdx
};
```

```
M=search;
F=linearSearch(arr:@(i64);val:i64):i64{
  lp(let i=0;i<arr.len;i=i+1){
    if(arr.get(i)=val){<i};
  };
  <-1
};
```
