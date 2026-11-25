#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load local .env if present (without overriding explicitly passed env vars)
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" =~ ^[[:space:]]*# ]] && continue
    k="${k// /}"
    if [[ -z "${!k:-}" ]]; then
      export "$k=$v"
    fi
  done < "$SCRIPT_DIR/.env"
fi

WEB_TOKEN="${VIBEONE_WEB_TOKEN:-${AGY_WEB_TOKEN:-}}"
if [[ -z "$WEB_TOKEN" ]]; then
  echo "ERROR: VIBEONE_WEB_TOKEN environment variable is required." >&2
  echo "Example: VIBEONE_WEB_TOKEN='your-secret-token' ./start.sh" >&2
  exit 1
fi
export VIBEONE_WEB_TOKEN="$WEB_TOKEN"

HOST="${VIBEONE_HOST:-${AGY_HOST:-127.0.0.1}}"
PORT="${VIBEONE_PORT:-${AGY_PORT:-8765}}"
export VIBEONE_WORKDIR="${VIBEONE_WORKDIR:-${AGY_WORKDIR:-$SCRIPT_DIR}}"

# Prefer local virtualenv python -m uvicorn (avoids hardcoded venv script shebang issues after directory renames)
if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "ERROR: python3 not found. Create the venv and install dependencies first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Ensure ~/.local/bin and /opt/homebrew/bin are in PATH so `agy` & `codex` can be located from non-login shells
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

echo "Starting VibeOne on http://${HOST}:${PORT}"
echo "Workspace directory (VIBEONE_WORKDIR): ${VIBEONE_WORKDIR}"
exec "$PYTHON_BIN" -m uvicorn server:app --host "$HOST" --port "$PORT" --ws-ping-interval 25 --ws-ping-timeout 25
