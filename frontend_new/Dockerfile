# Stage 0, "build-stage": 用 Bun 編譯 frontend_new
# 注意：build context 必須是專案根目錄（vite.config.js 的 templatesPlugin
# reads ../backend/app/ai/template_recommendation/catalog_json)
FROM oven/bun:1 AS build-stage

WORKDIR /app/frontend_new

COPY frontend_new/package.json frontend_new/bun.lock ./

RUN bun install

# Template JSON used by templatesPlugin.
COPY backend/app/ai/template_recommendation/catalog_json /app/backend/app/ai/template_recommendation/catalog_json

COPY ./frontend_new /app/frontend_new

ARG VITE_API_URL
ARG VITE_GOOGLE_CLIENT_ID
ARG ENABLE_SIGNUP
ENV ENABLE_SIGNUP=${ENABLE_SIGNUP}

RUN bun run build


# Stage 1: 用 Nginx 提供編譯後的靜態檔
FROM nginx:1

COPY --from=build-stage /app/frontend_new/dist/ /usr/share/nginx/html

COPY ./frontend_new/nginx.conf /etc/nginx/conf.d/default.conf
