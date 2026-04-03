#!/bin/bash
set -euo pipefail

# verify.sh — Run ON the cloud instance after setup to verify that all
# toolchains, packages, and environment variables are correctly configured.
# Idempotent: safe to run multiple times.

WORK_DIR="/opt/toke-corpus"
VENV_DIR="${WORK_DIR}/.venv"
TMP_DIR=$(mktemp -d)

# Clean up temp files on exit
trap 'rm -rf "${TMP_DIR}"' EXIT

PASS=0
FAIL=0
RESULTS=()

info()  { printf "\033[1;34m[TEST]\033[0m  %s\n" "$1"; }

record_pass() {
    printf "\033[1;32m[PASS]\033[0m  %s\n" "$1"
    RESULTS+=("PASS  $1")
    PASS=$((PASS + 1))
}

record_fail() {
    printf "\033[1;31m[FAIL]\033[0m  %s\n" "$1"
    RESULTS+=("FAIL  $1")
    FAIL=$((FAIL + 1))
}

# --------------------------------------------------------------------------
# 1. Required tools exist and report versions
# --------------------------------------------------------------------------

check_tool() {
    local name="$1"
    shift
    info "Checking ${name}"
    if "$@" > /dev/null 2>&1; then
        record_pass "${name}: $("$@" 2>&1 | head -1)"
    else
        record_fail "${name}: not found or not working"
    fi
}

check_tool "gcc"     gcc --version
check_tool "python3" python3 --version
check_tool "javac"   javac --version
check_tool "java"    java --version
check_tool "git"     git --version
check_tool "curl"    curl --version
check_tool "jq"      jq --version
check_tool "tmux"    tmux -V
check_tool "rsync"   rsync --version

# --------------------------------------------------------------------------
# 2. tkc binary
# --------------------------------------------------------------------------

info "Checking tkc binary"
TKC_PATH="${WORK_DIR}/bin/tkc"
if [ -x "${TKC_PATH}" ]; then
    record_pass "tkc binary exists and is executable: ${TKC_PATH}"
else
    record_fail "tkc binary missing or not executable: ${TKC_PATH}"
fi

# --------------------------------------------------------------------------
# 3. Compile a trivial toke program
# --------------------------------------------------------------------------

info "Compiling trivial toke program"
TOKE_SRC="${TMP_DIR}/test.tk"
echo 'M=test;F=main():i64{<42};' > "${TOKE_SRC}"

if [ -x "${TKC_PATH}" ]; then
    if "${TKC_PATH}" --check "${TOKE_SRC}" > /dev/null 2>&1; then
        record_pass "tkc --check test.tk succeeded"
    elif "${TKC_PATH}" "${TOKE_SRC}" > /dev/null 2>&1; then
        record_pass "tkc test.tk succeeded"
    else
        record_fail "tkc failed to compile trivial toke program"
    fi
else
    record_fail "tkc not available — skipping toke compilation test"
fi

# --------------------------------------------------------------------------
# 4. Compile a trivial C program
# --------------------------------------------------------------------------

info "Compiling trivial C program"
C_SRC="${TMP_DIR}/test.c"
C_BIN="${TMP_DIR}/test_c"
cat > "${C_SRC}" <<'CEOF'
#include <stdio.h>
int main(void) { printf("42\n"); return 0; }
CEOF

if gcc -o "${C_BIN}" "${C_SRC}" && [ "$("${C_BIN}")" = "42" ]; then
    record_pass "gcc compile + run succeeded"
else
    record_fail "gcc compile or run failed"
fi

# --------------------------------------------------------------------------
# 5. Run a trivial Python script
# --------------------------------------------------------------------------

info "Running trivial Python script"
PY_SRC="${TMP_DIR}/test.py"
echo 'print(42)' > "${PY_SRC}"

if [ "$(python3 "${PY_SRC}")" = "42" ]; then
    record_pass "python3 script succeeded"
else
    record_fail "python3 script failed"
fi

# --------------------------------------------------------------------------
# 6. Compile and run a trivial Java program
# --------------------------------------------------------------------------

info "Compiling and running trivial Java program"
JAVA_DIR="${TMP_DIR}/java"
mkdir -p "${JAVA_DIR}"
cat > "${JAVA_DIR}/Test.java" <<'JEOF'
public class Test {
    public static void main(String[] args) {
        System.out.println(42);
    }
}
JEOF

if javac "${JAVA_DIR}/Test.java" && [ "$(java -cp "${JAVA_DIR}" Test)" = "42" ]; then
    record_pass "javac compile + java run succeeded"
else
    record_fail "javac compile or java run failed"
fi

# --------------------------------------------------------------------------
# 7. Python venv and required packages
# --------------------------------------------------------------------------

info "Checking Python venv packages"

if [ -d "${VENV_DIR}" ]; then
    # Activate venv to check installed packages
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"

    MISSING_PKGS=()
    for pkg in httpx tiktoken numpy pytest ruff mypy; do
        if python3 -c "import importlib; importlib.import_module('${pkg}')" > /dev/null 2>&1; then
            : # package found
        else
            MISSING_PKGS+=("${pkg}")
        fi
    done

    if [ ${#MISSING_PKGS[@]} -eq 0 ]; then
        record_pass "All required Python packages installed in venv"
    else
        record_fail "Missing Python packages: ${MISSING_PKGS[*]}"
    fi
else
    record_fail "Python venv not found at ${VENV_DIR}"
fi

# --------------------------------------------------------------------------
# 8. Environment variables (API keys)
# --------------------------------------------------------------------------

info "Checking API key environment variables"

MISSING_VARS=()
for var in ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY DEEPSEEK_API_KEY; do
    val="${!var:-}"
    if [ -z "${val}" ]; then
        MISSING_VARS+=("${var}")
    fi
done

if [ ${#MISSING_VARS[@]} -eq 0 ]; then
    record_pass "All API key environment variables are set"
else
    record_fail "Missing or empty env vars: ${MISSING_VARS[*]}"
fi

# Check path variables
for var in TKC_PATH CORPUS_DIR LOG_DIR; do
    val="${!var:-}"
    if [ -z "${val}" ]; then
        record_fail "Environment variable ${var} is not set"
    else
        record_pass "Environment variable ${var}=${val}"
    fi
done

# --------------------------------------------------------------------------
# 9. Directory structure
# --------------------------------------------------------------------------

info "Checking directory structure"

for dir in "${WORK_DIR}/corpus" "${WORK_DIR}/logs" "${WORK_DIR}/metrics" "${WORK_DIR}/bin"; do
    if [ -d "${dir}" ]; then
        record_pass "Directory exists: ${dir}"
    else
        record_fail "Directory missing: ${dir}"
    fi
done

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

echo ""
echo "======================================================================"
echo "  Verification Summary"
echo "======================================================================"
echo ""
for result in "${RESULTS[@]}"; do
    echo "  ${result}"
done
echo ""
echo "----------------------------------------------------------------------"
echo "  Total: $((PASS + FAIL))   Passed: ${PASS}   Failed: ${FAIL}"
echo "----------------------------------------------------------------------"

if [ "${FAIL}" -gt 0 ]; then
    echo ""
    echo "  Some checks FAILED. Review the output above and fix before"
    echo "  running the corpus pipeline."
    echo ""
    exit 1
else
    echo ""
    echo "  All checks PASSED. Instance is ready for corpus generation."
    echo ""
    exit 0
fi
