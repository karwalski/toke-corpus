# Corpus Data Quality Notes

**Last updated:** 2026-04-15
**Corpus:** `corpus/phase2_deduplicated/` (188,830 records)

## Overall Quality

| Metric | Value | Notes |
|--------|-------|-------|
| Phase 2 syntax conformant | 88.3% | After autofix passes (10.8.2, 10.8.5b) |
| Compile pass rate (effective) | 94.8% | Includes broken_ok for BIFI/ERR-TRIPLE categories |
| Compile pass rate (strict) | ~50% | tk_source only, excluding repair categories |
| Runtime execution | 563 records ran | Most records are library-style (no main function) |
| Judge correctness | 55.6% of runnable | 313/563 judged correct by Sonnet 4.6 |

## Known Issues by Category

### Records with unfixable issues (22,078 total)

These records have uppercase identifier collisions that the autofix could not resolve without semantic analysis. They are **not included in training data** (filtered by the surface gate in `prepare_training_data.py`).

Highest concentrations:
- MUT-let_to_mut: 7,580
- BIFI-missing_semicolon: 2,864
- MUT-variable_rename: 2,055
- MUT-type_widening: 1,591
- BIFI-undefined_variable: 1,554

### Compile failures (9,804 records)

Records that fail `tkc --check` after all autofix passes. Root causes:
- Type-check errors (E4031) in ERR-TRIPLE and MUT categories
- These are deeper semantic issues, not syntax problems

### FUZZ entries (9,099 records)

Grammar-fuzzed programs with very low quality scores (mean 0.19 vs 0.68 for non-fuzz). **Excluded from training** via quality gate (`min_score 0.35`).

### Fabricated stdlib imports (3,064 records)

Records that import non-existent stdlib modules:
- `std.llm` (1,031), `std.io` (827), `std.dataframe` (468), `std.math` (361), `std.analytics` (287)
- These are hallucinated module names from LLM generation
- **Not included in training** because they fail compile check

### BIFI repair categories

BIFI (Bootstrap Iterative Fix) records contain intentionally broken `references.broken_source` alongside the fixed `tk_source`. The broken source is verified to **correctly fail** compilation (0 broken_bad). These are valid training data for error-repair tasks.

## Training Data Quality

The training set (`data/train.jsonl`, 18,890 rows) applies strict gates:
1. Phase 2 syntax conformant (surface gate)
2. Passes `tkc --check` (compile gate)
3. Composite quality score >= 0.35
4. No uppercase letters outside string literals and match-arm heads
5. No `==`, `!=`, or `[]` outside string literals
6. Max 2 mutations per seed task

**Result: 0 surface issues in final training data.**

## Source Provenance

| Source | Count in Training | % |
|--------|-------------------|---|
| LLM-generated (Claude Haiku, GPT-4.1-mini, Grok-3-mini, Sonnet) | 13,628 | 71.7% |
| Mutation/BIFI/Error repair | 4,438 | 23.4% |
| Hand-written | 933 | 4.9% |
| Transpiled | 0 | 0% |
