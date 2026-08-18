# toke-corpus

Corpus generation and audit pipeline for [toke](https://github.com/karwalski/toke-spec)
training data. This repo produces, validates, and quality-audits the corpus of
task-description → toke-source records used to fine-tune toke code models.

## Current pipeline: v0.4 regen (`regen/`)

The **live** pipeline is the v0.4 regeneration harness in `regen/`. It rebuilt the
corpus in current v0.4 / tkc 2.8.0 syntax using agent workers, replacing the older
Gate 2 corpus (of which only ~21% still compiled under tkc 2.8.0).

Key pieces:

- `regen/run_shard.py` — shard driver: `prepare` → worker wave → `validate` → `stats`
- `regen/driver.py`, `regen/check_one.py` — per-task generate/self-check loop
  (tkc `--check` + build + run test cases)
- `regen/audit.py`, `regen/audit_library.py` — Epic 129 quality-audit tooling
- `regen/syntax_card.md` — the **only** authoritative tkc 2.8.0 syntax spec used by
  workers; sha-stamped (`card_sha`, probe-verified against the compiler). All other
  syntax docs are stale by comparison.
- `regen/REBUILD_STATUS.md` — plan, per-shard status, and operating procedure

Status: **generation complete** — 23,382 / 23,826 records accepted (98.14%) across
all 12 shards, every shard clearing the 85% acceptance gate. Accepted records are
banked in `corpus/regen_v04/` (~23.4k records, **gitignored, local-only**). An
**Epic 129 quality audit** of the banked corpus is in progress (see recent
`audit(129.x)` commits).

Acceptance is never delegated to workers: `run_shard.py validate` independently
re-compiles, checks signature conformance, and re-runs test cases for every record
before banking.

## History

The earlier phase-1/phase-2 era (local Qwen generation, 4-language differential
testing, QLoRA training prep) is archived at
`~/tk/archive/toke-corpus-phase-eras-20260819`. `main.py` / `dashboard.py` and the
`infra/` scripts belong to that era's EC2 API-generation path (still referenced by
the deploy scripts); they are **not** part of the current pipeline.

## Repository layout

| Path | What it is |
|------|------------|
| `regen/` | **Current** v0.4 regen harness + Epic 129 audit tooling (see above) |
| `corpus/` | Banked corpus data — `regen_v04/` and phase-era dirs; **gitignored/local** (only `schema.json` is tracked) |
| `data/` | Aux datasets: holdout manifests, data-mix spec, quality notes; large `*.jsonl` gitignored/local |
| `pipeline/` | Phase-2 generation stages (interface/body/companion gen, difficulty scaling, transpile) |
| `generator/` | Programmatic task curriculum generators (`curriculum.py`, `curriculum_v2.py`) |
| `validate/` | Corpus validation: compile checks, dedup, diff-testing, autofixer, quality gates |
| `judge/` | Qwen judge agent + rubric (phase-era quality review) |
| `ingest/` | External-source ingestion (Rosetta, Exercism, repo scanning, AST transpile) |
| `registry/` | Source registry, corpus tagging, train/holdout split protocol, firewall |
| `transpile/` / `transform/` | C/Python → toke transpilers; syntax normalisation |
| `mutate/` / `fuzz/` | Error injection and grammar fuzzing for negative examples |
| `correct/` / `diff_test/` | Repair loop and differential-testing harness (phase era) |
| `compose/` / `curriculum/` | Task composition and phase preparation (phase era) |
| `dispatch/` / `store/` / `trial/` | LLM API dispatch, checkpoint/metrics store, trial runner (phase era) |
| `scripts/` | One-off audit/repair/canonicalisation scripts |
| `infra/` | EC2 deploy + training scripts (phase-era API-generation path) |
| `exemplars/` / `prompts/` | Doc-derived exemplars; prompt templates and constraints |
| `docs/` | Reports and summaries (largely phase-era; `regen/` is the current truth) |
| `metrics/` / `logs/` / `clean/` / `backlog/` | Run metrics, logs (local), cleaned phase data, planning notes |
| `tests/` | Integration tests |

## Where the data lives

Corpus data is **not** stored in git. `corpus/` and the large files under `data/`
live only on the project Mac Studio (`corpus/regen_v04/` holds the banked v0.4
records, `MANIFEST.jsonl`, and shard ledgers).

A fresh clone gets you: all harness/pipeline code, `regen/syntax_card.md`,
`regen/REBUILD_STATUS.md`, `corpus/schema.json`, docs, and the small tracked data
manifests. It does **not** get you the corpus records themselves — anything that
reads `corpus/regen_v04/` requires access to the local data.

## Requirements

- Python 3.11+
- [tkc](https://github.com/karwalski/tkc) 2.8.0 on PATH (the compiler is the root
  of trust — records are only banked if tkc accepts them)

## Related repos

- **toke / tkc** — the language spec and reference compiler; root of trust for the
  whole chain
- **toke-model** — consumes this corpus for fine-tuning
- **toke-eval** — benchmark tasks and hidden test cases
- **toke-tokenizer** — BPE tokenizer training and evaluation

## Licence

Apache 2.0.
