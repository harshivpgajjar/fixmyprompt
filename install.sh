#!/usr/bin/env bash
# Thin wrapper — the real installer is the cross-platform install.py (so macOS,
# Linux, and Windows share one code path). Run `python install.py` directly if
# you prefer.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "Python 3 not found on PATH. Install it, then re-run." >&2
  exit 1
fi
exec "$PY" "$DIR/install.py" "$@"
