# toke-corpus

Corpus generation and audit pipeline for [toke](https://github.com/karwalski/toke)
training data. This repo produces, validates, and quality-audits the corpus of
task-description → toke-source records used to fine-tune toke code models.

## About toke

> toke: a compiled language designed for LLM code generation, with a small grammar, one
> canonical form and compiler verification.

toke is a compiled programming language designed for LLM code generation. It has 14
keywords, a 59-character set, a backtrack-free grammar with bounded lookahead, and one
canonical form per construct, chosen by measurement in a 46-pattern catalogue and
reproduced by `tkc --min`. That makes generated code cheap to constrain during decoding,
cheap for a compiler to verify afterwards, and compact to emit. Token efficiency is one
measured property of toke, always reported with its tokenizer and its baseline, not the
whole claim.

*The one-liner and the paragraph above are reproduced word for word from the canonical
description,
[`docs/about/canonical.md`](https://github.com/karwalski/toke/blob/main/docs/about/canonical.md).
Every number published about toke comes from
[`docs/metrics-baseline.md`](https://github.com/karwalski/toke/blob/main/docs/metrics-baseline.md)
and nowhere else.*

## Current pipeline: v0.4 regen (`regen/`)

The **live** pipeline is the v0.4 regeneration harness in `regen/`. It rebuilt the
corpus in v0.4 syntax (generated under tkc 2.8.0) using agent workers, replacing the
older v0.3 Gate 2 corpus, which predates v0.4's breaking syntax changes.

Key pieces:

- `regen/run_shard.py` — shard driver: `prepare` → worker wave → `validate` → `stats`
- `regen/driver.py`, `regen/check_one.py` — per-task generate/self-check loop
  (tkc `--check` + build + run test cases)
- `regen/audit.py`, `regen/audit_library.py` — Epic 129 quality-audit tooling
- `regen/syntax_card.md` — the **only** authoritative syntax spec used by workers
  (card v2, tkc 3.1.0 / v0.4 idiom); sha-stamped (`card_sha`, gated by
  `regen/check_card.py`, probe-verified against the compiler). All other syntax docs
  are stale by comparison.
- `regen/tkc_pin.py` — pins one tkc binary for the life of a run and stamps its sha
  into every output
- `regen/pattern_sweep.py` — the Epic 131 pattern/conformance sweep that buckets every
  record for the rewrite waves
- `regen/REBUILD_STATUS.md` — plan, per-shard status, and operating procedure
- `regen/freeze/` — freeze summaries and the Epic 131 recheck ledgers

Status: **generation complete, rewrite waves not yet run.** All 12 shards were
generated, every shard clearing the 85% acceptance gate, and the accepted records are
banked in `corpus/regen_v04/` (**gitignored, local-only**). The Epic 129 quality audit
froze them on 2026-08-19 as `129-freeze-2026-08-19`, 23,382 records
(`regen/AUDIT_129.md`; the count is `corpus_records_v04_frozen` in
[`docs/metrics-baseline.md`](https://github.com/karwalski/toke/blob/main/docs/metrics-baseline.md)).
Epic 131 has reopened that freeze for pattern rewrites: the conformance sweep (131.13)
has run, but the agent rewrite wave (131.15) is prepared and **held** — it has not run —
so the corpus is not re-frozen and is not v0.4-canonical yet. Status lives in the toke
tracker, [`docs/progress.md`](https://github.com/karwalski/toke/blob/main/docs/progress.md).

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
| `correct/` | Repair loop (phase era); the differential test is `validate/diff_test.py` |
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
- [tkc](https://github.com/karwalski/toke) (toke 3.1.0, the version the syntax card is
  verified against; the compiler is the root of trust — records are only banked if tkc
  accepts them)
- the `toke` repo checked out beside this one (`~/tk/toke`): `regen/` execs its
  `tkc` (pinned, see `regen/tkc_pin.py`) and imports its
  `scripts/patterns/count_tokens.py`

## Running the tests

One interpreter runs every suite: the repo virtualenv `.venv`, with every
dependency declared in `pyproject.toml` (including `tokenizers`, which `regen/`
needs for `count_tokens.py`, and the `dev` extra for pytest). A system Python
does not work — Homebrew's, for one, has no `jsonschema` or `tokenizers`.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

One pytest run covers every suite, `regen/` included (the regen gate module is
`regen/regen_validate.py`, so it no longer shadows the top-level `validate/`
package):

```sh
.venv/bin/python -m pytest -q    # the whole repo (slow: regen/ builds with tkc)
```

It must pass before a merge.

## Related repos

- **toke / tkc** — the language spec and reference compiler; root of trust for the
  whole chain
- **toke-models** (local checkout `toke-model`) — consumes this corpus for fine-tuning
- **toke-eval** — benchmark tasks and evaluation harness; its held-out tests are never
  read by anything in this repository
- **toke-tokenizer** — BPE tokenizer training and evaluation

## Licence

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
