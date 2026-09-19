# 131.67 — A-ERR re-verification on the fixed compiler

Harness: `regen/err_recheck_131_67.py` · rows `corpus/regen_v04/audit/err_recheck_131.67.jsonl`
· summary `regen/freeze/err_recheck_131.67.json` · patch
`corpus/regen_v04/audit/pattern_sweep.131.67.patch.jsonl` (131.13 sweep schema).
Banks nothing — measurement only.

Compilers (both pinned per 131.39, so a concurrent `make` cannot corrupt the run):

| pass | toke HEAD | tkc sha256 | C001 |
|---|---|---|---|
| NEW | `b3da3d3` (contains `f55354c`) | `f99326281622` | 24/24 pass |
| OLD | `a455a5b` = `f55354c^` (private worktree) | `1e14c76b7598` | 12/24 pass |

`f55354c..HEAD` touches only `docs/progress.md`, so the OLD binary is
exactly HEAD-minus-the-fix. Both passes run today's machinery
(`audit.audit_one`, the 131.44 `$err` rendering in `validate.render_expected`,
the 131.40 driver fixes); the compiler is the only variable. A third pass
re-runs every untested record on the NEW compiler a second time, so a
run-dependent record is never mistaken for one the fix changed.

## The premise does not hold: the corpus never uses the broken construct

127.56 broke exactly one error-return form — the **constructor call**
`<$notfound("x")`. The **literal** form `<$e{$notfound:"x"}` always built a
correctly tagged box. Census of all 1,546 A-ERR sources:

| error-return form used | records |
|---|---|
| `literal` + `propagate` | 1,225 |
| `propagate` only | 1 |
| none (no error return at all) | 320 |
| **`ctor` (the form 127.56 fixed)** | **0** |

The 1,521/2/23 variant/struct/none split in the story row is reproduced
exactly by this harness — but it describes the *type declaration*, not the
*return form*. Every record declares a variant-style type and then returns it
through the literal form. **No record in A-ERR exercises the defect.**

## Before / after

| group | records | banked pass | OLD pass | NEW pass | changed verdict |
|---|---|---|---|---|---|
| non-gamed (131.42 "correct") | 1,117 | 21 | 80 | 80 | **0** |
| gamed (131.42) | 429 | 429 | 0 | 0 | 0 |

Nothing moved. The 21→80 lift is the 131.44/131.40 machinery, not the compiler:
the banked ledger executed only 21 of the 1,117.

### The 1,117 non-gamed, classified

| verdict | n | meaning |
|---|---|---|
| `unverified_behaviour_same` | 894 | no test cases; deterministic output identical on both compilers |
| `unverified_nondeterministic` | 87 | no test cases; **output is run-dependent** (see below) |
| `pass_err_verified` | 61 | passes, and a test case really expects an `err` result (16 bases) |
| `pass_never_reached_err` | 19 | passes, but no test case expects an `err` — the pass says nothing about error handling |
| `fail_stable` | 27 | fails its tests on **both** compilers (9 bases) — already broken, never caught |
| `driver_fail` | 29 | harness cannot synthesise a driver (map-typed input `@str:i64`) |

**981 of the 1,117 (87.8%) have no test cases at all** — 967 `full_program`,
3 `migrate_fix`, 11 `single_function`. They were never executed in the banked
audit (`banked_not_executed` = 1,096) and cannot be given a pass/fail now.
Their "correct" label was a 131.42 *return-type* verdict, never an execution one.

Answering the story's question directly: **no record now fails that used to
pass.** Of the 80 that hold an execution pass today, 19 never reach an error
path, so at most **61 of 1,117 (5.5%) are execution-verified error handling.**

### The 429 gamed still fail

All 429 fail execution on both compilers (`output mismatch`: they print the
a_tests marker, `render_expected` wants `err:<variant>`) and **429/429 still
fail the 131.42 `return_type` gate**. Unaffected by the fix, still wrong.

## Two findings that are not about 127.56

1. **87 `full_program` A-ERR records print heap pointers, not values.**
   `"\(mt f(x) {$ok:v v;$err:e "err"})"` with `str`-valued arms interpolates
   the pointer; the value changes every run, on both compilers. Reproduced
   standalone from `A-ERR-0007v14`. This is an unfiled compiler defect and
   the reason those records' "output" is meaningless.
2. **58 further non-gamed records fail the `return_type` gate** — all
   `full_program`, so outside 131.42's `single_function` scope. The driver
   callee (`describe`/`show`/`render`) is declared `str` where the spec wants
   the union: the same gaming shape as the 429, in the half of the category
   131.42 never looked at.

## Bucket patch

56 rows → `AGENT` (`rule: err-arm-unverified`, patterns `err-propagate` /
`err-default`): the 27 `fail_stable` + 29 `driver_fail` non-gamed records
(54 were `LEAVE`, 2 `EXEMPT`). The 967 `full_program` records are already
bucketed `REGEN` by 131.13 and need no patch.

    cat corpus/regen_v04/audit/pattern_sweep.jsonl \
        corpus/regen_v04/audit/pattern_sweep.131.42.patch.jsonl \
        corpus/regen_v04/audit/pattern_sweep.131.67.patch.jsonl \
        > corpus/regen_v04/audit/pattern_sweep.merged.jsonl
    python3 regen/pattern_prep.py --bucket corpus/regen_v04/audit/pattern_sweep.merged.jsonl

## Judgement

The compiler fix does not force a regeneration of A-ERR: it changes no
verdict, because the corpus never used the construct that was broken. But the
category is still unverified, for a plainer reason — 87.8% of the non-gamed
records have no tests, and only 61 of 1,117 have an execution-verified error
path.

Rewriting is the right tool for the 56 patched records and nothing more.
The 967 `full_program` records cannot be rewritten into correctness because
there is nothing to check a rewrite against; they are already bucketed
`REGEN`, and the 129.8 A-side `full_program` regeneration against the
corrected card is the cheaper and only sound route — with A-ERR test
authoring (129.7 `a_tests`) as its precondition, or the regenerated shard will
be exactly as unverified as this one.
