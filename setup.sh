#!/bin/bash
# setup.sh — One-time setup for Job Hunter
# Run this once before using the pipeline.
# Compatible with macOS.

set -e  # stop on first error

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # no color

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
step() { echo -e "\n${YELLOW}──${NC} $1"; }

echo ""
echo "╔══════════════════════════════╗"
echo "║    Job Hunter — Setup        ║"
echo "╚══════════════════════════════╝"
echo ""

# ─── 0. Xcode Command Line Tools ─────────────────────────────────────────────
step "Checking Xcode Command Line Tools..."
if ! xcode-select -p &>/dev/null; then
    warn "Xcode Command Line Tools not found. Installing..."
    xcode-select --install
    echo ""
    echo "  A popup will appear — click Install and wait for it to finish."
    echo "  Once done, re-run this script: bash setup.sh"
    exit 0
else
    ok "Xcode Command Line Tools already installed"
fi

# ─── 1. Homebrew ──────────────────────────────────────────────────────────────
step "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon Macs
    if [ -f "/opt/homebrew/bin/brew" ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    ok "Homebrew installed"
else
    ok "Homebrew already installed"
fi

# ─── 2. Python 3.10+ ──────────────────────────────────────────────────────────
step "Checking Python..."
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        VERSION=$($cmd --version 2>&1 | awk '{print $2}')
        MAJOR=$(echo "$VERSION" | cut -d. -f1)
        MINOR=$(echo "$VERSION" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON_CMD="$cmd"
            ok "Found $cmd ($VERSION)"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    warn "Python 3.10+ not found. Installing via Homebrew..."
    brew install python@3.12
    PYTHON_CMD="/opt/homebrew/bin/python3.12"
    ok "Python 3.12 installed"
fi

# ─── 3. Virtual environment ───────────────────────────────────────────────────
step "Setting up virtual environment..."
VENV_DIR="$(pwd)/venv"
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON_CMD -m venv "$VENV_DIR"
    ok "Virtual environment created at ./venv"
else
    ok "Virtual environment already exists"
fi

# Use the venv's Python for everything from here on
PYTHON_CMD="$VENV_DIR/bin/python3"
PIP_CMD="$VENV_DIR/bin/pip"

# ─── 4. pip dependencies ──────────────────────────────────────────────────────
step "Installing Python packages..."
$PIP_CMD install --upgrade pip -q
$PIP_CMD install -r requirements.txt -q
ok "Python packages installed"

# ─── 5. Playwright browser ────────────────────────────────────────────────────
step "Installing Playwright browser (Chromium)..."
$PYTHON_CMD -m playwright install chromium
ok "Playwright Chromium installed"

# ─── 6. Node.js / npm ─────────────────────────────────────────────────────────
step "Checking Node.js..."
if ! command -v node &>/dev/null; then
    warn "Node.js not found. Installing via Homebrew..."
    brew install node
    ok "Node.js installed"
else
    ok "Node.js already installed ($(node --version))"
fi

# ─── 7. Claude Code CLI ───────────────────────────────────────────────────────
step "Checking Claude Code CLI..."
if ! command -v claude &>/dev/null; then
    warn "Claude CLI not found. Installing..."
    npm install -g @anthropic-ai/claude-code
    ok "Claude Code CLI installed"
else
    ok "Claude Code CLI already installed ($(claude --version 2>/dev/null || echo 'unknown version'))"
fi

# ─── 8. Create run.sh helper ──────────────────────────────────────────────────
step "Creating run.sh helper..."
cat > run.sh << 'EOF'
#!/bin/bash
# run.sh — Activates the virtual environment and runs main.py (or any script).
# Usage: bash run.sh [main.py arguments]          <- runs main.py
#        bash run.sh login_linkedin.py             <- runs a specific script
# Examples:
#   bash run.sh --dry-run
#   bash run.sh --start-from 2
#   bash run.sh login_linkedin.py
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/venv/bin/activate"

# If first arg is a .py file, run it directly; otherwise run main.py
if [[ "$1" == *.py ]]; then
    python3 "$@"
else
    python3 main.py "$@"
fi
EOF
chmod +x run.sh
ok "run.sh created"

# ─── 9. Claude login ──────────────────────────────────────────────────────────
step "Claude login"
echo ""
echo "  You need to log in with your Claude Pro account."
echo "  This will open a browser — sign in and approve the connection."
echo ""
echo "  Running: claude auth login"
echo ""
claude auth login || warn "Login step skipped or failed — run 'claude auth login' manually before using the pipeline."

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔════════════════════════════════════════════════╗"
echo "║   Setup complete!                              ║"
echo "║                                                ║"
echo "║   Next steps:                                  ║"
echo "║   1. Copy and edit config:                     ║"
echo "║      cp config.example.yaml config.yaml        ║"
echo "║      nano config.yaml                          ║"
echo "║      (set resume paths, name, email, phone)    ║"
echo "║   2. Save LinkedIn session (once):             ║"
echo "║      bash run.sh login_linkedin.py             ║"
echo "║   3. Test without submitting forms:            ║"
echo "║      bash run.sh --dry-run                     ║"
echo "║   4. Full pipeline:                            ║"
echo "║      bash run.sh                               ║"
echo "╚════════════════════════════════════════════════╝"
echo ""
