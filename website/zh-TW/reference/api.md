---
layout: page
title: REST API
sidebar: false
---

<div class="vp-doc api-intro">

# REST API

所有 REST 路由都掛在 `/api/v1` 下。下方的參考在建置時由後端的 OpenAPI schema 產生，因此與本站建置時的 commit 一致。

- **認證。** 以 `POST /api/v1/login/access-token`（表單欄位 `username`、`password`）取得 bearer token。啟用兩步驟驗證的帳號會收到 TOTP 挑戰 token，再以 `POST /api/v1/login/totp` 完成。之後以 `Authorization: Bearer <token>` 帶上存取 token，用 `POST /api/v1/login/refresh-token`（JSON 內容 `{"refresh_token": "…"}`）更新。啟用 Cloudflare Turnstile 時，密碼與 LDAP 登入還要帶 `X-Turnstile-Token` 標頭，所以腳本無法只靠密碼登入。
- **AI API。** `/api/v1/ai-proxy/{models,chat/completions,completions,responses}` 接受 `ccai_*` 憑證而非使用者 token，並與 OpenAI 相容。見 [AI API 使用與部署手冊](/zh-TW/guides/ai-api-user-manual#_6-一般使用者申請與呼叫-api)。
- **即時更新與主控台**走 WebSocket 而非 REST：見 [WebSocket 端點](/zh-TW/reference/websocket)。
- **錯誤**是帶 `detail` 欄位的 JSON 物件。被速率限制的請求回 `429` 並附 `Retry-After`。
- 在運行中的部署上，互動式 Swagger UI 位於 `/docs`，但 `ENVIRONMENT=production` 時刻意不對外提供 schema。

</div>

<ApiReference />

<style>
.api-intro {
  max-width: 960px;
  margin: 0 auto;
  padding: 32px 24px 8px;
}
</style>
