FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

# slim 沒有 curl，但 compose 的 backend healthcheck 要靠它打 health-check。
# 其餘相依套件在 lock 裡都有 manylinux wheel，不需要編譯工具鏈。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#installing-uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# Compile bytecode
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#compiling-bytecode
ENV UV_COMPILE_BYTECODE=1

# uv Cache
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#caching
ENV UV_LINK_MODE=copy
# 非 root 執行後就寫不進 /root/.cache，快取改放自有目錄
ENV UV_CACHE_DIR=/cache/uv

# 以非 root 執行：容器被打穿時不會直接落地成 root。venv 與程式碼都在切換
# 使用者之後才建立／複製，所以不需要事後 chown -R 整個 /app（會多複製一層）。
RUN groupadd -r -g 1000 app \
    && useradd -r -u 1000 -g app -m -d /home/app -s /usr/sbin/nologin app \
    && mkdir -p /app /cache/uv \
    && chown -R app:app /app /cache

USER app

WORKDIR /app/

# Place executables in the environment at the front of the path
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#using-the-environment
ENV PATH="/app/.venv/bin:$PATH"

# Install dependencies
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/cache/uv,uid=1000,gid=1000 \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-workspace --package app

COPY --chown=app:app ./backend/scripts /app/backend/scripts

COPY --chown=app:app ./backend/pyproject.toml ./backend/alembic.ini /app/backend/
COPY --chown=app:app ./backend/config /app/backend/config

COPY --chown=app:app ./backend/app /app/backend/app

# Sync the project
# Ref: https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/cache/uv,uid=1000,gid=1000 \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --package app

# prestart.sh 由 compose 以 `bash scripts/prestart.sh` 啟動，但保留執行位以免
# 直接執行時失敗；logs/ 是 LOG_DIR 的預設相對路徑，要先建好給 app 寫。
RUN chmod +x /app/backend/scripts/*.sh \
    && mkdir -p /app/backend/logs

WORKDIR /app/backend/

# 單一 worker：教室 VNC session、presence、VNC ticket cookie 都是行程內狀態，
# 多 worker 會讓老師接管／直播觀看只在其中一個行程生效。排程器雖已有
# leader 鎖，但在這些狀態搬到 Redis 之前不要開多 worker；要橫向擴充請用
# 多個容器並把上述狀態外部化。
CMD ["fastapi", "run", "--workers", "1", "app/main.py"]
