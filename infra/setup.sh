#!/bin/bash
set -euo pipefail

# setup.sh — Provision a fresh Ubuntu 24.04 instance for the toke corpus pipeline.
# Idempotent: safe to run multiple times.
# Must be run as root (or with sudo).

WORK_DIR="/opt/toke-corpus"

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

info()  { printf "\033[1;34m[INFO]\033[0m  %s\n" "$1"; }
ok()    { printf "\033[1;32m[OK]\033[0m    %s\n" "$1"; }
fail()  { printf "\033[1;31m[FAIL]\033[0m  %s\n" "$1"; exit 1; }

# --------------------------------------------------------------------------
# 1. System packages
# --------------------------------------------------------------------------

info "Updating apt package index"
apt-get update -y

info "Installing system packages"
apt-get install -y \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    openjdk-21-jdk \
    tmux \
    git \
    curl \
    jq

ok "System packages installed"

# --------------------------------------------------------------------------
# 2. Working directory structure
# --------------------------------------------------------------------------

info "Creating working directory structure under ${WORK_DIR}"
mkdir -p "${WORK_DIR}/corpus"
mkdir -p "${WORK_DIR}/logs"
mkdir -p "${WORK_DIR}/metrics"
mkdir -p "${WORK_DIR}/bin"

ok "Directory structure ready"

# --------------------------------------------------------------------------
# 3. Python virtual environment
# --------------------------------------------------------------------------

VENV_DIR="${WORK_DIR}/.venv"

if [ ! -d "${VENV_DIR}" ]; then
    info "Creating Python virtual environment at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
else
    info "Python virtual environment already exists at ${VENV_DIR}"
fi

# Activate the venv for the rest of this script
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

info "Upgrading pip"
pip install --upgrade pip

ok "Python venv ready"

# --------------------------------------------------------------------------
# 4. Python dependencies from pyproject.toml
# --------------------------------------------------------------------------

# The pipeline code must already be deployed to WORK_DIR before this step
# works. If pyproject.toml is not present yet, skip with a warning.

if [ -f "${WORK_DIR}/pyproject.toml" ]; then
    info "Installing Python dependencies (pip install -e .)"
    pip install -e "${WORK_DIR}"
    pip install -e "${WORK_DIR}[dev]"
    ok "Python dependencies installed"
else
    info "pyproject.toml not found at ${WORK_DIR}/pyproject.toml — skipping pip install"
    info "Run 'deploy.sh' first, then re-run this script or manually run: pip install -e ${WORK_DIR}"
fi

# --------------------------------------------------------------------------
# 5. .env.template
# --------------------------------------------------------------------------

ENV_TEMPLATE="${WORK_DIR}/.env.template"

info "Writing .env.template to ${ENV_TEMPLATE}"
cat > "${ENV_TEMPLATE}" <<'ENVEOF'
# Required API keys — fill in before running the pipeline.
# DO NOT commit this file with real values.
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
DEEPSEEK_API_KEY=

# Paths (defaults — change only if layout differs)
TKC_PATH=/opt/toke-corpus/bin/tkc
CORPUS_DIR=/opt/toke-corpus/corpus
LOG_DIR=/opt/toke-corpus/logs
ENVEOF

ok ".env.template written"

# --------------------------------------------------------------------------
# 6. Verify toolchains
# --------------------------------------------------------------------------

info "Verifying installed toolchains"

ERRORS=0

verify_cmd() {
    local label="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        ok "${label}: $("$@" 2>&1 | head -1)"
    else
        fail "${label}: command failed"
        ERRORS=$((ERRORS + 1))
    fi
}

verify_cmd "gcc"     gcc --version
verify_cmd "python3" python3 --version
verify_cmd "javac"   javac --version
verify_cmd "java"    java --version
verify_cmd "git"     git --version
verify_cmd "curl"    curl --version
verify_cmd "jq"      jq --version
verify_cmd "tmux"    tmux -V

if [ "${ERRORS}" -ne 0 ]; then
    fail "${ERRORS} toolchain verification(s) failed"
fi

# --------------------------------------------------------------------------
# 7. Summary
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Setup complete"
echo "======================================================================"
echo ""
echo "  Working directory : ${WORK_DIR}"
echo "  Python venv       : ${VENV_DIR}"
echo "  Corpus output     : ${WORK_DIR}/corpus/"
echo "  Logs              : ${WORK_DIR}/logs/"
echo "  Metrics           : ${WORK_DIR}/metrics/"
echo "  Binaries          : ${WORK_DIR}/bin/"
echo "  Env template      : ${ENV_TEMPLATE}"
echo ""
echo "  gcc     : $(gcc --version 2>&1 | head -1)"
echo "  python3 : $(python3 --version 2>&1)"
echo "  javac   : $(javac --version 2>&1)"
echo "  java    : $(java --version 2>&1 | head -1)"
echo ""
echo "  Next steps:"
echo "    1. Deploy pipeline code:  ./deploy.sh <instance-host>"
echo "    2. Build/copy tkc:        ./build_tkc.sh"
echo "    3. Copy .env.template to .env and fill in API keys"
echo "    4. Run verification:      ./verify.sh"
echo "======================================================================"
