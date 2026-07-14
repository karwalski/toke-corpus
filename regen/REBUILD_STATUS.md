# Corpus v0.4 Rebuild — Plan & Status

_Last updated: 2026-07-14 (after shard 02 recovery)_
_Owner: Matthew Watt • Epic 116 Workstream C / Epic 118.3_

## Goal

Regenerate a ~23,826-record toke training corpus in current **v0.4 / tkc 2.8.0**
syntax to replace `toke-model/training-data-v03/` (the 25,953-record Gate 2
corpus), of which only ~21% of completions still compile under 2.8.0 — the
dominant break being `=`→`==` equality (E2002). This delivers the corpus via
agent workers instead of the compute-gated local model.

**Acceptance gate:** ≥85% of a shard's tasks must be accepted. All completed
shards are clearing it at ~98%.

## Pipeline (per shard)

Harness lives in `toke-corpus/regen/`; data lands in `corpus/regen_v04/`
(gitignored, on-disk only). One shard = ~1,986 stratified tasks (14 categories ×
difficulty 1–3), split into 100 batches of 20.

1. **`run_shard.py prepare`** — writes per-task prompts + batch manifests;
   skips any task already `accepted` in the ledger (idempotent).
2. **Workflow (one worker agent per batch)** — worker reads `syntax_card.md`
   (the ONLY authoritative 2.8.0 spec; all other docs are stale), generates
   toke source to `gen/out_<task_id>.tk`, and self-checks with `check_one.py`
   (tkc `--check` + build + run test cases), repairing up to 3 attempts.
   Model: **Opus 4.8** (Fable hit usage limit; default switched on 2026-07-13).
3. **`run_shard.py validate`** — the main thread **independently** re-checks
   every worker output (workers are never trusted for acceptance): compile,
   signature-conformance, and runtime output match. Accepted records are written
   to `<CATEGORY>/<id>.json`, appended to `MANIFEST.jsonl` and the shard ledger;
   processed outputs are archived as `*.tk.done`. Rejects get one retry via
   `retry_queue.jsonl`.
4. **`run_shard.py stats`** — per-category / reason accept-rate summary.

`card_sha` = `bea2083b3df2` (probe-verified vs tkc 2.8.0).

## Status

**Banked: 5,852 / ~23,826 accepted (24.6%). 3 of 12 shards complete.**

| Shard | Accepted | Rate | Non-accepts (all runtime-format) | State |
|-------|----------|------|----------------------------------|-------|
| 00    | 1951/1986 | 98.24% | 35 (23 failed_final, 12 pending-retry) | ✅ Done |
| 01    | 1951/1986 | 98.24% | 35 (31 failed_final, 4 pending-retry)  | ✅ Done |
| 02    | 1950/1986 | 98.19% | 36 (23 failed_final, 13 pending-retry) | ✅ Done — **recovered after crash** |
| 03–11 | —         | —      | —                                | ⏸️ Not started (9 shards, ~17,874 tasks) |

**Quality:** across all three shards, **zero surviving compile failures**. Every
non-accept is a runtime output-format mismatch concentrated in the config /
file-parse / CLI / web categories (D-CFG, D-FIO, D-CLI, D-WEB) — the same
non-critical tail in each shard. Idiom quality confirmed on target
(value-semantic collections, expression-if, builders, early-exit).

## Shard 02 crash recovery (2026-07-14)

The machine crashed mid-generation on shard 02. Recovery, using the harness's
idempotent design (gen outputs written before the crash survive on disk):

1. **Banked survivors** — `validate` over the 1,205 gen outputs that had been
   written pre-crash → **1,150 accepted, 55 rejected**. Created the shard_02
   ledger; archived processed outputs as `.done`.
2. **Re-batched the remainder** — `prepare` skipped the 1,150 accepted and
   re-queued the 836 remaining (781 never-generated + 55 rejected) into 42 fresh
   batches. Removed stale `batch_042`–`batch_099` left by the original prep
   (their still-needed tasks were duplicated into the new batches).
3. **Regenerated** — fresh Workflow (`regen-shard02-recover.js`), one Opus
   worker per batch. 41/42 workers returned cleanly; 1 (batch_008) dropped its
   API connection on the final return but had already written all 20 outputs to
   disk — no tasks lost.
4. **Final bank** — `validate` over all 836 fresh outputs → **800 accepted,
   36 rejected**. Shard 02 final: **1,950/1,986 = 98.19%**.

No data was lost to the crash; the ledger + `.done` archival made every step
safely re-runnable.

## Remaining work

- **Shards 03–11** (9 shards, ~17,874 tasks). Each: `prepare` → Workflow →
  `validate` → `stats`. At ~53 min wall-clock per shard (observed on shard 02's
  42-batch remainder at ~10-wide concurrency), a full shard of 100 batches is
  roughly 2 hrs.
- **Optional tail cleanup** (all shards): the ~36/shard runtime-format rejects
  each have one retry available via `retry_queue.jsonl`. Not chased — every
  completed shard already clears the 85% gate by ~13 points. Low value.

## Resume procedure (after any interruption)

From `toke-corpus/`, for the affected shard NN:
```
python3 regen/run_shard.py validate --shard corpus/regen_v04/shards/shard_NN.jsonl \
    --workdir corpus/regen_v04/work/shard_NN --outdir corpus/regen_v04   # bank survivors
python3 regen/run_shard.py prepare  --shard corpus/regen_v04/shards/shard_NN.jsonl \
    --workdir corpus/regen_v04/work/shard_NN --outdir corpus/regen_v04   # re-batch remainder
#   → then launch a fresh Workflow over the (new, lower-numbered) batches, and validate again.
```
Do **not** use `resumeFromRunId` if changing the worker model arg (invalidates
the cache and re-runs everything). If `prepare` leaves stale high-numbered
batches from a prior 100-batch layout, delete them before launching.

## Deferred (follow-up stories — NOT this run)

- qwen idiom-judge ≥0.6 gate (118.5)
- `--min` re-measure + tokenizer retrain (116.9 / 118.4)
- Export accepted corpus → chat-format `toke-model/training-data-v04/`
  (~50-line adapter of the v03 format, once the full corpus is reviewed)

See the `project-corpus-regen` memory and `reference-toke-lang-gotchas` for the
verified 2.8.0 semantics the syntax card encodes.
