# regen/freeze — freeze-129 manifest + Epic 131 rewrite bookkeeping

Story 131.12 reopened the 2026-08-19 training-data freeze (`regen/AUDIT_129.md`)
for the Epic 131 corpus rewrite. This directory holds the git-tracked copies of
the freeze manifest (the on-disk `corpus/` tree is gitignored) and documents the
ledger + provenance schemas every Epic 131 rewrite wave must follow.

## Files

| File | What |
|---|---|
| `freeze_129_manifest.jsonl` | One line per frozen record (23,382): `task_id, category, path, sha256, min_bytes, audit`. `sha256` is over the record file bytes. Copy of `corpus/regen_v04/audit/freeze_129_manifest.jsonl`. |
| `freeze_129_summary.json` | Counts per category, total, tarball sha256, tkc version, toke-corpus HEAD, date. |
| `freeze_manifest.py` | Regenerates both (run from anywhere; paths are repo-relative). |
| `manifest_check_131.35.json` | 131.35: `corpus/regen_v04/MANIFEST.jsonl` integrity before/after the rebuild (2,618 stale lines from the 129.4/129.5 repair wave → 0). `MANIFEST.jsonl` `sha256` is now the record **file-bytes** sha256 (same meaning as this freeze manifest and the rewrite ledger); `source_sha256` carries the old tk_source sha. Tool: `regen/manifest_tool.py check|rebuild|stamp`; every bank path stamps on write. |
| `err_union_check_131.42.json` | 131.42: per-base counts of `T!Err` single_function records whose target DECLARES a non-union return (`gamed_err_marker` = prints the a_tests err marker as a `str`; `wrong_return_type`; `correct`), the 20+5 before/after proof of the `return_type` validate gate (`run_shard.validate_one_gates`), and the routing written to `audit/buckets/agent.txt` + `agent_reasons.jsonl` + `audit/pattern_sweep.131.42.patch.jsonl` (apply: `cat` sweep + patch → merged bucket; `load_bucket` takes the later line per task_id). Tool: `regen/err_union_check.py [--route] [--all-task-types]`; rows in `corpus/regen_v04/audit/err_union_check.jsonl`. |
| `autofix_131.14.json` | 131.14: the deterministic auto-rewrite wave over the AUTO bucket (643 rows, minus the 2 that 131.42 re-routed to AGENT = 641 processed). Dry run + bank run decision tables, bank rate decomposed by task_type, every reject reason, the four consistency checks (ledger 424 / archive 424 / `manifest_tool check` 0 stale / per-record sha cross-check), the post-bank re-sweep of the 424 banked ids, the one `--fix` defect found (`D-WEB-0006v87`), and why the 53 `library_auto.txt` rows are left to 131.17. Tool: `regen/pattern_autofix.py --bucket … --bank`; ledger `corpus/regen_v04/ledger/rewrite_131.jsonl`, originals `corpus/regen_v04/audit/replaced/131/`. |
| `signature_gate_131.43.json` | 131.43: the type-aware signature gate (`regen_validate.signature_conforms`: arity + every parameter type after `norm_type`, lossless `SIGNATURE_WIDENING` only) A/B'd against the verbatim arity-only gate over all 23,382 records: 209 single_function records flip to reject (339 slots: `u64`->`i64` 231, `@u64`->`@i64` 108), nothing else changes; 131.40's 252 vs these 209; routing of all 209 to AGENT. Tool: `regen/signature_gate_131_43.py [--route]`; rows `corpus/regen_v04/audit/signature_gate_131.43.jsonl`, patch `audit/pattern_sweep.131.43.patch.jsonl`. |
| `err_union_check_131.68.json` | 131.68: `regen/err_union_check.py --story 131.68 --all-task-types --reuse-exec --route-patch` — the full_program half of 131.42 (single_function verdicts unchanged: 429 gamed / 147 correct). The 58 full_program records 131.67 counted as `return_type` gate failures were all the SAME false positive: the gate applied the single_function wrapper rule (driver.find_target's last same-arity decl) to programs that run their own main(), so a `str` helper formatting a correct union target was read as a gaming wrapper. With the rule scoped to single_function: full_program 914 records, 914 correct, 0 hard-fail, 0 routed; the 58 are listed as `full_program.str_helper_not_wrapper_task_ids` (exactly 131.67's 58). Rows `corpus/regen_v04/audit/err_union_check.131.68.jsonl`. |
| `map_input_recheck_131.71.json` | 131.71: every banked record with a map-typed input (the 29 `driver_fail` rows of 131.67) re-executed after `driver.lit` learned map literals. Tool: `regen/map_input_recheck_131_71.py`. |
| `rewrite_131_reconcile.131.80.json` | 131.80: `ledger/rewrite_131.jsonl` reconciled against `audit/replaced/131/` (bytes checked against the freeze-129 tarball), the freeze manifest, `MANIFEST.jsonl` and each record's `regen.rewrite131` block — per-wave counts for AUDIT_131. Tool: `regen/reconcile_rewrite_131.py`. |
| `migrate_lock_131.60.json` | 131.60: every `migrate_fix` record given a test lock where one exists (route (a): the A-side base named inside the MIG id has an execution-verified a_tests file whose signature a declared function matches) and the ORIGINAL run against it; every other record is listed as compile-only (route (c), stated explicitly). Route (b) is unavailable by construction. Of 2,226 migrate_fix records (tkc 3.1.0 `5774da8c`): lock_pass 485, lock_fail 310 (159 of them differ ONLY in bool spelling, `true` vs the driver's stale `1` — 131.102; 151 genuinely wrong), driver_fail 8, own main() 24, compile-only 1,399 (1,355 D-side/N-series with no a_tests bank, 43 signature mismatch, 1 ambiguous target). AUTO bucket (211): 50 / 45 (23 bool-only) / 2 / 1 / 113. Tool: `regen/migrate_lock.py --census` (library `lock_spec` / `drive_spec`, used by `diff_check` and `pattern_autofix`); rows `corpus/regen_v04/audit/migrate_lock_131.60.jsonl`. |
| `interp_name_check_131.83.json` | 131.83: every banked-accepted record whose `tkc --check` diagnostic lies inside an interpolation segment (segments were neither name-resolved nor type-checked before toke 127.24). Tool: `regen/interp_name_check_131_83.py [--route]`; rows `corpus/regen_v04/audit/interp_name_check_131.83.jsonl`, patch `audit/pattern_sweep.131.83.patch.jsonl`. |
| `pattern_sweep_131.64.json` | 131.64: the 131.13 sweep re-run after the `nested-if-ladder` lint rule shipped (tkc 3.1.0 `9f1eb921`, catalogue `c42dcc38a16c`, thresholds unchanged), compared with the 131.13 output and the merged routing. Buckets 131.13 → re-run (all 24,965): AUTO 696 → 228, AGENT 2,469 → 2,517, LEAVE 14,473 → 14,893, EXEMPT 1,678 and REGEN 5,649 unchanged. The moves are the 131.14 auto wave (432 AUTO rows whose source changed) and 42 library rows whose `unused-let` warnings lost their fix (127.81/127.82); **`nested-if-ladder` moves nothing**: 1,107 regen + 496 library records carry 1,878 ladder hits, but `idiom_judge.PATTERN_RULES` does not list the rule, so the sweep files them under `other_lint`. Counterfactual with the rule counted as `cond-elif-chain` form b (deterministic when fixed): 641 rows move LEAVE/EXEMPT → AUTO, AGENT unchanged. Tool: `regen/pattern_sweep_131_64.py` over a `pattern_sweep.py run`/`report` into a read-only corpus mirror. |

Routing patches (131.43 / 131.68 / 131.83, `regen/route_patch.py`) are built
from each record's CURRENT sweep row (`audit/pattern_sweep.merged.jsonl` plus
the earlier patches of this list), so applying them never drops another
story's routing. Until 2026-11-09 they were applied with
`cat audit/pattern_sweep.jsonl audit/pattern_sweep.131.42.patch.jsonl audit/pattern_sweep.131.67.patch.jsonl audit/pattern_sweep.131.15.looprebuild.patch.jsonl audit/pattern_sweep.131.43.patch.jsonl audit/pattern_sweep.131.68.patch.jsonl audit/pattern_sweep.131.83.patch.jsonl > audit/pattern_sweep.merged.jsonl`
— but the on-disk merged file was never rebuilt after 131.43 and 131.83, and
`cat` after a newer sweep would restore the older sweep's rows.

**Since Z95 the merged routing is built by `regen/route_merge.py`** (no `cat`):
the 131.64 re-run sweep (`audit/pattern_sweep.131.64.jsonl`, sha256
`b8e0f969…`) with every patch REPLAYED in this order — 131.42, 131.67,
131.15.looprebuild, 131.43, 131.68, 131.83, 131.106, 131.105, 131.111 — each adding only
its own violation, bucket and reason to the fresh row (REGEN sticky for the
A-*/full_program class). `route_merge.py --check` proves the file on disk is
that build; `pattern_prep.py` now reads it by default. The previous merged
file is kept as `audit/pattern_sweep.merged.20260919.jsonl`.

| File | What |
|---|---|
| `route_merge_131.15.json` | Z95: the merged routing — base sweep sha, each patch file (rows, sha, origin), per-patch replay counts, buckets re-run vs merged, transitions, and the REGEN rows outside the A-*/full_program class (no regeneration wave: 131.109). Tool: `regen/route_merge.py [--check]`. |
| `check_routing_131.106.json` | 131.106: `tkc --check` over all 24,965 live sources with the pinned integrate tkc; every banked record that fails is routed (REGEN for the A-*/full_program class, AGENT otherwise) via `audit/pattern_sweep.131.106.patch.jsonl`, with its diagnostics in the reason; gate result after routing. Tool: `regen/check_routing_131_106.py run|route|gate [--live]`; rows `corpus/regen_v04/audit/check_routing_131.106.jsonl`. The gate test is `regen/tests/test_check_routing_131_106.py`. |
| `check_routing_131.111.json` | 131.111 (2026-11-09): the same check on the 131.103 corpus pin (toke 3.1.0 `1f39c2998110`, sha256 `d4003759…`). 195 banked records fail (regen 190, library 5). The 20 that are new since 131.106 are exactly the 127.244 `m.get(k)` E4031 set (A-STR 9, A-ERR 8, D-CRY 2, D-FIO 1). `route --story 131.111` routes only those 20 (`audit/pattern_sweep.131.111.patch.jsonl`: REGEN->REGEN 16, LEAVE->AGENT 4). The 131.106 patch is left unchanged. A-ERR-0030v39 from 131.106 now passes (banked compile-only by 136.72). Gate after routing: 0 violations. |

131.105's three records (`A-ARR-0017v41`, `D-CFG-0018v98`, `D-FIO-0002v99`)
are routed to AGENT by `regen/route_131_105.py` (`audit/pattern_sweep.131.105.patch.jsonl`),
the reason naming the archived original to restore the `let` from.

Rebuild sequence. The patches on disk are the record, and `route_merge.py`
rebuilds the merged file from them (`--check` proves it). The original build
order was `check_routing_131_106.py run` → `route` → `route_131_105.py` →
`route_merge.py` → `gate`. To route new failures after a checker change
(131.111), do not re-run the plain 131.106 `route`, which would fold them into
the 131.106 patch. Instead run `check_routing_131_106.py run` →
`route --story <next CHECK_STORIES entry>` → `route_merge.py` → `gate`. A later
story routes only the failures no earlier check-routing patch covered. `run`
and `gate --live` default to the corpus pin (`regen/corpus_pin.py`, 131.103).

Byte snapshot: `~/tk/archive/toke-corpus-regen_v04-freeze129-20260819/` (local
archive, see its `MANIFEST.md`). Git tag: `freeze-129-20260819`.

## `ledger/rewrite_131.jsonl` — append-only rewrite ledger

Location: `corpus/regen_v04/ledger/rewrite_131.jsonl` (gitignored, on disk).
One JSON object per line, one line per attempt/outcome; never edit or reorder
earlier lines.

| Field | Type | Meaning |
|---|---|---|
| `task_id` | string | record id, e.g. `A-ARR-0001v24` |
| `wave` | `auto` \| `agent` \| `regen` \| `library` \| `struct-layout` | which Epic 131 wave touched it (`struct-layout` = the 131.78 repair, which also carries `story`) |
| `status` | `rewritten` \| `unchanged` \| `failed` \| `skipped` \| `flagged` | outcome of this attempt (`flagged` = 131.78 found no valid repair; no bytes changed) |
| `reason` | string | why (pattern ids fixed, lint rule, or failure summary) |
| `attempts` | int | cumulative attempts for this task_id in this wave |
| `prev_sha256` | string | sha256 of the record bytes before this attempt (matches `freeze_129_manifest.jsonl` on the first touch) |
| `new_sha256` | string \| null | sha256 after banking; null when not rewritten |
| `ts` | string | ISO-8601 UTC timestamp |

Count rewrites from THIS ledger, not from `regen.rewrite131`: a record rewritten
by two waves keeps only the later provenance block (131.80: `D-CRY-0012v285`,
auto then struct-layout, so the blocks say auto 423 where the ledger says 424).
Repairs outside Epic 131 keep their own ledger (`ledger/repair_136.jsonl`, 136.72).

Originals of every rewritten record go to `corpus/regen_v04/audit/replaced/131/<task_id>.tk`
(new subdir — the flat `audit/replaced/` holds the 2,618 129-era originals and
must not be clobbered).

## `regen.rewrite131` — provenance block on rewritten records

**No schema bump**: records keep `version: 2` (a bump breaks `audit.py` /
`assemble.py` for no consumer benefit). `regen.repaired` (Epic 129) is left
intact as history. Add under `regen`:

```json
"rewrite131": {
  "wave": "auto | agent | regen | library",
  "story": "131.x",
  "ts": "ISO-8601 UTC",
  "prev_sha256": "sha256 of the record bytes before rewrite",
  "card_sha": "syntax_card.md sha (short) used",
  "catalogue_sha": "pattern catalogue sha (short) used",
  "tkc_sha": "tkc build sha used to verify",
  "patterns_fixed": ["pattern ids"],
  "lint_violations": 0,
  "lint_exempt": ["lint rule ids exempted, with reason encoded in the ledger"],
  "proxy_tokens_before": 0,
  "proxy_tokens_after": 0,
  "perf": { "tier": "string", "ratio": 1.0, "verdict": "pass | fail | n/a" }
}
```

`regen.audit` stays `129-freeze-2026-08-19` until the Epic 131 freeze
(`AUDIT_131.md`) restamps it.
