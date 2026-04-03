#!/bin/bash
set -euo pipefail

# deploy.sh — Deploy the toke corpus pipeline from the local machine to a
# remote cloud instance via rsync + SSH.
#
# Usage:
#   ./deploy.sh <user@instance-host>
#
# Example:
#   ./deploy.sh ubuntu@<instance-host>
#
# Prerequisites:
#   - SSH access to the instance (key in ssh-agent or default key)
#   - rsync installed locally and on the instance
#   - setup.sh has already been run on the instance

WORK_DIR="/opt/toke-corpus"

info()  { printf "\033[1;34m[INFO]\033[0m  %s\n" "$1"; }
ok()    { printf "\033[1;32m[OK]\033[0m    %s\n" "$1"; }
fail()  { printf "\033[1;31m[FAIL]\033[0m  %s\n" "$1"; exit 1; }

# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

if [ $# -lt 1 ]; then
    echo "Usage: $0 <user@instance-host>"
    echo ""
    echo "  Deploys the corpus pipeline to the remote instance."
    echo "  The instance must already have setup.sh run."
    exit 1
fi

REMOTE="$1"

# --------------------------------------------------------------------------
# Resolve local repo root (the toke-corpus directory)
# --------------------------------------------------------------------------

# This script lives in infra/, so the repo root is one level up.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

info "Local repo root: ${REPO_ROOT}"
info "Remote target:   ${REMOTE}:${WORK_DIR}/"

# --------------------------------------------------------------------------
# Upload pipeline code via rsync
# --------------------------------------------------------------------------

info "Syncing pipeline code to ${REMOTE}:${WORK_DIR}/"

rsync -avz --delete \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='.venv/' \
    --exclude='*.pyc' \
    --exclude='node_modules/' \
    --exclude='.env' \
    --exclude='infra/' \
    --include='generator/***' \
    --include='dispatch/***' \
    --include='trial/***' \
    --include='validate/***' \
    --include='prompts/***' \
    --include='pipeline/***' \
    --include='diff_test/***' \
    --include='judge/***' \
    --include='monitor/***' \
    --include='corpus/schema.json' \
    --include='pyproject.toml' \
    --include='main.py' \
    --exclude='corpus/*' \
    --exclude='*' \
    "${REPO_ROOT}/" \
    "${REMOTE}:${WORK_DIR}/"

ok "Pipeline code synced"

# --------------------------------------------------------------------------
# Install/update Python dependencies on the instance
# --------------------------------------------------------------------------

info "Installing Python dependencies on instance"

# shellcheck disable=SC2087
ssh "${REMOTE}" bash <<SSHEOF
set -euo pipefail
cd "${WORK_DIR}"

# Activate the venv created by setup.sh
source "${WORK_DIR}/.venv/bin/activate"

# Install the package in editable mode
pip install -e .
pip install -e ".[dev]"
SSHEOF

ok "Python dependencies updated on instance"

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Deployment complete"
echo "======================================================================"
echo ""
echo "  Remote host     : ${REMOTE}"
echo "  Working dir     : ${WORK_DIR}"
echo ""
echo "  Synced directories:"
echo "    generator/    dispatch/    trial/       validate/"
echo "    prompts/      pipeline/    diff_test/   judge/"
echo "    monitor/      corpus/schema.json        pyproject.toml"
echo ""
echo "  Next steps:"
echo "    1. Ensure tkc is built: ssh ${REMOTE} 'ls -l ${WORK_DIR}/bin/tkc'"
echo "    2. Copy .env.template to .env and set API keys"
echo "    3. Run verification: ssh ${REMOTE} '${WORK_DIR}/verify.sh'"
echo "======================================================================"
