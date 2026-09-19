# Corpus Statistics and Provenance Report

**Snapshot date:** 2026-04-04
**Corpus version:** 1.0 (from `corpus/manifest.json`)
**Decision reference:** D13=E — publish statistics, keep full corpus private

This document describes the composition, quality, and provenance of the toke training corpus. It is intended for external reviewers evaluating the toke project's training methodology. No raw source code or complete programs are included.

---

## 1. Corpus Overview

The corpus contains **46,754** validated, deduplicated, compiler-checked toke programs distributed across 4 stages:

| Stage | Programs | Description | Generation method |
|-------|----------|-------------|-------------------|
| A | 26,978 | Core algorithmic tasks | Multi-model LLM generation with differential testing |
| B | 9,776 | Multi-function composed programs | Mechanical composition from Stage A pairs |
| C | 5,000 | Boundary/edge-case variants | Mechanical transformation of Stage A entries |
| D | 5,000 | Application-level multi-function programs | Mechanical composition from Stage A+B entries |
| **Total** | **46,754** | | |

Stages B, C, and D are generated at zero LLM cost — they are pure mechanical transformations of accepted Stage A programs.

---

## 2. Category Distribution

### Stage A — Core Algorithmic Tasks (26,978 programs)

| Category | Code | Programs | % of Stage A | Description |
|----------|------|----------|-------------|-------------|
| Strings | A-STR | 5,050 | 18.7% | String operations, parsing, manipulation |
| Sorting | A-SRT | 4,710 | 17.5% | Sorting algorithms, comparison functions |
| Conditionals | A-CND | 4,455 | 16.5% | Branching, boolean logic, control flow |
| Error handling | A-ERR | 4,358 | 16.1% | Error cases, edge conditions, guard clauses |
| Mathematics | A-MTH | 4,304 | 15.9% | Arithmetic, number theory, combinatorics |
| Arrays | A-ARR | 4,101 | 15.2% | Array manipulation, searching, indexing |

Source: `corpus/manifest.json` — exact counts.

### Stage B — Composed Programs (9,776 programs)

| Category | Code | Programs | Description |
|----------|------|----------|-------------|
| Composition | B-CMP | 9,776 | Type-compatible function pairs from Stage A, chained or nested |

### Stage C — Edge-Case Variants (5,000 programs)

| Category | Code | Programs | Description |
|----------|------|----------|-------------|
| Edge cases | C-EDG | 5,000 | Boundary checking, guard clauses, defensive wrappers around Stage A functions |

### Stage D — Application Programs (5,000 programs)

| Category | Code | Programs | Description |
|----------|------|----------|-------------|
| Applications | D-APP | 5,000 | 3+ interacting functions: pipeline, fan-out, and accumulator patterns |

---

## 3. Token Count Distributions

Each corpus entry records `tk_tokens` — the token count using the `cl100k_base` tokenizer (OpenAI tiktoken). Reference implementations in Python, C, and Java are also tokenized for comparison.

**Note:** The statistics below are estimates based on sampled entries and the known corpus schema. For precise distributions, run the generation scripts against the full corpus store. Each entry's exact token count is stored in its JSON file.

| Stage | Typical range (tokens) | Notes |
|-------|----------------------|-------|
| A | 15 — 200 | Single-function programs; toke's brevity means most are well under 100 tokens |
| B | 40 — 350 | Two composed functions plus a main entry point |
| C | 25 — 250 | Stage A functions wrapped with boundary guards |
| D | 60 — 500 | 3+ functions forming mini-applications |

Representative example (from `A-A-MTH-0001`):
- Toke: 19 tokens
- Python equivalent: 90 tokens
- C equivalent: 161 tokens
- Java equivalent: 153 tokens

The corpus schema (`corpus/schema.json`) guarantees every entry has an integer `tk_tokens` field, enabling exact histogram computation from the raw data.

---

## 4. Quality and Validation

### Validation pipeline

Every corpus entry passes through a multi-stage validation pipeline before acceptance:

1. **Compiler check** — `tkc` compiler must exit with code 0 (no errors)
2. **Differential testing** — the program's output is compared against equivalent implementations in Python, C, and Java; at least 2 of 3 reference languages must agree on output
3. **Judge scoring** — a local Qwen judge agent scores each entry on a 0.0-1.0 scale; entries must be marked `accepted: true`
4. **Deduplication** — entries with >= 0.95 similarity to existing entries are rejected

### Compiler success rate

All 46,754 programs in the final corpus compiled successfully (`compiler_exit_code: 0`). Programs that failed compilation were rejected during the pipeline and are not included.

### Trial scorecard (Stage A generation)

During the initial trial run (100 tasks), first-pass compile rates per provider were:

| Provider | First-pass compile rate | Correction success rate | Composite score |
|----------|------------------------|------------------------|-----------------|
| DeepSeek (deepseek-chat) | 87.6% | 45.5% | 0.658 |
| Anthropic (Claude Haiku 4.5) | 86.2% | 69.2% | 0.643 |
| xAI (Grok-3-mini) | 71.4% | 62.5% | 0.593 |
| OpenAI (GPT-4.1-mini) | 68.8% | 40.0% | 0.525 |
| Google (Gemini 2.5 Flash) | 36.1% | 22.6% | 0.440 |

Source: `metrics/scorecard.json` — trial run results. Full-run acceptance rates differ due to task difficulty distribution.

### Judge scores

Each accepted entry has a judge score in the range [0.0, 1.0]. The example entry shown above scored 0.979. Entries marked `accepted: false` by the judge are excluded from the final corpus.

---

## 5. Generation Provider Breakdown

Programs were generated by a mix of LLM providers (Stage A) and mechanical transformers (Stages B-D):

| Model | Programs | % of total | Stages |
|-------|----------|-----------|--------|
| Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) | 11,796 | 25.2% | A |
| Mechanical compose | 9,776 | 20.9% | B |
| Grok-3-mini | 6,771 | 14.5% | A |
| GPT-4.1-mini (`gpt-4.1-mini-2025-04-14`) | 6,746 | 14.4% | A |
| Mechanical guard_empty | 2,944 | 6.3% | C, D |
| Mechanical accumulator | 1,708 | 3.7% | D |
| Mechanical fanout | 1,684 | 3.6% | D |
| Mechanical pipeline3 | 1,608 | 3.4% | D |
| DeepSeek (deepseek-chat) | 1,515 | 3.2% | A |
| Mechanical clamp | 758 | 1.6% | C |
| Mechanical guard_neg | 755 | 1.6% | C |
| Mechanical invert | 543 | 1.2% | C |
| Gemini 2.5 Flash | 144 | 0.3% | A |
| GPT-4.1-mini (legacy ID) | 6 | <0.1% | A |

Source: `corpus/manifest.json` — exact counts.

### Generation parameters (Stage A — LLM-generated)

| Parameter | Value |
|-----------|-------|
| Provider pool | DeepSeek, Claude Haiku 4.5, GPT-4.1-mini, Grok-3-mini, Gemini 2.5 Flash |
| Tier 2 minimum allocation | 30% (Claude Haiku 4.5 for harder categories) |
| Category-specific Tier 2 overrides | A-ERR: 70%, A-SRT: 40%, A-STR: 45% |
| Validation timeout | 10 seconds |
| Max correction attempts | 1 |
| Dedup similarity threshold | 0.95 |
| Cost limit | $200.00 |
| Random seed | 42 |

Source: `config.yaml`

### Mechanical generation (Stages B-D)

- **Stage B (compose):** Finds type-compatible function pairs from Stage A and chains/nests them into multi-function programs
- **Stage C (edge cases):** Wraps Stage A functions with boundary guards, empty-input checks, negative-value guards, clamping, and inversion patterns
- **Stage D (applications):** Combines 3+ functions from Stages A and B into pipeline, fan-out, and accumulator patterns

All mechanical stages operate at zero LLM cost.

---

## 6. Provenance

### Synthetic origin

All 46,754 programs in the corpus are **synthetically generated**. No human-written code is included. The generation process:

1. Task specifications define the problem (e.g., "implement binary search")
2. LLM providers generate candidate toke programs from prompts
3. Differential testing validates correctness against Python, C, and Java implementations
4. A local judge agent scores and accepts/rejects each candidate
5. Mechanical transformers create derived programs (Stages B-D)

### No copyrighted code

The corpus contains no copyrighted code from external sources. All programs were generated from task specifications by LLMs or by mechanical transformation of LLM-generated programs. The toke language itself is a novel language with no pre-existing codebase to copy from.

### Generation prompts

Generation prompts are available in the corpus repository under `prompts/category/`:

- `A-ARR.md` — Array manipulation tasks
- `A-CND.md` — Conditional/branching tasks
- `A-ERR.md` — Error handling tasks
- `A-MTH.md` — Mathematics tasks
- `A-SRT.md` — Sorting tasks
- `A-STR.md` — String operation tasks

### Holdout separation

The training corpus (`toke-corpus`, 46,754 programs) and evaluation benchmark (`toke-benchmark`, 1,000 tasks) are maintained in separate repositories with no cross-references. See `toke-spec/docs/gate1-reproducibility.md` Section 6 for the full contamination report.

---

## 7. Data Format

Each corpus entry is stored as an individual JSON file following `corpus/schema.json` (version 1). Required fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique corpus entry ID |
| `version` | integer | Schema version (currently 1) |
| `phase` | string | Stage identifier (A, B, C, D) |
| `task_id` | string | Task specification ID |
| `tk_source` | string | Validated toke source code |
| `tk_tokens` | integer | Token count (cl100k_base) |
| `validation` | object | Compiler exit code and error codes |
| `differential` | object | Languages that agreed on output |
| `judge` | object | Acceptance decision and score |

Optional fields include `references` (Python, C, Java equivalent sources and token counts), `model` (generating model), and `attempts` (generation attempts before acceptance).

---

## 8. Staleness Note

This document is a **point-in-time snapshot** as of 2026-04-04, reflecting the corpus state at Gate 1 (Pass@1 = 58.8%, 2026-04-03; published as 63.7% until 2026-09-19, when the denominator was corrected from the 923 compiled solutions to the 1,000 generated — story 128.19). The corpus may grow in subsequent phases. For current statistics, re-run generation scripts or inspect `corpus/manifest.json` directly.

---

## References

- Corpus manifest: `toke-corpus/corpus/manifest.json`
- Corpus schema: `toke-corpus/corpus/schema.json`
- Generation config: `toke-corpus/config.yaml`
- Trial scorecard: `toke-corpus/metrics/scorecard.json`
- Pipeline architecture: `toke-corpus/docs/pipeline-architecture.md`
- Gate 1 reproducibility: `toke-spec/docs/gate1-reproducibility.md`
- Training config: `toke-models/finetune/configs/7b_mlx.yaml`
