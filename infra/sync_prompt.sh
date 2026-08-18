#!/usr/bin/env bash
# sync_prompt.sh — copy the canonical system prompt from toke-model to toke-corpus
# Intended for CI or local use after updating the prompt in toke-model.

set -euo pipefail

CANONICAL="$(dirname "$0")/../../toke-model/corpus/system_prompt_phase2.txt"
TARGET="$(dirname "$0")/system_prompt_phase2.txt"

if [ ! -f "$CANONICAL" ]; then
  echo "ERROR: canonical prompt not found at $CANONICAL" >&2
  exit 1
fi

cp "$CANONICAL" "$TARGET"
echo "Synced system_prompt_phase2.txt from toke-model to toke-corpus/infra/"
