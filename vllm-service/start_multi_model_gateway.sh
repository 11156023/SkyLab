#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
MAIN_LOG="$LOG_DIR/main.log"
PID_FILE="$RUNTIME_DIR/multi-model-gateway.pid"

if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "找不到可用的 Python（需要 python3）。"
    exit 1
fi

mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

if [[ -f "$PID_FILE" ]]; then
    EXISTING_PID="$(cat "$PID_FILE")"
    if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" >/dev/null 2>&1; then
        echo "多模型 Gateway 已在背景執行中 (PID: $EXISTING_PID)。"
        echo "主控日誌: $MAIN_LOG"
        echo "Gateway API 日誌: $LOG_DIR/gateway.log"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

cd "$PROJECT_ROOT"
nohup "$PYTHON_BIN" main.py gateway --base-env .env.API "$@" >> "$MAIN_LOG" 2>&1 &
PID="$!"
echo "$PID" > "$PID_FILE"

echo "多模型 Gateway 已背景啟動 (PID: $PID)。"
echo "主控日誌: $MAIN_LOG"
echo "Gateway API 日誌: $LOG_DIR/gateway.log"
