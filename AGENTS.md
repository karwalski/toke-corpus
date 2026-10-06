# AGENTS.md
## Agent operating notes — toke-corpus

**Version:** 2.0 (story 131.95, 2026-10-07)
**Supersedes:** v1.0, which described the pre-split monorepo (`tkc/`,
`stdlib/`, `corpus/`, `tokenizer/`, `models/`, `benchmark/`, `spec/` in one
repository). None of those trees except `corpus/` live in this repo any more.

---

## 1. Workspace rules come first

This repo is one of the sibling repositories in the toke workspace (`~/tk/`).
The binding operating contract for every agent is the workspace file
**`~/tk/AGENTS.md`**: repo map, hygiene rules, published-claims gates, hidden
tests isolation, git rules (branching, conventional commits, pathspec commits
when agents share a working tree), and story discipline. Read it in full first.
This file only adds what is specific to `toke-corpus`; where the two disagree,
`~/tk/AGENTS.md` wins.

- **Tracker:** every story lives in `~/tk/toke/docs/progress.md` (the single
  cross-repo tracker). This repo has no tracker of its own.
- **Metrics:** any number about toke comes from
  `~/tk/toke/docs/metrics-baseline.md`; corpus counts in prose are checked by
  `make check-claims-all` in `~/tk/toke`.

---

## 2. What this repo is

The corpus generation and audit pipeline: task description → toke source
records used to fine-tune toke code models. See `README.md` for the full
layout. The parts an agent touches most:

| Path | What it is |
|---|---|
| `regen/` | **Live** v0.4 regen harness and Epic 129/131 audit tooling (`run_shard.py`, `regen_validate.py`, `bank_*.py`, `syntax_card.md`, `REBUILD_STATUS.md`) |
| `store/writer.py` | `CorpusWriter`: validates every entry against `corpus/schema.json` and refuses holdout task IDs before writing |
| `corpus/schema.json` | Normative corpus entry schema (the only tracked file under `corpus/`) |
| `validate/` | Compile checks, dedup (`dedup.py`), differential test (`diff_test.py`), quality gates |
| `scripts/id_disjoint.py` | Corpus/benchmark ID disjointness check (`PASS` / `VACUOUS` / `FAIL`) and `HoldoutGuard` |
| `judge/`, `pipeline/`, `dispatch/`, `trial/`, `infra/` | Phase-era pipeline, kept for reference and still tested |

The compiler is **not** here. `regen/` execs the pinned `tkc` from the `toke`
repo checked out beside this one (`regen/tkc_pin.py`) and imports its
`scripts/patterns/count_tokens.py`. Compiler rules, conformance tests
(`make conform`) and stdlib rules belong to `~/tk/toke`. The tokenizer and the
model pipelines live in `toke-tokenizer` and `toke-model`; the benchmark lives
in `toke-eval`.

---

## 3. Corpus rules

- **Validation before banking.** A record enters the corpus only after the
  validation pipeline accepts it: `tkc` compiles it and its test cases run
  (`regen/regen_validate.py`, driven by `run_shard.py validate` and the
  `bank_*.py` scripts). Acceptance is never delegated to workers.
- **Schema.** `corpus/schema.json` is normative. Do not add or change a field
  without updating the schema, `CorpusWriter` in `store/writer.py`, and its
  tests in `store/tests/test_store.py`.
- **Deduplication** runs before every batch commit to the corpus. Do not add a
  bypass flag.
- **Holdout isolation.** Never read any `hidden_tests/` directory
  (`~/tk/AGENTS.md` §5). Held-out benchmark task IDs must never appear in
  corpus data; `CorpusWriter` refuses a write without a holdout set, and the
  corpus/benchmark ID check (`scripts/id_disjoint.py`) must report `PASS`, not
  `VACUOUS`, before a batch is finalised.
- **No corpus data in git.** `corpus/` (except `schema.json`) and the large
  files under `data/` are gitignored and local-only. No JSONL corpus, weights
  or results get committed (`~/tk/AGENTS.md` §4).
- **Syntax.** `regen/syntax_card.md` is the only authoritative tkc syntax
  reference for workers; other syntax docs in this repo are older.

---

## 4. Commands

Tests run with the repo virtualenv; a system `python3` lacks `jsonschema` and
`tokenizers` and will fail at import. Setup is in `README.md`, "Running the
tests":

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

**One pytest run covers every suite**, `regen/` included (since 131.88):

```sh
.venv/bin/python -m pytest -q            # whole repo; slow, regen/ builds with tkc
```

It must pass before a merge. Narrower runs while iterating:

```sh
.venv/bin/python -m pytest -q store/tests/test_store.py   # schema check (CorpusWriter)
.venv/bin/python -m pytest -q regen/tests                 # regen harness only
.venv/bin/ruff check <paths>                              # lint (config in pyproject.toml)
```

Published-claims guards (run on anything you touched that states a toke fact):

```sh
python3 ~/tk/toke/scripts/check_metrics_claims.py <paths>
python3 ~/tk/toke/scripts/check_canonical.py <paths>
```

There is no Makefile in this repo; there is no `make conform`, `make lint` or
`make ci` here.

---

## 5. Python conventions

- Python 3.11+, type annotations on function signatures, `ruff` for lint and
  `mypy` (strict) configured in `pyproject.toml`.
- No bare `except:`; CLI scripts use `argparse`; no hardcoded absolute paths.
- Changes to pipeline or regen code ship with tests beside the code
  (`<package>/tests/` or `regen/tests/`).

---

## 6. Git

Follow `~/tk/AGENTS.md` §7: never commit to `main`; branch as
`feature|fix|refactor|test|docs/<component>-<description>` where the component
names the area of this repo (`regen`, `pipeline`, `store`, `validate`, ...);
conventional commits with a scope and the story ID; pathspec commits
(`git commit -m "..." -- <paths>`, then `git show --stat`). This is a public
repo: no credentials, IPs, key paths or `.env` contents.
