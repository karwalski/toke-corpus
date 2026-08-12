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
| idiom floor | rule-based idiom score ≥ 0.6 (`idiom_judge.py`, 4 detectors from idiom-v0.4.md) | anti-pattern rejection |

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
| lint | `tkc --lint` | 0 warnings (hints allowed) |

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
