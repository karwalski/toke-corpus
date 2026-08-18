#!/usr/bin/env bash
# ec2_refine.sh — Deploy and run LLM refinement on EC2
#
# Story 57.16.9 + 57.16.10
#
# Prerequisites on EC2 instance:
#   - Python 3.11+, pip install anthropic
#   - tkc binary compiled and in PATH (or set TKC env var)
#   - Training data and corpus files uploaded
#
# Usage:
#   # From local machine — upload and run:
#   ./scripts/ec2_refine.sh upload
#   ./scripts/ec2_refine.sh run --workers 8 --max-records 5000
#   ./scripts/ec2_refine.sh status
#   ./scripts/ec2_refine.sh download
#
# Or directly on EC2:
#   export ANTHROPIC_API_KEY=sk-...
#   export TKC=/home/ubuntu/toke/tkc
#   nohup python3 scripts/refine_corpus_llm.py \
#     --training-data data/refreshed/train.jsonl \
#     --corpus data/corpus_default.jsonl \
#     --output data/refined_full.jsonl \
#     --max-records 18814 --diverse --resume \
#     --skip-categories FUZZ \
#     --workers 8 \
#     > logs/refine_$(date +%Y%m%d_%H%M).log 2>&1 &

set -euo pipefail

EC2_HOST="${EC2_CORPUS_HOST:-3.111.50.47}"
EC2_USER="${EC2_CORPUS_USER:-ubuntu}"
EC2_KEY="${EC2_CORPUS_KEY:-$HOME/.ssh/mumbai.pem}"
EC2_DIR="/home/ubuntu/toke-corpus"

SSH="ssh -i $EC2_KEY -o StrictHostKeyChecking=no $EC2_USER@$EC2_HOST"
SCP="scp -i $EC2_KEY -o StrictHostKeyChecking=no"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

cmd="${1:-help}"
shift || true

case "$cmd" in
  upload)
    echo "=== Uploading scripts and data to EC2 ==="
    $SSH "mkdir -p $EC2_DIR/{scripts,data/refreshed,logs,infra}"

    # Upload scripts
    $SCP "$SCRIPT_DIR/refine_corpus_llm.py" \
         "$SCRIPT_DIR/phase2_syntax_audit.py" \
         "$EC2_USER@$EC2_HOST:$EC2_DIR/scripts/"

    # Upload system prompt
    $SCP "$REPO_DIR/../toke-model/corpus/system_prompt_phase2.txt" \
         "$EC2_USER@$EC2_HOST:$EC2_DIR/../toke-model/corpus/system_prompt_phase2.txt" 2>/dev/null || \
    $SCP "$REPO_DIR/infra/system_prompt_phase2.txt" \
         "$EC2_USER@$EC2_HOST:$EC2_DIR/infra/"

    # Upload training data (large — rsync for efficiency)
    rsync -avz --progress -e "ssh -i $EC2_KEY" \
      "$REPO_DIR/data/refreshed/train.jsonl" \
      "$EC2_USER@$EC2_HOST:$EC2_DIR/data/refreshed/"

    rsync -avz --progress -e "ssh -i $EC2_KEY" \
      "$REPO_DIR/data/corpus_default.jsonl" \
      "$EC2_USER@$EC2_HOST:$EC2_DIR/data/"

    echo "=== Upload complete ==="
    ;;

  run)
    echo "=== Starting refinement on EC2 ==="
    WORKERS="${WORKERS:-8}"
    MAX="${MAX_RECORDS:-18814}"

    $SSH "cd $EC2_DIR && \
      export ANTHROPIC_API_KEY=\$ANTHROPIC_API_KEY && \
      export TKC=\${TKC:-/home/ubuntu/toke/tkc} && \
      mkdir -p logs && \
      nohup python3 scripts/refine_corpus_llm.py \
        --training-data data/refreshed/train.jsonl \
        --corpus data/corpus_default.jsonl \
        --output data/refined_full.jsonl \
        --max-records $MAX --diverse --resume \
        --skip-categories FUZZ \
        --workers $WORKERS $* \
        > logs/refine_\$(date +%Y%m%d_%H%M).log 2>&1 &
      echo \"PID: \$!\"
      echo \"Log: logs/refine_\$(date +%Y%m%d_%H%M).log\""
    ;;

  status)
    echo "=== Checking refinement status ==="
    $SSH "cd $EC2_DIR && \
      echo '--- Process ---' && \
      ps aux | grep refine_corpus_llm | grep -v grep || echo 'Not running' && \
      echo '' && \
      echo '--- Output size ---' && \
      wc -l data/refined_full.jsonl 2>/dev/null || echo 'No output yet' && \
      echo '' && \
      echo '--- Latest summary ---' && \
      cat data/refined_full.summary.json 2>/dev/null || echo 'No summary yet' && \
      echo '' && \
      echo '--- Last 5 log lines ---' && \
      ls -t logs/refine_*.log 2>/dev/null | head -1 | xargs tail -5 2>/dev/null || echo 'No logs'"
    ;;

  download)
    echo "=== Downloading results from EC2 ==="
    mkdir -p "$REPO_DIR/data/ec2_results"
    $SCP "$EC2_USER@$EC2_HOST:$EC2_DIR/data/refined_full.jsonl" \
         "$REPO_DIR/data/ec2_results/"
    $SCP "$EC2_USER@$EC2_HOST:$EC2_DIR/data/refined_full.summary.json" \
         "$REPO_DIR/data/ec2_results/" 2>/dev/null || true
    echo "=== Download complete: data/ec2_results/ ==="
    ;;

  help|*)
    echo "Usage: $0 {upload|run|status|download}"
    echo ""
    echo "  upload    — Push scripts + data to EC2"
    echo "  run       — Start refinement (env: WORKERS=8, MAX_RECORDS=18814)"
    echo "  status    — Check progress"
    echo "  download  — Pull results back"
    echo ""
    echo "Environment:"
    echo "  EC2_CORPUS_HOST  (default: 3.111.50.47)"
    echo "  EC2_CORPUS_USER  (default: ubuntu)"
    echo "  EC2_CORPUS_KEY   (default: ~/.ssh/mumbai.pem)"
    echo "  ANTHROPIC_API_KEY — must be set on EC2 (export before run)"
    ;;
esac
