---
description: "「使用者管理」頁的三個註冊開關（自行註冊、Google 首次登入自動建帳、管理員審核）、開放註冊前的準備，以及如何設定 Google 登入。"
---

# 註冊設定與 Google 登入

## 後台的三個註冊開關

管理員登入後，前往「使用者管理」頁面，可以調整三個彼此獨立的開關：

- **允許自行註冊**：控制登入頁的註冊入口與新的信箱註冊。開啟時不限制信箱網域；關閉後現有帳號仍可登入，管理員仍可手動新增使用者。
- **允許 Google 首次登入自動建帳**：控制還沒有 SkyLab 帳號的 Google 信箱首次以 Google 登入時，是否自動建立學生帳號。這個開關與信箱註冊分開，也不需要 SMTP 或 Turnstile，因為信箱已由 Google 驗證。關閉時既有帳號仍可用 Google 登入，首次登入的 Google 帳號則會看到「尚未註冊」。只有在設定了 Google 登入時才有作用（見下文）。
- **註冊後需管理員審核**：上面兩個開關都關閉時也會保留這個設定。啟用審核時，任何新帳號完成信箱驗證，或由 Google 首次登入自動建立，都會進入「待審核註冊」，管理員核准後才能登入，也可以拒絕。重新開放註冊時沿用當時的審核設定。

即使關閉人工審核，自行填寫信箱的註冊仍須點擊驗證郵件中的連結並設定密碼。開啟自動建帳時，Google 帳號首次登入也視為新註冊。切換審核開關只影響之後完成驗證或新建立的帳號；已在待審核清單中的帳號仍需管理員處理。

## 開放註冊前的準備

- 後端要能寄信（設好 `SMTP_HOST` 與 `EMAILS_FROM_EMAIL`），「允許自行註冊」才能啟用。不是 `local` 環境時，還要填好兩把 Cloudflare Turnstile 金鑰，而且 SMTP 不能是 `mailcatcher`；`ENABLE_SIGNUP=true` 卻缺這些設定時，後端會拒絕啟動（見[部署指南](/zh-TW/guides/deployment#_2-環境變數)）。Google 開關沒有這些限制。
- 未曾在後台儲存設定時，「允許自行註冊」的初始值沿用 `.env` 的 `ENABLE_SIGNUP`（上述條件不齊時一律關閉），Google 開關也從同一個值開始；儲存後以後台設定為準。
- 從還沒有 Google 開關的版本升級時，對應的 migration 會把「允許自行註冊」當時的值複製過去，升級不會改變誰能登入。

## 設定 Google 登入

1. 在 Google Cloud Console 的 **Google Auth Platform → Clients（用戶端）** 建立或選用 Web application OAuth client，設定網站的 Authorized JavaScript origins。
2. 將 OAuth **Client ID** 填到 `.env` 的 `GOOGLE_CLIENT_ID`，再重新建置前端並重建後端（`docker compose up -d --build frontend backend`）。這不是 API key，也用不到 Client secret。

後端會驗證 Google ID token 的 audience 與已驗證信箱，但不依學校或教育網域過濾帳號。哪些 Google 帳號可以授權，由 **Google Auth Platform → Audience（目標對象）** 決定：

- **Internal（內部）**：只有學校 Google Workspace／Cloud Identity 組織內的帳號可以登入。Google Cloud 專案必須屬於該組織，個人專案無法啟用內部模式。
- **External（外部）**：一般 Google 帳號（包含個人 Gmail）都能授權。要限制誰能使用，請搭配上面的「註冊後需管理員審核」，或關閉「允許 Google 首次登入自動建帳」改由管理員手動建立帳號。
