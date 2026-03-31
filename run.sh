#!/usr/bin/env bash
# Run the bot locally using the project venv (mirrors Docker's Python 3.12 + requirements.txt)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv"

if [ ! -f "$VENV/bin/python3" ]; then
    echo "Creating venv with python3.12..."
    python3.12 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip
    "$VENV/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
fi

# Sync deps if requirements.txt is newer than last install
if [ "$SCRIPT_DIR/requirements.txt" -nt "$VENV/.last_install" ] 2>/dev/null; then
    echo "Syncing requirements.txt → venv..."
    "$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
    touch "$VENV/.last_install"
fi

exec "$VENV/bin/python3" -m app.main "$@"
