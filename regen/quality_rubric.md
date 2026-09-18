# Corpus quality rubric — Epic 129.1

Codifies "high-quality training program" for every asset that can reach
training (regen corpus, library, examples). Enforced by `audit.py`
(sweep/report) and, at freeze, wired into `run_shard.py validate` (129.6).

## Gates (hard pass/fail)

| Gate | Definition | Rationale |
|------|------------|-----------|
| compile | `tkc --check` rc==0 | root-of-trust minimum |
| build | `tkc -o` rc==0 | --check alone misses codegen failures (127.6: method-`upper`/`lower`/`ends`/`replace` E9003 at link) |
| tests | where spec test_cases + a harness exist: every expected line matches, in order | functional correctness, not just compilability |
| exit code | test binary exits 0 | **tightened 129.1** — old gate accepted non-zero exits |
| no extra output | stdout has exactly the expected lines | **tightened 129.1** — old gate silently ignored trailing lines |
| idiom floor | idiom score ≥ 0.6 (`idiom_judge.py`; **131.10:** scored from the `tkc --lint` pattern rules below + a regex for `hand-rolled-parser`; was 4 regex detectors) | anti-pattern rejection |

## Pattern gates (Epic 131)

Added 2026-09-18 (story 131.10; rules from 131.9, `docs/lint-rules-v1.md`
"Pattern rules"). The judge and the linter are one implementation: `metrics.lint()`
runs `tkc --lint --diag-json` once and both the idiom score and the hard gate
read that diagnostic list.

| Gate | Definition | Rationale |
|------|------------|-----------|
| pattern lint (hard) | no pattern-rule diagnostic at severity `error` or `warning`: `discarded-value-result` (error), `mut-flag-if`, `flag-soup`, `string-concat-chain` (warning). Hints (`single-use-let`, `loop-rebuilds-array`) pass. Applied on every validate path (`validate_one`, `validate_one_gates`, library ingest). | idiom rules 1/2/5 + the value-semantics bug are AST-decidable now; the regex judge missed most of them |
| idiom score (kept) | penalties per hit, capped at 3 per rule: mut-flag-if −0.15, flag-soup −0.15, string-concat-chain −0.15 (was `nested-concat`), discarded-value-result −0.20, loop-rebuilds-array −0.05, single-use-let −0.02, hand-rolled-parser −0.20 (regex); floor 0.6 unchanged | continuity with the 129 scores; `RESCORE_131.md` reports old vs new |
| proxy budget (soft) | `regen.proxy_tokens` = proxy8k count of the string-masked `tkc --min` program (131.4 metric, `scripts/patterns/count_tokens.py`); `regen.over_budget = true` when > 1.5 × the frozen-corpus median for the record's `category/task_type` (`regen/freeze/proxy_budget_v04.json`, computed once by `rescore_131.py`) | flag, not reject — 131.13 decides whether it becomes hard |

Exemptions carried into the pattern gate: the **stub-import exemption** (a
single_function record starts with the `m=harness;i=io:std.io;i=s:std.str;`
+ context-stub prefix that `assemble.py` prepends unconditionally; diagnostics
located in that prefix — `unused-import` on 74/200 sampled records — are
dropped in `metrics.lint()`), and the **spec style mandate** below
(`idiom_judge.MANDATE_EXEMPTIONS`: "…mutable binding…" exempts `mut-flag-if`,
`flag-soup`, `loop-rebuilds-array`; "…helper variable…" exempts
`single-use-let`; "…loop to compute the result…" exempts `loop-rebuilds-array`).
`discarded-value-result` is never exempt: the discarded value is a bug.
Exempted rules that actually fired are recorded in `regen.lint_exempt`.

> **Vacuous lint gate (found 2026-09-18, 131.9).** `tkc --lint --diag-json`
> emitted no JSON before commit e9746f2, so `metrics.lint()` always returned
> `[]` and the "lint 0 warnings" row below never rejected anything on the
> 129.6 shard runs or the freeze audit. Every frozen record has now been
> re-scored with the real rules (`regen/RESCORE_131.md`); the backlog it
> reveals is 131.13's input. The row is real from 131.10 on.

single_function records execute via a synthesized driver `main()`
(`driver.py`): real-bodied context stubs (closed 29-signature set, all
probe-verified) + one printed line per expected value. Records whose spec has
no test cases are gated on compile+build+structure only.

## Structural thresholds (LOCKED 2026-08-12 from the 129.2 sweep distributions;
corpus-wide: depth p50/90/99 = 1/3/4, func-bytes p99 = 622, min-bytes p99 = 715)

| Metric | Source | Threshold |
|--------|--------|-----------|
| max control nesting depth | AST (`tkc --dump-ast`), IF/LOOP/MATCH ancestors; el-if chains count once | > 3 flag (compaction queue), > 4 hard fail |
| function length | AST subtree extent, bytes | > 600 flag |
| functions per program | AST FUNC_DECL count | ≥ 2 for difficulty-3 full programs (flag) — complex items built from small sub-functions |
| minified size | `tkc --min` bytes | reported; > 750 flag (≈ p99) |
| lint | `tkc --lint` | 0 warnings (hints allowed) — **vacuous until 131.10**, see "Pattern gates"; pattern-rule warnings/errors are a hard gate on every path, other warnings gate library ingest only |
| proxy tokens | proxy8k of masked `--min` (`metrics.proxy_tokens`) | reported (`regen.proxy_tokens`, `tk_tokens`); > 1.5 × category/task_type median → `over_budget` flag |

Flags are advisory in the sweep; the compaction pass (129.4) rewrites flagged
records **test-locked** (must reproduce exact outputs). Thresholds get locked
here after the sweep and become hard gates in `validate` (129.6).

## Exemptions

- **Spec-mandated style**: task descriptions that explicitly require a pattern
  (e.g. "Use nested if/el blocks", "Accumulate the result in a mutable
  binding" — ~600 specs each) are exempt from the contradicted structural/idiom
  flag; prompt-output alignment beats uniform style. The audit records
  `style_mandate` per row.
- **Compiler-bug victims**: records failing only via a confirmed compiler bug
  (127.6/127.7/127.8) are bucketed for rewrite-to-alias-forms, not deleted;
  the bugs themselves are tracked as Epic 127 stories, never papered over.

## Known-broken constructs (must NOT appear in accepted training source)

Confirmed on tkc 2.8.0 (2026-08-12, Epic 129 probes; card updated same day):

- method-style str calls: `x.upper()` `x.lower()` `x.ends(p)` `x.replace(a;b)`
  — E9003 at link (127.6)
- interpolating method-call-derived strs — prints a pointer (127.7)
- `x.len` on a str — silent wrong value; always `s.len(x)` (127.8)
- `s.join(arr;sep)` arg order — SIGSEGV (127.5); `arr.fold` — E9003 (127.4)
- bare `arr.push(v)`/`arr.set(i;v)` statements — no-op/crash (127.2/127.3)
