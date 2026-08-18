#!/bin/bash
set -euo pipefail

# run_gate2_pipeline.sh — Gate 2 training pipeline for EC2 deployment.
#
# Orchestrates the full Gate 2 pipeline:
#   1. Tokenizer retrain on combined Phase A + Phase B corpus
#   2. Training data preparation (corpus -> instruction-tuning format)
#   3. QLoRA fine-tuning on CUDA (Qwen 2.5 Coder 7B)
#   4. Pass@1 evaluation against Gate 2 targets
#
# Gate 2 targets:
#   - Pass@1 >= 75%
#   - Token reduction >= 15%
#   - Compile rate >= 95%
#
# All paths are parameterizable via environment variables.
# Designed to run directly on the EC2 training instance (not via SSH).
#
# Usage:
#   ./run_gate2_pipeline.sh                        # full pipeline
#   ./run_gate2_pipeline.sh --skip-tokenizer       # skip tokenizer retrain
#   ./run_gate2_pipeline.sh --skip-training        # skip fine-tuning
#   ./run_gate2_pipeline.sh --eval-only            # run evaluation only
#   ./run_gate2_pipeline.sh --dry-run              # print config, no execution

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

info()  { printf "\033[1;34m[INFO]\033[0m  %s\n" "$1"; }
ok()    { printf "\033[1;32m[OK]\033[0m    %s\n" "$1"; }
fail()  { printf "\033[1;31m[FAIL]\033[0m  %s\n" "$1"; exit 1; }
warn()  { printf "\033[1;33m[WARN]\033[0m  %s\n" "$1"; }

# --------------------------------------------------------------------------
# Environment variable defaults (EC2 paths)
# --------------------------------------------------------------------------

# Corpus directories
CORPUS_DIR="${CORPUS_DIR:-/opt/toke-corpus/corpus}"
CORPUS_DATA_DIR="${CORPUS_DATA_DIR:-/opt/toke-corpus/data}"
PHASE_A_DIR="${PHASE_A_DIR:-${CORPUS_DIR}/phase_a}"
PHASE_B_DIR="${PHASE_B_DIR:-${CORPUS_DIR}/phase_b}"

# Model and tokenizer directories
MODEL_DIR="${MODEL_DIR:-/opt/toke-model}"
TOKENIZER_DIR="${TOKENIZER_DIR:-/opt/toke-tokenizer}"
EVAL_DIR="${EVAL_DIR:-/opt/toke-eval}"

# Compiler
TKC_BIN="${TKC_BIN:-/usr/local/bin/tkc}"

# Benchmark directory for Pass@1 evaluation
BENCHMARK_DIR="${BENCHMARK_DIR:-${EVAL_DIR}/benchmark/hidden_tests}"

# Training config
TRAINING_CONFIG="${TRAINING_CONFIG:-${MODEL_DIR}/finetune/configs/7b.yaml}"

# Output directories (timestamped per run)
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RUN_DIR="${RUN_DIR:-${MODEL_DIR}/runs/gate2_${TIMESTAMP}}"
TOKENIZER_OUTPUT_DIR="${RUN_DIR}/tokenizer"
TRAINING_DATA_DIR="${RUN_DIR}/training-data"
TRAINING_OUTPUT_DIR="${RUN_DIR}/model-output"
EVAL_OUTPUT_DIR="${RUN_DIR}/eval"
LOG_FILE="${RUN_DIR}/gate2_pipeline.log"

# Python virtual environment
VENV_DIR="${VENV_DIR:-${MODEL_DIR}/.venv}"

# Gate 2 thresholds
GATE2_PASS_AT_1="${GATE2_PASS_AT_1:-0.75}"
GATE2_TOKEN_REDUCTION="${GATE2_TOKEN_REDUCTION:-0.15}"
GATE2_COMPILE_RATE="${GATE2_COMPILE_RATE:-0.95}"

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

SKIP_TOKENIZER=false
SKIP_TRAINING=false
EVAL_ONLY=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-tokenizer)
            SKIP_TOKENIZER=true
            shift
            ;;
        --skip-training)
            SKIP_TRAINING=true
            shift
            ;;
        --eval-only)
            EVAL_ONLY=true
            SKIP_TOKENIZER=true
            SKIP_TRAINING=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --run-dir)
            RUN_DIR="$2"
            TOKENIZER_OUTPUT_DIR="${RUN_DIR}/tokenizer"
            TRAINING_DATA_DIR="${RUN_DIR}/training-data"
            TRAINING_OUTPUT_DIR="${RUN_DIR}/model-output"
            EVAL_OUTPUT_DIR="${RUN_DIR}/eval"
            LOG_FILE="${RUN_DIR}/gate2_pipeline.log"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-tokenizer    Skip tokenizer retraining (use existing)"
            echo "  --skip-training     Skip model fine-tuning (use existing adapter)"
            echo "  --eval-only         Only run Pass@1 evaluation"
            echo "  --run-dir DIR       Override the timestamped run directory"
            echo "  --dry-run           Print configuration without executing"
            echo ""
            echo "Environment variables:"
            echo "  CORPUS_DIR          Corpus root (default: /opt/toke-corpus/corpus)"
            echo "  CORPUS_DATA_DIR     Corpus JSONL data (default: /opt/toke-corpus/data)"
            echo "  PHASE_A_DIR         Phase A corpus (default: \$CORPUS_DIR/phase_a)"
            echo "  PHASE_B_DIR         Phase B corpus (default: \$CORPUS_DIR/phase_b)"
            echo "  MODEL_DIR           toke-model root (default: /opt/toke-model)"
            echo "  TOKENIZER_DIR       toke-tokenizer root (default: /opt/toke-tokenizer)"
            echo "  EVAL_DIR            toke-eval root (default: /opt/toke-eval)"
            echo "  TKC_BIN             Path to tkc compiler (default: /usr/local/bin/tkc)"
            echo "  BENCHMARK_DIR       Benchmark tasks dir (default: \$EVAL_DIR/benchmark/hidden_tests)"
            echo "  TRAINING_CONFIG     YAML config for training (default: \$MODEL_DIR/finetune/configs/7b.yaml)"
            echo "  VENV_DIR            Python venv (default: \$MODEL_DIR/.venv)"
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
done

# --------------------------------------------------------------------------
# Print configuration
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Gate 2 Training Pipeline"
echo "======================================================================"
echo ""
echo "  Timestamp          : ${TIMESTAMP}"
echo "  Run directory      : ${RUN_DIR}"
echo ""
echo "  Corpus root        : ${CORPUS_DIR}"
echo "  Corpus data        : ${CORPUS_DATA_DIR}"
echo "  Phase A dir        : ${PHASE_A_DIR}"
echo "  Phase B dir        : ${PHASE_B_DIR}"
echo ""
echo "  Model dir          : ${MODEL_DIR}"
echo "  Tokenizer dir      : ${TOKENIZER_DIR}"
echo "  Eval dir           : ${EVAL_DIR}"
echo "  Compiler           : ${TKC_BIN}"
echo "  Benchmark dir      : ${BENCHMARK_DIR}"
echo "  Training config    : ${TRAINING_CONFIG}"
echo "  Python venv        : ${VENV_DIR}"
echo ""
echo "  Gate 2 targets:"
echo "    Pass@1           >= ${GATE2_PASS_AT_1}"
echo "    Token reduction  >= ${GATE2_TOKEN_REDUCTION}"
echo "    Compile rate     >= ${GATE2_COMPILE_RATE}"
echo ""
echo "  Pipeline steps:"
echo "    Tokenizer retrain: $([ "${SKIP_TOKENIZER}" = true ] && echo "SKIP" || echo "RUN")"
echo "    Data preparation : $([ "${EVAL_ONLY}" = true ] && echo "SKIP" || echo "RUN")"
echo "    Model fine-tuning: $([ "${SKIP_TRAINING}" = true ] && echo "SKIP" || echo "RUN")"
echo "    Pass@1 evaluation: RUN"
echo ""
echo "======================================================================"
echo ""

if [ "${DRY_RUN}" = true ]; then
    info "Dry run -- exiting without running."
    exit 0
fi

# --------------------------------------------------------------------------
# Create run directory and start logging
# --------------------------------------------------------------------------

mkdir -p "${RUN_DIR}" "${TOKENIZER_OUTPUT_DIR}" "${TRAINING_DATA_DIR}" \
         "${TRAINING_OUTPUT_DIR}" "${EVAL_OUTPUT_DIR}"

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== Gate 2 Pipeline started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# --------------------------------------------------------------------------
# Activate Python environment
# --------------------------------------------------------------------------

if [ -d "${VENV_DIR}" ]; then
    info "Activating Python venv: ${VENV_DIR}"
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
else
    warn "No venv at ${VENV_DIR}, using system Python"
fi

# --------------------------------------------------------------------------
# Pre-flight checks
# --------------------------------------------------------------------------

info "Running pre-flight checks"

# Check CUDA availability
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || true
    ok "GPU detected"
else
    warn "nvidia-smi not found -- training may fail without GPU"
fi

# Check compiler
if [ -x "${TKC_BIN}" ]; then
    TKC_VERSION=$("${TKC_BIN}" --version 2>&1 || echo "unknown")
    ok "Compiler: ${TKC_BIN} (${TKC_VERSION})"
else
    warn "Compiler not found at ${TKC_BIN} -- evaluation will fail"
fi

# Check PyTorch CUDA
python3 -c "
import torch
print(f'  PyTorch {torch.__version__}, CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'  CUDA device: {torch.cuda.get_device_name(0)}')
" 2>/dev/null || warn "PyTorch CUDA check failed"

echo ""

# ==========================================================================
# STEP 1: Merge Phase A + Phase B corpus and retrain tokenizer
# ==========================================================================

if [ "${SKIP_TOKENIZER}" = false ]; then
    echo "======================================================================"
    echo "  Step 1: Tokenizer Retrain (SentencePiece BPE, 8K vocab)"
    echo "======================================================================"
    echo ""

    MERGED_CORPUS="${RUN_DIR}/corpus_merged.jsonl"

    # ---- 1a. Merge Phase A and Phase B corpus into a single JSONL ----

    info "Merging Phase A + Phase B corpus"

    > "${MERGED_CORPUS}"

    # Phase A: look for existing JSONL data files
    for src in \
        "${CORPUS_DATA_DIR}/corpus_default.jsonl" \
        "${CORPUS_DATA_DIR}/mutations.jsonl" \
        "${CORPUS_DATA_DIR}/error_triples.jsonl" \
        "${CORPUS_DATA_DIR}/fuzzed.jsonl" \
        "${CORPUS_DATA_DIR}/transpiled.jsonl" \
        "${CORPUS_DATA_DIR}/parallel_corpus.jsonl" \
        "${CORPUS_DATA_DIR}/negative_examples.jsonl"; do
        if [ -f "${src}" ]; then
            LINE_COUNT=$(wc -l < "${src}" | tr -d ' ')
            info "  Phase A: ${src} (${LINE_COUNT} lines)"
            cat "${src}" >> "${MERGED_CORPUS}"
        fi
    done

    # Phase B: convert individual JSON files into JSONL and append
    if [ -d "${PHASE_B_DIR}" ]; then
        PHASE_B_COUNT=0
        while IFS= read -r json_file; do
            # Each Phase B JSON file contains a single corpus entry;
            # extract tk_source and wrap it as a JSONL line
            python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    obj = json.load(f)
# Normalise: the entry itself is the corpus record
if 'tk_source' in obj or 'toke_source' in obj or 'source' in obj:
    print(json.dumps(obj, ensure_ascii=False))
" "${json_file}" >> "${MERGED_CORPUS}" 2>/dev/null && PHASE_B_COUNT=$((PHASE_B_COUNT + 1))
        done < <(find "${PHASE_B_DIR}" -name '*.json' -type f)
        info "  Phase B: ${PHASE_B_COUNT} files merged from ${PHASE_B_DIR}"
    else
        warn "  Phase B directory not found: ${PHASE_B_DIR}"
    fi

    TOTAL_LINES=$(wc -l < "${MERGED_CORPUS}" | tr -d ' ')
    ok "Merged corpus: ${TOTAL_LINES} entries -> ${MERGED_CORPUS}"

    # ---- 1b. Retrain BPE tokenizer on merged corpus ----

    info "Retraining SentencePiece BPE tokenizer"

    RETRAIN_SCRIPT="${TOKENIZER_DIR}/scripts/retrain_bpe.py"
    OLD_MODEL="${TOKENIZER_DIR}/models/toke.model"

    if [ ! -f "${RETRAIN_SCRIPT}" ]; then
        fail "Tokenizer retrain script not found: ${RETRAIN_SCRIPT}"
    fi

    python3 "${RETRAIN_SCRIPT}" \
        --corpus-jsonl "${MERGED_CORPUS}" \
        --old-model "${OLD_MODEL}" \
        --output-dir "${TOKENIZER_OUTPUT_DIR}"

    # Copy the retrained model for use in subsequent steps
    NEW_MODEL="${TOKENIZER_OUTPUT_DIR}/models/toke_default_8k.model"
    if [ -f "${NEW_MODEL}" ]; then
        cp "${NEW_MODEL}" "${RUN_DIR}/toke.model"
        ok "Retrained tokenizer saved to ${RUN_DIR}/toke.model"
    else
        warn "Retrained tokenizer model not found at expected path"
    fi

    echo ""
else
    info "Step 1: SKIPPED (--skip-tokenizer)"
    MERGED_CORPUS="${CORPUS_DATA_DIR}/corpus_default.jsonl"
    echo ""
fi

# ==========================================================================
# STEP 2: Prepare training data (instruction-tuning format)
# ==========================================================================

if [ "${EVAL_ONLY}" = false ]; then
    echo "======================================================================"
    echo "  Step 2: Training Data Preparation"
    echo "======================================================================"
    echo ""

    PREPARE_SCRIPT="${MODEL_DIR}/finetune/prepare_data.py"

    if [ ! -f "${PREPARE_SCRIPT}" ]; then
        fail "Data preparation script not found: ${PREPARE_SCRIPT}"
    fi

    # Use the merged corpus from Step 1, or the default corpus
    CORPUS_INPUT="${MERGED_CORPUS:-${CORPUS_DATA_DIR}/corpus_default.jsonl}"
    if [ ! -f "${CORPUS_INPUT}" ]; then
        CORPUS_INPUT="${CORPUS_DATA_DIR}/corpus_default.jsonl"
    fi

    if [ ! -f "${CORPUS_INPUT}" ]; then
        fail "No corpus file found for data preparation"
    fi

    info "Preparing training data from ${CORPUS_INPUT}"

    python3 "${PREPARE_SCRIPT}" \
        --corpus "${CORPUS_INPUT}" \
        --output-dir "${TRAINING_DATA_DIR}" \
        --split 0.95

    TRAIN_COUNT=$(wc -l < "${TRAINING_DATA_DIR}/train.jsonl" | tr -d ' ')
    EVAL_COUNT=$(wc -l < "${TRAINING_DATA_DIR}/eval.jsonl" | tr -d ' ')
    ok "Training data: ${TRAIN_COUNT} train, ${EVAL_COUNT} eval"
    echo ""
else
    info "Step 2: SKIPPED (--eval-only)"
    echo ""
fi

# ==========================================================================
# STEP 3: QLoRA fine-tuning on CUDA
# ==========================================================================

if [ "${SKIP_TRAINING}" = false ]; then
    echo "======================================================================"
    echo "  Step 3: QLoRA Fine-tuning (CUDA)"
    echo "======================================================================"
    echo ""

    TRAIN_SCRIPT="${MODEL_DIR}/finetune/train_qlora.py"

    if [ ! -f "${TRAIN_SCRIPT}" ]; then
        fail "Training script not found: ${TRAIN_SCRIPT}"
    fi

    if [ ! -f "${TRAINING_CONFIG}" ]; then
        fail "Training config not found: ${TRAINING_CONFIG}"
    fi

    # Create a run-specific config that points to our prepared data
    GATE2_CONFIG="${RUN_DIR}/gate2_training_config.yaml"
    python3 -c "
import yaml, sys

with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f)

# Override data paths to use this run's prepared training data
cfg['data']['train_file'] = sys.argv[2] + '/train.jsonl'
cfg['data']['eval_file'] = sys.argv[2] + '/eval.jsonl'

# Override output to this run's output directory
cfg['output']['dir'] = sys.argv[3]
cfg['output']['adapter_dir'] = sys.argv[3] + '/adapter'

with open(sys.argv[4], 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False)
print(f'Config written to {sys.argv[4]}')
" "${TRAINING_CONFIG}" "${TRAINING_DATA_DIR}" "${TRAINING_OUTPUT_DIR}" "${GATE2_CONFIG}"

    info "Starting QLoRA training"
    info "  Config: ${GATE2_CONFIG}"
    info "  Output: ${TRAINING_OUTPUT_DIR}"

    python3 "${TRAIN_SCRIPT}" \
        --config "${GATE2_CONFIG}"

    ok "Training complete"

    # Record adapter location for evaluation
    ADAPTER_DIR="${TRAINING_OUTPUT_DIR}/adapter"
    if [ -d "${ADAPTER_DIR}" ]; then
        ok "Adapter saved to ${ADAPTER_DIR}"
    else
        warn "Adapter directory not found at ${ADAPTER_DIR}"
    fi
    echo ""
else
    info "Step 3: SKIPPED (--skip-training)"
    # Try to find an existing adapter for evaluation
    ADAPTER_DIR="${TRAINING_OUTPUT_DIR}/adapter"
    if [ ! -d "${ADAPTER_DIR}" ]; then
        ADAPTER_DIR="${MODEL_DIR}/output/7b-qlora/adapter"
    fi
    echo ""
fi

# ==========================================================================
# STEP 4: Pass@1 Evaluation
# ==========================================================================

echo "======================================================================"
echo "  Step 4: Pass@1 Evaluation"
echo "======================================================================"
echo ""

EVAL_SCRIPT="${EVAL_DIR}/scripts/pass_at_k.py"

if [ ! -f "${EVAL_SCRIPT}" ]; then
    fail "Pass@1 evaluation script not found: ${EVAL_SCRIPT}"
fi

# Predictions directory: if we just trained, generate predictions first.
# If predictions already exist, use them directly.
PREDICTIONS_DIR="${EVAL_OUTPUT_DIR}/predictions"
mkdir -p "${PREDICTIONS_DIR}"

# Generate predictions from the fine-tuned model if an adapter exists
GENERATE_SCRIPT="${EVAL_DIR}/scripts/generate_predictions.py"
if [ -f "${GENERATE_SCRIPT}" ] && [ -d "${ADAPTER_DIR:-}" ]; then
    info "Generating predictions from fine-tuned model"
    python3 "${GENERATE_SCRIPT}" \
        --adapter-dir "${ADAPTER_DIR}" \
        --benchmark-dir "${BENCHMARK_DIR}" \
        --output-dir "${PREDICTIONS_DIR}" \
        --samples-per-task 20 \
        --temperatures "0.0,0.2" \
        2>&1 || warn "Prediction generation failed -- falling back to dry-run eval"
fi

# Count prediction files
PRED_COUNT=$(find "${PREDICTIONS_DIR}" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')

if [ "${PRED_COUNT}" -gt 0 ]; then
    info "Running Pass@1 evaluation on ${PRED_COUNT} prediction file(s)"

    python3 "${EVAL_SCRIPT}" \
        --predictions-dir "${PREDICTIONS_DIR}" \
        --benchmark-dir "${BENCHMARK_DIR}" \
        --output-dir "${EVAL_OUTPUT_DIR}" \
        --compiler "${TKC_BIN}" \
        --k-values "1" \
        --temperatures "0.0,0.2" \
        --samples-per-task 20

    ok "Pass@1 evaluation complete"
else
    warn "No prediction files found. Running dry-run evaluation for pipeline validation."

    python3 "${EVAL_SCRIPT}" \
        --benchmark-dir "${BENCHMARK_DIR}" \
        --output-dir "${EVAL_OUTPUT_DIR}" \
        --k-values "1" \
        --temperatures "0.0,0.2" \
        --samples-per-task 20 \
        --dry-run --seed 42

    warn "Dry-run results only -- real predictions required for Gate 2 assessment"
fi

echo ""

# ==========================================================================
# GATE 2 ASSESSMENT
# ==========================================================================

echo "======================================================================"
echo "  Gate 2 Assessment"
echo "======================================================================"
echo ""

RESULTS_JSON="${EVAL_OUTPUT_DIR}/pass_at_k_results.json"

if [ -f "${RESULTS_JSON}" ]; then
    python3 -c "
import json, sys

with open(sys.argv[1]) as f:
    report = json.load(f)

gate2_pass1 = float(sys.argv[2])
gate2_token = float(sys.argv[3])
gate2_compile = float(sys.argv[4])

print('Gate 2 Targets vs Results:')
print('=' * 50)

# Extract best Pass@1 across temperatures
best_pass1 = 0.0
for agg in report.get('aggregates', []):
    p1 = agg.get('mean_pass_at_k', {}).get('1', 0.0)
    if p1 > best_pass1:
        best_pass1 = p1

pass1_status = 'PASS' if best_pass1 >= gate2_pass1 else 'FAIL'
print(f'  Pass@1:          {best_pass1:.4f}  (target >= {gate2_pass1})  [{pass1_status}]')

# Token reduction and compile rate need separate measurement
# (not part of pass_at_k output -- logged as placeholders)
print(f'  Token reduction: (measure separately via tokenizer eval)')
print(f'  Compile rate:    (measure separately via compile-check sweep)')
print()

mode = report.get('mode', 'unknown')
if mode == 'dry-run':
    print('  NOTE: Results are from dry-run (synthetic). Real evaluation pending.')
    print()

# Per-temperature breakdown
for agg in report.get('aggregates', []):
    temp = agg.get('temperature', 0.0)
    n = agg.get('n_tasks', 0)
    p1 = agg.get('mean_pass_at_k', {}).get('1', 0.0)
    print(f'  T={temp:.1f}  tasks={n}  Pass@1={p1:.4f}')

print()
print('=' * 50)

overall = 'PENDING' if mode == 'dry-run' else (pass1_status)
print(f'  Gate 2 overall: {overall}')
" "${RESULTS_JSON}" "${GATE2_PASS_AT_1}" "${GATE2_TOKEN_REDUCTION}" "${GATE2_COMPILE_RATE}"
else
    warn "No evaluation results found at ${RESULTS_JSON}"
fi

echo ""

# ==========================================================================
# Summary
# ==========================================================================

echo "======================================================================"
echo "  Pipeline Complete"
echo "======================================================================"
echo ""
echo "  Run directory    : ${RUN_DIR}"
echo "  Log file         : ${LOG_FILE}"
echo "  Tokenizer output : ${TOKENIZER_OUTPUT_DIR}"
echo "  Training data    : ${TRAINING_DATA_DIR}"
echo "  Model output     : ${TRAINING_OUTPUT_DIR}"
echo "  Eval output      : ${EVAL_OUTPUT_DIR}"
echo ""
echo "  Artifacts:"

[ -f "${RUN_DIR}/toke.model" ] && \
    echo "    Tokenizer model : ${RUN_DIR}/toke.model"
[ -d "${ADAPTER_DIR:-}" ] && \
    echo "    LoRA adapter    : ${ADAPTER_DIR}"
[ -f "${RESULTS_JSON}" ] && \
    echo "    Eval results    : ${RESULTS_JSON}"
[ -f "${EVAL_OUTPUT_DIR}/pass_at_k_summary.csv" ] && \
    echo "    Eval CSV        : ${EVAL_OUTPUT_DIR}/pass_at_k_summary.csv"

echo ""
echo "=== Gate 2 Pipeline finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
