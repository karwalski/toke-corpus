#!/bin/bash
set -euo pipefail

# run_api_generation.sh — Run API-based corpus generation on EC2.
#
# Executes the transpiler pipeline (Story 9.2.2): generates task descriptions,
# calls LLM APIs to produce Python code, transpiles to toke, and validates.
# Uses the dispatch/ provider pool for multi-provider generation.
#
# Supports checkpoint/resume, configurable task count, provider mix,
# and cost limits. Logs to a timestamped log file.
#
# Idempotent: resumes from the last checkpoint if interrupted.
#
# Usage:
#   ./run_api_generation.sh                    # defaults
#   ./run_api_generation.sh --tasks 10000      # custom task count
#   ./run_api_generation.sh --cost-limit 200   # custom cost ceiling
#   ./run_api_generation.sh --resume           # resume from checkpoint
#   ./run_api_generation.sh --dry-run          # print config, do not run
#
# Required environment variables:
#   CORPUS_EC2_HOST — hostname or IP of the corpus generation EC2 instance
#   SSH_KEY         — path to the SSH private key
#   SSH_USER        — SSH username (default: ubuntu)

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
CORPUS_EC2_HOST="${CORPUS_EC2_HOST:?Error: CORPUS_EC2_HOST is not set}"
SSH_KEY="${SSH_KEY:?Error: SSH_KEY is not set}"

REMOTE="${SSH_USER}@${CORPUS_EC2_HOST}"
REMOTE_DIR="/opt/toke-corpus"
SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

# Pipeline defaults (overridable via CLI flags).
TOTAL_TASKS=50000
COST_LIMIT=500.00
CONFIG_FILE="config.yaml"
RESUME_FLAG=""
DRY_RUN=false

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tasks)
            TOTAL_TASKS="$2"
            shift 2
            ;;
        --cost-limit)
            COST_LIMIT="$2"
            shift 2
            ;;
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --resume)
            RESUME_FLAG="--resume"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--tasks N] [--cost-limit N] [--config FILE] [--resume] [--dry-run]"
            echo ""
            echo "Options:"
            echo "  --tasks N        Number of tasks to generate (default: 50000)"
            echo "  --cost-limit N   Maximum API cost in USD (default: 500.00)"
            echo "  --config FILE    Config file name in ${REMOTE_DIR} (default: config.yaml)"
            echo "  --resume         Resume from last checkpoint"
            echo "  --dry-run        Print configuration without running"
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
LOG_FILE="generation_${TIMESTAMP}.log"

# --------------------------------------------------------------------------
# Print configuration
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  API Corpus Generation — Configuration"
echo "======================================================================"
echo ""
echo "  Remote host     : ${REMOTE}"
echo "  Working dir     : ${REMOTE_DIR}"
echo "  Config file     : ${CONFIG_FILE}"
echo "  Total tasks     : ${TOTAL_TASKS}"
echo "  Cost limit      : \$${COST_LIMIT}"
echo "  Resume          : ${RESUME_FLAG:-no}"
echo "  Log file        : ${REMOTE_DIR}/logs/${LOG_FILE}"
echo "  Timestamp       : ${TIMESTAMP}"
echo ""
echo "======================================================================"
echo ""

if [ "${DRY_RUN}" = true ]; then
    info "Dry run — exiting without running."
    exit 0
fi

# --------------------------------------------------------------------------
# Validate connectivity and prerequisites
# --------------------------------------------------------------------------

info "Verifying remote instance is ready"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail

# Check .env exists
if [ ! -f "${REMOTE_DIR}/.env" ]; then
    echo "ERROR: ${REMOTE_DIR}/.env not found. Deploy first."
    exit 1
fi

# Check pipeline code exists
if [ ! -f "${REMOTE_DIR}/main.py" ]; then
    echo "ERROR: ${REMOTE_DIR}/main.py not found. Deploy first."
    exit 1
fi

# Check tkc binary
if [ ! -x "${REMOTE_DIR}/bin/tkc" ]; then
    echo "ERROR: tkc binary not found at ${REMOTE_DIR}/bin/tkc. Build or deploy it first."
    exit 1
fi

# Check venv
if [ ! -d "${REMOTE_DIR}/.venv" ]; then
    echo "ERROR: Python venv not found. Run deploy_corpus_gen.sh first."
    exit 1
fi

# Verify API keys are set (at least one)
source "${REMOTE_DIR}/.env" 2>/dev/null || true
KEYS_SET=0
for var in ANTHROPIC_API_KEY OPENAI_API_KEY DEEPSEEK_API_KEY XAI_API_KEY GOOGLE_API_KEY; do
    if [ -n "\${!var:-}" ]; then
        KEYS_SET=\$((KEYS_SET + 1))
    fi
done

if [ "\${KEYS_SET}" -eq 0 ]; then
    echo "ERROR: No API keys set in ${REMOTE_DIR}/.env"
    exit 1
fi

echo "Pre-checks passed. \${KEYS_SET} API key(s) configured."
SSHEOF

ok "Remote instance ready"

# --------------------------------------------------------------------------
# Launch generation in tmux (persists across SSH disconnect)
# --------------------------------------------------------------------------

info "Launching generation on ${REMOTE} in tmux session 'corpusgen'"

# shellcheck disable=SC2029
ssh ${SSH_OPTS} "${REMOTE}" bash <<SSHEOF
set -euo pipefail

# Kill existing tmux session if present (idempotent restart).
tmux kill-session -t corpusgen 2>/dev/null || true

# Build the command to run inside tmux.
cat > "${REMOTE_DIR}/run_generation.sh" <<'INNEREOF'
#!/bin/bash
set -euo pipefail

cd "${REMOTE_DIR}"
source .venv/bin/activate
source .env

export TKC_PATH="${REMOTE_DIR}/bin/tkc"
export CORPUS_DIR="${REMOTE_DIR}/corpus"
export LOG_DIR="${REMOTE_DIR}/logs"

echo "=== Corpus generation started at \$(date) ==="
echo "Tasks: ${TOTAL_TASKS}, Cost limit: \$${COST_LIMIT}"
echo ""

python3 main.py \\
    --config "${CONFIG_FILE}" \\
    ${RESUME_FLAG} \\
    2>&1 | tee "${REMOTE_DIR}/logs/${LOG_FILE}"

echo ""
echo "=== Corpus generation finished at \$(date) ==="
INNEREOF

chmod +x "${REMOTE_DIR}/run_generation.sh"

# Start in tmux so it survives SSH disconnect.
tmux new-session -d -s corpusgen "${REMOTE_DIR}/run_generation.sh"

echo "tmux session 'corpusgen' started."
SSHEOF

ok "Generation launched in tmux session 'corpusgen'"

# --------------------------------------------------------------------------
# Monitoring instructions
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Generation Running"
echo "======================================================================"
echo ""
echo "  The pipeline is running in a tmux session on the remote instance."
echo ""
echo "  Monitor progress:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'tmux attach -t corpusgen'"
echo ""
echo "  View live log:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'tail -f ${REMOTE_DIR}/logs/${LOG_FILE}'"
echo ""
echo "  Check corpus size:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'wc -l ${REMOTE_DIR}/corpus/*.jsonl'"
echo ""
echo "  Check cost tracker:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'cat ${REMOTE_DIR}/metrics/cost.json'"
echo ""
echo "  Stop generation:"
echo "    ssh ${SSH_OPTS} ${REMOTE} 'tmux kill-session -t corpusgen'"
echo ""
echo "  Download results:"
echo "    rsync -avz -e \"ssh ${SSH_OPTS}\" ${REMOTE}:${REMOTE_DIR}/corpus/ ./corpus/"
echo "    rsync -avz -e \"ssh ${SSH_OPTS}\" ${REMOTE}:${REMOTE_DIR}/metrics/ ./metrics/"
echo "======================================================================"
