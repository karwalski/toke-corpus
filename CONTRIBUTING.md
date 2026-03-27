# Contributing to toke-corpus

## Rules

- Never commit corpus data files to git. corpus/ contains only schema.json
  and a .gitkeep file. Generated data stays on local storage.
- Never read from benchmark/hidden_tests/ in any script in this repository.
  Holdout task isolation is a hard requirement.
- The corpus schema in corpus/schema.json is normative. Schema changes
  require updating all consumers and bumping the schema version field.
- Deduplication must run before every batch commit to the corpus.
  Do not add a bypass flag.

## Testing

    python -m pytest generator/tests/ pipeline/tests/ judge/tests/ diff_test/tests/

All tests must pass before any PR is merged.
