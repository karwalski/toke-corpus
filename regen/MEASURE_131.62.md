# 131.62 (a) — what the wrong spec prompt did to the v0.4 corpus

Batch Z234, 2026-11-23. Read-only: no record, ledger, MANIFEST or routing
file was changed. Data: `regen/freeze/measure_131.62.json`. Tool:
`regen/measure_131_62.py --tkc <tkc>` (tests: `regen/tests/test_measure_131_62.py`).
tkc: the wave-A/B integration build, `toke 3.2.0 (git 0558545d5b78)`, sha256
`baa81830…`. The tkc is used only for the construct probes. Every count below
comes from reading source text.

## The short answer

1. **The wrong page never reached the generator of any of the 23,382 records.**
   Every record carries `card_sha` `bea2083b3df2`, which is card v1
   (`regen/syntax_card.md` at `550e9fb`, 2026-07-13). That card teaches `^`
   and `~` as native bitwise operators, `mt` throughout, `==`, `!=`, `&&`,
   `%`, expression-`if` and `el if`. It does not name `sc` and has no keyword
   list. The shard workflow (`~/tk/.claude/workflows/regen-shard-wave.js`)
   points the workers at that card only. None of the 23,826 task prompts
   contains the page's wording: `reserved ^`, `56 char`, `Keywords (12)` and
   `spec-reference` each occur 0 times (quoted retired wording; facts-exempt: 2026-11-23 131.62 quotes the pre-132.15 page).
   Only the phase-era generators read
   `prompts/spec-reference.md` (`prompts/constraints/constraints.py`,
   `prompts/domain/domain_templates.py`, `scripts/ec2_generate_all.py`).
2. **The page still got in, second-hand, through `migrate_fix`.** All 2,226
   `migrate_fix` records (`MIG-*`) were made by migrating legacy phase-era
   source, which the worker is shown verbatim. The pre-132.15 page told that
   generation to write `!(a=b)` for not-equal, `!(a>b)` for less-or-equal,
   nested `if` for AND/OR and `a-a/b*b` for modulo, and it said so in exactly
   those words. Those four substitutions are in **30.9 %** of `migrate_fix`
   outputs. Native records of the same 529 task families have them in
   **1.9 %**. **97 % of them (668 / 688) were carried over from the legacy
   input** rather than introduced by the worker. That is expected, because
   the migration prompt says to "preserve the original behaviour exactly".
3. **The prompt did not suppress the four constructs named in the row.**
   `mt`, `^` and `~` are used where the task calls for them. `sc` is used
   nowhere, but no task in either the regen set or the library asks for
   concurrency.

## Counts

### Constructs (GEN = raw worker output for the 23,382 banked ids; LIVE = banked source today)

| | GEN | LIVE | library (1,583) |
|---|---:|---:|---:|
| `mt` | 1,747 (7.5 %) | 1,912 | 84 (5.3 %) |
| `sc` | 0 | 0 | 0 |
| `^` | 119 | 136 | 25 |
| `~` | 52 | 52 | 7 |

Reference expectation: what each task asks for, judged from its description.

| demand (GEN) | tasks | uses the construct |
|---|---:|---|
| XOR named | 129 | 118 use a bit operator (117 use `^`) and 11 call the module's `xor()` helper. **0 use neither.** |
| complement named | 67 | 66 follow the task's own prescription, "255 - value". |
| value an `mt` can consume (error union / sum type / fallible std call) | 2,050 | `mt` in 1,519 (74.1 %) |
| …and the task says "use pattern matching" | 303 | `mt` in 236 (77.9 %) |
| error-union return type | 1,470 | `mt` in 986 (67.1 %) |
| concurrency | 0 (regen), 3 (library) | none of the 3 library tasks needs concurrent execution |

When an `mt` is not used, the record propagates with `!` or uses
`getor` / a precondition `if`, which is the canonical `err-default` form b.
`mt` appears at the same rate in native and `migrate_fix` records of the same
families (3.4 % vs 3.7 %).

### Page-prescribed substitutions (GEN, by task type)

| shape | full_program 12,722 | single_function 8,434 | migrate_fix 2,226 | same-family native 16,774 |
|---|---:|---:|---:|---:|
| `a-a/b*b` (no `%`) | 12 | 11 | **206** | 16 (0.10 %) |
| `!(a==b)` (no `!=`) | 26 | 10 | **246** | 34 (0.20 %) |
| `!(a>b)` (no `<=`/`>=`) | 17 | 13 | **218** | 15 (0.09 %) |
| `if(a){if(b){…}}` (no `&&`) | 182 | 125 | **185** | 261 (1.56 %) |
| **any of the four** | 237 (1.9 %) | 159 (1.9 %) | **688 (30.9 %)** | 326 (1.94 %) |

In `migrate_fix`, the four shapes were inherited 668 times and introduced 20
times. In another 297 records the worker removed them.

Today, in LIVE source, 551 `migrate_fix` records still carry one of the four.
Their merged-routing buckets are **LEAVE 411**, AUTO 93, AGENT 44 and
EXEMPT 3. No tkc lint rule and no 131.13 sweep pattern detects these shapes,
which is why they are routed LEAVE (filed as 131.135). Across all task types,
907 live records carry a shape. Their buckets are LEAVE 647, AUTO 95, REGEN 92,
AGENT 58 and EXEMPT 15.

Shapes the 131.13/131.64 sweep found that also follow from the page's
teaching (statement-only `if`, no `el if`, no `&&`) are at native rates in
regen: `mut-flag-if` 27, `flag-soup` 33, `nested-if-ladder` 1,107. The
library rates are much higher (231 / 68 / 496), but the library comes from a
different generator and era (below).

Other workaround shapes are at noise level: xor emulation 0 and bit-loop xor
0; `-x-1` for `~` 2 hits, one of which is a false positive (`-maxi-1`); a
hand-rolled digit accumulation instead of `s.toint` 12.

### Library (1,583)

The library programs (toke-test-programs, written up to 2026-06-01) were
generated before 114.8 made `^` / `~` live on 2026-06-23, in the legacy `$i64`
spelling, with a generator whose quick reference
(`toke-test-programs/infra/toke_docs_lookup.py`) lists the 14 keywords
including `mt` and `sc`, and never reads spec-reference.md. Its 5.5 % mod
emulation and 24 % nested-if AND are therefore language-era effects, not
effects of this prompt. Its low `mt` rate (6 % where a value is matchable)
predates 127.319: `s.toint` and similar calls returned plain values when it
was written, and the migration is tracked as 127.464. 36 library sources have
changed since 131.13 indexed them.

### Probes

With the integration tkc, `--check` passes on programs using `^`, `~`, `mt`,
`sc{…}` and `!=`/`&&`/`%`/`el if`.

## Stratified sample

`samples` in the JSON lists up to 3 ids per category for each construct and
each shape, and up to 5 per task type for each shape (`…:by_task_type`). Picks
are stable, in sha256(id) order, and each comes with its matching line. For
example:

- `MIG-P2-A-A-CND-0041v25-45701ef2`: `let div4=year-year/4*4;` and `if(!(div4==0)){<false};`
- `MIG-P2-A-A-MTH-0105-5404258f`: `lp(let i=2;!(i>n);i=i+1){` and `let modsum=sum-(sum/m)*m;`
- `MIG-P2-B-D-NET-0009v282-f649ca77`: `if(c0==49){if(c1==50){if(c2==55){if(c3==46){result=true}}}}`
- `MIG-P2-A-A-ARR-0004v7-e5a31410`: `lp(let zw=0;!(left==right);zw=0){`
- native baseline: `A-MTH-0068v25` `if(cand-cand/d*d==0)`, `A-ARR-0029v15` `lp(let i=n-1;!(i<0);i=i-1)`
- constructs: `D-CRY-0009v64` `let r=x^y;`, `D-CRY-0022v45` `let mask=~(1<<q);`, `A-CND-0078v54` `<mt classify(num1) {$even:e true;$odd:o false}`

## Reference used, and what was not done

The reference is: card v1's own text (what the workers were taught); the
task descriptions (what each task calls for); the canonical catalogue
(`toke/patterns/catalogue.json`: the canonical form uses `mt` in 10 of 46 entries;
`err-default`'s canonical form b uses no `mt`); and native records of the
same task families as a control for `migrate_fix`. No record was generated
after the prompt fix, because the regen finished on 2026-08-11 and the page
was fixed on 2026-10-27. The row's "small regeneration under the corrected
prompt" was **not** run: it would generate records, which this report-only
step does not do. The phase-2 corpus (188,828 records) was not used as an
exposed group, because it is v0.3 syntax written before `^` / `~` / `mt`
existed in their current form, so it cannot be compared.

## Options for (b), and what the numbers imply (not decided here)

The row's options:

- **(b1) Widen 131.16's A-side regeneration to more of the corpus.** On these
  numbers, widening by category would not hit the damage. The excess sits
  in one task type, `migrate_fix`, and is spread over all 13 categories that
  have 171 `migrate_fix` records each (A-ERR has 3): from 25 / 171 (A-ARR) to
  88 / 171 (D-CRY). Native records in every category are at about
  1–2 %. 131.16 as filed is A-side `full_program`, which has no excess.
  Also, 131.16 would run the shard workflow, whose worker prompt contradicts
  the card (131.136).
- **(b2) A targeted re-prompt of the affected categories.** The affected unit
  is the 2,226 `migrate_fix` records, or the 551 that still carry a shape. The
  same migration prompt would carry the shapes forward again unless it says
  to rewrite them, because it currently asks for exact behaviour
  preservation.
- **What else the numbers allow** (for the owner, not adopted here): each of
  the four shapes has a same-meaning canonical spelling: `!(a==b)`→`a!=b`,
  `!(a>b)`→`a<=b`, `a-a/b*b`→`a%b` (same as `%` under truncating integer
  division) and a bare nested `if`→`&&`. They could therefore be routed to the
  existing AUTO/AGENT rewrite path once a detector exists (131.135), rather
  than regenerated. Whether the `%` and `&&` rewrites are exact in every
  emitted case (division by zero, side effects in the inner condition) would
  need checking before any `fix` is called deterministic.
- On the row's AUDIT_131 requirement ("state which prompt each record was
  generated under"): 23,382 / 23,382 regen records were generated under
  card v1 `bea2083b3df2` plus the shard worker prompt. 2,226 of them were
  additionally shown legacy source from the spec-reference era.
