# toke Corpus Summary — Phase 2 Training Data

**Date:** 2026-04-12
**Prepared for:** Research review teams (T1–T8)
**Corpus version:** Post-Epic 10.10 (single-line canonicalised, Phase 1 disinfected)

---

## 1. What Are We Training?

A **QLoRA adapter** on top of **Qwen/Qwen2.5-Coder-7B** to generate programs in toke — a small structural programming language with a 56-character alphabet. The model will receive a natural-language task description and emit compilable toke source.

**Base model:** Qwen2.5-Coder-7B
**Method:** QLoRA (rank 64, alpha 128)
**Training format:** Chat JSONL (`system` / `user` / `assistant`)
**Max sequence length:** 2,048 tokens

---

## 2. Training Data at a Glance

| Metric | Value |
|--------|-------|
| **Training rows** | 18,890 |
| **Eval rows** | 994 |
| **Total** | **19,884** |
| **Unique assistant programs** | 15,925 (83.2%) |
| **Unique user prompts** | 4,671 |
| **Unique module names** | 5,467 |
| **Phase 1 contamination** | 0 (fully disinfected) |
| **Syntax audit findings** | 0 in training data |
| **Single-line programs** | 99.96% (8 exceptions: `\n` inside string literal data) |

### Assistant Message Length Distribution

| Chars | Count | % |
|-------|-------|---|
| 0–100 | 823 | 4.3% |
| 101–200 | 4,766 | 24.9% |
| 201–400 | 6,696 | 35.0% |
| 401–800 | 5,227 | 27.3% |
| 801–1,600 | 1,396 | 7.3% |
| 1,600+ | 221 | 1.2% |

Median ~300 chars. Mean 392. P95 at 948. The 1,600+ outliers are multi-function application-tier programs.

---

## 3. How Each Training Row Looks

Every row is a three-message chat exchange:

```json
{
  "messages": [
    {"role": "system",    "content": "<91-line Phase 2 system prompt>"},
    {"role": "user",      "content": "<task-specific prompt>"},
    {"role": "assistant", "content": "<single-line toke source>"}
  ]
}
```

The **system prompt** is identical across all 20,156 rows. It defines:
- The 56-character alphabet and file skeleton (`m=` / `i=` / `t=$name{...}` / `f=name()`)
- Type sigils (`$str`, `$ok`, `$err`, `@(...)` arrays/maps, `!$err` error returns)
- Statement forms (`let`/`mut`, `if`/`el`, `lp`/`br`, match `|{Variant:bind body}`)
- Standard library idioms (`str.len(s)`, `log.info("msg")`, `file.read(...)`)
- A 14-row "Forbidden Forms" table mapping common mistakes to correct Phase 2

The **user prompt** varies by category (see examples below). The **assistant** is raw toke source starting with `m=` — no markdown fences, no prose.

---

## 4. Concrete Examples

### Example 1: Error Repair (BIFI — missing semicolon)

**User:**
```
The following toke program has a missing `;` terminator. Fix it.

Compiler error:
{"schema_version":"1.0","diagnostic_id":"D0001","error_code":"E2003",
"severity":"error","stage":"parse","message":"missing semicolon",...}

Broken program:
m=test
i=yaml:std.yaml;
f=demo():i64{
  let y=yaml.enc("hello");
  let p=yaml.enc("key: value");
  < 0
};
```

**Assistant:**
```
m=test;i=yaml:std.yaml;f=demo():i64{let y=yaml.enc("hello");let p=yaml.enc("key: value");<0};
```

The model sees the broken program with the compiler diagnostic, and emits a correct, single-line fix.

---

### Example 2: Algorithm Generation (A-MTH — math)

**User:**
```
Write a toke program that uses arithmetic / math operations.
```

**Assistant:**
```
m=clamp;f=clamp(x:u64;lo:u64;hi:u64):u64{if(x=lo){<lo};if(x=hi){<hi};if(x<lo){<lo};if(x>hi){<hi};<x};
```

---

### Example 3: Domain Application (EC2-D-CFG — configuration parsing)

**User:**
```
Write a toke program for configuration parsing or management.
```

**Assistant:**
```
m=cfgcheck;f=solve(first:$str):bool{let blank=mut.false;if(first.len=0 as u64){blank=true};let comment=mut.false;if(!(first.len=0 as u64)){let c=first.get(0 as u64);if(c=35 as u8){comment=true};if(!(c=35 as u8)){if(c=32 as u8){comment=true};if(!(c=32 as u8)){if(c=9 as u8){comment=true}}}};let result=mut.false;if(blank){result=true};if(!(blank)){if(comment){result=true}};<result};
```

---

### Example 4: Multi-function Composition (COMPOSE-NEW — chain)

**User:**
```
Write a toke program that composes multiple functions in a chain pattern.
```

**Assistant:**
```
m=boolnotissorted;f=issorted(arr:@i64):bool{let n=arr.len;if(n<2 as u64){<true};lp(let i=1;i<n;i=i+1){let prev=arr.get(i-1);let curr=arr.get(i);if(prev>curr){<false}};<true};f=boolnot(val:bool):bool{if(val){<false};<true};f=boolnotissorted(arr:@i64):bool{<boolnot(issorted(arr))};
```

---

### Example 5: Error Repair (BIFI — undefined variable)

**User:**
```
The following toke program has an undefined variable. Fix it.

Compiler error:
{"schema_version":"1.0","diagnostic_id":"D0001","error_code":"E3011",
"severity":"error","stage":"name_resolution",
"message":"undefined identifier 'countttt'",...}

Broken program:
m=accsign;f=multiplymod(a:i64;b:i64;m:i64):i64{let result=mut.0;lp(let j=0;j<b;j=j+1){let newresult=countttt+a;result=newresult;let modresult=result-m*(result/m);result=modresult;};<result;}
```

**Assistant:**
```
m=accsign;f=multiplymod(a:i64;b:i64;m:i64):i64{let result=mut.0;lp(let j=0;j<b;j=j+1){let newresult=result+a;result=newresult;let modresult=result-m*(result/m);result=modresult;};<result;}
```

---

## 5. Source Type Breakdown

The training set is assembled from four source pools, weighted in the training config:

| Source Type | Config Weight | Train Count | Train % | Description |
|-------------|--------------|-------------|---------|-------------|
| **hand_written** | 20% | 1,005 | 5.3% | Human-authored examples (DOC-EXP documentation excerpts, ERR-GOLD hand-crafted error patterns) |
| **llm_generated** | 40% | 13,586 | 71.0% | LLM-generated programs from task specs — algorithms, domain apps, stdlib demos, compositions |
| **mutation** | 15% | 2,848 | 15.1% | BIFI error-repair bootstrap (inject error → fix it) and ERR-TRIPLE validated error triplets. BIFI capped at 12% |
| **transpiled** | 25% | 0 | 0% | Reserved for future transpiled programs; weight redistributed to other pools |

Hand-written data is <5% of the corpus, so the config avoids oversampling it (research showed forcing 45% via repetition degrades quality).

---

## 6. Category Breakdown (Training Set)

40 categories contribute to the training set, grouped into families:

### Algorithm Families (A-*)
| Category | Count | % | Description |
|----------|-------|---|-------------|
| A-CND | 1,230 | 6.4% | Conditionals, branching |
| A-MTH | 1,179 | 6.2% | Arithmetic, math operations |
| A-SRT | 1,118 | 5.8% | Sorting algorithms |
| A-ARR | 1,044 | 5.5% | Array manipulation |
| A-STR | 870 | 4.5% | String processing |

### Benchmark Programs (B-*)
| Category | Count | % | Description |
|----------|-------|---|-------------|
| B-CMP | 2,176 | 11.4% | Benchmark complexity programs |

### Compositions (COMPOSE-*)
| Category | Count | % | Description |
|----------|-------|---|-------------|
| COMPOSE-NEW | 1,460 | 7.6% | Mechanically composed multi-function chains |
| COMPOSE-C | 984 | 5.1% | Chain composition pattern |
| COMPOSE-D | 769 | 4.0% | Delegating composition pattern |

### Error Repair — BIFI Bootstrap
| Category | Count | % | Description |
|----------|-------|---|-------------|
| BIFI-missing_semicolon | 1,788 | 9.3% | Fix missing `;` (shown with compiler diagnostic) |
| BIFI-undefined_variable | 866 | 4.5% | Fix undefined identifier |
| BIFI-immutable_reassignment | 852 | 4.5% | Fix reassignment to immutable binding |
| BIFI-type_mismatch | 702 | 3.7% | Fix type mismatch |
| BIFI-wrong_argument_count | 117 | 0.6% | Fix wrong number of arguments |

### Error Repair — ERR-TRIPLE
| Category | Count | % | Description |
|----------|-------|---|-------------|
| ERR-TRIPLE-type_mismatch | 54 | 0.3% | Validated error→fix triplets |
| ERR-TRIPLE-immutable_reassignment | 48 | 0.3% | |
| ERR-TRIPLE-undefined_variable | 43 | 0.2% | |
| ERR-TRIPLE-wrong_argument_count | 39 | 0.2% | |
| ERR-TRIPLE-missing_semicolon | 27 | 0.1% | |

### Domain Applications (EC2-D-*)
| Category | Count | % | Description |
|----------|-------|---|-------------|
| EC2-D-DAT | 413 | 2.2% | Data processing |
| EC2-D-CRY | 320 | 1.7% | Cryptographic operations |
| EC2-D-WEB | 319 | 1.7% | Web/HTTP handling |
| EC2-D-CFG | 303 | 1.6% | Configuration parsing |
| EC2-D-FIO | 290 | 1.5% | File I/O |
| EC2-D-CLI | 278 | 1.5% | CLI argument handling |
| EC2-D-NET | 245 | 1.3% | Networking |
| EC2-D-TST | 230 | 1.2% | Testing patterns |

### Hand-Written and Reference
| Category | Count | % | Description |
|----------|-------|---|-------------|
| DOC-EXP | 980 | 5.1% | Documentation-derived examples |
| ERR-GOLD | 25 | 0.1% | Hand-crafted gold-standard error patterns |

### Standard Library Focused (STD-*, STDLIB)
| Category | Count | % | Description |
|----------|-------|---|-------------|
| STDLIB | 55 | 0.3% | General stdlib usage |
| STD-STR | 13 | 0.1% | `std.str` focused |
| STD-DB | 12 | 0.1% | `std.db` focused |
| APP-MULTI | 11 | 0.1% | Multi-stdlib applications |
| STD-TOON | 11 | 0.1% | `std.toon` serialisation |
| STD-HTTP | 10 | 0.1% | `std.http` focused |
| STD-CRYPTO | 8 | <0.1% | `std.crypto` focused |
| STD-TEST | 8 | <0.1% | `std.test` focused |

### Other
| Category | Count | % | Description |
|----------|-------|---|-------------|
| OSS-INST | 106 | 0.6% | OSS-Instruct seed generation |
| MED-CMPLX | 44 | 0.2% | Medium-complexity augmentation |
| SIMPLE-ALG | 30 | 0.2% | Simple algorithm patterns |

---

## 7. Complexity Tier Distribution

Programs are classified into four complexity tiers based on function count, AST depth, token count, and structural features:

| Tier | Train Count | Train % | Target | Status |
|------|-------------|---------|--------|--------|
| **simple** | 3,838 | 20.3% | 25–30% | Floored at 20% via stratified resampling |
| **medium** | 6,882 | 36.4% | 40–45% | Within target |
| **complex** | 3,898 | 20.6% | 20–25% | Within target |
| **application** | 4,272 | 22.6% | 20–25% | Capped at 25% via stratified resampling |
| unclassified | 0 | 0.0% | <15% | Met |

**Notes:**
- Simple tier floored at 20% via stratified resampling: 1,073 unique simple records drawn from the eligible pool plus 1,576 oversampled (repeated with shuffle) from existing non-BIFI simple records.
- Application tier capped at 25%: B-CMP and COMPOSE-NEW deprioritised, 775 records dropped.
- BIFI repair examples capped at ~12% of training (was 22.7%) to align with Gate 2 evaluation format (generation, not repair).

---

## 8. Diversity Metrics

| Metric | Value |
|--------|-------|
| Unique programs (exact dedup) | 15,925 of 19,149 (83.2%) |
| Seed diversity ratio (corpus-level) | 95.8% post-dedup |
| Unique module names (`m=<name>;`) | 5,467 |
| Categories in training | 40 |
| Distinct prompt templates | ~30 (category-specific user prompts) |
| Models used for generation | grok-3-mini, claude-haiku-4-5, claude-sonnet-4-6, bifi-bootstrap, mechanical-chain |
| Generation methods | LLM generation from task specs, BIFI error injection/repair, mechanical composition, OSS-Instruct seeding, hand-authored |

---

## 9. Quality Gates Applied

Every training row passes all of the following before inclusion:

| Gate | Description | Records Filtered |
|------|-------------|-----------------|
| Phase 2 syntax conformant | `syntax_audit.phase2_conformant == true` | 9,053 |
| Compile pass | `compile_check.passed == true` via `tkc --check` | 4,092 |
| Min composite score | Quality score >= 0.35 (coherence + complexity + stdlib + error + multi-fn) | 2,060 |
| Fuzz excluded | Grammar-fuzzed programs (FUZZ category) excluded | All FUZZ |
| Surface check | No uppercase outside match-arm heads, no `==`, `!=`, `[]` | 14 |
| Mutation cap | Max 2 mutations per seed (MUT-* only) | 5 |
| Single-line canonicalised | All whitespace outside strings collapsed, comments stripped | — |
| Phase 1 disinfected | No `F=`, `M=`, `T=`, or camelCase identifiers | — |

**Pipeline**: 188,828 raw corpus records → 106,294 eligible (pass all gates) → 20,156 sampled (source-weighted, stdlib quota top-up).

---

## 10. Raw Corpus Structure

The raw corpus lives in `corpus/phase2_deduplicated/` (188,828 JSON files across 61 category subdirectories). Each file is a self-contained record:

```
corpus/phase2_deduplicated/
  A-ARR/
    P2-A-A-ARR-0001v1-4e4955a2.json
    P2-A-A-ARR-0001v10-a9513451.json
    ...
  BIFI-missing_semicolon/
    BIFI-missing_semicolon-0000-3ddffffe.json
    ...
  COMPOSE-NEW/
    P2-N-COMPOSE-NEW-00001-6ed63aba.json
    ...
  DOC-EXP/
    B-DOCX-0010-2785498b.json
    ...
  (61 categories total)
```

### Corpus Record Schema (v2)

Each JSON record contains:

```json
{
  "id": "P2-A-A-ARR-0001v1-4e4955a2",
  "version": 2,
  "phase": "B",
  "task_id": "A-ARR-0001v1",
  "category": "A-ARR",
  "model": "grok-3-mini",
  "attempts": 1,

  "tk_source": "m=sum;f=sum(arr:@i64):i64{let total=mut.0;lp(...);};",
  "tk_tokens": 46,

  "validation": {"compiler_exit_code": 0, "error_codes": []},
  "differential": {
    "languages_agreed": ["c", "java", "python"],
    "majority_output": "15\n0\n42\n3\n0"
  },
  "judge": {"accepted": true, "score": 0.9713},

  "references": {
    "python_source": "def sum(arr): ...",
    "python_tokens": 178,
    "c_source": "#include <stdio.h> ...",
    "c_tokens": 298,
    "java_source": "public class Solution { ... }",
    "java_tokens": 192
  },

  "syntax_audit": {
    "violations": [],
    "autofix_applied": false,
    "phase2_conformant": true,
    "checked_at": "2026-04-10T23:15:37Z"
  },
  "compile_check": {
    "passed": true,
    "exit_code": 0,
    "error_codes": [],
    "ran_at": "2026-04-12T00:03:31Z",
    "tkc_version": "tkc 0.1.0"
  },
  "imported_modules": [],
  "imports_unresolved": [],
  "runtime_check": {
    "ran": false,
    "skipped_reason": "no_main",
    "exit_code": null,
    "stdout": null,
    "stderr": null
  },
  "judge_output_check": {
    "verified": false,
    "correct": null
  },
  "canonicalisation": {
    "single_line": true,
    "comments_stripped": true,
    "orig_newlines": 12,
    "orig_len": 480,
    "new_len": 392
  }
}
```

**BIFI records** additionally have `references.broken_source`, `references.injection_type`, and `references.diagnostic` (the compiler error produced by the broken form).

**COMPOSE-NEW records** additionally have `composition.pattern` (chain/edge-guard/accumulator/pipeline3/fanout) and `composition.source_entry_ids` (the seed records composed together).

---

## 11. Corpus-Level Statistics

| Metric | Value |
|--------|-------|
| Total raw records | 188,828 |
| Categories | 61 |
| Compile pass rate | 94.8% (179,024 pass `tkc --check`) |
| Phase 2 conformant | 88.7% (167,448 pass syntax audit) |
| Runtime checked | 563 (1,442 attempted, 879 link-fail from unimplemented stdlib) |
| Runtime correct (LLM-judged) | 313 of 563 (55.6%) |

### Top 20 Raw Corpus Categories

| Category | Records | Description |
|----------|---------|-------------|
| BIFI-missing_semicolon | 32,994 | Error injection: missing `;` |
| MUT-let_to_mut | 32,090 | Mutation: `let` ↔ `mut` swap |
| BIFI-undefined_variable | 17,015 | Error injection: undefined ident |
| BIFI-immutable_reassignment | 16,692 | Error injection: assign to `let` |
| BIFI-type_mismatch | 12,575 | Error injection: wrong types |
| MUT-variable_rename | 12,426 | Mutation: rename identifiers |
| FUZZ | 8,400 | Grammar-fuzzed (excluded from training) |
| MUT-type_widening | 7,802 | Mutation: widen numeric types |
| MUT-constant_variation | 5,376 | Mutation: tweak constant values |
| B-CMP | 4,436 | Benchmark programs |
| A-CND | 3,663 | Conditional algorithms |
| A-MTH | 3,502 | Math algorithms |
| A-SRT | 3,422 | Sorting algorithms |
| COMPOSE-NEW | 3,093 | Multi-function compositions |
| A-ARR | 2,984 | Array algorithms |
| A-STR | 2,070 | String algorithms |
| COMPOSE-C | 2,062 | Chain compositions |
| BIFI-wrong_argument_count | 1,823 | Error injection: wrong arg count |
| COMPOSE-D | 1,575 | Delegating compositions |
| DOC-EXP | 1,488 | Documentation examples |

### Smallest Categories (long tail)

| Category | Records |
|----------|---------|
| STD-LOG | 8 |
| ERR-PATTERN | 8 |
| STD-I18N | 7 |
| STD-JSON | 7 |
| STD-FILE | 7 |
| STD-ENV | 6 |
| STD-YAML | 5 |
| A-ERR | 4 |
| ERR-HANDLE | 3 |
| ERR-TRIPLE-wrong_operator | 1 |

---

## 12. Training Configuration

```yaml
base_model: Qwen/Qwen2.5-Coder-7B
method: qlora
lora_rank: 64
lora_alpha: 128
epochs: 3  # 2 broad + 1 refinement (two-stage curriculum)
batch_size: 4
gradient_accumulation_steps: 8
eval_split: 0.05
warmup_ratio: 0.03
max_seq_length: 2048
```

**Two-stage curriculum** (planned):
- **Stage 1** (2 epochs, LR 2e-4): Train on all source types — broad exposure to algorithms, error repair, compositions, domain apps
- **Stage 2** (1 epoch, LR 5e-5): Refine on hand-written + LLM-generated only — exclude mutations and transpiled, lower learning rate for polish

**Source weights**: hand_written 20%, llm_generated 40%, transpiled 25% (currently empty, redistributed), mutation 15%.

---

## 13. Known Gaps and Limitations

| Gap | Current | Target | Notes |
|-----|---------|--------|-------|
| Stdlib coverage | 22/74 functions at floor (>=20 examples) | All 74 | 52 functions have <20 examples in the entire corpus; 11 have zero. Requires new program generation |
| Simple complexity | 20.3% | 25–30% | Floored at 20% via resampling; remaining gap is acceptable |
| Application complexity | 22.6% | 20–25% | Capped via stratified resampling |
| Match-arm idiom | 2.5% | >=5% | Corpus density limit — few programs use `\|{...}` pattern matching |
| Tokenizer | Not retrained | Custom BPE | Eval pending (Epic 10.5) |
| Runtime verification | 563 checked | More | Link failures from unimplemented stdlib modules |

---

## 14. Scripts and Reproducibility

All preparation scripts live in `toke-corpus/scripts/`:

| Script | Purpose |
|--------|---------|
| `prepare_training_data.py` | Source-weighted sampling, quality gates, chat-format JSONL output |
| `validate_training_format.py` | Validates every row: schema, system prompt match, surface checks |
| `canonicalise_tk_source.py` | Single-line canonicalisation of `tk_source` (string-aware) |
| `disinfect_task_specs.py` | Phase 1 → Phase 2 rewrite of task specs and corpus |
| `audit_phase2_syntax.py` | Full syntax audit with match-arm-aware state machine |
| `classify_complexity_dedup.py` | Complexity tier classification (simple/medium/complex/application) |
| `compile_check_corpus.py` | Bulk `tkc --check` on all 188,828 records |
| `phase2_syntax_audit.py` | Original syntax audit + autofix pass |
| `phase2_autofix_v2.py` | `$str` type fix + match-arm restoration |
| `deduplicate_corpus.py` | Structural deduplication (197K → 188,828) |
| `bifi_bootstrap.py` | BIFI error injection/repair pipeline |
| `expand_compositions.py` | Mechanical multi-function composition |
| `generate_manifest.py` | Corpus manifest generation |

**To regenerate training data from scratch:**
```bash
python3 scripts/prepare_training_data.py \
  --config infra/training_config.yaml \
  --corpus-dir corpus/phase2_deduplicated \
  --output-dir data \
  --seed 42
```

**To validate:**
```bash
python3 scripts/validate_training_format.py
python3 scripts/audit_phase2_syntax.py --corpus-only --summary
```

---

## 15. Epics Completed (Corpus Preparation)

| Epic | Stories | Status |
|------|---------|--------|
| 10.1 — Corpus Deduplication and Quality Gate | 4/4 | Done |
| 10.2 — Error Handling Corpus | 4/4 | Done |
| 10.3 — Standard Library Coverage Expansion | 4/4 | Done |
| 10.4 — Program Complexity Rebalancing | 6/6 | Done |
| 10.8 — Corpus Record Validation and Enrichment | 9/9 | Done |
| 10.9 — Training Format and Instruction Quality | 5/5 | Done |
| 10.10 — Single-Line Canonicalisation and Syntax Audit | 3/3 | Done |

**Remaining (training execution):** 10.5 Tokenizer Retraining (1/3), 10.6 Data Mixing (2/3), 10.7 Gate 2 Evaluation (0/5).
