#! /usr/bin/env bash

set -e
set -x

# 這支腳本只給容器用（compose 的 prestart 服務）。映像建置時已用 workspace 的
# uv.lock 把依賴裝進 /app/.venv 並放到 PATH 最前面，這裡直接呼叫 venv 裡的
# 執行檔即可。不要再包 `uv run`：映像裡沒有 workspace 根目錄的 pyproject.toml
# 與 uv.lock，`uv run` 會在 /app/backend 另建一個 .venv 並只拿 backend/pyproject.toml
# 重新解依賴，拿不到根 pyproject.toml 的 override-dependencies（redis>=8 對 arq
# 的 redis<6 釘選），結果無解直接 exit 1；就算解得開也要連網重抓整套套件。
# 本機要跑的話從 backend/ 下用 `uv run bash scripts/prestart.sh`。

# Let the DB start
python app/backend_pre_start.py

# Run migrations
alembic upgrade head

# Create initial data in DB
python app/initial_data.py
