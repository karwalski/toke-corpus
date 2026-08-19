# Epic 129 — Training-asset quality audit: final report & TRAINING-DATA FREEZE

_Frozen: 2026-08-19 • audits in `corpus/regen_v04/audit/` • rubric: `regen/quality_rubric.md`_

## Freeze declaration

The v0.4 regen corpus (23,382 records) plus the 1,583 verified library programs
are **frozen for Epic 128 training** as of 2026-08-19. Every record that CAN be
executed passes the full tightened gate set; every record compiles and builds;
idiom and structural rubric violations are zero (net of spec-mandated styles).

## Corpus final state (23,382 records)

| Bucket | Count | Meaning |
|--------|------:|---------|
| pass | 14,727 | compiles + builds + exact test match + exit 0 + no extra output + idiom ≥0.6 + structure within rubric |
| not_executable | 8,531 | no test harness exists yet: 5,649 A-side full_program (→129.8), 3,084 migrate_fix, D-side no-test specs — all compile+build+rubric clean |
| driver_fail | 124 | audit-harness synthesis limits (not record faults); treated as compile-only |
| test_fail / build_fail | 0 | all repaired (129.4/129.5) |

- **Repair/compaction waves:** 2,639 records repaired or rewritten across 2 rounds
  (round 1: 1,539 — D-side execution failures + structural/idiom flags;
  round 2: 1,100 — A-side failures exposed by the new 129.7 test cases).
  Every replacement re-verified by the independent audit before banking;
  originals archived in `audit/replaced/` with `regen.repaired` provenance.
- **129.7:** all 631 A-category base tasks now carry execution-verified test
  cases (Python-reference verified at banking, `audit/a_tests/`); 2,894
  A-side single_function records executed for the first time.
- Idiom below 0.6 floor: **0**. Structure: depth p50/90/99 = 1/3/4,
  fn-bytes p99 = 502, min-bytes p99 = 709, lint warnings p99 = 0.
- Duplicate sources: 460 (tagged; disposition at export).
- Metadata corrected on all records: honest mixed-model provenance, `regen.min_bytes`/`max_depth`/`audit` stamps.

## Library + examples (129.3)

- Library **1,583/1,583 PASS** all gates (incl. gazeta 123) on current tkc.
- Orphan solutions (597, non-manifest): 239 compile-fail — **excluded from training**.
- toke/examples + library-examples compile debt → story 119.9.
- Style divergence (idiom-below-floor 707, depth p99 10) → Epic 126.2 scope.

## Found by this audit (filed, never papered over)

- **Compiler bugs 127.6–127.10** (all P0, silent-wrong or check-blind classes):
  method-style str E9003 links, interp-of-method-result pointers, `x.len` wrong
  values, `s.fields()` interp pointers, interp-string append dangling.
  Syntax card updated with verified workarounds (all future waves inherit).
- **Spec-generation defects:** A-categories shipped with zero test cases;
  3,976 A-side specs carry corrupted `input_types` (@(T);U split) — toolchain
  now derives types from description signatures.
- **Acceptance-gate gaps now closed in `run_shard.py validate`:** non-zero exit
  accepted, extra stdout ignored, multi-line expecteds unmatchable, no idiom or
  structural gates, single_function/migrate_fix never executed.

## What is NOT in this freeze

- 129.8 (planned): regenerate 5,649 A-side full_program records against the
  129.7 test cases — they are compile/rubric-clean but functionally unverified.
- migrate_fix behaviour-equivalence checking (out of scope).
- Export to `toke-model/training-data-v04/` consumes THIS freeze (deferred story).
