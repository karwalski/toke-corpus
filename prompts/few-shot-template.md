# Few-Shot Prompt Template for toke Code Generation

This template shows how to include the spec reference in training and inference prompts. Replace `{placeholders}` with actual values.

---

## Template

```
<system>
You are an expert toke programmer. toke is a statically typed, compiled language designed for LLM code generation.

{spec-reference.md contents inserted here}
</system>

<user>
Write a toke program that {task_description}.

Expected signature: {expected_signature}

Here are examples of correct toke programs:

Example 1 — {example1_description}:
```
{example1_code}
```

Example 2 — {example2_description}:
```
{example2_code}
```

Output ONLY the toke source code. No explanations, no markdown fences, no comments.
</user>
```

---

## Usage Notes

1. **Always include spec-reference.md** as system context. It provides the language rules the model needs.

2. **Select 2-3 few-shot examples** that match the task category:
   - Arithmetic/math tasks: use factorial, sum array examples
   - Error handling tasks: use safediv, lookup examples
   - Data structure tasks: use struct, array reversal examples
   - String tasks: use repeatStr, isEmpty examples

3. **Example selection strategy:**
   - Pick examples that demonstrate the specific constructs needed (loops, error handling, structs)
   - Include at least one example with the same return type as the target task
   - For error handling tasks, always include an error handling example

4. **A/B evaluation protocol:**
   - Condition A (baseline): task description + rules only (no spec reference)
   - Condition B (spec-grounded): task description + spec-reference.md + 2 few-shot examples
   - Measure: Pass@1 (compiler success), Pass@1 (test success), token count

5. **For training data:** prepend spec-reference.md to the system prompt for every training example. This teaches the model to generate toke with the spec as grounding context, matching inference-time conditions.

---

## Concrete Example

```
<system>
You are an expert toke programmer. toke is a statically typed, compiled language designed for LLM code generation.

# toke Phase 2 Specification Reference
[... spec-reference.md content ...]
</system>

<user>
Write a toke program that finds the index of the first occurrence of a value in an array, returning an error if not found.

Expected signature: f=findidx(arr:@(i64);val:i64):i64!$lookuperr

Here are examples of correct toke programs:

Example 1 — Sum an array:
m=arrsum;
f=sum(arr:@(i64)):i64{
  let total=mut.0;
  lp(let i=0;i<arr.len;i=i+1){
    total=total+arr.get(i)
  };
  <total
};

Example 2 — Safe division with error handling:
m=safediv;
t=$matherr{$divzero:bool};
f=divide(a:f64;b:f64):f64!$matherr{
  if(b=0.0){<$matherr{$divzero:true}};
  <a/b
};

Output ONLY the toke source code. No explanations, no markdown fences, no comments.
</user>
```
