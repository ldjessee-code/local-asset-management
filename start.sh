#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x .venv/bin/python ]] && .venv/bin/python -c "import sys" >/dev/null 2>&1; then
  exec .venv/bin/python ./start.py "$@"
fi
if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
  exec python3 ./start.py "$@"
fi
if command -v python >/dev/null 2>&1 && python -c "import sys" >/dev/null 2>&1; then
  exec python ./start.py "$@"
fi

echo "Install Python 3.11+: https://www.python.org/downloads/" >&2
echo "macOS: brew install python" >&2
echo "Debian/Ubuntu: sudo apt install python3 python3-venv python3-pip" >&2
exit 1
