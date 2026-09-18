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
