# Third-Party Licenses

This document lists all third-party licenses applicable to source material
ingested into the toke-corpus. The corpus itself is licensed under
Apache License 2.0 (see `LICENSE`).

---

## CC-BY-SA-3.0 -- Creative Commons Attribution-ShareAlike 3.0

**Used by:** Exercism exercise content

Exercism exercises and their exemplar solutions are licensed under
CC-BY-SA-3.0. Transpiled toke versions in this corpus constitute adapted
material and are shared under compatible terms.

Full text: <https://creativecommons.org/licenses/by-sa/3.0/legalcode>

---

## GFDL-1.2 -- GNU Free Documentation License 1.2

**Used by:** Rosetta Code task solutions

Rosetta Code content is published under the GNU Free Documentation License,
version 1.2. Code examples extracted and transpiled for this corpus retain
their GFDL-1.2 provenance.

Full text: <https://www.gnu.org/licenses/old-licenses/fdl-1.2.html>

---

## CC-BY-4.0 -- Creative Commons Attribution 4.0 International

**Used by:** 30-seconds-of-python snippet collection

Snippets from the 30-seconds-of-python repository are licensed under
CC-BY-4.0. Attribution is provided per the license requirements.

Full text: <https://creativecommons.org/licenses/by/4.0/legalcode>

---

## CC-BY-3.0 -- Creative Commons Attribution 3.0 Unported

**Used by:** Go by Example

Go by Example content is licensed under CC-BY-3.0. Go snippets are
collected for future transpilation; attribution is preserved.

Full text: <https://creativecommons.org/licenses/by/3.0/legalcode>

---

## CC-BY-SA-4.0 -- Creative Commons Attribution-ShareAlike 4.0 International

**Used by:** LeetCode benchmark tasks

The LeetCode benchmark dataset included in `data/benchmarks/leetcode/` is
licensed under CC-BY-SA-4.0. These tasks are used for evaluation only.

Full text: <https://creativecommons.org/licenses/by-sa/4.0/legalcode>

---

## MIT License

**Used by:** HumanEval benchmark (OpenAI), APPS benchmark (Dan Hendrycks)

The HumanEval and APPS benchmark datasets are licensed under the MIT License.
These are used for evaluation purposes.

Full text: <https://opensource.org/licenses/MIT>

---

## Apache License 2.0

**Used by:** CodeContests benchmark (DeepMind), TACO benchmark

The CodeContests and TACO benchmark datasets are licensed under the
Apache License, Version 2.0. These are used for evaluation purposes.

Full text: <https://www.apache.org/licenses/LICENSE-2.0>

---

## Permissive Open-Source Licenses (AST-Harvested Repositories)

**Used by:** Functions harvested via `ingest/repo_scanner.py`

The AST harvesting pipeline (`ingest/ast_harvest.py`, `ingest/repo_scanner.py`)
extracts pure functions from open-source repositories. Each repository's
license is auto-detected and recorded in the corpus entry metadata. Accepted
license types include:

- MIT
- Apache-2.0
- BSD-2-Clause
- BSD-3-Clause
- ISC
- Unlicense / Public Domain
- MPL-2.0

Repositories with undetected or copyleft (GPL) licenses are flagged and
require manual review before inclusion.
