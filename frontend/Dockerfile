# Stage 0, "build-stage": 用 Bun 編譯 frontend
# 注意：build context 為專案根目錄（COPY 路徑以根目錄為準）
FROM oven/bun:1 AS build-stage

WORKDIR /app

# 前端是根目錄 bun workspace 的成員：鎖檔與 overrides 都在根 package.json／bun.lock。
# 與 CI 用同一份鎖檔並 --frozen-lockfile，正式映像裝到的版本才會和測過的一致，
# 不會在建映像時重新解析。
COPY package.json bun.lock ./
COPY frontend/package.json ./frontend/

RUN bun install --frozen-lockfile

COPY ./frontend /app/frontend

WORKDIR /app/frontend

ARG VITE_API_URL
ARG VITE_GOOGLE_CLIENT_ID
ARG ENABLE_SIGNUP
# 瀏覽器端 Sentry；VITE_SENTRY_DSN 留空時 SDK 不會被載入
ARG VITE_SENTRY_DSN
ARG VITE_SENTRY_ENVIRONMENT
ARG VITE_SENTRY_RELEASE
# 「關於」分頁顯示的 commit：build context 沒有 .git，由 compose／CI 帶入
ARG SOURCE_COMMIT
ENV ENABLE_SIGNUP=${ENABLE_SIGNUP} SOURCE_COMMIT=${SOURCE_COMMIT}

RUN bun run build


# Stage 1: 用 Nginx 提供編譯後的靜態檔
# 非 root 版 nginx（以 uid 101 執行、預設聽 8080）；靜態檔只需讀取權限，
# COPY 由 builder 以 root 寫入不受 USER 影響。上游 nginx/default.conf.template 與
# compose 的 port mapping 都要對到 8080。
FROM nginxinc/nginx-unprivileged:1

COPY --from=build-stage /app/frontend/dist/ /usr/share/nginx/html

COPY ./frontend/nginx.conf /etc/nginx/conf.d/default.conf
