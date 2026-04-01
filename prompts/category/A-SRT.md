## Sorting/Searching — Key Patterns

- No slice syntax — copy elements one at a time with loop
- Swap pattern: `let tmp=arr[i]; arr[i]=arr[j]; arr[j]=tmp;`
- Build new array: `let result=mut.[]; lp(...){result=result+[elem]};`

## Working Examples

```
M=sorted;
F=isSorted(arr:[i64]):bool{
  lp(let i=0;i+1<arr.len;i=i+1){
    if(arr[i]>arr[i+1]){<false};
  };
  <true
};
```

```
M=findmax;
F=findMaxIndex(arr:[f64]):i64{
  let maxIdx=mut.0;
  let maxVal=mut.arr[0];
  lp(let i=0;i<arr.len;i=i+1){
    let curr=arr[i];
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
F=linearSearch(arr:[i64];val:i64):i64{
  lp(let i=0;i<arr.len;i=i+1){
    if(arr[i]=val){<i};
  };
  <-1
};
```
