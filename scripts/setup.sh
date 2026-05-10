#!/bin/bash
set -e

PROJECT_DIR="/share-new/xiongjunqi/claw-eval"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

cd "${PROJECT_DIR}"

# ====================================================================
# Step 1: Install dependencies via uv
# ====================================================================
info "Step 1: Installing dependencies"
if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[mock,sandbox]"
uv pip install -r requirements-sandbox-server.txt
pass "Dependencies installed"

# ====================================================================
# Step 2: Validate task definitions
# ====================================================================
info "Step 2: Validating task definitions"
python scripts/validate_tasks.py --all && pass "All tasks validated" || {
    echo -e "${YELLOW}[WARN]${NC} Some tasks have validation issues (non-fatal)"
}
