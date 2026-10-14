# Google 登入與公開註冊

Google Cloud Console → **Google Auth Platform → Audience（目標對象）** 控制哪些 Google 帳號可以授權 OAuth 應用。若選 **Internal（內部）**，Google Cloud 專案必須屬於學校的 Google Workspace／Cloud Identity 組織；個人專案無法啟用內部模式。若維持 **External（外部）**，Google 端允許一般 Google 帳號授權。本網站後端不再依 NTUB 或教育網域過濾帳號。

到 **Google Auth Platform → Clients（用戶端）** 建立或選用 Web application OAuth client，設定網站的 Authorized JavaScript origins。將 OAuth **Client ID** 填到 `.env` 的 `GOOGLE_CLIENT_ID`，重新建置前端並重啟後端。這不是 API key，也不需把 Client secret 放到前端。後端仍會驗證 Google ID token 的 audience 與已驗證信箱。

管理員後台「使用者管理」有「允許自行註冊」與「註冊後需管理員審核」兩個獨立開關。註冊開啟時，任何有效信箱都可申請；一般信箱註冊須先點驗證信連結並設定密碼。Google 帳號首次登入也視為新註冊。審核開啟時，兩種來源的新帳號都要等待管理員核准，包含 NTUB 帳號。關閉註冊只會阻止新申請，不會改變審核設定；重新開放註冊時沿用該設定。已在待審核清單中的帳號仍需管理員處理。

正式環境開放註冊前，請先設定可寄信的 SMTP 與 Turnstile。部署更新後需執行資料庫遷移。
