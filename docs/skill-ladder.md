# Toke Skill Ladder for Curriculum Learning

**Version:** 1.0
**Date:** 2026-04-04
**Story:** 9.2.1

This document defines the skill ladder used to structure training data for
curriculum learning. The model learns toke incrementally, starting from simple
expressions and progressing to full-featured programs with error handling and
standard library usage.

All syntax uses Phase 2 lowercase keywords (`m=`, `f=`, `i=`, `t=`, etc.).

---

## Stages

### Stage 1 — Expressions and Bindings

**Scope:** The model learns to produce syntactically valid toke expressions,
variable bindings, and simple return values.

**Language features:**

| Feature | Example | Notes |
|---------|---------|-------|
| Module declaration | `m=hello;` | Every file starts with `m=` |
| Integer literals | `42`, `0xFF`, `0b1010` | i64, u64 |
| Float literals | `3.14` | f64 |
| String literals | `"hello"` | Basic string values |
| Boolean literals | `true`, `false` | Bool type |
| Let bindings | `x := 10;` | Immutable by default |
| Mutable bindings | `x :~ 10;` | Mutable with `:~` |
| Arithmetic | `a + b`, `a * b`, `a - b`, `a / b` | Standard operators |
| Comparison | `a == b`, `a < b`, `a >= b` | Returns Bool |
| Logical operators | `a & b`, `a \| b`, `!a` | Boolean logic |
| Return expression | `<expr` | Explicit return with `<` |

**Example difficulty:** Single-function programs that compute a value and
return it. No branching, no user-defined types, no imports.

**Expected token count:** 15-60 tokens per sample.

**Tagging criteria:** A sample belongs to Stage 1 if it uses only the features
listed above. No `if`, no `lp`, no `f=` (beyond a single entry-point function),
no `t=`, no `i=`.

---

### Stage 2 — Functions and Types

**Scope:** The model learns to define multiple functions, use typed parameters,
declare user-defined types, and work with type sigils.

**Language features:**

| Feature | Example | Notes |
|---------|---------|-------|
| Function definition | `f=add(a:i64;b:i64):i64{<a+b};` | `f=` keyword |
| Multiple functions | Several `f=` in one module | Call between them |
| Typed parameters | `x:Str`, `n:u64` | Explicit type annotations |
| Return types | `f=foo():Bool{...}` | Declared after `)` |
| Type aliases | `t=Age:u64;` | Simple type declarations |
| Struct types | `t=Point{x:f64;y:f64};` | Product types |
| Type sigils | `:i64`, `:Str`, `:Bool` | Colon-prefixed type annotations |
| Function calls | `add(1;2)` | Semicolon-separated arguments |
| String operations | `str.len(s)`, `str.concat(a;b)` | Basic string stdlib |

**Example difficulty:** Multi-function programs with explicit types. Simple
struct construction and field access. No branching or looping.

**Expected token count:** 40-120 tokens per sample.

**Tagging criteria:** Uses `f=` (multiple functions) or `t=` (type definitions)
but no `if`/`el`, no `lp`/`br`, no `i=`, no sum types, no `match`.

---

### Stage 3 — Control Flow

**Scope:** The model learns conditionals, loops, recursion, and nested control
flow structures.

**Language features:**

| Feature | Example | Notes |
|---------|---------|-------|
| Conditional | `if cond {body}` | Basic if |
| If-else | `if cond {body} el {body}` | `el` keyword for else |
| Nested conditions | `if a {if b {...} el {...}}` | Arbitrarily nested |
| Loop | `lp {body}` | Infinite loop |
| Break | `br;` | Exit loop |
| Break with value | `br expr;` | Loop as expression |
| Recursion | `f=fib(n:u64):u64{...fib(n-1)...}` | Direct recursion |
| Nested loops | `lp {lp {br;} ...}` | Inner/outer loop control |
| Early return | `if err {<default}` | Guard clauses |
| Compound conditions | `if a & (b \| c) {...}` | Complex predicates |

**Example difficulty:** Programs with branching logic: FizzBuzz, binary search,
simple sorting, recursive algorithms (factorial, Fibonacci, GCD).

**Expected token count:** 60-200 tokens per sample.

**Tagging criteria:** Uses `if`/`el` or `lp`/`br` or recursion. Does not use
`i=`, sum types, `match`, or stdlib beyond basic operations.

---

### Stage 4 — Modules, Imports, and Sum Types

**Scope:** The model learns multi-module programs, the import system, sum types,
and pattern matching.

**Language features:**

| Feature | Example | Notes |
|---------|---------|-------|
| Import | `i=std.str;` | `i=` keyword |
| Multiple imports | `i=std.str; i=std.file;` | Several modules |
| Module resolution | `str.len(s)` | Qualified access |
| Sum types | `t=Shape:Circle{r:f64}\|Rect{w:f64;h:f64};` | `\|`-separated variants |
| Match expression | `match shape {Circle{r}=>{...} Rect{w;h}=>{...}}` | Exhaustive matching |
| Exhaustive matching | All variants must be handled | Compiler-enforced |
| Option-like types | `t=Maybe:Some{val:i64}\|None;` | User-defined optionals |
| Multi-file programs | Module A imports module B | Cross-module calls |
| Type re-export | Types visible through imports | Public type interface |

**Example difficulty:** Programs split across modules. Data modelling with sum
types. Pattern matching for dispatch. Simple data structures (linked list,
tree node).

**Expected token count:** 100-350 tokens per sample.

**Tagging criteria:** Uses `i=` or sum types or `match`. May use all Stage 1-3
features. Does not use result types for error handling or stdlib I/O.

---

### Stage 5 — Error Handling and Standard Library

**Scope:** The model learns idiomatic error handling, result types, error
propagation, and full standard library usage including I/O.

**Language features:**

| Feature | Example | Notes |
|---------|---------|-------|
| Result type | `t=Result:Ok{val:i64}\|Err{msg:Str};` | Convention for fallible ops |
| Error propagation | Match on result, propagate Err | Manual propagation pattern |
| Partial functions | Functions that return Result | Declared error types |
| File I/O | `file.read(path)`, `file.write(path;data)` | `std.file` module |
| Network I/O | `net.listen(port)`, `http.get(url)` | `std.net`, `std.http` |
| JSON handling | `json.parse(s)`, `json.encode(obj)` | `std.json` module |
| Database access | `db.query(conn;sql)` | `std.db` module |
| String formatting | `str.fmt(template;args)` | Formatted output |
| Chained error handling | Nested match on multiple Results | Composition |
| Stdlib composition | Combining multiple stdlib modules | Real-world patterns |

**Example difficulty:** Full programs: HTTP handlers, file processors, CLI
tools, data transformers. Multiple error paths. Real-world patterns.

**Expected token count:** 150-500 tokens per sample.

**Tagging criteria:** Uses result-type error handling or stdlib I/O modules
(`std.file`, `std.net`, `std.http`, `std.db`, `std.json`). Represents the
full complexity of the language.

---

### Stage 6 — Advanced Patterns and Composition (stretch)

**Scope:** Complex multi-module applications combining all language features.
This stage is optional and serves as a stretch goal for curriculum completeness.

**Language features:**

| Feature | Example | Notes |
|---------|---------|-------|
| Higher-order functions | Functions that accept/return functions | Functional patterns |
| Generic-like patterns | Type-parameterised structures | If supported by spec |
| Complex data structures | Trees, graphs, hash maps | Composed from structs/sum types |
| Multi-module architecture | 3+ modules with dependency graph | Realistic project structure |
| Error recovery patterns | Retry, fallback, default values | Beyond simple propagation |
| Performance-sensitive code | Arena-aware patterns | Memory-conscious toke |

**Expected token count:** 300-800 tokens per sample.

**Tagging criteria:** Requires composition of 3+ Stage 4/5 features in a single
program, or multi-module programs with 3+ files.

---

## Curriculum Schedule

### Training Data Ordering

Curriculum learning orders training data from simple to complex. The schedule
below defines how to transition between stages during training.

```
Phase       Epochs    Data Mix (by stage)            Rationale
──────────  ────────  ─────────────────────────────  ─────────────────────────────
Warmup      1-2       S1:80%  S2:20%                 Learn tokenisation and basic syntax
Foundation  3-5       S1:30%  S2:50%  S3:20%         Build function/type fluency
Expansion   6-8       S1:10%  S2:20%  S3:40%  S4:30% Introduce modules and sum types
Full        9-12      S1:5%   S2:10%  S3:20%  S4:30%  S5:35%   Full language coverage
Polish      13-15     S1:5%   S2:10%  S3:15%  S4:25%  S5:35%  S6:10%  Include stretch
```

### Stage Transition Criteria

Do not advance to the next phase until the model demonstrates competence at
the current level. Evaluation criteria per phase:

| Transition | Gate Metric | Threshold |
|------------|-------------|-----------|
| Warmup -> Foundation | Pass@1 on Stage 1 tasks | >= 85% |
| Foundation -> Expansion | Pass@1 on Stage 2 tasks | >= 70% |
| Expansion -> Full | Pass@1 on Stage 3 tasks | >= 60% |
| Full -> Polish | Pass@1 on Stage 4 tasks | >= 50% |

Metrics are evaluated on a held-out validation set of 50 tasks per stage,
drawn from `toke-benchmark/tasks/` (non-hidden subset).

### Adaptive Reweighting

If evaluation shows regression on earlier stages during later phases:
1. Increase the proportion of regressed-stage samples by 10%
2. Decrease the current-stage proportion by 10%
3. Re-evaluate after 1 epoch
4. If regression persists for 2 consecutive checkpoints, halt and investigate

This connects to Story 9.2.2 (TAROT-style test-driven curriculum), which will
automate this reweighting using compiler diagnostic feedback.

---

## Tagging Scheme

### Tag Format

Each training sample in the corpus JSONL gets a `curriculum_stage` field:

```json
{
  "task_id": "expr_arith_042",
  "source": "m=test;\nf=main():i64{x:=3+4;<x};",
  "curriculum_stage": 1,
  "curriculum_features": ["let_binding", "arithmetic", "return_expr"],
  "token_count": 22
}
```

### Feature Tags

Each stage defines a set of feature tags. A sample's stage is determined by
the highest-stage feature it uses.

```
Stage 1 features:
  module_decl, int_literal, float_literal, string_literal, bool_literal,
  let_binding, mut_binding, arithmetic, comparison, logical_op, return_expr

Stage 2 features:
  func_def, multi_func, typed_param, return_type, type_alias, struct_type,
  type_sigil, func_call, string_ops

Stage 3 features:
  if_cond, if_else, nested_cond, loop, break, break_value, recursion,
  nested_loop, early_return, compound_cond

Stage 4 features:
  import, multi_import, module_resolution, sum_type, match_expr,
  exhaustive_match, option_type, multi_file, type_reexport

Stage 5 features:
  result_type, error_propagation, partial_func, file_io, network_io,
  json_handling, db_access, string_format, chained_error, stdlib_composition

Stage 6 features:
  higher_order_func, generic_pattern, complex_data_structure,
  multi_module_arch, error_recovery, perf_sensitive
```

### Automated Tagging Algorithm

To tag existing training samples:

1. **Parse:** Run `tkc --ast-dump` on each sample to get the AST
2. **Feature scan:** Walk the AST and collect all feature tags present
3. **Stage assignment:** Assign `curriculum_stage = max(stage of each feature found)`
4. **Token count:** Run the toke tokenizer to get `token_count`
5. **Validation:** Verify stage distribution is roughly pyramid-shaped (more
   Stage 1 samples than Stage 5)

Expected distribution after tagging the initial corpus:

```
Stage 1:  ~30% of samples  (simplest, most numerous)
Stage 2:  ~25%
Stage 3:  ~20%
Stage 4:  ~15%
Stage 5:  ~8%
Stage 6:  ~2%              (fewest, most complex)
```

If the actual distribution is significantly skewed (e.g., <5% Stage 1), the
corpus generation pipeline should be adjusted to produce more samples at the
underrepresented stages before training begins.

---

## References

1. **Bengio et al. (2009)** — *Curriculum Learning.* ICML 2009. Foundational
   paper showing that ordering training examples from easy to hard improves
   convergence and generalisation.

2. **Curriculum Learning for Small Code LMs** — Demonstrates that curriculum
   ordering by syntactic complexity improves Pass@1 for code generation models
   under 7B parameters. Directly applicable to toke's model size targets.

3. **Soviany et al. (2022)** — *Curriculum Learning: A Survey.* IEEE TPAMI.
   Comprehensive survey of curriculum strategies including self-paced learning,
   teacher-student, and competence-based curricula.

4. **TAROT (2026)** — Test-driven adaptive reweighting using compiler feedback.
   The basis for Story 9.2.2, which will automate stage transitions using
   diagnostic error codes as a weakness signal.

5. **WizardCoder / Evol-Instruct-Code-80K** — Complexity escalation via
   evolutionary prompting. Related to Story 9.1.3 which generates evolved
   variants at each skill ladder stage.

---

## Dependencies

- **Story 9.1.1** (Reverse OSS-Instruct): Provides seed corpus to be tagged
- **Story 9.1.2** (Parallel corpus): Provides multi-language reference pairs
- **Story 9.8.1** (BPE retrain): Tokenizer used for token count measurement
- **Story 9.2.2** (TAROT curriculum): Extends this ladder with automated
  reweighting (depends on this document)
- **Story 9.2.3** (Teacher-student loop): Uses this ladder to target weak
  stages (depends on this document)
