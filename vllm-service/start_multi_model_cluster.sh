#!/usr/bin/env bash

# Start only the local vLLM instances listed in models.json.  The public
# multi-model API is LiteLLM, which is started separately (scripts/prepare-ai-stack.sh).
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
RUNTIME_DIR="$PROJECT_ROOT/.runtime"
MAIN_LOG="$LOG_DIR/cluster.log"
PID_FILE="$RUNTIME_DIR/multi-model-cluster.pid"

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
        echo "多模型 cluster 已在背景執行中 (PID: $EXISTING_PID)。"
        echo "主控日誌: $MAIN_LOG"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

cd "$PROJECT_ROOT"
# flashinfer JIT compile 需要 ninja 等工具，必須讓 venv/bin 在 PATH 中
export PATH="$PROJECT_ROOT/.venv/bin:$PATH"
nohup "$PYTHON_BIN" main.py cluster --base-env .env.API "$@" >> "$MAIN_LOG" 2>&1 &
PID="$!"
echo "$PID" > "$PID_FILE"

echo "多模型 vLLM cluster 已背景啟動 (PID: $PID)。"
echo "主控日誌: $MAIN_LOG"
