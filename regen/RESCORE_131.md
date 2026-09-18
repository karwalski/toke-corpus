# RESCORE_131 — corpus re-score on the tkc pattern rules (story 131.10)

Generated 2026-09-18T13:33:11Z · 23,382 records · tkc sha256 3753cc55cec3, 69de23080d72 · proxy proxy8k-5f3f74314ce4.json · idiom floor 0.6

**Why this exists.** Before 131.9 `tkc --lint --diag-json` emitted no JSON, so `metrics.lint()` returned `[]` on every record and the 129.6 "lint 0 warnings" gate was vacuous; the only idiom signal on the frozen corpus was a regex approximation. This sweep scores every frozen record with the real pattern rules (`docs/lint-rules-v1.md`, Pattern rules) and reports what the new hard gate (`run_shard.py validate`: any pattern-rule error/warning fails, hints pass, spec style mandates exempt) would reject. It is the input to 131.13's bucketing; no record was modified.

## Headline

| metric | value |
|---|---|
| records failing the new hard gate (net of style mandates) | **306** (1.3%) |
| records failing gross (before mandate exemption) | 600 |
| records with any pattern hit (incl. hints) | 8,686 |
| below idiom floor — old regex judge | 0 |
| below idiom floor — new pattern judge | 16 (newly below: 16) |
| mean idiom old → new | 0.9862 → 0.9823 (9,638 records changed score: 8,006 down, 1,632 up; 0 were below floor only under the regex) |
| over proxy budget (> 1.5× category/task_type median) | 4,788 |
| proxy tokens p50 / p90 / p99 | 26 / 56 / 106 |
| records with a stub `unused-import` dropped (harness exemption) | 8,439 |
| `discarded-value-result` hits suppressed as a linter false positive (expression-if branch value; see below) | 48 hits on 31 records |
| sweep errors | 0 |

**Linter false positive found by this sweep.** `discarded-value-result` (severity error) fires on a value-returning call that is the last expression (the value) of an expression-`if` branch — `out=if(c){out.append(v)}el{out}`, `let y=if(c){x.push(9)}el{x}`, `<if(c){r.append(v)}el{r}`, `m=if(t){let v=a.get(k);k=k+1;m.append(v)}el{m}` — where the value is the branch's value, not discarded (the programs run correctly). `idiom_judge.suppress_linter_fps` tags those hits `suppressed` (not scored, not gated) until `src/lint.c` is fixed; the counts above are the suppressed hits. Needs a 127/131 story.

## Hard-gate failures by rule (records)

| rule | severity | penalty | records hit | total hits | fixable hits | hard-fail gross | mandate-exempt | hard-fail net |
|---|---|---|---|---|---|---|---|---|
| `mut-flag-if` | warning | −0.15 | 267 | 270 | 0 | 267 | 237 | 30 |
| `flag-soup` | warning | −0.15 | 98 | 99 | 0 | 98 | 58 | 40 |
| `string-concat-chain` | warning | −0.15 | 238 | 294 | 0 | 238 | 0 | 238 |
| `discarded-value-result` | error | −0.20 | 0 | 0 | 0 | 0 | 0 | 0 |
| `loop-rebuilds-array` | hint | −0.05 | 1,481 | 1,733 | 0 | 0 | 286 | 0 |
| `single-use-let` | hint | −0.02 | 7,105 | 11,079 | 1,741 | 0 | 1,761 | 0 |

Mandate exemptions (`quality_rubric.md` Exemptions, `idiom_judge.MANDATE_EXEMPTIONS`): "…mutable binding…" → mut-flag-if, flag-soup, loop-rebuilds-array; "…helper variable…" → single-use-let; "…loop to compute the result…" → loop-rebuilds-array. `discarded-value-result` is never exempt.

## Per-category violations by rule (records with ≥ 1 hit)

| category | records | `mut-flag-if` | `flag-soup` | `string-concat-chain` | `discarded-value-result` | `loop-rebuilds-array` | `single-use-let` | hard-fail net | below floor old → new |
|---|---|---|---|---|---|---|---|---|---|
| A-ARR | 1,714 | 2 | 1 | 17 | 0 | 339 | 316 | 19 | 0 → 0 |
| A-CND | 1,714 | 26 | 31 | 0 | 0 | 1 | 467 | 11 | 0 → 0 |
| A-ERR | 1,546 | 3 | 0 | 1 | 0 | 103 | 817 | 1 | 0 → 0 |
| A-MTH | 1,714 | 8 | 2 | 1 | 0 | 2 | 334 | 6 | 0 → 1 |
| A-SRT | 1,714 | 0 | 0 | 12 | 0 | 199 | 433 | 12 | 0 → 0 |
| A-STR | 1,714 | 10 | 2 | 19 | 0 | 42 | 581 | 23 | 0 → 0 |
| D-CFG | 1,596 | 26 | 10 | 25 | 0 | 45 | 476 | 28 | 0 → 0 |
| D-CLI | 1,636 | 19 | 6 | 13 | 0 | 21 | 448 | 22 | 0 → 0 |
| D-CRY | 1,714 | 22 | 10 | 0 | 0 | 16 | 699 | 7 | 0 → 0 |
| D-DAT | 1,686 | 2 | 1 | 56 | 0 | 449 | 432 | 57 | 0 → 6 |
| D-FIO | 1,546 | 28 | 0 | 54 | 0 | 87 | 417 | 55 | 0 → 6 |
| D-NET | 1,714 | 41 | 26 | 14 | 0 | 7 | 706 | 26 | 0 → 1 |
| D-TST | 1,713 | 49 | 5 | 11 | 0 | 158 | 470 | 22 | 0 → 1 |
| D-WEB | 1,661 | 31 | 4 | 15 | 0 | 12 | 509 | 17 | 0 → 1 |
| **all** | 23,382 | 267 | 98 | 238 | 0 | 1,481 | 7,105 | 306 | 0 → 16 |

## Proxy-token distribution and budget

Budget file: `regen/freeze/proxy_budget_v04.json` — median per `category/task_type` over the frozen corpus, budget = 1.5 × median; `over_budget` is a **soft** flag (131.13 decides whether it becomes hard). Task-type keying: a single_function record is the function plus the harness stub, a full_program carries `main` and its prints — one median per category would flag nearly every full_program in the A-categories.

| category / task_type | n | median | budget | p50 | p90 | p99 | max | over budget |
|---|---|---|---|---|---|---|---|---|
| A-ARR/full_program | 911 | 35 | 52 | 35 | 59 | 86 | 118 | 151 |
| A-ARR/migrate_fix | 171 | 24 | 36 | 24 | 42 | 80 | 157 | 28 |
| A-ARR/single_function | 632 | 15.0 | 22 | 15 | 31 | 51 | 121 | 145 |
| A-CND/full_program | 955 | 38 | 57 | 38 | 61 | 113 | 148 | 118 |
| A-CND/migrate_fix | 171 | 25 | 38 | 25 | 55 | 149 | 149 | 46 |
| A-CND/single_function | 588 | 21.0 | 32 | 21 | 40 | 106 | 156 | 104 |
| A-ERR/full_program | 967 | 42 | 63 | 42 | 65 | 89 | 127 | 111 |
| A-ERR/migrate_fix | 3 | 28 | 42 | 28 | 49 | 49 | 49 | 1 |
| A-ERR/single_function | 576 | 23.0 | 34 | 23 | 51 | 139 | 216 | 149 |
| A-MTH/full_program | 944 | 35.0 | 52 | 35 | 68 | 105 | 153 | 187 |
| A-MTH/migrate_fix | 171 | 33 | 50 | 33 | 66 | 100 | 111 | 38 |
| A-MTH/single_function | 599 | 20 | 30 | 20 | 46 | 98 | 256 | 141 |
| A-SRT/full_program | 952 | 47.0 | 70 | 47 | 89 | 160 | 254 | 196 |
| A-SRT/migrate_fix | 171 | 49 | 74 | 49 | 84 | 148 | 199 | 28 |
| A-SRT/single_function | 591 | 28 | 42 | 28 | 92 | 166 | 276 | 185 |
| A-STR/full_program | 920 | 39.0 | 58 | 39 | 71 | 129 | 145 | 190 |
| A-STR/migrate_fix | 171 | 36 | 54 | 36 | 73 | 117 | 170 | 41 |
| A-STR/single_function | 623 | 26 | 39 | 26 | 55 | 109 | 133 | 167 |
| D-CFG/full_program | 798 | 22.0 | 33 | 22 | 36 | 71 | 119 | 113 |
| D-CFG/migrate_fix | 171 | 29 | 44 | 29 | 62 | 132 | 144 | 49 |
| D-CFG/single_function | 627 | 15 | 22 | 15 | 50 | 115 | 140 | 160 |
| D-CLI/full_program | 848 | 22.0 | 33 | 22 | 43 | 72 | 129 | 186 |
| D-CLI/migrate_fix | 171 | 28 | 42 | 28 | 73 | 122 | 154 | 38 |
| D-CLI/single_function | 617 | 14 | 21 | 14 | 37 | 55 | 108 | 171 |
| D-CRY/full_program | 942 | 28.0 | 42 | 28 | 54 | 83 | 107 | 170 |
| D-CRY/migrate_fix | 171 | 34 | 51 | 34 | 76 | 119 | 137 | 50 |
| D-CRY/single_function | 601 | 16 | 24 | 16 | 34 | 63 | 91 | 160 |
| D-DAT/full_program | 910 | 33.0 | 50 | 33 | 51 | 72 | 128 | 94 |
| D-DAT/migrate_fix | 171 | 21 | 32 | 21 | 46 | 111 | 130 | 35 |
| D-DAT/single_function | 605 | 15 | 22 | 15 | 29 | 47 | 57 | 132 |
| D-FIO/full_program | 761 | 22 | 33 | 22 | 38 | 62 | 125 | 128 |
| D-FIO/migrate_fix | 171 | 30 | 45 | 30 | 55 | 97 | 109 | 30 |
| D-FIO/single_function | 614 | 17.0 | 26 | 17 | 31 | 48 | 76 | 110 |
| D-NET/full_program | 948 | 24.5 | 37 | 25 | 51 | 87 | 122 | 195 |
| D-NET/migrate_fix | 171 | 36 | 54 | 36 | 82 | 128 | 168 | 55 |
| D-NET/single_function | 595 | 17 | 26 | 17 | 38 | 64 | 79 | 166 |
| D-TST/full_program | 1,003 | 25 | 38 | 25 | 46 | 101 | 118 | 196 |
| D-TST/migrate_fix | 171 | 15 | 22 | 15 | 41 | 69 | 80 | 46 |
| D-TST/single_function | 539 | 13 | 20 | 13 | 27 | 70 | 83 | 80 |
| D-WEB/full_program | 863 | 23 | 34 | 23 | 50 | 83 | 241 | 184 |
| D-WEB/migrate_fix | 171 | 34 | 51 | 34 | 78 | 192 | 209 | 43 |
| D-WEB/single_function | 627 | 15 | 22 | 15 | 39 | 62 | 121 | 171 |
| **all** | 23,382 | — | — | 26 | 56 | 106 | 276 | 4,788 |

## Other lint rules seen (not gated on the shard path; gated for library ingest)

| rule | total hits |
|---|---|
| `mutable-never-mutated` | 162 |
| `unused-import` | 58 |
| `unused-let` | 6 |

## Samples per hard-gate rule (for false-positive triage)

- `mut-flag-if`: `A-ARR-0014v5`, `A-CND-0017v53`, `A-CND-0018v77`, `A-CND-0019v77`, `A-CND-0023v5`, `A-CND-0024v21`, `A-CND-0026v69`, `A-CND-0027v53`
- `string-concat-chain`: `A-ARR-0032v46`, `A-ARR-0042v13`, `A-ARR-0044v38`, `A-ARR-0045v8`, `A-ARR-0056`, `A-ARR-0057v37`, `A-ARR-0068v37`, `A-ARR-0072v19`
- `flag-soup`: `A-CND-0013v3`, `A-CND-0043v37`, `A-CND-0050v29`, `A-CND-0050v37`, `A-CND-0050v45`, `A-CND-0050v5`, `A-CND-0050v53`, `A-CND-0050v61`

Per-record rows: `corpus/regen_v04/audit/rescore_131.jsonl` (`idiom_score_old`, `idiom_score_new`, `violations`, `hard_fail_gross`, `hard_fail_net`, `lint_exempt`, `proxy_tokens`, `over_budget`); aggregates: `audit/rescore_131_summary.json`.
