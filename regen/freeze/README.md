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

Byte snapshot: `~/tk/archive/toke-corpus-regen_v04-freeze129-20260819/` (local
archive, see its `MANIFEST.md`). Git tag: `freeze-129-20260819`.

## `ledger/rewrite_131.jsonl` — append-only rewrite ledger

Location: `corpus/regen_v04/ledger/rewrite_131.jsonl` (gitignored, on disk).
One JSON object per line, one line per attempt/outcome; never edit or reorder
earlier lines.

| Field | Type | Meaning |
|---|---|---|
| `task_id` | string | record id, e.g. `A-ARR-0001v24` |
| `wave` | `auto` \| `agent` \| `regen` \| `library` | which Epic 131 wave touched it |
| `status` | `rewritten` \| `unchanged` \| `failed` \| `skipped` | outcome of this attempt |
| `reason` | string | why (pattern ids fixed, lint rule, or failure summary) |
| `attempts` | int | cumulative attempts for this task_id in this wave |
| `prev_sha256` | string | sha256 of the record bytes before this attempt (matches `freeze_129_manifest.jsonl` on the first touch) |
| `new_sha256` | string \| null | sha256 after banking; null when not rewritten |
| `ts` | string | ISO-8601 UTC timestamp |

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
