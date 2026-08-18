# Corpus Record Schema v2

*Canonical JSON schema for every entry in `corpus/phase2_deduplicated/<CATEGORY>/*.json`.*

Schema version: **2** (introduced 2026-04-11 under Story 10.8.1).

v2 supersedes v1 by adding enrichment sub-objects for Phase 2 syntax audit,
compile-check results, imported-module metadata, and runtime/LLM-judgment
placeholders. v1 records are migrated in place by `scripts/validate_schema.py`
by filling missing fields with their null/default values.

## Top-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Stable unique identifier for this record. Format varies by source (e.g. `P2-A-A-ARR-0001v1-4e4955a2`, `SEED-HTTP-005-v02`, `HAND-HTTP-01`). |
| `version` | integer | yes | Schema version. Must be `2`. |
| `phase` | string | yes | Corpus phase; normally `"B"` for Phase 2 records. |
| `task_id` | string | yes | Lineage key — task / seed / mutation parent this record derives from. |
| `category` | string | yes | Category folder name (e.g. `STD-HTTP`, `A-ARR`, `BIFI-missing_semicolon`). Must match the directory the file lives in. |
| `tk_source` | string | yes | The toke program source. Post-10.8.2, this is the *canonical* (possibly autofixed) form. See `syntax_audit.original_source` for the pre-autofix form when `autofix_applied=true`. |
| `tk_tokens` | integer | yes | Whitespace-split token count of `tk_source` (approximate; informational). |
| `attempts` | integer | no | How many LLM/generation attempts were made to produce this record. |
| `model` | string | no | Provenance tag. Examples: `"claude-sonnet-seed-gen"`, `"hand-written"`, `"transpile-python-c-java"`, `"mutation-variable-rename"`. |
| `references` | object | yes | Free-form lineage metadata (story ID, seed ID, source file, transpile sources, mutation details). Structure varies by source type — see per-source notes below. |
| `validation` | object | no | Original generation-time validation (kept for historical continuity). Do not confuse with the new `compile_check` field introduced by Story 10.8.3. |
| `differential` | object | no | Differential-testing record from generation time. |
| `judge` | object | no | LLM-judge acceptance record from generation time. |
| `syntax_audit` | object | yes (post-10.8.2) | Phase 2 syntax audit result. See below. |
| `compile_check` | object | yes (post-10.8.3) | `tkc --check` result against the canonical `tk_source`. See below. |
| `imported_modules` | array of string | yes (post-10.8.4) | Fully-qualified imported module names, e.g. `["std.http", "std.str"]`. |
| `imports_unresolved` | array of string | yes (post-10.8.4) | Imported module names that are NOT in the known stdlib list. Empty array if none. |
| `expected_input` | string or null | yes (post-10.8.5) | Placeholder for future runtime input (stdin or argv). Initialised to `null`. |
| `expected_output` | string or null | yes (post-10.8.5) | Placeholder for the stdout this program *should* produce. Initialised to `null`; populated later by a runtime-testing or hand-authoring story. |
| `runtime_check` | object | yes (post-10.8.5) | Runtime execution result placeholder. See below. |
| `judge_output_check` | object | yes (post-10.8.5) | LLM correctness-judgment placeholder. See below. |

## `syntax_audit` sub-object

```json
{
  "violations": [
    {"rule": "uppercase_identifier", "name": "arrMax", "line": 1, "col": 10, "fixable": true},
    {"rule": "double_equals", "line": 5, "col": 12, "fixable": true}
  ],
  "autofix_applied": true,
  "original_source": "m=arrmax;f=arrMax(arr:@i64):i64{...}",
  "phase2_conformant": true,
  "collisions": [],
  "checked_at": "2026-04-11T14:03:22Z"
}
```

| Field | Type | Description |
|---|---|---|
| `violations` | array | List of Phase 2 rule violations detected in the *original* (pre-autofix) source. Each entry: `{rule, name?, line, col, fixable}`. |
| `autofix_applied` | bool | `true` iff the autofixer rewrote `tk_source` in place. When `true`, `original_source` is the pre-rewrite form. When `false`, `original_source` is omitted. |
| `original_source` | string (optional) | Only present when `autofix_applied=true`. The pre-autofix `tk_source`. |
| `phase2_conformant` | bool | `true` iff *after* autofix, no Phase 2 violations remain. `false` means the entry could not be mechanically fixed — quarantined from training. |
| `collisions` | array of string | Identifier-lowering collisions discovered during autofix (e.g. two distinct camelCase names that would collapse to the same lowercase string). Empty array if none. Presence of any collision forces `autofix_applied=false` and `phase2_conformant=false` for that record. |
| `checked_at` | ISO-8601 | When the audit ran. |

### Phase 2 rules checked

| `rule` value | Meaning | Fixable? |
|---|---|---|
| `uppercase_identifier` | Identifier contains an uppercase letter | yes (→ lowercase concat, unless collision) |
| `underscore_identifier` | Identifier contains `_` | yes (→ strip underscore, unless collision) |
| `double_equals` | `==` used for equality | yes (→ `=`) |
| `bang_equals` | `!=` used for inequality | yes (→ `!(a=b)`) |
| `len_parens` | `.len()` instead of `.len` | yes (→ `.len`) |
| `comma_separator` | `,` used where `;` is required | yes (→ `;`) where unambiguous |
| `void_no_dollar` | `:void` instead of `:$void` | yes (→ `:$void`) — see note |
| `reserved_name_collision` | Identifier matches a reserved/builtin | no |

**Note on `:void` vs `:$void`**: The current spec (stdlib-signatures.md) uses bare `:void`, but many validated corpus records use `:$void`. Story 10.8.2 must resolve which is canonical before autofixing, by running both against `tkc --check` on a minimal example. Whichever is canonical becomes the fix target; the other becomes the violation.

## `compile_check` sub-object

```json
{
  "passed": true,
  "exit_code": 0,
  "error_codes": [],
  "stage": null,
  "diagnostic": null,
  "inverted": false,
  "expected_error_code": null,
  "ran_at": "2026-04-11T14:20:09Z",
  "tkc_version": "tkc@abcd1234"
}
```

| Field | Type | Description |
|---|---|---|
| `passed` | bool | `true` iff `tkc --check` produced exit code 0. For **inverted** categories (`BIFI-*`, `ERR-TRIPLE-*` broken variants), `passed=true` means the broken-side source failed with the *expected* error code (i.e., it's a correct broken example). |
| `exit_code` | integer | Actual `tkc --check` exit code. |
| `error_codes` | array of string | Diagnostic codes emitted by tkc (e.g. `["E4031"]`). |
| `stage` | string or null | Stage the first error was raised in (`lex` / `parse` / `type_check`). Null on success. |
| `diagnostic` | string or null | First diagnostic message (trimmed). Null on success. |
| `inverted` | bool | `true` for BIFI / ERR-TRIPLE broken-variant records where failure is the success condition. |
| `expected_error_code` | string or null | For `inverted=true` records, the error code the broken variant *should* produce. `passed=true` requires this code to appear in `error_codes`. |
| `ran_at` | ISO-8601 | When the check ran. |
| `tkc_version` | string | Commit hash or version identifier of the tkc binary used. |

## `runtime_check` sub-object (placeholder, Story 10.8.5)

```json
{
  "linked": false,
  "ran": false,
  "exit_code": null,
  "stdout": null,
  "stderr": null,
  "captured_at": null,
  "timeout_s": null,
  "link_errors": []
}
```

All fields are null / false / empty until Story 10.8.6 populates them.

| Field | Type | Description |
|---|---|---|
| `linked` | bool | Full `tkc` compile (not `--check`) produced a binary. |
| `ran` | bool | Binary ran to completion or timed out. |
| `exit_code` | integer or null | Process exit code, or null if did not run. |
| `stdout` | string or null | Captured stdout (first 16 KiB). |
| `stderr` | string or null | Captured stderr (first 16 KiB). |
| `captured_at` | ISO-8601 or null | When the run happened. |
| `timeout_s` | integer or null | Wall-clock timeout applied (typically 5). |
| `link_errors` | array of string | Link-stage errors (unresolved symbols etc.) when `linked=false`. |

## `judge_output_check` sub-object (placeholder, Story 10.8.5)

```json
{
  "verified": false,
  "correct": null,
  "reasoning": null,
  "confidence": null,
  "judge_model": null,
  "judged_at": null
}
```

Populated by Story 10.8.7. `correct=true` means an LLM judge reviewed the
(source, captured stdout) pair and agreed the output is what the program should
produce. `verified=false` means judgment has not yet run.

## `references` sub-object — notes

Structure varies by source type. Common keys:

- `story` — backlog story that created the record (e.g. `"10.3.4"`, `"10.3.5"`).
- `source` — provenance tag (`"hand-crafted"`, `"transpile"`, `"oss-instruct"`, `"mutation"`, `"seed-gen"`).
- `seed_id` — if derived from a seed program.
- `variation` — mutation/variation index.
- `complexity` — inferred complexity tier (`simple` / `medium` / `complex` / `application`).
- `module` — for stdlib-targeted records, the primary stdlib module under test.
- `task` — short human-readable task description (when available).
- `original_source` — for mutation records, the pre-mutation seed source.
- `mutation_type` / `mutation_details` — mutation lineage.
- `python_source` / `c_source` / `java_source` — for transpile records, the source-language inputs.
- `doc_source` — for DOC-EXP records, the path to the source documentation file.

No specific keys are *required* in `references`, but every record must have the field present as an object.

## Migration from v1 to v2

Run `scripts/validate_schema.py --migrate` to fill missing v2 fields with
their default values (`syntax_audit: null`, `compile_check: null`, etc.).
This is idempotent and preserves all v1 data. Records with `version=2` are
left untouched.

## Validator

`scripts/validate_schema.py` performs the following checks per record:

1. All required fields present and of the correct type.
2. `version == 2`.
3. `category` matches the parent directory name.
4. `id` is unique within the parent category (scanned at end-of-run).
5. Cross-field consistency: `syntax_audit.autofix_applied=true` ⇒ `original_source` present; `runtime_check.ran=true` ⇒ `captured_at` present; etc.

Exit code 0 if all records conform, 1 otherwise. Prints a per-category
pass/fail summary on stderr.
