# AGENTS.md
## Claude Code Operating Specification — toke (tk)

**Version:** 1.0  
**Repository:** https://github.com/tokelang/spec  
**Project:** toke — machine-native language for LLM code generation  
**Compiler binary:** tkc  
**File extension:** .tk

This file is the authoritative operating specification for any AI coding agent working in this repository. Read it in full before touching any file. It is not a suggestion document — it is a contract.

---

## 1. What This Project Is

toke is a compiled, statically typed programming language designed for LLM code generation. The repository contains:

- **`tkc/`** — the reference compiler, written in C, zero external dependencies (excluding LLVM)
- **`stdlib/`** — the toke standard library, written in toke
- **`corpus/`** — corpus generation pipeline, validation harness, Qwen judge agent, differential testing
- **`tokenizer/`** — Phase 2 BPE tokenizer training and evaluation scripts
- **`models/`** — QLoRA fine-tuning scripts (MLX) and benchmark evaluation harness
- **`benchmark/`** — task definitions, hidden test cases, reference implementations
- **`spec/`** — formal grammar (EBNF), semantics, error code registry

The compiler is the root of trust for everything else. A bug in the compiler corrupts the corpus. A corrupted corpus corrupts the model. Work on the compiler with extra caution.

---

## 2. Core Objectives

You must:

- produce correct, maintainable code that compiles cleanly on macOS ARM64, Linux x86-64, and Linux ARM64
- preserve compiler correctness at all times — never leave tkc in a state that produces wrong output or crashes on valid input
- work in small verifiable increments with explicit acceptance criteria per story
- add or update tests with every change to the compiler frontend or stdlib
- run the conformance suite after every compiler change
- keep `docs/progress.md` and `PROJECT_STATUS.md` current
- use git carefully, with clean branch structure and meaningful commit messages
- make the structured diagnostic output machine-readable at all times — this is a load-bearing feature, not optional polish

You must optimise for compiler correctness and diagnostic accuracy above all else. A wrong error message is worse than a missing feature because it breaks the repair loop.

---

## 3. Project-Specific Rules

These rules are specific to toke and take precedence over general coding agent conventions.

### 3.1 Compiler rules

- The lexer, parser, name resolver, type checker, arena validator, and IR lowerer are each independently testable. Keep them that way. Do not merge concerns between stages.
- Every error path in the compiler must produce a structured diagnostic. There must be no path that prints a string to stderr and returns without emitting a schema-conforming JSON diagnostic record.
- The `fix` field in a diagnostic must only be populated when the suggested fix is deterministically correct. An incorrect `fix` breaks the automated repair loop. If you are not certain, leave the field absent.
- Error codes are stable. Never renumber an existing error code. Never change the meaning of an existing error code. Add new codes at the end of their series range.
- The compiler frontend (lexer through IR lowering) must stay under 5,000 lines of C. If a change would push it over this limit, refactor first.
- Never add an external C dependency without creating a tracking issue and getting explicit sign-off. The zero-dependency property of the compiler frontend is a hard constraint.

### 3.2 Corpus rules

- Never add entries to the corpus that have not passed the full validation pipeline (compiler + differential test + Qwen judge). No exceptions.
- Never commit test task IDs to the corpus directory. Held-out benchmark tasks in `benchmark/hidden_tests/` must not appear in `corpus/`.
- The corpus metadata schema defined in `corpus/schema.json` is normative. Do not add fields without updating the schema and the schema validation tests.
- Corpus deduplication runs before every batch commit to the corpus. Do not bypass it.

### 3.3 Diagnostic schema rules

- The diagnostic schema is defined in `spec/errors.md` and `Appendix B` of the RFC. It is versioned. Do not change field names, field types, or the set of required fields without bumping the schema version and updating all consumers.
- The tooling protocol schemas in `Appendix C` of the RFC are normative. Any change to request or response structure is a breaking change and requires a version bump.

### 3.4 Standard library rules

- All stdlib function signatures are normative per the RFC. Do not change a stdlib function signature without updating the spec, the .tki interface file, all call sites in the corpus, and the conformance tests.
- Stdlib source files are toke source (.tk). They must compile cleanly with the current tkc before being committed.
- Stdlib implementations may use C FFI internally. FFI usage in stdlib must be clearly commented with the C function signature and the rationale for using FFI over a toke implementation.

### 3.5 Benchmark and test isolation rules

- The `benchmark/hidden_tests/` directory is write-only for humans. Agents must never read files from this directory during corpus generation or training data preparation.
- The task ID deduplication check between corpus and benchmark must run before any corpus batch is finalised. Agents must not skip this check.

---

## 4. Git Rules

### 4.1 Branching

Never commit directly to `main`. Every change goes through a branch.

Branch naming:

```
feature/<component>-<description>
fix/<component>-<description>
refactor/<component>-<description>
test/<component>-<description>
```

Where `<component>` is one of: `compiler`, `lexer`, `parser`, `typechecker`, `backend`, `stdlib`, `corpus`, `tokenizer`, `models`, `benchmark`, `spec`, `ci`.

Examples:

```
feature/compiler-phase2-sigil-support
fix/typechecker-arena-escape-e5001
test/lexer-string-literal-escapes
refactor/corpus-pipeline-escalation-logic
```

### 4.2 Commit format

Follow conventional commits. Component scope is required.

```
feat(typechecker): enforce exhaustive match E4010
fix(lexer): emit E1003 for out-of-set characters in structural position
test(compiler): add conformance tests for T-series type rules
refactor(corpus): extract Qwen judge into standalone module
docs(spec): update error code registry with E5002
chore(ci): add ARM64 Linux target to build matrix
```

### 4.3 Before every commit

- Run `make test` for the component you changed
- Run the conformance suite if any compiler stage was touched: `make conform`
- Run `make lint` — no warnings allowed in C code
- Confirm no `.env` files, API keys, model weights, or corpus data exceeding 100MB are staged
- Review `git diff --staged` before committing — no unrelated edits

### 4.4 Commit scope

One logical change per commit. Do not bundle a bugfix with a refactor with a new feature in one commit. If you discover a separate bug while fixing something else, fix it in a separate commit with its own message.

### 4.5 Force push policy

Never force push `main`. Feature branches may be force-pushed after a rebase, with a comment in the PR noting that history was rewritten.

---

## 5. Development Cycle

For each story, follow this sequence:

1. Read the story's acceptance criteria in `docs/epics_and_stories.md` before writing any code
2. Update `docs/progress.md` status to `in_progress`
3. Write a one-paragraph plan in the PR description or in a comment at the top of the relevant issue
4. Implement the smallest coherent slice that advances toward acceptance criteria
5. Add or update tests for that slice
6. Run `make conform` if compiler was changed, `make test` otherwise
7. Refactor if needed — keep the C under the line limit, keep functions under 50 lines
8. Update `docs/progress.md` with what was done and what validation passed
9. Commit with a clean message
10. In the PR, list: files changed, acceptance criteria met, tests added, validation run

A story is not done until every acceptance criterion in the story document is verifiably met. Do not mark a story `done` if any criterion is unmet, even if the main path works.

---

## 6. Testing Rules

### 6.1 Compiler conformance tests

Every change to any compiler stage must be accompanied by conformance tests. The conformance suite lives in `tkc/test/`.

Test categories and their file locations:

```
tkc/test/lexical/        L-series: lexer behaviour
tkc/test/grammar/        G-series: parser behaviour
tkc/test/names/          N-series: name resolution
tkc/test/types/          T-series: type checking
tkc/test/codegen/        C-series: code generation output
tkc/test/diagnostics/    D-series: diagnostic schema and fix field accuracy
tkc/test/protocol/       P-series: tooling protocol
```

Each test file specifies:

```yaml
input: |
  M=test;
  F=add(a:i64;b:i64):i64{<a+b};
expected_exit_code: 0
expected_error_codes: []
expected_output: ""
```

For error tests:

```yaml
input: |
  M=test;
  F=bad(x:u64):Str{<x};
expected_exit_code: 1
expected_error_codes: ["E4021"]
expected_fix: ""
```

### 6.2 Fix field tests

The `fix` field is load-bearing. Every error code that has a documented mechanical fix must have at least one D-series test that verifies the fix field value exactly. These tests are not optional.

### 6.3 Corpus pipeline tests

Changes to the corpus pipeline (`corpus/pipeline/`) must include Python unit tests in `corpus/pipeline/tests/`. The test suite covers: task deduplication, API escalation logic, majority vote logic, metadata schema validation, and holdout task isolation.

### 6.4 Minimum rule

No compiler change is complete until:
- the relevant conformance tests pass
- `make conform` passes in full
- no new warnings appear in `make lint`
- `docs/progress.md` is updated

No corpus pipeline change is complete until:
- Python unit tests pass
- schema validation passes against `corpus/schema.json`
- a dry-run of the pipeline produces correct output on 10 sample tasks

---

## 7. Validation Commands

Run these after every development cycle on the relevant component:

```bash
# Compiler: full conformance suite
make conform

# Compiler: specific test series only
make conform SERIES=T

# Compiler: lint (C)
make lint

# Compiler: build for all targets
make build-all

# Corpus pipeline: unit tests
cd corpus && python -m pytest tests/

# Corpus pipeline: schema validation
python corpus/pipeline/validate_schema.py --check-all

# Tokenizer: evaluation against cl100k_base
python tokenizer/eval.py --compare cl100k_base

# Models: benchmark evaluation
python models/eval/run_benchmark.py --tasks benchmark/tasks/ --model <path>

# Full CI equivalent (slow)
make ci
```

If validation fails:
- fix the issue before claiming the story is done
- if the failure is a pre-existing bug unrelated to your change, log it in `docs/progress.md` as a blocker with an issue reference and continue only if the CI failure is proven pre-existing on `main`

---

## 8. Folder Structure

Respect the existing structure. Do not create new top-level directories without a tracking issue.

```
tokelang/
├── tkc/                    Compiler (C, zero external dependencies)
│   ├── src/
│   │   ├── lexer.c         Lexer — Stage 1
│   │   ├── lexer.h
│   │   ├── parser.c        Parser — Stage 2, LL(1)
│   │   ├── parser.h
│   │   ├── names.c         Name resolver — Stage 3
│   │   ├── names.h
│   │   ├── types.c         Type checker — Stage 4
│   │   ├── types.h
│   │   ├── arena.c         Arena validator — Stage 5
│   │   ├── arena.h
│   │   ├── ir.c            IR lowering — Stage 6
│   │   ├── ir.h
│   │   ├── llvm.c          LLVM IR backend — Stage 7
│   │   ├── llvm.h
│   │   ├── diag.c          Structured diagnostic emitter
│   │   ├── diag.h
│   │   └── main.c          CLI entry point
│   ├── test/               Conformance suite
│   │   ├── lexical/        L-series
│   │   ├── grammar/        G-series
│   │   ├── names/          N-series
│   │   ├── types/          T-series
│   │   ├── codegen/        C-series
│   │   ├── diagnostics/    D-series
│   │   └── protocol/       P-series
│   └── Makefile
│
├── stdlib/                 Standard library (.tk source)
│   ├── std.str.tk
│   ├── std.http.tk
│   ├── std.db.tk
│   ├── std.json.tk
│   ├── std.file.tk
│   └── std.net.tk
│
├── corpus/
│   ├── generator/          Task curriculum generator
│   ├── pipeline/           Generation + validation harness
│   │   ├── tests/          Pipeline unit tests
│   │   └── validate_schema.py
│   ├── judge/              Qwen local judge agent
│   ├── diff_test/          4-language differential testing
│   └── schema.json         Corpus entry schema (normative)
│
├── tokenizer/
│   ├── train.py
│   └── eval.py
│
├── models/
│   ├── finetune/           QLoRA training scripts (MLX)
│   └── eval/               Benchmark evaluation harness
│
├── benchmark/
│   ├── tasks/              Task definitions (used in corpus)
│   ├── hidden_tests/       Held-out only — agents MUST NOT read during generation
│   └── baselines/          Reference implementations
│
├── spec/
│   ├── grammar.ebnf        Formal grammar (normative)
│   ├── semantics.md        Type rules and memory model
│   └── errors.md           Error code registry
│
└── docs/
    ├── progress.md         Live progress tracker
    ├── architecture/       Architecture decision records
    │   └── ADR-0001.md     Initial language and compiler architecture
    ├── decisions/          Significant design decisions
    ├── conventions.md      Coding conventions
    └── testing-strategy.md Test strategy
```

File placement rules:
- New C source files go in `tkc/src/`
- New conformance tests go in the correct series subdirectory under `tkc/test/`
- New stdlib modules go in `stdlib/` as `.tk` files
- Architecture decisions go in `docs/architecture/ADR-NNNN.md`
- Do not create `helpers.c`, `utils.c`, `misc.py`, or similar vague files

---

## 9. Coding Standards

### 9.1 C code (compiler)

The compiler frontend is written in C99. No C++ allowed. No POSIX extensions beyond the required set (stdio.h, stdlib.h, string.h, unistd.h).

Style rules:
- 4-space indentation, no tabs
- function names: `snake_case`
- type names: `PascalCase` for structs, `snake_case_t` for typedefs
- constants and macros: `UPPER_SNAKE_CASE`
- maximum function length: 50 lines. If a function exceeds this, split it
- maximum file length: 800 lines for non-test files. If a file approaches this, split it
- every function has a comment stating what it does if it is not obvious from the name
- every `#define` constant has a comment explaining why it is that value

Error handling in C:
- functions that can fail return an int (0 = success, non-zero = failure) or a pointer (NULL = failure)
- never return error codes as magic integers without a named constant
- every error path emits a diagnostic via `diag_emit()` before returning — do not return failure silently

Memory in C:
- all arena allocation goes through the arena abstraction in `tkc/src/arena.c`
- do not call `malloc` directly anywhere except in `arena.c`
- do not call `free` directly anywhere except in `arena.c`
- every allocation that could fail must check the return value

```c
/* Good */
Node *node = arena_alloc(arena, sizeof(Node));
if (!node) {
    diag_emit(DIAG_ERROR, E2007, pos, "out of memory");
    return NULL;
}

/* Bad */
Node *node = malloc(sizeof(Node));
```

### 9.2 Python code (pipeline, tokenizer, models)

- Python 3.11+
- type annotations on all function signatures
- `ruff` for linting (configured in `pyproject.toml`)
- `mypy` for type checking (strict mode)
- no bare `except:` clauses
- all CLI scripts use `argparse` with a `--help` flag
- logging uses the standard `logging` module, not `print()`
- no hardcoded paths — all paths come from config or CLI arguments

```python
# Good
def generate_task(template: str, variant_id: int) -> Task:
    ...

# Bad
def generate(t, v):
    ...
```

### 9.3 toke source (stdlib)

- one module per file, module name matches file path
- all types defined before all functions
- every partial function has its error type explicitly declared
- no function longer than 30 statements
- every stdlib file compiles cleanly with `tkc --check` before commit

### 9.4 Comments

Write comments that explain why, not what:

```c
/* Good: explains the non-obvious invariant */
/* The arena depth counter must be incremented before calling
   type_check_expr so that any allocations inside the expression
   are attributed to the correct scope. */

/* Bad: restates the code */
/* increment arena depth */
arena_depth++;
```

Document every design decision that is not obvious from the code in `docs/decisions/`.

### 9.5 Error handling philosophy

The repair loop depends on error quality. Every error message must:
- state what was expected
- state what was found
- include enough context for a model to fix it without reading the full source
- provide a `fix` if and only if the fix is correct in every case this error is emitted

Do not trade diagnostic quality for implementation convenience.

---

## 10. Progress Tracking

### 10.1 File locations

```
docs/progress.md         Active story status
docs/sprints/            Sprint records when running parallel workstreams
PROJECT_STATUS.md        High-level project phase and gate status
```

### 10.2 Story status values

```
backlog      not yet started
planned      estimated and ready to start
in_progress  being worked on now
blocked      cannot proceed — blocker documented
review       implementation done, awaiting validation or review
done         all acceptance criteria met, tests pass, docs updated
```

### 10.3 Progress entry format

```markdown
## Story 1.2.1 — Lexer implementation

**Status:** done  
**Branch:** feature/lexer-phase1-implementation  
**Acceptance criteria met:**
- [x] Correctly classifies all 80 Phase 1 characters
- [x] Whitespace consumed silently
- [x] E1001, E1002, E1003 emitted correctly
- [x] 200-token file lexed in under 1ms
- [x] Under 300 lines of C

**Tests added:** tkc/test/lexical/L001–L047  
**Validation:** make conform SERIES=L — all 47 tests pass  
**Notes:** none
```

### 10.4 Blocked stories

If a story is blocked, document it immediately:

```markdown
## Story 2.1.2 — Async task model

**Status:** blocked  
**Blocker:** Concurrency semantics for arena-allocated values across thread boundaries
not yet resolved. Waiting on architecture decision ADR-0007.  
**Unblocked by:** ADR-0007 decision
```

Do not silently skip blocked work. Log it and move to the next unblocked story.

---

## 11. Architecture Decision Records

When a design decision is made that affects compiler behaviour, diagnostic output, stdlib signatures, or corpus pipeline structure, write an ADR.

ADRs live in `docs/architecture/`. File naming: `ADR-NNNN-short-description.md`.

ADR template:

```markdown
# ADR-NNNN: <Title>

**Date:** YYYY-MM-DD  
**Status:** proposed | accepted | superseded | deprecated  
**Deciders:** <names or agent>

## Context

What is the problem or question?

## Decision

What was decided?

## Rationale

Why was this decision made over alternatives?

## Consequences

What becomes easier or harder as a result?

## Alternatives considered

What else was considered and why was it rejected?
```

Examples of decisions that require an ADR:
- changing the diagnostic schema fields
- adding a new stdlib module
- changing the arena allocation model
- changing the compiler's treatment of a specific error class
- any change to the tooling protocol request or response shape

---

## 12. Parallel Sprint Execution

When multiple workstreams are running in parallel, use this coordination model.

### 12.1 Parallelisation rules

Only run workstreams in parallel if they have no shared file dependencies. High-risk shared files for toke:

```
tkc/src/types.c          Type checker — touches everything
tkc/src/diag.c           Diagnostic emitter — all stages depend on it
tkc/src/ir.h             IR node definitions — all stages depend on it
corpus/schema.json       Corpus schema — pipeline and judge depend on it
spec/errors.md           Error code registry — conformance tests depend on it
```

If two sprints both need to touch a high-risk shared file, they are not parallel candidates.

### 12.2 Sprint boundaries for toke

Good parallel candidates:

```
Sprint A: lexer hardening and new L-series tests
Sprint B: corpus pipeline Qwen judge improvements
Sprint C: tokenizer training script updates
Sprint D: benchmark task generation (new tasks)
```

Bad parallel candidates:

```
Sprint A: type checker — changing type rule for sum types
Sprint B: compiler diagnostics — changing fix field for E4010
(both touch types.c and diag.c)
```

### 12.3 Orchestrator responsibilities

When coordinating parallel sprints:
- assign file ownership per sprint before starting
- log the file ownership in `docs/sprints/sprint-NN.md`
- block Sprint B if it needs a file Sprint A is currently modifying
- run `make conform` as integration check before merging any compiler branch
- merge compiler branches one at a time, never in parallel

---

## 13. Behaviour to Avoid

Do not:

- suppress conformance test failures by modifying expected outputs without fixing the underlying bug
- change error codes for existing errors
- populate the `fix` field with a fix that is incorrect in any case
- access `benchmark/hidden_tests/` during corpus generation, training data preparation, or tokenizer training
- add external C dependencies to the compiler frontend without approval
- merge a compiler change that causes `make conform` to drop below 100% pass
- add stdlib functions that are not in the RFC spec without creating a spec amendment issue
- commit corpus entries that have not passed all three validation agents
- add model weights, large datasets, or generated binaries to git

---

## 14. Reporting Format

At the end of each development cycle, report using this structure:

```
## Cycle Summary

**Story:** <story ID and title>
**Branch:** <branch name>

**Files changed:**
- tkc/src/types.c — added exhaustive match enforcement for E4010
- tkc/test/types/T-042.yaml — new conformance test
- docs/progress.md — updated story 1.2.5 to done

**Implementation decisions:**
- Exhaustive match check runs after all arm types are resolved to avoid
  false positives from forward-referenced variant names.

**Tests added or updated:**
- T-042: non-exhaustive match emits E4010 with correct span
- T-043: adding variant to sum type triggers E4010 on all existing matches

**Validation:**
- make conform: 447/447 pass
- make lint: 0 warnings
- make build-all: success on x86-64 and ARM64

**Risks or follow-up:**
- E4010 fix field is currently empty. Story 1.2.6 covers diagnostic fix fields.

**Tracker update:**
- Story 1.2.5: done
- Story 1.2.6: next
```

---

## 15. Short Operational Prompt

If a shorter version is needed for a tool system prompt:

> You are a senior compiler engineering agent working on tkc, the reference compiler for the toke programming language. Follow these rules without exception: never commit to main; use feature/<component>-<description> branches; run make conform after every compiler change and fix all failures before proceeding; never change error code numbers or meanings; only populate the diagnostic fix field when the fix is correct in all cases; never access benchmark/hidden_tests/ during corpus work; keep the compiler frontend under 5,000 lines of C with no external dependencies; update docs/progress.md after every story; one logical change per commit using conventional commit format; if blocked, log the blocker and move to the next unblocked story. A story is done only when all acceptance criteria are met, all tests pass, and docs/progress.md is updated.

---

## 16. First Action on Every Session

Before writing any code in a new session:

1. Read `PROJECT_STATUS.md` to understand the current phase and gate status
2. Read `docs/progress.md` to find the current `in_progress` or highest-priority `planned` story
3. Run `git status` and `git log --oneline -10` to understand what is in flight
4. Run `make conform` to confirm the baseline is green before touching anything
5. Identify which story you are working on and confirm its acceptance criteria in `docs/epics_and_stories.md`

Only then start writing code.

---

*This specification is maintained in `AGENTS.md` at the repository root. It is updated when project conventions change. The latest version in `main` is authoritative.*
