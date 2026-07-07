#!/usr/bin/env bash
# Thin wrapper — the real uninstaller is the cross-platform install.py.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "Python 3 not found on PATH." >&2
  exit 1
fi
exec "$PY" "$DIR/install.py" --uninstall "$@"
