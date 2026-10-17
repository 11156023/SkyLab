# 註冊設定與 Google 登入

## 後台的兩個註冊開關

管理員登入後，前往「使用者管理」頁面，可以調整兩個彼此獨立的開關：

- **允許自行註冊**：控制登入頁的註冊入口，以及新的信箱註冊和 Google 首次登入自動建立帳號。開啟時不限制信箱網域；關閉後現有帳號仍可登入，管理員仍可手動新增使用者。
- **註冊後需管理員審核**：關閉註冊時也會保留這個設定。開放註冊且啟用審核時，任何新帳號完成信箱驗證，或首次以 Google 登入，都會進入「待審核註冊」，管理員核准後才能登入，也可以拒絕。重新開放註冊時沿用當時的審核設定。

即使關閉人工審核，自行填寫信箱的註冊仍須點擊驗證郵件中的連結並設定密碼。Google 帳號首次登入也視為新註冊。切換審核開關只影響之後完成驗證或新建立的帳號；已在待審核清單中的帳號仍需管理員處理。

## 開放註冊前的準備

- 後端必須已設定可寄信的 SMTP，註冊開關才能啟用。
- 正式環境請先設定 Cloudflare Turnstile。
- 首次部署或更新後先執行資料庫遷移。
- 未曾在後台儲存設定時，「允許自行註冊」的初始值沿用 `.env` 的 `ENABLE_SIGNUP`；儲存後以後台設定為準。

## 設定 Google 登入

1. 在 Google Cloud Console 的 **Google Auth Platform → Clients（用戶端）** 建立或選用 Web application OAuth client，設定網站的 Authorized JavaScript origins。
2. 將 OAuth **Client ID** 填到 `.env` 的 `GOOGLE_CLIENT_ID`，重新建置前端並重啟後端。這不是 API key，也不需把 Client secret 放到前端。

後端會驗證 Google ID token 的 audience 與已驗證信箱，但不依學校或教育網域過濾帳號。哪些 Google 帳號可以授權，由 **Google Auth Platform → Audience（目標對象）** 決定：

- **Internal（內部）**：只有學校 Google Workspace／Cloud Identity 組織內的帳號可以登入。Google Cloud 專案必須屬於該組織，個人專案無法啟用內部模式。
- **External（外部）**：一般 Google 帳號（包含個人 Gmail）都能授權。要限制誰能使用，請搭配上面的「註冊後需管理員審核」。
