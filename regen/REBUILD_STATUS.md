# Corpus v0.4 Rebuild — Plan & Status

_Last updated: 2026-08-11 (shard 11 complete — ALL 12 SHARDS DONE)_
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

**GENERATION COMPLETE: 23,382 / 23,826 accepted (98.14%). All 12 shards done
(00–11), every shard clears the 85% gate at ~98%. No wave running.**

**Automation is OFF by user request (2026-08-03)** — no 6-hourly resume cron. Waves
are launched one at a time by hand; after each completes, drive the resume
procedure manually: `validate` → `prepare` → delete stale higher-numbered batches →
launch `~/tk/.claude/workflows/regen-shard-wave.js` with `{shard: "shard_NN",
nBatches: <count>}`. Workers inherit the session model.

| Shard | Accepted | Rate | Non-accepts (all runtime-format) | State |
|-------|----------|------|----------------------------------|-------|
| 00    | 1951/1986 | 98.24% | 35 (23 failed_final, 12 pending-retry) | ✅ Done |
| 01    | 1951/1986 | 98.24% | 35 (31 failed_final, 4 pending-retry)  | ✅ Done |
| 02    | 1950/1986 | 98.19% | 36 (23 failed_final, 13 pending-retry) | ✅ Done — **recovered after crash** |
| 03    | 1949/1986 | 98.14% | 37 (17 failed_final, 20 pending-retry) | ✅ Done 2026-07-19 — **first full Fable 5 shard**, 3 waves across usage-window cuts |
| 04    | 1950/1986 | 98.19% | 36 (13 failed_final, 23 pending-retry) | ✅ Done 2026-07-19 — 4 Fable waves + the UnicodeDecode harness fix |
| 05    | 1956/1986 | 98.49% | 30 (1 failed_final, 29 pending-retry)  | ✅ Done 2026-07-20 — highest rate yet; clean 99-worker wave, 0 dead |
| 06    | 1953/1985 | 98.39% | 32 (23 failed_final, 9 pending-retry)  | ✅ Done 2026-07-20 — 2 waves; first run's deaths were transient 529/500 server overload (not Fable limit) |
| 07    | 1942/1985 | 97.83% | 43 (13 failed_final, 30 pending-retry) | ✅ Done 2026-08-01 — 3 waves: wave 1 stalled 2026-07-20 (session died, 568 banked on resume), wave 2 cut by Fable limit at 36/71 workers (805), wave 3 clean 31/31 (569) |
| 08    | 1954/1985 | 98.44% | 31 (6 failed_final, 25 pending-retry) | ✅ Done 2026-08-03 — 4 waves: waves 1–2 chewed up by the Fable 5 usage limit (164 then +390), Fable hit its **hard** limit so the session switched to Opus, wave 3 ran briefly (+70) before a user-requested pause, then a clean **claude-opus-5** wave 4 (69/69 workers, 0 dead) banked the last 1,330. The mid-shard 94.4% was a truncation artifact of the killed waves, not a quality regression — the shard landed on the usual ~98%. |
| 09    | 1937/1985 | 97.58% | 48 (all pending-retry) | ✅ Done 2026-08-03 — **single clean wave**, 100/100 workers, 0 dead, no usage-limit interruption |
| 10    | 1944/1985 | 97.93% | 41 (13 failed_final, 28 pending-retry) | ✅ Done 2026-08-03 — 2 waves: wave 1 cut by the session limit at 25/100 workers (banked 575 — the dying workers wrote ~590 outputs, *more* than the wave's own 500-attempted count, so always `validate` before re-batching), wave 2 clean 71/71 after the reset. Its validate pass was killed mid-run and simply re-run: `.done` archival made it resume from where it stopped. |
| 11    | 1945/1985 | 97.98% | 40 (17 failed_final, 23 pending-retry) | ✅ Done 2026-08-11 — 4 waves: wave 1 hit a mid-run network outage (~42 ENOTFOUND deaths) then the session limit (35/100 returned, but dead workers left 866 outputs on disk → banked 833), wave 2 halved by the session limit again (29/58, +655), wave 3 near-clean 22/25 with 3 tail deaths on the Fable hard limit (+453), wave 4 clean 3/3 retry mop-up (+4). Rejects all runtime-format, the usual D-CFG/D-FIO/D-CLI/D-WEB tail. |

**Harness fixes during the run:**
- `run_shard.py` `run_test_cases` decoded a generated program's stdout as strict
  UTF-8 (`text=True`), so a program emitting **non-UTF-8 bytes** raised
  `UnicodeDecodeError` and **aborted the whole validate pass** (banked nothing).
  Fixed 2026-07-19: added `errors="replace"` — such a program now just fails its
  output-match and is rejected, instead of blocking the bank. (Only affects
  `run_test_cases`, which runs the arbitrary generated binary; compiler-output
  decoders are always UTF-8-safe.)
- **Zombie waves:** a wave where most workers die on the Fable limit stays "open"
  for hours (dead/stalled agents retry, ~18 min each). If a resume tick finds the
  newest `gen/out_*.tk` is >~30 min stale with no completion notification, stop
  the workflow task (`TaskStop <taskId>`, not the run-id) before banking/resuming.
  **BUT check freshness FIRST and act on the result — do not stop in parallel.** A
  stalled wave *self-heals at the reset*: its retrying agents resume producing the
  instant Fable/window resets, which is exactly when a tick fires. On the shard 05
  21:55 tick the wave was writing files *seconds old* (18 in ~20 s) and was stopped
  anyway — harmless (idempotent bank + relaunch recovered it) but wasteful. Rule:
  if the newest gen file is <~5 min old, the wave is LIVE — leave it, end the turn.

**Quality:** across all completed shards, **zero surviving compile failures**. Every
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

**Shard generation is complete (2026-08-11).** What remains is downstream:

- **Epic 129 quality audit** — full independent re-audit of all banked records
  (compile + test-run + structural metrics) before the training-data freeze;
  see `toke/docs/progress.md` Epic 129.
- **Optional tail cleanup** (all shards): the ~30–48/shard runtime-format
  rejects each have one retry available via `retry_queue.jsonl`. Not chased —
  every shard clears the 85% gate by ~13 points. Low value.
- **Export** accepted corpus → chat-format `toke-model/training-data-v04/`
  (deferred story, below) — gated on the Epic 129 audit/freeze.

## Automated shard loop (2026-07-18)

Shard 03 launched 2026-07-18 on **Fable 5** (workers inherit the session model).
The generation workflow is a durable named script:
`~/tk/.claude/workflows/regen-shard-wave.js` — parameterized
`{shard: "shard_NN", nBatches: B}`; launch via Workflow with that scriptPath
after `prepare` reports B batches.

**Loop protocol** (also encoded in a 6-hourly in-session cron tick at
03:25/09:25/15:25/21:25 AEST, sized to the 6-hour usage window): find the lowest
incomplete shard → `validate` (bank survivors) → `prepare` (re-batch remainder;
delete stale higher-numbered batch files) → workflow → `validate` → `stats`
(≥85% gate) → next shard. Never two generation workflows at once. Update this
doc after each shard. The cron tick is session-only — if the session is
restarted, re-create it or drive the loop manually from this doc.

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
