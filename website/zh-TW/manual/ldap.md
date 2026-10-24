# LDAP

LDAP 頁面設定以 LDAP 或 Active Directory 帳號登入。啟用後，登入頁會多出「校園帳號」分頁，使用者可以用學校目錄的帳號密碼登入平台。側欄：管理員設定 → LDAP。路徑：`/ldap`。

## 誰可以使用

只有管理員可以使用。

## 開始之前

- 目錄伺服器的位址（`ldap://` 或 `ldaps://`）。
- 一個有權搜尋使用者的服務帳號（Bind DN 與密碼）。
- 使用者項目所在的 Base DN，以及使用者登入時輸入的帳號對應哪個屬性（例如 Active Directory 的 `sAMAccountName` 或 OpenLDAP 的 `uid`）。
- 選填：成員要成為教師或管理員的群組 DN。
- 使用 `ldaps://` 或 StartTLS，且憑證是自簽或由校內私有 CA 簽發時，要先在伺服器上設定 CA（`LDAP_CA_CERT_FILE`），請見部署指南的 [LDAP / Active Directory over TLS](/zh-TW/guides/deployment)。

## 操作步驟

### 設定伺服器位址

1. 在「LDAP / Active Directory 登入」卡片輸入「伺服器 URI *」（例如 `ldap://dc.example.edu:389` 或 `ldaps://` 位址）。
2. 必要時調整「連線逾時（秒）」。
3. 使用 `ldap://` 位址時，勾選「使用 StartTLS」把連線加密；`ldaps://` 位址不需要。

![設定伺服器位址（圖中編號對應上方步驟）](/manual-shots/zh-TW/ldap/s01.webp)

### 設定服務帳號與使用者搜尋

1. 在「服務帳號與使用者搜尋」卡片輸入「服務帳號 DN（Bind DN）」（例如 `CN=svc-skylab,OU=Service,DC=example,DC=edu`）。
2. 輸入「服務帳號密碼」。已經儲存過密碼時，欄位會顯示「已設定（留空表示不變）」，留空即沿用。
3. 輸入「使用者搜尋 Base DN」（例如 `OU=Users,DC=example,DC=edu`）。
4. 輸入「使用者過濾範本」，例如 `(sAMAccountName={username})` 或 `(uid={username})`。`{username}` 會換成登入時輸入的帳號。
5. 輸入「Email 屬性」（例如 `mail`）與「姓名屬性」（例如 `displayName`）。

平台會先用服務帳號登入目錄，在 Base DN 底下依過濾範本找到使用者，再用對方輸入的密碼驗證。

![設定服務帳號與使用者搜尋（圖中編號對應上方步驟）](/manual-shots/zh-TW/ldap/s02.webp)

### 設定帳號建立與角色對映

1. 在「帳號建立與角色對映」卡片勾選「自動建立帳號」，讓第一次用 LDAP 登入的人自動取得帳號。
2. 視需要輸入「教師群組 DN（選填）」與「管理員群組 DN（選填）」。

新帳號預設為學生。教師群組的成員會成為教師，管理員群組的成員會成為管理員（同時屬於兩個群組時以管理員為準）。群組 DN 以完整字串比對，不分大小寫。

![設定帳號建立與角色對映（圖中編號對應上方步驟）](/manual-shots/zh-TW/ldap/s03.webp)

### 測試設定

1. 按「測試連線」。
2. 畫面會顯示服務帳號能否登入目錄。

測試使用表單上目前的值（包含尚未儲存的修改），不會儲存任何設定。

![測試設定（圖中編號對應上方步驟）](/manual-shots/zh-TW/ldap/s04.webp)

### 啟用 LDAP 登入

1. 測試成功後，在第一張卡片勾選「啟用 LDAP 登入」。
2. 按「儲存 LDAP 設定」。

![啟用 LDAP 登入（圖中編號對應上方步驟）](/manual-shots/zh-TW/ldap/s05.webp)

### 放棄未儲存的修改

1. 按「還原」（有修改時才會出現）。
2. 表單會回到上次儲存的值。

有未儲存的修改時離開頁面，會先跳出確認提醒。

![放棄未儲存的修改（圖中編號對應上方步驟）](/manual-shots/zh-TW/ldap/s06.webp)

## 欄位與選項

| 欄位 | 意義 | 備註 |
| --- | --- | --- |
| 啟用 LDAP 登入 | 在登入頁顯示「校園帳號」分頁 | 建議先測試 |
| 伺服器 URI * | 目錄伺服器位址 | 必填，最多 255 字元 |
| 連線逾時（秒） | 等待伺服器回應的時間 | 1–60 |
| 使用 StartTLS | 把 `ldap://` 連線升級為加密連線 | `ldaps://` 不需要 |
| 服務帳號 DN（Bind DN） | 用來搜尋使用者的服務帳號 | 最多 512 字元 |
| 服務帳號密碼 | 服務帳號的密碼 | 留空表示沿用已儲存的密碼；儲存後不會再顯示 |
| 使用者搜尋 Base DN | 搜尋使用者項目的位置 | 最多 512 字元 |
| 使用者過濾範本 | 找到使用者的過濾條件 | `{username}` 會換成輸入的帳號；最多 512 字元 |
| Email 屬性 | 存放使用者 email 的屬性 | 最多 64 字元；取得的值必須是有效的 email |
| 姓名屬性 | 存放顯示名稱的屬性 | 最多 64 字元 |
| 自動建立帳號 | 第一次用 LDAP 登入時建立帳號 | 關閉後僅已存在的本地帳號可用 LDAP 登入 |
| 教師群組 DN（選填） | 成員會成為教師的群組 | 最多 512 字元 |
| 管理員群組 DN（選填） | 成員會成為管理員的群組 | 最多 512 字元 |

## 限制與注意事項

- 修改「伺服器 URI」或「服務帳號 DN（Bind DN）」時，必須重新輸入「服務帳號密碼」，否則儲存與測試都會被拒絕。
- `ldaps://` 與 StartTLS 一律驗證伺服器憑證與主機名稱：伺服器 URI 裡的主機必須列在憑證的 subjectAltName。私有 CA 要在伺服器上設定，請見[部署指南](/zh-TW/guides/deployment)。
- LDAP 帳號每次透過 LDAP 登入都會依群組重新決定角色，在[使用者管理](/zh-TW/manual/admin-users)手動改的角色會被蓋回。未填管理員群組時，已經是管理員的帳號不會被降級。
- 有人用和既有本地帳號相同的 email 透過 LDAP 登入時，該帳號會轉為 LDAP 帳號，之後密碼改由目錄管理。
- LDAP 帳號沒有本地密碼：在[使用者管理](/zh-TW/manual/admin-users)會帶「LDAP」標籤，密碼欄位也會鎖住。
- Email 屬性取得的值不是有效的 email 時，登入會被拒絕，請檢查「Email 屬性」。
- 已綁定兩步驟驗證的帳號，輸入目錄密碼後仍需要輸入驗證碼。
- 已停用的帳號也無法透過 LDAP 登入。

## 相關頁面

- [使用者管理](/zh-TW/manual/admin-users) — LDAP 帳號、角色與停用帳號
- [登入](/zh-TW/manual/login) — 使用者看到的「校園帳號」分頁
- [稽核日誌](/zh-TW/manual/audit) — LDAP 登入失敗與設定變更都有紀錄
- [部署指南](/zh-TW/guides/deployment) — LDAP / Active Directory over TLS 與 `LDAP_CA_CERT_FILE`
