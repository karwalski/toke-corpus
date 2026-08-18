#!/bin/bash
set -euo pipefail

# deploy_corpus_gen.sh — Deploy the toke corpus generation pipeline to EC2.
#
# Rsyncs the toke-corpus repo, installs Python dependencies in a venv,
# copies the tkc binary, and configures API key environment from a .env file.
#
# Idempotent: safe to run multiple times.
#
# Usage:
#   ./deploy_corpus_gen.sh
#
# Required environment variables (or export before running):
#   CORPUS_EC2_HOST — hostname or IP of the corpus generation EC2 instance
#   SSH_KEY         — path to the SSH private key (e.g. ~/.ssh/mumbai.pem)
#   SSH_USER        — SSH username (default: ubuntu)

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
CORPUS_EC2_HOST="${CORPUS_EC2_HOST:?Error: CORPUS_EC2_HOST is not set}"
SSH_KEY="${SSH_KEY:?Error: SSH_KEY is not set}"

REMOTE="${SSH_USER}@${CORPUS_EC2_HOST}"
REMOTE_DIR="/opt/toke-corpus"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

# Resolve local repo root (this script lives in infra/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Path to the local tkc binary (Linux amd64 build).
TKC_LOCAL="${TKC_LOCAL:-${REPO_ROOT}/../tkc/tkc}"
# Path to the local .env file with API keys.
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

info "Local repo root : ${REPO_ROOT}"
info "Remote target   : ${REMOTE}:${REMOTE_DIR}/"
info "SSH key         : ${SSH_KEY}"

# --------------------------------------------------------------------------
# Validate SSH connectivity
# --------------------------------------------------------------------------

info "Testing SSH connectivity"
if ! ssh ${SSH_OPTS} "${REMOTE}" 'echo ok' > /dev/null 2>&1; then
    fail "Cannot connect to ${REMOTE}. Check CORPUS_EC2_HOST, SSH_KEY, and that the instance is running."
fi
ok "SSH connection verified"

# --------------------------------------------------------------------------
# Ensure remote directory structure exists
# --------------------------------------------------------------------------

info "Creating remote directory structure"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail
sudo mkdir -p "${REMOTE_DIR}"/{corpus,logs,metrics,bin}
sudo chown -R \$(whoami):\$(whoami) "${REMOTE_DIR}"
SSHEOF

ok "Remote directories ready"

# --------------------------------------------------------------------------
# Rsync pipeline code
# --------------------------------------------------------------------------

info "Syncing pipeline code to ${REMOTE}:${REMOTE_DIR}/"

rsync -avz --delete \
    -e "ssh ${SSH_OPTS}" \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='.venv/' \
    --exclude='*.pyc' \
    --exclude='node_modules/' \
    --exclude='.env' \
    --exclude='infra/' \
    --exclude='toke_corpus.egg-info/' \
    --include='generator/***' \
    --include='dispatch/***' \
    --include='trial/***' \
    --include='validate/***' \
    --include='correct/***' \
    --include='prompts/***' \
    --include='pipeline/***' \
    --include='diff_test/***' \
    --include='judge/***' \
    --include='monitor/***' \
    --include='store/***' \
    --include='transpile/***' \
    --include='fuzz/***' \
    --include='mutate/***' \
    --include='ingest/***' \
    --include='scripts/***' \
    --include='configs/***' \
    --include='corpus/schema.json' \
    --include='pyproject.toml' \
    --include='config.example.yaml' \
    --include='main.py' \
    --exclude='corpus/*' \
    --exclude='data/*' \
    --exclude='logs/*' \
    --exclude='metrics/*' \
    --exclude='*' \
    "${REPO_ROOT}/" \
    "${REMOTE}:${REMOTE_DIR}/"

ok "Pipeline code synced"

# --------------------------------------------------------------------------
# Copy tkc binary (if available)
# --------------------------------------------------------------------------

if [ -f "${TKC_LOCAL}" ]; then
    info "Copying tkc binary from ${TKC_LOCAL}"
    rsync -avz -e "ssh ${SSH_OPTS}" "${TKC_LOCAL}" "${REMOTE}:${REMOTE_DIR}/bin/tkc"
    ssh ${SSH_OPTS} "${REMOTE}" "chmod +x ${REMOTE_DIR}/bin/tkc"
    ok "tkc binary deployed"
else
    warn "tkc binary not found at ${TKC_LOCAL}"
    warn "Set TKC_LOCAL to the path of a Linux amd64 tkc binary, or build on the instance."
    warn "See: infra/build_tkc.sh"
fi

# --------------------------------------------------------------------------
# Copy .env file (API keys)
# --------------------------------------------------------------------------

if [ -f "${ENV_FILE}" ]; then
    info "Copying .env file to instance"
    rsync -avz -e "ssh ${SSH_OPTS}" "${ENV_FILE}" "${REMOTE}:${REMOTE_DIR}/.env"
    ssh ${SSH_OPTS} "${REMOTE}" "chmod 600 ${REMOTE_DIR}/.env"
    ok ".env file deployed (permissions set to 600)"
else
    warn ".env file not found at ${ENV_FILE}"
    warn "Create ${ENV_FILE} with your API keys before running the pipeline."
fi

# --------------------------------------------------------------------------
# Install Python dependencies in a venv
# --------------------------------------------------------------------------

info "Setting up Python venv and installing dependencies"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail
cd "${REMOTE_DIR}"

# Create venv if it does not exist
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -e . --quiet
pip install -e ".[dev]" --quiet

echo "Python packages installed:"
pip list --format=columns | head -20
SSHEOF

ok "Python dependencies installed"

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Corpus Generation Deployment Complete"
echo "======================================================================"
echo ""
echo "  Remote host     : ${REMOTE}"
echo "  Working dir     : ${REMOTE_DIR}"
echo ""
echo "  Required API keys (set in ${REMOTE_DIR}/.env):"
echo "    ANTHROPIC_API_KEY  — Claude models (Haiku/Sonnet)"
echo "    OPENAI_API_KEY     — GPT-4.1 Mini"
echo "    DEEPSEEK_API_KEY   — DeepSeek Chat"
echo "    XAI_API_KEY        — xAI Grok"
echo "    GOOGLE_API_KEY     — Gemini Flash"
echo ""
echo "  Next steps:"
echo "    1. Verify .env is configured:  ssh ${SSH_OPTS} ${REMOTE} 'cat ${REMOTE_DIR}/.env'"
echo "    2. Run verification:           ssh ${SSH_OPTS} ${REMOTE} '${REMOTE_DIR}/infra/verify.sh'"
echo "    3. Start generation:           ./infra/run_api_generation.sh"
echo "======================================================================"
