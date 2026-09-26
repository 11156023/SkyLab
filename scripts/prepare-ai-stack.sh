#!/usr/bin/env bash
# Prepare the integrated Compose without sourcing or printing secret files.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${AI_STACK_PYTHON:-$repo_root/vllm-service/.venv/bin/python}"
if [[ -z "${AI_STACK_PYTHON:-}" && ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi
if ! "$python_bin" -c 'import yaml, dotenv' >/dev/null 2>&1; then
  echo 'Python 需安裝 PyYAML 與 python-dotenv；可用 AI_STACK_PYTHON 指定已安裝相依套件的 Python。' >&2
  exit 1
fi
cd "$repo_root"
exec "$python_bin" vllm-service/tools/prepare_ai_stack.py "$@"
