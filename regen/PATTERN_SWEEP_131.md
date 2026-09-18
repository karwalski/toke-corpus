# PATTERN_SWEEP_131 — corpus + library conformance sweep (story 131.13)

Generated 2026-09-18T15:58:08Z · 24,965 rows (23,382 regen records + 1,583 library programs) · tkc sha256 936ce131174f · catalogue `015cb73f4a2c` (46 entries) · sweep errors 0

**What this is.** Every frozen record and every library program linted with the real `tkc --lint --diag-json` pattern rules, each hit mapped to a `patterns/catalogue.json` entry, exemptions applied, a catalogue-estimated proxy-token saving and a perf-sensitivity flag derived, and ONE bucket assigned per row. The bucket file `corpus/regen_v04/audit/pattern_sweep.jsonl` is the input to 131.14 (AUTO), 131.15 (AGENT), 131.16 (REGEN) and 131.17 (library). No record was modified.

## Thresholds and protocol (CLI parameters, also in `pattern_sweep_summary.json`)

| parameter | value |
|---|---|
| `--agent-saving` (AGENT when est_saving_tokens ≥) | 3 |
| `--concat-fix-armed` (string-concat-chain fix counts as deterministic) | False |
| `--preform-exempt` (migrate_fix pre-forms exempt) | True |
| REGEN carve-out class | `A-*/full_program` |
| deterministic rules | `discarded-value-result`, `single-use-let` |
| `--budget-max-over` (share of records allowed over budget) | 5% |
| bucket order | REGEN carve-out (A-*/full_program) → EXEMPT → AUTO → AGENT → LEAVE |

Protocol: **REGEN** = A-side `full_program` (no test lock; regenerated once with card v2 by 131.16 — never rewritten; the bucket the row would otherwise take is kept in `would_bucket`). **EXEMPT** = ≥ 1 hit, all exempt. **AUTO** = every net violation carries a deterministic tkc `fix` (`single-use-let` inline, `discarded-value-result` receiver; the concat fix is dormant). **AGENT** = a gate (error/warning) violation without a fix, or est. saving ≥ 3 proxy tokens, or a perf-sensitive pattern. **LEAVE** = 0 net violations, or only fix-less hints below the saving threshold and not perf-flagged. Library rows also go to AGENT on a non-lint 131.17 gate failure (structure, new-judge idiom floor, compile, NUL bytes) and to AUTO when only fixable lint warnings remain.

## Buckets

| scope | records | EXEMPT | AUTO | AGENT | LEAVE | REGEN | AGENT batches (20) | AUTO batches (20) |
|---|---|---|---|---|---|---|---|---|
| regen | 23,382 | 1,678 | 643 | 1,197 | 14,215 | 5,649 | 60 | 33 |
| library | 1,583 | 0 | 53 | 1,272 | 258 | 0 | 64 | 3 |
| **all** | 24,965 | 1,678 | 696 | 2,469 | 14,473 | 5,649 | 124 | 35 |

REGEN rows would otherwise bucket as: EXEMPT 514, AUTO 67, AGENT 338, LEAVE 4,730.

### Per category

| category | records | EXEMPT | AUTO | AGENT | LEAVE | REGEN | net-violation records | hard-fail net | perf_sensitive | est_saving Σ |
|---|---|---|---|---|---|---|---|---|---|---|
| A-ARR | 1,714 | 55 | 19 | 98 | 631 | 911 | 463 | 19 | 0 | 1,013 |
| A-CND | 1,714 | 72 | 30 | 8 | 649 | 955 | 334 | 8 | 1 | 62 |
| A-ERR | 1,546 | 44 | 5 | 34 | 496 | 967 | 727 | 1 | 0 | 269 |
| A-MTH | 1,714 | 37 | 54 | 3 | 676 | 944 | 238 | 4 | 0 | 24 |
| A-SRT | 1,714 | 35 | 23 | 83 | 621 | 952 | 485 | 12 | 0 | 868 |
| A-STR | 1,714 | 41 | 20 | 35 | 698 | 920 | 519 | 23 | 35 | 451 |
| D-CFG | 1,596 | 187 | 17 | 75 | 1,317 | 0 | 387 | 28 | 5 | 291 |
| D-CLI | 1,636 | 144 | 24 | 46 | 1,422 | 0 | 353 | 22 | 5 | 230 |
| D-CRY | 1,714 | 206 | 191 | 20 | 1,297 | 0 | 536 | 3 | 1 | 69 |
| D-DAT | 1,686 | 154 | 31 | 413 | 1,088 | 0 | 656 | 57 | 0 | 1,684 |
| D-FIO | 1,546 | 118 | 44 | 145 | 1,239 | 0 | 430 | 55 | 12 | 759 |
| D-NET | 1,714 | 204 | 71 | 40 | 1,399 | 0 | 569 | 26 | 8 | 224 |
| D-TST | 1,713 | 205 | 54 | 159 | 1,295 | 0 | 453 | 21 | 0 | 570 |
| D-WEB | 1,661 | 176 | 60 | 38 | 1,387 | 0 | 387 | 17 | 9 | 210 |
| L-AIA | 106 | 0 | 0 | 102 | 4 | 0 | 102 | 85 | 3 | 1,587 |
| L-CRY | 99 | 0 | 7 | 58 | 34 | 0 | 85 | 35 | 4 | 496 |
| L-DAT | 122 | 0 | 1 | 117 | 4 | 0 | 117 | 87 | 5 | 987 |
| L-DEV | 120 | 0 | 4 | 108 | 8 | 0 | 108 | 89 | 6 | 1,252 |
| L-EDU | 122 | 0 | 1 | 115 | 6 | 0 | 117 | 94 | 4 | 1,276 |
| L-FIN | 122 | 0 | 2 | 55 | 65 | 0 | 105 | 34 | 1 | 240 |
| L-GAM | 91 | 0 | 1 | 77 | 13 | 0 | 80 | 48 | 2 | 443 |
| L-GAZ | 123 | 0 | 0 | 122 | 1 | 0 | 122 | 122 | 1 | 1,043 |
| L-MED | 122 | 0 | 2 | 75 | 45 | 0 | 94 | 44 | 6 | 339 |
| L-MFG | 97 | 0 | 3 | 90 | 4 | 0 | 89 | 65 | 0 | 755 |
| L-MSG | 110 | 0 | 4 | 100 | 6 | 0 | 102 | 90 | 3 | 1,646 |
| L-NET | 7 | 0 | 3 | 4 | 0 | 0 | 5 | 3 | 0 | 17 |
| L-SCI | 105 | 0 | 2 | 88 | 15 | 0 | 97 | 55 | 0 | 288 |
| L-SEC | 40 | 0 | 10 | 18 | 12 | 0 | 36 | 16 | 0 | 157 |
| L-SOC | 142 | 0 | 11 | 96 | 35 | 0 | 131 | 91 | 1 | 1,080 |
| L-SYS | 55 | 0 | 2 | 47 | 6 | 0 | 47 | 38 | 0 | 377 |

### Per task_type

| task_type | records | EXEMPT | AUTO | AGENT | LEAVE | REGEN | net-violation records |
|---|---|---|---|---|---|---|---|
| full_program | 12,722 | 855 | 252 | 551 | 5,415 | 5,649 | 4,035 |
| migrate_fix | 2,226 | 9 | 211 | 106 | 1,900 | 0 | 794 |
| single_function | 8,434 | 814 | 180 | 540 | 6,900 | 0 | 1,708 |
| stdin_program | 1,583 | 0 | 53 | 1,272 | 258 | 0 | 1,437 |

## Violations by rule (all rows)

| rule | pattern(s) | records gross | records net | hits gross | hits net | deterministic hits | exempt hits |
|---|---|---|---|---|---|---|---|
| `mut-flag-if` | `cond-bind-if` | 498 | 258 | 602 | 360 | 0 | 242 |
| `flag-soup` | `cond-bool-combine` | 166 | 101 | 181 | 115 | 0 | 66 |
| `string-concat-chain` | `str-interp-vs-join`, `str-build-loop` | 1,128 | 1,128 | 2,299 | 2,299 | 0 | 0 |
| `discarded-value-result` | — | 2 | 0 | 9 | 0 | 0 | 9 |
| `loop-rebuilds-array` | `iter-map`, `iter-filter` | 1,529 | 1,243 | 1,813 | 1,482 | 0 | 331 |
| `single-use-let` | `fn-chain-vs-let` | 8,453 | 6,692 | 15,578 | 13,065 | 1,891 | 2,513 |
| `hand-rolled-parser` | `parse-json` | 112 | 112 | 142 | 142 | 0 | 0 |

Exemptions by reason: `stub_prefix` 11,981, `style_mandate` 3,140, `migrate_fix_preform` 12, `suspect-fp-vec-handle-receiver` 9. Records exempt only through a migrate_fix pre-form: 10 (NOTE: `run_shard.validate_one_gates` does not know this exemption — a re-validate of those records still hard-fails on the warning; decision for the main thread).

### By pattern_id (gross → net hits, records, Σ est. saving)

| pattern_id | hits gross | hits net | records | Σ est_saving | perf_sensitive |
|---|---|---|---|---|---|
| `fn-chain-vs-let` | 15,578 | 13,065 | 6,692 | 0 |  |
| `str-build-loop` | 1,171 | 1,171 | 744 | 2,342 |  |
| `str-interp-vs-join` | 1,128 | 1,128 | 631 | 7,896 |  |
| `iter-map` | 1,109 | 916 | 829 | 2,748 |  |
| `iter-filter` | 704 | 566 | 474 | 2,264 |  |
| `cond-bind-if` | 602 | 360 | 258 | 1,800 |  |
| `cond-bool-combine` | 181 | 115 | 101 | 805 |  |
| `parse-json` | 142 | 142 | 112 | 852 | hot_path=c |
| `(none)` | 9 | 0 | 0 | 0 |  |

Rule → pattern mapping: `mut-flag-if` → `cond-bind-if`(b); `flag-soup` → `cond-bool-combine`(b); `string-concat-chain` → `str-interp-vs-join`(c), `str-build-loop`(a); `single-use-let` → `fn-chain-vs-let`(b); `loop-rebuilds-array` → `iter-map`(b), `iter-filter`(b); `discarded-value-result` → no catalogue entry; `hand-rolled-parser` → `parse-json`(b). `string-concat-chain` inside a `lp` body → `str-build-loop`, else `str-interp-vs-join`; `loop-rebuilds-array` 'copies' → `iter-filter`, transform / index → `iter-map`. Alternatives sharing a rule (not distinguishable from the diagnostic): `mut-flag-if`: cond-elif-chain, cond-clamp, cond-bool-render; `single-use-let`: err-default; `hand-rolled-parser`: parse-delim-split, parse-fields, parse-int, io-read-lines.

## Estimated saving per bucket (proxy8k tokens, per record)

| bucket | records | Σ est_saving | p50 | p90 | max |
|---|---|---|---|---|---|
| EXEMPT | 1,678 | 0 | 0 | 0 | 0 |
| AUTO | 696 | 0 | 0 | 0 | 0 |
| AGENT | 2,469 | 17,280 | 4 | 16 | 95 |
| LEAVE | 14,473 | 0 | 0 | 0 | 0 |
| REGEN | 5,649 | 1,427 | 0 | 0 | 16 |
| **all** | 24,965 | 18,707 | | | |

### Top-10 pattern_ids by estimated saving

| # | pattern_id | Σ est_saving | net hits | records |
|---|---|---|---|---|
| 1 | `str-interp-vs-join` | 7,896 | 1,128 | 631 |
| 2 | `iter-map` | 2,748 | 916 | 829 |
| 3 | `str-build-loop` | 2,342 | 1,171 | 744 |
| 4 | `iter-filter` | 2,264 | 566 | 474 |
| 5 | `cond-bind-if` | 1,800 | 360 | 258 |
| 6 | `parse-json` | 852 | 142 | 112 |
| 7 | `cond-bool-combine` | 805 | 115 | 101 |
| 8 | `fn-chain-vs-let` | 0 | 13,065 | 6,692 |

## perf_sensitive

Records flagged: **112** (regen 76, library 36). A record is perf-sensitive when a matched catalogue entry has a `hot_path` form or its canonical form is expected-worse at runtime than a sibling. Catalogue entries that qualify: `acc-min-max` (hot_path=a), `parse-json` (hot_path=c), `parse-kv-lines` (hot_path=b), `iter-filter-sum` (hot_path=b), `fn-helper-vs-inline` (canonical a runtime tied vs a 'best' sibling), `fn-recursion-vs-loop` (hot_path=b), `io-write-accumulate` (hot_path=a).

By pattern: `parse-json` 112

Of the tkc pattern rules only `hand-rolled-parser` (judge regex → `parse-json`, hot path `c`) reaches a perf-sensitive entry: the linter's rules describe token-shape patterns, not the runtime trade-offs, so Tier-2 in 131.15 is confined to those rows.

## AGENT wave size

| scope | AGENT records | batches of 20 |
|---|---|---|
| regen (131.15) | 1,197 | 60 |
| library (131.17) | 1,272 | 64 |
| AUTO regen (131.14) | 643 | 33 |
| AUTO library | 53 | 3 |
| REGEN (131.16, fixed) | 5,649 | 283 |

## Library split (1,583 programs → 131.17)

Buckets: EXEMPT 0, AUTO 53, AGENT 1,272, LEAVE 258, REGEN 0. Gate failures on the current source (any combination; new pattern judge, `metrics.analyse` structure): `structure` 1,029, `idiom` 354, `lint` 207, `nul_bytes` 4. Bucket reasons: library gate 1,057, gate violation without fix 214, only fix-less hints, est_saving 0 < 3 185, 0 hits 73, library lint warnings all carry a fix 44, all 1 net violation(s) carry a deterministic fix 6, all 3 net violation(s) carry a deterministic fix 2, est_saving 3 >= 3 1, all 2 net violation(s) carry a deterministic fix 1.

Library other-lint warnings (gated for ingest): `unused-let` 150 (150 fixable), `mutable-never-mutated` 83 (83 fixable), `unused-import` 28 (28 fixable).

## Proxy budget factor

Rule: smallest factor of the frozen category/task_type median with ≤ 5% of the 23,382 regen records over budget (budget = round(factor × median), `metrics.over_budget` semantics). **Selected factor = 2.5** (131.10 proposal 1.5 kept as `factor_129_proposal`, flagged 4,788); over budget at the selected factor: 1,159. Written to `regen/freeze/proxy_budget_v04.json` (`factor`, `budget`, `factor_selected`, `mode: soft`).

| factor | over budget | share |
|---|---|---|
| 1.0 | 11,363 | 48.6% |
| 1.25 | 7,257 | 31.0% |
| 1.5 | 4,788 | 20.5% |
| 1.75 | 3,200 | 13.7% |
| 2.0 | 2,169 | 9.3% |
| 2.25 | 1,546 | 6.6% |
| 2.5 **←** | 1,159 | 5.0% |
| 2.75 | 849 | 3.6% |
| 3.0 | 634 | 2.7% |
| 3.25 | 464 | 2.0% |
| 3.5 | 354 | 1.5% |
| 3.75 | 274 | 1.2% |
| 4.0 | 220 | 0.9% |

**Recommendation: keep `over_budget` SOFT until after 131.15.** The agent wave lowers proxy_tokens on the AGENT bucket (the long tail is exactly where the verbose forms live) and the medians move; the REGEN class is regenerated wholesale. Re-run `pattern_sweep.py report --rebudget` after 131.15/131.16 and decide the hard gate in 131.19.

## Unclassified rule hits

Pattern-rule hits with no catalogue entry: `discarded-value-result` 0 net hits (value-semantics bug, deterministic receiver fix — AUTO, saving 0); **9 gross hits exempted as a suspected linter false positive** — the receiver is a `std.vec` handle (`i=v:std.vec; let x=v.new(); x.push(…)`) called method-style, a reference collection that mutates in place (both programs pass their tests 2/2; the rule only excludes the alias form `v.push(x;1)`). Needs a 127/131 story, like 127.33.. Other lint rules seen (not gated on the shard path; gated for library ingest): `mutable-never-mutated` 245 (245 fixable), `unused-let` 156 (156 fixable), `unused-import` 86 (86 fixable).

## Samples per rule

- `single-use-let`: `A-ARR-0001v30`, `A-ARR-0001v68`, `A-ARR-0003v12`, `A-ARR-0004v38`, `A-ARR-0004v57`, `A-ARR-0004v66`, `A-ARR-0004v69`, `A-ARR-0006v13`
- `loop-rebuilds-array`: `A-ARR-0027v10`, `A-ARR-0027v33`, `A-ARR-0027v39`, `A-ARR-0027v4`, `A-ARR-0027v54`, `A-ARR-0027v57`, `A-ARR-0028v31`, `A-ARR-0028v38`
- `string-concat-chain`: `A-ARR-0032v46`, `A-ARR-0042v13`, `A-ARR-0044v38`, `A-ARR-0045v8`, `A-ARR-0056`, `A-ARR-0057v37`, `A-ARR-0068v37`, `A-ARR-0072v19`
- `flag-soup`: `A-CND-0013v3`, `A-CND-0050v80`, `A-STR-0051v27`, `D-NET-0006v48`, `MIG-P2-A-A-ARR-0108v13-187260f3`, `MIG-P2-A-A-CND-0024v60-61f18c3a`, `MIG-P2-A-A-CND-0031v15-6a582da0`, `MIG-P2-A-A-CND-0031v35-eccc63a5`
- `hand-rolled-parser`: `A-CND-0098v67`, `A-STR-0002v57`, `A-STR-0013v69`, `A-STR-0014v13`, `A-STR-0014v2`, `A-STR-0014v21`, `A-STR-0014v24`, `A-STR-0014v26`
- `mut-flag-if`: `A-MTH-0015v39`, `A-STR-0082v55`, `D-CLI-0002v22`, `D-CLI-0005v104`, `D-CLI-0021v28`, `D-CRY-0008v60`, `D-CRY-0017v220`, `D-FIO-0009v376`

Per-row file: `corpus/regen_v04/audit/pattern_sweep.jsonl` (schema in `pattern_sweep.py`); aggregates `audit/pattern_sweep_summary.json`; id lists `audit/buckets/{auto,agent,regen,library_agent,library_auto}.txt`.
