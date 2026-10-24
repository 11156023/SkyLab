---
layout: page
title: REST API
sidebar: false
---

<div class="vp-doc api-intro">

# REST API

Every REST route is mounted under `/api/v1`. The reference below is generated from the backend's OpenAPI schema at build time, so it matches the commit this site was built from.

- **Authentication.** Obtain a bearer token with `POST /api/v1/login/access-token` (form fields `username`, `password`). Accounts with two-step verification receive a TOTP challenge token to finish with `POST /api/v1/login/totp`. Send the access token as `Authorization: Bearer <token>`; refresh it with `POST /api/v1/login/refresh-token` (JSON body `{"refresh_token": "…"}`). When Cloudflare Turnstile is enabled, password and LDAP logins also need an `X-Turnstile-Token` header, so scripts cannot log in with a password alone.
- **AI API.** `/api/v1/ai-proxy/{models,chat/completions,completions,responses}` accept a `ccai_*` credential instead of a user token and are OpenAI compatible. See the [AI API user manual](/guides/ai-api-user-manual#_6-requesting-and-calling-the-api-as-a-regular-user).
- **Live updates and consoles** use WebSocket, not REST: see [WebSocket endpoints](/reference/websocket).
- **Errors** are JSON objects with a `detail` field. Rate-limited requests return `429` with `Retry-After`.
- On a running deployment the interactive Swagger UI is available at `/docs` unless `ENVIRONMENT=production`, where the schema is intentionally not exposed.

</div>

<ApiReference />

<style>
.api-intro {
  max-width: 960px;
  margin: 0 auto;
  padding: 32px 24px 8px;
}
</style>
