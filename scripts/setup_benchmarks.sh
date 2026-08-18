#!/usr/bin/env bash
# setup_benchmarks.sh — Download all benchmark datasets to data/benchmarks/
#
# Story 9.2.6 (Part 3).
#
# Idempotent: skips sources whose artefact already exists.
# Run from the toke-corpus repository root.
set -euo pipefail

CACHE_DIR="data/benchmarks"
mkdir -p "$CACHE_DIR"

downloaded=0
skipped=0

# --- humaneval ---
if [ -e "$CACHE_DIR/human-eval/data/HumanEval.jsonl.gz" ]; then
  echo "SKIP humaneval (already cached)"
  skipped=$((skipped + 1))
else
  git clone --depth 1 https://github.com/openai/human-eval.git "$CACHE_DIR/human-eval"
  echo "DONE humaneval"
  downloaded=$((downloaded + 1))
fi

# --- mbpp ---
if [ -e "$CACHE_DIR/mbpp/mbpp.jsonl" ]; then
  echo "SKIP mbpp (already cached)"
  skipped=$((skipped + 1))
else
  mkdir -p "$CACHE_DIR/mbpp"
  curl -fSL -o "$CACHE_DIR/mbpp/mbpp.jsonl" \
    "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl"
  echo "DONE mbpp"
  downloaded=$((downloaded + 1))
fi

# --- apps ---
if [ -e "$CACHE_DIR/apps/train" ]; then
  echo "SKIP apps (already cached)"
  skipped=$((skipped + 1))
else
  git clone --depth 1 https://github.com/hendrycks/apps.git "$CACHE_DIR/apps"
  echo "DONE apps"
  downloaded=$((downloaded + 1))
fi

# --- codecontests ---
if [ -e "$CACHE_DIR/code_contests" ]; then
  echo "SKIP codecontests (already cached)"
  skipped=$((skipped + 1))
else
  git clone --depth 1 https://github.com/google-deepmind/code_contests.git "$CACHE_DIR/code_contests"
  echo "DONE codecontests"
  downloaded=$((downloaded + 1))
fi

# --- taco ---
if [ -e "$CACHE_DIR/TACO" ]; then
  echo "SKIP taco (already cached)"
  skipped=$((skipped + 1))
else
  git clone --depth 1 https://github.com/FlagOpen/TACO.git "$CACHE_DIR/TACO"
  echo "DONE taco"
  downloaded=$((downloaded + 1))
fi

# --- leetcodedataset ---
if [ -e "$CACHE_DIR/leetcode" ]; then
  echo "SKIP leetcodedataset (already cached)"
  skipped=$((skipped + 1))
else
  git clone --depth 1 https://github.com/doocs/leetcode.git "$CACHE_DIR/leetcode"
  echo "DONE leetcodedataset"
  downloaded=$((downloaded + 1))
fi

echo ""
echo "=== Summary ==="
echo "Downloaded: $downloaded"
echo "Skipped:    $skipped"
echo "All benchmarks ready."
