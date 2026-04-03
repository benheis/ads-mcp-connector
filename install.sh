#!/usr/bin/env bash
# ============================================================================
# install.sh — ads-mcp-connector installer
#
# One command to connect Claude Code to Meta Ads and Google Ads.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/bennymayo/ads-mcp-connector/main/install.sh | bash
#
# Or if you've already cloned the repo:
#   bash install.sh
#
# Idempotent — safe to run again if something goes wrong.
# ============================================================================
set -euo pipefail

REPO_URL="https://github.com/bennymayo/ads-mcp-connector.git"
DEFAULT_INSTALL_DIR="$HOME/ads-mcp-connector"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills/ads-connect"
BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
CYAN="\033[0;36m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "${CYAN}[info]${RESET}  $1"; }
success() { echo -e "${GREEN}[done]${RESET}  $1"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $1"; }
error()   { echo -e "${RED}[error]${RESET} $1"; }

# ─── Banner ───────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}  ads-mcp-connector${RESET}"
echo -e "  Connect Claude Code to Meta Ads + Google Ads"
echo -e "  ─────────────────────────────────────────────"
echo ""

# ─── Step 1: Python check ─────────────────────────────────────────────────────

info "Checking Python version..."

PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    version=$("$cmd" -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
    major=$("$cmd" -c "import sys; print(sys.version_info.major)")
    minor=$("$cmd" -c "import sys; print(sys.version_info.minor)")
    if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  error "Python 3.10 or newer is required."
  echo ""
  echo "  Download it free at: https://www.python.org/downloads/"
  echo "  It takes about 3 minutes to install."
  echo "  Then run this installer again."
  echo ""
  exit 1
fi

success "Python found: $($PYTHON --version)"

# ─── Step 2: Install directory ────────────────────────────────────────────────

echo ""
echo -e "  ${BOLD}Where should ads-mcp-connector be installed?${RESET}"
echo -e "  Default: $DEFAULT_INSTALL_DIR"
echo -e "  Press Enter to use the default, or type a different path."
echo ""
read -r -p "  Install path: " USER_DIR
INSTALL_DIR="${USER_DIR:-$DEFAULT_INSTALL_DIR}"
INSTALL_DIR="${INSTALL_DIR/#\~/$HOME}"
echo ""
info "Installing to: $INSTALL_DIR"

# ─── Step 3: Clone or update repo ────────────────────────────────────────────

if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Found existing installation — updating..."
  git -C "$INSTALL_DIR" pull --quiet
  success "Updated to latest version"
elif [[ -d "$INSTALL_DIR" && -f "$INSTALL_DIR/server.py" ]]; then
  # Installed from a zip or copied directly — already there
  success "Source files found at $INSTALL_DIR"
else
  info "Downloading ads-mcp-connector..."
  if command -v git &>/dev/null; then
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    success "Downloaded to $INSTALL_DIR"
  else
    # Fallback: download zip
    warn "git not found — downloading zip instead..."
    TMP_ZIP=$(mktemp /tmp/ads-mcp-XXXXXX.zip)
    if command -v curl &>/dev/null; then
      curl -fsSL "https://github.com/bennymayo/ads-mcp-connector/archive/refs/heads/main.zip" -o "$TMP_ZIP"
    elif command -v wget &>/dev/null; then
      wget -q "https://github.com/bennymayo/ads-mcp-connector/archive/refs/heads/main.zip" -O "$TMP_ZIP"
    else
      error "Neither git, curl, nor wget found. Cannot download."
      echo "  Install git (https://git-scm.com) and try again."
      exit 1
    fi
    mkdir -p "$INSTALL_DIR"
    unzip -q "$TMP_ZIP" -d "$INSTALL_DIR" --strip-components=1
    rm "$TMP_ZIP"
    success "Downloaded and extracted to $INSTALL_DIR"
  fi
fi

# ─── Step 4: Virtual environment ─────────────────────────────────────────────

VENV_DIR="$INSTALL_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating Python environment..."
  echo "  (Think of this as a dedicated workspace just for this tool —"
  echo "   it keeps everything isolated and tidy.)"
  "$PYTHON" -m venv "$VENV_DIR"
  success "Python environment created"
else
  success "Python environment already exists"
fi

# ─── Step 5: Install dependencies ────────────────────────────────────────────

info "Installing dependencies..."
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet -r "$INSTALL_DIR/requirements.txt"
success "Dependencies installed"

# ─── Step 6: Copy .env.example → .env ────────────────────────────────────────

ENV_FILE="$INSTALL_DIR/.env"
ENV_EXAMPLE="$INSTALL_DIR/.env.example"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  success "Created credentials file at $ENV_FILE"
else
  success "Credentials file already exists (not overwritten)"
fi

# ─── Step 7: Register MCP server in Claude settings ──────────────────────────

info "Registering with Claude Code..."
mkdir -p "$(dirname "$CLAUDE_SETTINGS")"

"$VENV_PYTHON" - <<PYEOF
import json, sys
from pathlib import Path

settings_path = Path("$CLAUDE_SETTINGS")
data = {}
if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        data = {}

data.setdefault("mcpServers", {})
data["mcpServers"]["ads-mcp-connector"] = {
    "command": "$VENV_PYTHON",
    "args": ["$INSTALL_DIR/server.py"],
    "env": {}
}
settings_path.write_text(json.dumps(data, indent=2))
print("ok")
PYEOF

success "Registered in ~/.claude/settings.json"

# ─── Step 8: Install Claude skill ────────────────────────────────────────────

if [[ -f "$INSTALL_DIR/SKILL.md" ]]; then
  mkdir -p "$CLAUDE_SKILLS_DIR"
  cp "$INSTALL_DIR/SKILL.md" "$CLAUDE_SKILLS_DIR/SKILL.md"
  success "Installed /ads-connect skill to ~/.claude/skills/"
else
  warn "SKILL.md not found — skill not installed"
fi

# ─── Step 9: Install pre-commit security hook ────────────────────────────────

GIT_HOOKS_DIR="$INSTALL_DIR/.git/hooks"
HOOK_SOURCE="$INSTALL_DIR/hooks/pre-commit"

if [[ -d "$GIT_HOOKS_DIR" && -f "$HOOK_SOURCE" ]]; then
  cp "$HOOK_SOURCE" "$GIT_HOOKS_DIR/pre-commit"
  chmod +x "$GIT_HOOKS_DIR/pre-commit"
  success "Security hook installed (scans for API keys before every commit)"
else
  warn "No .git directory found — skipping security hook."
  echo "  If you connect this to GitHub later, run bash install.sh"
  echo "  again to install the security hook."
fi

# ─── Step 10: Connection status ──────────────────────────────────────────────

echo ""
"$VENV_PYTHON" "$INSTALL_DIR/auth_check.py"

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}  Installation complete${RESET}"
echo -e "  ─────────────────────────────────────────────"
echo ""
echo -e "  Open Claude Code and type:"
echo ""
echo -e "  ${CYAN}/ads-connect${RESET}"
echo ""
echo -e "  The skill will walk you through connecting"
echo -e "  Meta Ads and Google Ads step by step."
echo ""
