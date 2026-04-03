#!/bin/bash
set -euo pipefail

# build_tkc.sh — Build (or cross-compile) the tkc compiler for Linux amd64
# and install it to /opt/toke-corpus/bin/tkc.
#
# Usage:
#   On the cloud instance (native build):
#     ./build_tkc.sh /path/to/tkc-source
#
#   Cross-compile from macOS (requires a Linux cross-compiler toolchain):
#     ./build_tkc.sh /path/to/tkc-source --cross
#
# The tkc source directory must contain a Makefile at its root.

WORK_DIR="/opt/toke-corpus"
BIN_DIR="${WORK_DIR}/bin"

info()  { printf "\033[1;34m[INFO]\033[0m  %s\n" "$1"; }
ok()    { printf "\033[1;32m[OK]\033[0m    %s\n" "$1"; }
fail()  { printf "\033[1;31m[FAIL]\033[0m  %s\n" "$1"; exit 1; }

# --------------------------------------------------------------------------
# Arguments
# --------------------------------------------------------------------------

if [ $# -lt 1 ]; then
    echo "Usage: $0 <tkc-source-dir> [--cross]"
    echo ""
    echo "  <tkc-source-dir>  Path to the tkc compiler source (must contain a Makefile)"
    echo "  --cross           Cross-compile for Linux amd64 from macOS"
    exit 1
fi

TKC_SRC="$1"
CROSS="${2:-}"

if [ ! -d "${TKC_SRC}" ]; then
    fail "tkc source directory not found: ${TKC_SRC}"
fi

if [ ! -f "${TKC_SRC}/Makefile" ]; then
    fail "No Makefile found in ${TKC_SRC}"
fi

# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

mkdir -p "${BIN_DIR}"

if [ "${CROSS}" = "--cross" ]; then
    # Cross-compilation from macOS to Linux amd64.
    # Requires a cross-compiler such as x86_64-linux-gnu-gcc (e.g. from
    # Homebrew: brew install x86_64-elf-binutils, or a musl cross toolchain).
    #
    # Alternatively, use Docker:
    #   docker run --rm -v "${TKC_SRC}:/src" -w /src ubuntu:24.04 \
    #       bash -c "apt-get update && apt-get install -y build-essential && make clean && make"
    #
    # The flags below assume a GNU cross-compiler is available on PATH.
    info "Cross-compiling tkc for Linux amd64 from macOS"
    info "Using CC=x86_64-linux-gnu-gcc (must be installed)"

    if ! command -v x86_64-linux-gnu-gcc &> /dev/null; then
        echo ""
        echo "Cross-compiler not found. Options:"
        echo ""
        echo "  1. Install via Homebrew (if available):"
        echo "       brew install FiloSottile/musl-cross/musl-cross"
        echo "     Then use CC=x86_64-linux-musl-gcc"
        echo ""
        echo "  2. Use Docker to build natively in a Linux container:"
        echo "       docker run --rm -v \"${TKC_SRC}:/src\" -w /src ubuntu:24.04 \\"
        echo "           bash -c 'apt-get update && apt-get install -y build-essential llvm-18-dev && make clean && make'"
        echo "       docker cp <container>:/src/tkc ${BIN_DIR}/tkc"
        echo ""
        echo "  3. Build on the cloud instance instead (recommended):"
        echo "       scp -r ${TKC_SRC} user@<instance-host>:/opt/toke-corpus/tkc-src"
        echo "       ssh user@<instance-host> 'cd /opt/toke-corpus/tkc-src && make clean && make'"
        echo ""
        fail "Cross-compiler x86_64-linux-gnu-gcc not found on PATH"
    fi

    cd "${TKC_SRC}"
    make clean || true
    CC=x86_64-linux-gnu-gcc make
    cp tkc "${BIN_DIR}/tkc"
else
    # Native build (on the cloud instance itself).
    info "Building tkc natively"
    cd "${TKC_SRC}"
    make clean || true
    make
    cp tkc "${BIN_DIR}/tkc"
fi

ok "tkc binary copied to ${BIN_DIR}/tkc"

# --------------------------------------------------------------------------
# Verify
# --------------------------------------------------------------------------

info "Verifying tkc binary"

if [ -x "${BIN_DIR}/tkc" ]; then
    # Try --version first, fall back to --help if not supported.
    if "${BIN_DIR}/tkc" --version > /dev/null 2>&1; then
        ok "tkc --version: $("${BIN_DIR}/tkc" --version 2>&1 | head -1)"
    elif "${BIN_DIR}/tkc" --help > /dev/null 2>&1; then
        ok "tkc --help succeeded (--version not supported)"
    else
        # tkc may not support --version or --help yet; check it is a valid ELF/Mach-O binary.
        FILE_TYPE=$(file "${BIN_DIR}/tkc")
        ok "tkc binary type: ${FILE_TYPE}"
    fi
else
    fail "tkc binary is not executable at ${BIN_DIR}/tkc"
fi

echo ""
echo "tkc installed at: ${BIN_DIR}/tkc"
