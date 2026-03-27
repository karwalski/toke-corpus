# Corpus Pipeline Architecture

## Overview

The corpus generation pipeline produces validated toke training examples
using a four-stage process:

1. **Task curriculum generation** — programmatic generation of coding tasks
   using templates and variant parameters
2. **LLM generation** — local Qwen 2.5 Coder 32B generates toke solutions;
   Claude API used for escalation on repeated failures
3. **Validation** — tkc compiles the generated code; differential testing
   against Python, C, and Java reference implementations verifies correctness
4. **Quality review** — local Qwen judge agent scores idiom quality and
   token efficiency

Only entries that pass all four stages enter the corpus.

## Holdout isolation

Benchmark task IDs in `toke-benchmark/tasks/` are checked against generated
task IDs before any batch is finalised. This prevents training data contamination.
Held-out test cases in `benchmark/hidden_tests/` are never read by this pipeline.

## Storage

Corpus data is stored locally on the Mac Studio. It is NOT committed to git.
Only the schema (`corpus/schema.json`) and pipeline code are version-controlled.
