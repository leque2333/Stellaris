#!/usr/bin/env bash
# 启动 Stellaris 中枢
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-18895}"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "找不到 $PYTHON，请先安装 Python 3.10+" >&2
    exit 1
fi

exec "$PYTHON" console/server.py "$PORT"
