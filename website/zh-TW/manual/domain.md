# 網域管理

網域管理把 SkyLab 連上你的 Cloudflare 帳號，讓你瀏覽與修改各個 Zone 的 DNS 紀錄。機器能以自己的網址發布服務、平台入口能自動建立 DNS 紀錄，靠的都是這裡的連線。側欄：管理員設定 → 網域管理。路徑：`/domain`。

## 誰可以使用

只有管理員。老師與學生不會開啟這一頁；他們在 [防火牆](/zh-TW/manual/firewall) 頁以網址發布服務時，用的就是這裡設定好的 Zone。

## 開始之前

- 準備一個 Cloudflare 帳號（裡面有要讓 SkyLab 使用的網域，也就是 Zone），以及它的「Account ID」。
- 在 Cloudflare 建立一組 API Token，賦予 SkyLab 讀取 Zone 與管理 DNS 紀錄所需的權限，不要給多餘的權限。
- 要用網址發布服務時，Gateway VM 也必須設定好（見 [閘道 VM](/zh-TW/manual/gateway)），並先確認 Gateway 的對外位址，它就是預設 DNS 目標。

## 操作步驟

### 連上 Cloudflare

1. 開啟頁面。尚未設定時會顯示「尚未連線 Cloudflare」。
2. 按「連線設定」。
3. 填入「Account ID」，並貼上「API Token」。欄位旁的「前往 Cloudflare 建立 Token」會開啟 Cloudflare 的 Token 頁面。
4. 設定「預設 DNS Target 類型」（「A（IP 位址）」或「CNAME（主機名）」）與「預設 DNS Target 值」，例如 Gateway 的對外 IP 或主機名稱。SkyLab 建立的 DNS 紀錄都會指向這裡。
5. 按「儲存」，Zone 清單隨即載入。
6. 按頁首的「測試連線」檢查 Token。這只會驗證設定是否有效，不會修改任何 DNS 紀錄。

頁首下方的狀態列會顯示「已連線」、帳號（縮短顯示，滑鼠移上去可看完整內容）與上次驗證的時間。

之後要修改設定時，按頁首的「連線設定」。「API Token」留空表示沿用目前的 Token。

![連上 Cloudflare（圖中編號對應上方步驟）](/manual-shots/zh-TW/domain/s01.webp)

### 瀏覽 Zone 的 DNS 紀錄

1. 左側的「Zones」清單列出帳號中的每個網域與其狀態，標題中的數字是網域數量。
2. 點選一個 Zone，右側就會顯示它的 DNS 紀錄。
3. 每筆紀錄顯示名稱，下一行是類型、內容、TTL（TTL 為 1 時顯示 Auto）與備註。
4. 徽章說明紀錄的來源與服務方式：
   - 「本系統 · VM」加上 VMID：該機器以網址發布服務時由 SkyLab 建立。
   - 「外部建立」：在 Cloudflare 或其他地方建立，不是 SkyLab 建的。
   - 「Proxied」或「DNS only」：流量是否經過 Cloudflare。
5. 要找某筆紀錄時，在搜尋框輸入名稱的一部分並按 Enter。

![瀏覽 Zone 的 DNS 紀錄（圖中編號對應上方步驟）](/manual-shots/zh-TW/domain/s02.webp)

### 新增 DNS 紀錄

1. 選好 Zone 後按「新增紀錄」。
2. 選擇「類型」（A、AAAA、CNAME、TXT、MX 或 NS），設定「TTL」（1 代表 Auto）。
3. 填入「名稱」（例如 `www` 或 `www.example.com`）與「內容」（例如 IP 位址）。
4. MX 紀錄還要填「優先順序」。
5. 視需要填寫「備註」，並勾選「經由 Cloudflare Proxy（橘色雲）」。
6. 按「儲存」。

![新增 DNS 紀錄（圖中編號對應上方步驟）](/manual-shots/zh-TW/domain/s03.webp)

### 編輯或刪除 DNS 紀錄

1. 按紀錄上的編輯按鈕，修改欄位後按「儲存」。清空「備註」會移除備註。
2. 按紀錄上的刪除按鈕，再按「刪除」確認。此操作無法復原。

![編輯或刪除 DNS 紀錄（圖中編號對應上方步驟）](/manual-shots/zh-TW/domain/s04.webp)

### 讓機器擁有自己的網址

機器網址不是在這一頁建立的。機器擁有者（或其老師、管理員）要在 [防火牆](/zh-TW/manual/firewall) 頁或機器的詳情頁發布：

1. 選「開放服務給外部」，再在「怎麼開放」選「用網址」。
2. 填寫服務所在的 port、網址開頭，並以這一頁的某個 Zone 做為網址結尾，決定是否啟用 HTTPS。
3. SkyLab 會確認網址沒有被使用，在 Zone 中建立指向預設 DNS 目標的 DNS 紀錄，並把轉發規則加到 Gateway 的 nginx。HTTPS 使用閘道 VM 頁設定的憑證。

新的紀錄之後會出現在這裡，並帶有「本系統 · VM」徽章。在防火牆頁刪除那條連線時，它的 DNS 紀錄也會一併刪除。

![讓機器擁有自己的網址（圖中編號對應上方步驟）](/manual-shots/zh-TW/domain/s05.webp)

## 欄位與選項

| 欄位 | 意義 | 備註 |
| --- | --- | --- |
| Account ID | Cloudflare 帳號 ID | 最長 128 字元 |
| API Token | SkyLab 呼叫 Cloudflare 用的 Token | 第一次必填（至少 20 字元）；之後留空表示沿用已儲存的 Token |
| 預設 DNS Target 類型 | 「A（IP 位址）」或「CNAME（主機名）」 | 以網址發布服務時必須設定 |
| 預設 DNS Target 值 | SkyLab 建立的紀錄所指向的 IP 或主機名稱 | 通常是 Gateway 的對外位址 |
| 類型 | DNS 紀錄類型 | A、AAAA、CNAME、TXT、MX、NS；編輯其他類型的既有紀錄時會保留原類型 |
| 名稱 | 紀錄名稱 | 必填 |
| 內容 | 紀錄的值，例如 IP 位址 | 必填 |
| TTL | 存活時間（秒） | 1 代表 Auto |
| 優先順序 | MX 優先順序 | 只有 MX 需要，0–65535，預設 10 |
| 備註 | 隨紀錄存在 Cloudflare 的文字 | 選填 |
| 經由 Cloudflare Proxy（橘色雲） | 讓紀錄經 Cloudflare 提供服務 | 預設不勾選 |

## 限制與注意事項

- 必須設定好 Cloudflare Token 與預設 DNS 目標、至少有一個啟用中的 Zone，而且 Gateway 已就緒，使用者才能用網址發布服務；否則發布對話框會說明缺少什麼。
- 名稱已被其他機器使用、是平台入口的網域，或 Zone 中已有同名紀錄（不論是不是 SkyLab 建的）時，都無法發布該網址。
- 這一頁最多載入 50 個 Zone，每個 Zone 最多顯示 100 筆紀錄；找不到的紀錄請用搜尋。
- 在這裡做的修改會直接寫入 Cloudflare。編輯或刪除標示「本系統」的紀錄，會改變那台機器發布網址的 DNS。
- 閘道 VM 頁的平台入口，也可能在這裡管理的 Zone 中建立 DNS 紀錄。

## 相關頁面

- [防火牆](/zh-TW/manual/firewall) — 以網址發布機器上的服務
- [閘道 VM](/zh-TW/manual/gateway) — HTTPS 憑證與平台入口
- [資源詳情](/zh-TW/manual/resource-detail) — 機器的對外服務
- [部署指南](/zh-TW/guides/deployment) — 平台入口與 HTTPS 憑證
