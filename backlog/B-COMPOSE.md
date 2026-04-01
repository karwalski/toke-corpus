# B-COMPOSE: Composed Multi-Function Programs from Phase A Entries

**Phase:** B
**Priority:** Medium
**Depends on:** Phase A corpus completion (50K single-function programs)

## Summary

Mechanically compose pairs of accepted Phase A single-function programs into two-step programs that call one function with the result of another. This generates multi-function corpus entries at zero LLM cost, teaching the model function composition and inter-function calling patterns.

## Approach

1. Select compatible function pairs from Phase A corpus (output type of F1 matches input type of F2)
2. Generate composed toke programs: `F=composed(...):RetType{ let intermediate=f1(...); <f2(intermediate) };`
3. Generate matching Python/C/Java reference implementations mechanically
4. Validate through existing pipeline (tkc + differential testing)
5. No LLM generation needed — pure mechanical composition

## Examples

Given accepted entries:
- `F=sum(arr:[i64]):i64` — sums an array
- `F=abs(x:i64):i64` — absolute value

Compose into:
```
M=absSum;
F=sum(arr:[i64]):i64{
  let total=mut.0;
  lp(let i=0;i<arr.len;i=i+1){total=total+arr[i]};
  <total
};
F=abs(x:i64):i64{
  if(x<0){<0-x}el{<x}
};
F=absSum(arr:[i64]):i64{
  <abs(sum(arr))
};
```

## Acceptance Criteria

- [ ] Type-compatible pair detection working
- [ ] Mechanical composition generates valid toke
- [ ] Reference implementations generated for Python/C/Java
- [ ] All composed programs pass tkc --check
- [ ] Differential testing passes
- [ ] At least 5,000 composed entries generated

## Notes

- This is an augmentation strategy — supplements LLM-generated corpus, not a replacement
- Pairs should be semantically meaningful (not random combinations)
- Category combinations to prioritize: MTH+MTH, ARR+MTH, STR+STR, ARR+SRT
