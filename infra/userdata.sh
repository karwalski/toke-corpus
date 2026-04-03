#!/bin/bash
# userdata.sh — EC2 / Lightsail user-data script.
# Paste this into the "User data" field when launching the instance.
# It runs as root on first boot and logs to /var/log/cloud-init-output.log.
#
# What it does:
#   1. Installs all system packages (gcc, python3, java, tmux, git, etc.)
#   2. Creates the /opt/toke-corpus directory structure
#   3. Sets up a Python venv
#   4. Clones the toke-corpus repo (if REPO_URL is set) or leaves it for deploy.sh
#   5. Writes the .env.template
#   6. Prints a summary to the log
#
# The instance is ready to receive deploy.sh + build_tkc.sh after this completes.
# Check progress: tail -f /var/log/cloud-init-output.log

set -euo pipefail

WORK_DIR="/opt/toke-corpus"
VENV_DIR="${WORK_DIR}/.venv"
LOG_TAG="[toke-setup]"

log() { echo "${LOG_TAG} $(date '+%H:%M:%S') $1"; }

# --------------------------------------------------------------------------
# 1. System packages
# --------------------------------------------------------------------------
log "Updating apt"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y

log "Installing packages"
apt-get install -y \
    build-essential \
    python3 \
    python3-pip \
    python3-venv \
    openjdk-21-jdk-headless \
    tmux \
    git \
    curl \
    jq \
    unzip

# --------------------------------------------------------------------------
# 2. Directory structure
# --------------------------------------------------------------------------
log "Creating ${WORK_DIR}"
mkdir -p "${WORK_DIR}"/{corpus,logs,metrics,bin}

# Make it writable by the default user (ubuntu on EC2, bitnami on Lightsail)
for user in ubuntu bitnami; do
    if id "${user}" &>/dev/null; then
        chown -R "${user}:${user}" "${WORK_DIR}"
        log "Ownership set to ${user}"
        break
    fi
done

# --------------------------------------------------------------------------
# 3. Python venv
# --------------------------------------------------------------------------
log "Creating Python venv"
python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip --quiet

# --------------------------------------------------------------------------
# 4. .env.template
# --------------------------------------------------------------------------
cat > "${WORK_DIR}/.env.template" <<'ENVEOF'
# Required API keys — copy to .env and fill in before running the pipeline.
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
DEEPSEEK_API_KEY=

# Paths
TKC_PATH=/opt/toke-corpus/bin/tkc
CORPUS_DIR=/opt/toke-corpus/corpus
LOG_DIR=/opt/toke-corpus/logs
ENVEOF

# --------------------------------------------------------------------------
# 5. Marker file
# --------------------------------------------------------------------------
cat > "${WORK_DIR}/.setup-complete" <<EOF
setup_completed=$(date -Iseconds)
gcc=$(gcc --version 2>&1 | head -1)
python=$(python3 --version 2>&1)
java=$(java --version 2>&1 | head -1)
javac=$(javac --version 2>&1)
EOF

# --------------------------------------------------------------------------
# 6. Summary
# --------------------------------------------------------------------------
log "========================================="
log "Setup complete"
log "  gcc:     $(gcc --version 2>&1 | head -1)"
log "  python:  $(python3 --version 2>&1)"
log "  javac:   $(javac --version 2>&1)"
log "  java:    $(java --version 2>&1 | head -1)"
log "  workdir: ${WORK_DIR}"
log "========================================="
log "Next: SSH in and run deploy.sh + build_tkc.sh"
