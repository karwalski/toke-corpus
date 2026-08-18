#!/bin/bash
set -euo pipefail

# run_training.sh — Run model training on the training EC2 instance.
#
# Merges all corpus files, runs tokenizer preparation, and launches
# fine-tuning with configurable hyperparameters. Saves checkpoints
# to a timestamped directory.
#
# Idempotent: safe to run multiple times (creates new timestamped runs).
#
# Usage:
#   ./run_training.sh                             # defaults (CUDA QLoRA)
#   ./run_training.sh --config 7b_mlx.yaml        # custom config
#   ./run_training.sh --epochs 5 --lr 2e-5        # override hyperparams
#   ./run_training.sh --skip-merge                 # skip corpus merge step
#   ./run_training.sh --dry-run                    # print config only
#
# Required environment variables:
#   TRAINING_EC2_HOST — hostname or IP of the training EC2 instance
#   SSH_KEY           — path to the SSH private key
#   SSH_USER          — SSH username (default: ubuntu)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

info()  { printf "\033[1;34m[INFO]\033[0m  %s\n" "$1"; }
ok()    { printf "\033[1;32m[OK]\033[0m    %s\n" "$1"; }
fail()  { printf "\033[1;31m[FAIL]\033[0m  %s\n" "$1"; exit 1; }
warn()  { printf "\033[1;33m[WARN]\033[0m  %s\n" "$1"; }

# --------------------------------------------------------------------------
# Configuration defaults
# --------------------------------------------------------------------------

SSH_USER="${SSH_USER:-ubuntu}"
TRAINING_EC2_HOST="${TRAINING_EC2_HOST:?Error: TRAINING_EC2_HOST is not set}"
SSH_KEY="${SSH_KEY:?Error: SSH_KEY is not set}"

REMOTE="${SSH_USER}@${TRAINING_EC2_HOST}"
REMOTE_MODEL_DIR="/opt/toke-model"
REMOTE_CORPUS_DIR="/opt/toke-corpus-data"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

# Training defaults (overridable via CLI).
TRAINING_CONFIG="7b.yaml"
EPOCHS=""
LEARNING_RATE=""
BATCH_SIZE=""
SKIP_MERGE=false
DRY_RUN=false

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            TRAINING_CONFIG="$2"
            shift 2
            ;;
        --epochs)
            EPOCHS="$2"
            shift 2
            ;;
        --lr)
            LEARNING_RATE="$2"
            shift 2
            ;;
        --batch-size)
            BATCH_SIZE="$2"
            shift 2
            ;;
        --skip-merge)
            SKIP_MERGE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--config FILE] [--epochs N] [--lr RATE] [--batch-size N] [--skip-merge] [--dry-run]"
            echo ""
            echo "Options:"
            echo "  --config FILE     Training config in finetune/configs/ (default: 7b.yaml)"
            echo "  --epochs N        Override number of training epochs"
            echo "  --lr RATE         Override learning rate (e.g. 2e-5)"
            echo "  --batch-size N    Override batch size"
            echo "  --skip-merge      Skip corpus merge step (use existing merged data)"
            echo "  --dry-run         Print configuration without running"
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
done

# --------------------------------------------------------------------------
# Timestamp for this run
# --------------------------------------------------------------------------

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
CHECKPOINT_DIR="${REMOTE_MODEL_DIR}/checkpoints/run_${TIMESTAMP}"
LOG_FILE="training_${TIMESTAMP}.log"

# --------------------------------------------------------------------------
# Print configuration
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Model Training — Configuration"
echo "======================================================================"
echo ""
echo "  Remote host      : ${REMOTE}"
echo "  Model dir        : ${REMOTE_MODEL_DIR}"
echo "  Corpus data dir  : ${REMOTE_CORPUS_DIR}"
echo "  Training config  : finetune/configs/${TRAINING_CONFIG}"
echo "  Checkpoint dir   : ${CHECKPOINT_DIR}"
echo "  Log file         : ${CHECKPOINT_DIR}/${LOG_FILE}"
echo "  Timestamp        : ${TIMESTAMP}"
if [ -n "${EPOCHS}" ]; then
    echo "  Epochs override  : ${EPOCHS}"
fi
if [ -n "${LEARNING_RATE}" ]; then
    echo "  LR override      : ${LEARNING_RATE}"
fi
if [ -n "${BATCH_SIZE}" ]; then
    echo "  Batch override   : ${BATCH_SIZE}"
fi
echo "  Skip merge       : ${SKIP_MERGE}"
echo ""
echo "======================================================================"
echo ""

if [ "${DRY_RUN}" = true ]; then
    info "Dry run — exiting without running."
    exit 0
fi

# --------------------------------------------------------------------------
# Validate connectivity
# --------------------------------------------------------------------------

info "Verifying remote instance is ready"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail

if [ ! -d "${REMOTE_MODEL_DIR}" ]; then
    echo "ERROR: ${REMOTE_MODEL_DIR} not found. Run deploy_training.sh first."
    exit 1
fi

if [ ! -d "${REMOTE_MODEL_DIR}/.venv" ]; then
    echo "ERROR: Python venv not found. Run deploy_training.sh first."
    exit 1
fi

echo "Pre-checks passed."
SSHEOF

ok "Remote instance ready"

# --------------------------------------------------------------------------
# Build the training script on the remote instance
# --------------------------------------------------------------------------

info "Preparing training run on ${REMOTE}"

# Build override flags for the training command.
TRAIN_OVERRIDES=""
if [ -n "${EPOCHS}" ]; then
    TRAIN_OVERRIDES="${TRAIN_OVERRIDES} --epochs ${EPOCHS}"
fi
if [ -n "${LEARNING_RATE}" ]; then
    TRAIN_OVERRIDES="${TRAIN_OVERRIDES} --lr ${LEARNING_RATE}"
fi
if [ -n "${BATCH_SIZE}" ]; then
    TRAIN_OVERRIDES="${TRAIN_OVERRIDES} --batch-size ${BATCH_SIZE}"
fi

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail

mkdir -p "${CHECKPOINT_DIR}"

cat > "${REMOTE_MODEL_DIR}/run_training.sh" <<'INNEREOF'
#!/bin/bash
set -euo pipefail

cd "${REMOTE_MODEL_DIR}"
source .venv/bin/activate

TIMESTAMP="${TIMESTAMP}"
CHECKPOINT_DIR="${CHECKPOINT_DIR}"
CORPUS_DIR="${REMOTE_CORPUS_DIR}"
TRAINING_DATA_DIR="${REMOTE_MODEL_DIR}/training-data-p2"
LOG="${CHECKPOINT_DIR}/training_${TIMESTAMP}.log"

echo "=== Training run started at \$(date) ===" | tee "\${LOG}"

# ------------------------------------------------------------------
# Step 1: Merge corpus files
# ------------------------------------------------------------------

if [ "${SKIP_MERGE}" != "true" ]; then
    echo "" | tee -a "\${LOG}"
    echo "--- Step 1: Merging corpus files ---" | tee -a "\${LOG}"

    MERGED="\${CORPUS_DIR}/corpus_merged.jsonl"

    # Start fresh
    > "\${MERGED}"

    # Merge all available corpus sources
    for src in \
        "\${CORPUS_DIR}/corpus_default.jsonl" \
        "\${CORPUS_DIR}/mutations.jsonl" \
        "\${CORPUS_DIR}/error_triples.jsonl" \
        "\${CORPUS_DIR}/fuzzed.jsonl" \
        "\${CORPUS_DIR}/transpiled.jsonl" \
        "\${CORPUS_DIR}/parallel_corpus.jsonl" \
        "\${CORPUS_DIR}/negative_examples.jsonl"; do
        if [ -f "\${src}" ]; then
            echo "  Merging: \${src} (\$(wc -l < "\${src}") lines)" | tee -a "\${LOG}"
            cat "\${src}" >> "\${MERGED}"
        else
            echo "  Skipping (not found): \${src}" | tee -a "\${LOG}"
        fi
    done

    TOTAL_LINES=\$(wc -l < "\${MERGED}")
    echo "  Merged corpus: \${TOTAL_LINES} examples" | tee -a "\${LOG}"
else
    echo "--- Step 1: Skipping merge (--skip-merge) ---" | tee -a "\${LOG}"
fi

# ------------------------------------------------------------------
# Step 2: Prepare training data (tokenize + split)
# ------------------------------------------------------------------

echo "" | tee -a "\${LOG}"
echo "--- Step 2: Preparing training data ---" | tee -a "\${LOG}"

mkdir -p "\${TRAINING_DATA_DIR}"

CORPUS_INPUT="\${CORPUS_DIR}/corpus_merged.jsonl"
if [ ! -f "\${CORPUS_INPUT}" ]; then
    CORPUS_INPUT="\${CORPUS_DIR}/corpus_default.jsonl"
fi

if [ -f "\${CORPUS_INPUT}" ]; then
    python3 finetune/prepare_data.py \
        --corpus "\${CORPUS_INPUT}" \
        --output-dir "\${TRAINING_DATA_DIR}" \
        2>&1 | tee -a "\${LOG}"
    echo "  Training data prepared in \${TRAINING_DATA_DIR}" | tee -a "\${LOG}"
else
    echo "ERROR: No corpus file found to prepare." | tee -a "\${LOG}"
    exit 1
fi

# ------------------------------------------------------------------
# Step 3: Launch training
# ------------------------------------------------------------------

echo "" | tee -a "\${LOG}"
echo "--- Step 3: Launching training ---" | tee -a "\${LOG}"
echo "  Config: finetune/configs/${TRAINING_CONFIG}" | tee -a "\${LOG}"
echo "  Overrides: ${TRAIN_OVERRIDES}" | tee -a "\${LOG}"
echo "  Checkpoint dir: \${CHECKPOINT_DIR}" | tee -a "\${LOG}"

# Determine training backend based on config name.
if echo "${TRAINING_CONFIG}" | grep -q "mlx"; then
    echo "  Backend: MLX (Apple Silicon)" | tee -a "\${LOG}"
    python3 finetune/train_mlx.py \
        --config "finetune/configs/${TRAINING_CONFIG}" \
        ${TRAIN_OVERRIDES} \
        2>&1 | tee -a "\${LOG}"
else
    echo "  Backend: CUDA (QLoRA)" | tee -a "\${LOG}"
    python3 finetune/train_qlora.py \
        --config "finetune/configs/${TRAINING_CONFIG}" \
        ${TRAIN_OVERRIDES} \
        2>&1 | tee -a "\${LOG}"
fi

# ------------------------------------------------------------------
# Step 4: Copy final adapter to checkpoint directory
# ------------------------------------------------------------------

echo "" | tee -a "\${LOG}"
echo "--- Step 4: Saving checkpoint ---" | tee -a "\${LOG}"

if [ -d "${REMOTE_MODEL_DIR}/output" ]; then
    cp -r "${REMOTE_MODEL_DIR}/output/"* "\${CHECKPOINT_DIR}/" 2>/dev/null || true
    echo "  Checkpoint saved to \${CHECKPOINT_DIR}" | tee -a "\${LOG}"
fi

echo "" | tee -a "\${LOG}"
echo "=== Training run finished at \$(date) ===" | tee -a "\${LOG}"
INNEREOF

chmod +x "${REMOTE_MODEL_DIR}/run_training.sh"
SSHEOF

ok "Training script prepared"

# --------------------------------------------------------------------------
# Launch training in tmux
# --------------------------------------------------------------------------

info "Launching training in tmux session 'training'"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail

# Kill existing session if present.
tmux kill-session -t training 2>/dev/null || true

# Start training in tmux.
tmux new-session -d -s training "${REMOTE_MODEL_DIR}/run_training.sh"

echo "tmux session 'training' started."
SSHEOF

ok "Training launched in tmux session 'training'"

# --------------------------------------------------------------------------
# Monitoring instructions
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Training Running"
echo "======================================================================"
echo ""
echo "  The training pipeline is running in a tmux session."
echo ""
echo "  Monitor progress:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'tmux attach -t training'"
echo ""
echo "  View live log:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'tail -f ${CHECKPOINT_DIR}/${LOG_FILE}'"
echo ""
echo "  Check GPU usage:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'nvidia-smi'"
echo ""
echo "  Stop training:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'tmux kill-session -t training'"
echo ""
echo "  Download checkpoint:"
echo "    rsync -avz -e \"ssh ${SSH_OPTS}\" ${REMOTE}:${CHECKPOINT_DIR}/ ./checkpoints/run_${TIMESTAMP}/"
echo "======================================================================"
