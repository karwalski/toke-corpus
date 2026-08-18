# Attribution

This document provides per-source attribution for all third-party material
ingested into the toke-corpus, as required by their respective licenses.

---

## Ingested Source Material

### Exercism

| Field | Value |
|-------|-------|
| **Source** | Exercism Python Track |
| **URL** | <https://github.com/exercism/python> |
| **License** | CC-BY-SA-3.0 |
| **Copyright** | Exercism contributors |
| **Usage** | Exemplar solutions are transpiled to toke via `ingest/exercism.py` |
| **Corpus ID prefix** | `exercism-python-*` |
| **Modifications** | Source code transpiled from Python to the toke language; original Python is not redistributed |

**Required notice (CC-BY-SA-3.0):** This corpus contains adapted material
based on Exercism exercise solutions, originally licensed under
[CC-BY-SA-3.0](https://creativecommons.org/licenses/by-sa/3.0/).
The adapted material is shared under compatible license terms.

---

### Rosetta Code

| Field | Value |
|-------|-------|
| **Source** | Rosetta Code (via RosettaCodeData mirror) |
| **URL** | <https://github.com/acmeism/RosettaCodeData> |
| **License** | GFDL-1.2 |
| **Copyright** | Rosetta Code contributors |
| **Usage** | Python and C task solutions are transpiled to toke via `ingest/rosetta.py` |
| **Corpus ID prefix** | `rosetta-*` |
| **Modifications** | Source code transpiled from Python/C to the toke language; originals are not redistributed |

**Required notice (GFDL-1.2):** This corpus contains material derived from
Rosetta Code, licensed under the
[GNU Free Documentation License 1.2](https://www.gnu.org/licenses/old-licenses/fdl-1.2.html).

---

### 30-seconds-of-python

| Field | Value |
|-------|-------|
| **Source** | 30-seconds-of-python |
| **URL** | <https://github.com/30-seconds/30-seconds-of-python> |
| **License** | CC-BY-4.0 |
| **Copyright** | 30-seconds contributors |
| **Usage** | Python function snippets extracted from Markdown and transpiled to toke via `ingest/snippets.py` |
| **Corpus ID prefix** | `snippet-30-seconds-of-python-*` |
| **Modifications** | Code blocks extracted from Markdown files and transpiled to toke |

**Required notice (CC-BY-4.0):** Based on material from
[30-seconds-of-python](https://github.com/30-seconds/30-seconds-of-python),
licensed under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).

---

### Go by Example

| Field | Value |
|-------|-------|
| **Source** | Go by Example |
| **URL** | <https://github.com/mmcgrana/gobyexample> |
| **License** | CC-BY-3.0 |
| **Copyright** | Mark McGranaghan and contributors |
| **Usage** | Go functions extracted for future transpilation via `ingest/snippets.py` |
| **Corpus ID prefix** | `snippet-go-by-example-*` |
| **Modifications** | Functions extracted from example files; Go transpiler pending |

**Required notice (CC-BY-3.0):** Based on material from
[Go by Example](https://gobyexample.com) by Mark McGranaghan,
licensed under [CC-BY-3.0](https://creativecommons.org/licenses/by/3.0/).

---

## Benchmark Datasets (Evaluation Only)

The following datasets are included under `data/benchmarks/` for evaluation
purposes. They are used to generate task specifications and measure corpus
quality; their content is not directly included in training data.

### HumanEval

| Field | Value |
|-------|-------|
| **Source** | HumanEval |
| **URL** | <https://github.com/openai/human-eval> |
| **License** | MIT |
| **Copyright** | OpenAI |
| **Local path** | `data/benchmarks/human-eval/` |

---

### APPS

| Field | Value |
|-------|-------|
| **Source** | APPS (Automated Programming Progress Standard) |
| **URL** | <https://github.com/hendrycks/apps> |
| **License** | MIT |
| **Copyright** | 2021 Dan Hendrycks |
| **Local path** | `data/benchmarks/apps/` |

---

### CodeContests

| Field | Value |
|-------|-------|
| **Source** | CodeContests |
| **URL** | <https://github.com/google-deepmind/code_contests> |
| **License** | Apache-2.0 |
| **Copyright** | DeepMind |
| **Local path** | `data/benchmarks/code_contests/` |

---

### TACO

| Field | Value |
|-------|-------|
| **Source** | TACO |
| **URL** | <https://github.com/FlagOpen/TACO> |
| **License** | Apache-2.0 |
| **Copyright** | FlagOpen / BAAI |
| **Local path** | `data/benchmarks/TACO/` |

---

### LeetCode

| Field | Value |
|-------|-------|
| **Source** | LeetCode dataset |
| **License** | CC-BY-SA-4.0 |
| **Local path** | `data/benchmarks/leetcode/` |

---

### MBPP

| Field | Value |
|-------|-------|
| **Source** | Mostly Basic Python Problems (MBPP) |
| **URL** | <https://github.com/google-research/google-research/tree/master/mbpp> |
| **License** | CC-BY-4.0 |
| **Copyright** | Google Research |
| **Local path** | `data/benchmarks/mbpp/` |

---

## AST-Harvested Open-Source Functions

Functions harvested via `ingest/repo_scanner.py` and `ingest/ast_harvest.py`
carry per-entry license metadata in the corpus JSONL `source.license` field.
Each entry records the detected license of the originating repository. Only
repositories with permissive licenses (MIT, Apache-2.0, BSD, ISC, Unlicense)
are included by default.

---

## LLM-Generated Corpus Entries

The majority of corpus entries (phases A through D) are generated by large
language models (Claude, GPT-4.1-mini, Grok, Gemini, DeepSeek) from task
specifications. These entries are original works and do not carry third-party
license obligations. They are covered by the project's Apache-2.0 license.

---

## Document Exemplars

The `exemplars/` directory contains code examples extracted from the toke
language documentation (toke-web). These are original works by the toke
project and licensed under Apache-2.0.
