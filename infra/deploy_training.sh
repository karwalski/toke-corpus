#!/bin/bash
set -euo pipefail

# deploy_training.sh — Deploy the toke training pipeline and corpus data to EC2.
#
# Rsyncs toke-model, generated corpus data, and installs training dependencies.
# The training instance should have a GPU (CUDA) for QLoRA training.
#
# Idempotent: safe to run multiple times.
#
# Usage:
#   ./deploy_training.sh
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
# Configuration
# --------------------------------------------------------------------------

SSH_USER="${SSH_USER:-ubuntu}"
TRAINING_EC2_HOST="${TRAINING_EC2_HOST:?Error: TRAINING_EC2_HOST is not set}"
SSH_KEY="${SSH_KEY:?Error: SSH_KEY is not set}"

REMOTE="${SSH_USER}@${TRAINING_EC2_HOST}"
REMOTE_MODEL_DIR="/opt/toke-model"
REMOTE_CORPUS_DIR="/opt/toke-corpus-data"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

# Resolve local paths.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORPUS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MODEL_ROOT="${MODEL_ROOT:-${CORPUS_ROOT}/../toke-model}"

# Local corpus data directory.
CORPUS_DATA_DIR="${CORPUS_DATA_DIR:-${CORPUS_ROOT}/data}"

info "Local model root  : ${MODEL_ROOT}"
info "Local corpus data : ${CORPUS_DATA_DIR}"
info "Remote target     : ${REMOTE}"
info "SSH key           : ${SSH_KEY}"

# --------------------------------------------------------------------------
# Validate inputs
# --------------------------------------------------------------------------

if [ ! -d "${MODEL_ROOT}" ]; then
    fail "toke-model directory not found at ${MODEL_ROOT}. Set MODEL_ROOT."
fi

if [ ! -d "${CORPUS_DATA_DIR}" ]; then
    warn "Corpus data directory not found at ${CORPUS_DATA_DIR}."
    warn "Set CORPUS_DATA_DIR or generate corpus first."
fi

# --------------------------------------------------------------------------
# Validate SSH connectivity
# --------------------------------------------------------------------------

info "Testing SSH connectivity"
if ! ssh ${SSH_OPTS} "${REMOTE}" 'echo ok' > /dev/null 2>&1; then
    fail "Cannot connect to ${REMOTE}. Check TRAINING_EC2_HOST, SSH_KEY, and that the instance is running."
fi
ok "SSH connection verified"

# --------------------------------------------------------------------------
# Ensure remote directory structure
# --------------------------------------------------------------------------

info "Creating remote directory structure"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail
sudo mkdir -p "${REMOTE_MODEL_DIR}"/{output,checkpoints}
sudo mkdir -p "${REMOTE_CORPUS_DIR}"
sudo chown -R \$(whoami):\$(whoami) "${REMOTE_MODEL_DIR}" "${REMOTE_CORPUS_DIR}"
SSHEOF

ok "Remote directories ready"

# --------------------------------------------------------------------------
# Rsync toke-model repo
# --------------------------------------------------------------------------

info "Syncing toke-model to ${REMOTE}:${REMOTE_MODEL_DIR}/"

rsync -avz --delete \
    -e "ssh ${SSH_OPTS}" \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='.venv/' \
    --exclude='*.pyc' \
    --exclude='output/' \
    --exclude='checkpoints/' \
    --exclude='results/' \
    "${MODEL_ROOT}/" \
    "${REMOTE}:${REMOTE_MODEL_DIR}/"

ok "toke-model synced"

# --------------------------------------------------------------------------
# Rsync corpus data files
# --------------------------------------------------------------------------

if [ -d "${CORPUS_DATA_DIR}" ]; then
    info "Syncing corpus data to ${REMOTE}:${REMOTE_CORPUS_DIR}/"

    rsync -avz \
        -e "ssh ${SSH_OPTS}" \
        --include='*.jsonl' \
        --include='*.json' \
        --exclude='*' \
        "${CORPUS_DATA_DIR}/" \
        "${REMOTE}:${REMOTE_CORPUS_DIR}/"

    ok "Corpus data synced"
else
    warn "Skipping corpus data sync (directory not found)"
fi

# --------------------------------------------------------------------------
# Install Python dependencies
# --------------------------------------------------------------------------

info "Setting up Python venv and installing training dependencies"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail
cd "${REMOTE_MODEL_DIR}"

# Create venv if it does not exist
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip --quiet

# Install CUDA dependencies for GPU training
pip install -e ".[cuda]" --quiet 2>&1 || {
    echo "WARN: CUDA deps failed. Trying MLX deps instead (Apple Silicon)."
    pip install -e ".[mlx]" --quiet
}

pip install -e ".[dev]" --quiet

echo ""
echo "Installed packages:"
pip list --format=columns | head -25
SSHEOF

ok "Training dependencies installed"

# --------------------------------------------------------------------------
# Pre-flight checklist
# --------------------------------------------------------------------------

info "Running pre-flight checklist on instance"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<'SSHEOF'
set -uo pipefail

echo ""
echo "======================================================================"
echo "  Pre-flight Checklist"
echo "======================================================================"
echo ""

# GPU check
echo "--- GPU ---"
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null \
        && echo "  GPU: Available" \
        || echo "  GPU: nvidia-smi found but query failed"
else
    echo "  GPU: nvidia-smi not found (no NVIDIA GPU or drivers not installed)"
fi
echo ""

# CUDA check
echo "--- CUDA ---"
if command -v nvcc &> /dev/null; then
    nvcc --version 2>&1 | tail -1
else
    echo "  nvcc not found. Check CUDA toolkit installation."
fi
echo ""

# Disk space
echo "--- Disk Space ---"
df -h / | tail -1 | awk '{print "  Total: "$2"  Used: "$3"  Available: "$4"  Use%: "$5}'
echo ""

# Memory
echo "--- Memory ---"
free -h 2>/dev/null | head -2 || echo "  (free command not available)"
echo ""

# Python + torch
echo "--- Python / PyTorch ---"
source /opt/toke-model/.venv/bin/activate 2>/dev/null || true
python3 -c "
import sys
print(f'  Python: {sys.version}')
try:
    import torch
    print(f'  PyTorch: {torch.__version__}')
    print(f'  CUDA available: {torch.cuda.is_available()}')
    if torch.cuda.is_available():
        print(f'  CUDA device: {torch.cuda.get_device_name(0)}')
        print(f'  CUDA version: {torch.version.cuda}')
except ImportError:
    print('  PyTorch: not installed')
" 2>/dev/null || echo "  Python check failed"
echo ""

# Corpus data
echo "--- Corpus Data ---"
CORPUS_DIR="/opt/toke-corpus-data"
if [ -d "${CORPUS_DIR}" ]; then
    FILE_COUNT=$(find "${CORPUS_DIR}" -name '*.jsonl' -o -name '*.json' | wc -l)
    TOTAL_SIZE=$(du -sh "${CORPUS_DIR}" 2>/dev/null | cut -f1)
    echo "  Directory: ${CORPUS_DIR}"
    echo "  Files: ${FILE_COUNT}"
    echo "  Total size: ${TOTAL_SIZE}"
else
    echo "  No corpus data found at ${CORPUS_DIR}"
fi

echo ""
echo "======================================================================"
SSHEOF

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Training Deployment Complete"
echo "======================================================================"
echo ""
echo "  Remote host     : ${REMOTE}"
echo "  Model dir       : ${REMOTE_MODEL_DIR}"
echo "  Corpus data dir : ${REMOTE_CORPUS_DIR}"
echo ""
echo "  Next steps:"
echo "    1. Review the pre-flight checklist above"
echo "    2. Ensure GPU + CUDA are working"
echo "    3. Run training: ./infra/run_training.sh"
echo "======================================================================"
