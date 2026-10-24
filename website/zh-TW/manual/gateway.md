# 閘道 VM

閘道 VM（Gateway VM）是 SkyLab 對外的那台機器：它的 nginx 負責 Port 轉發、機器網域（網址）與平台入口，並執行 SkyLab Connect 桌面端使用的 WireGuard VPN。這一頁全程透過 SSH 管理它。側欄：管理員設定 → 閘道 VM。路徑：`/gateway`。

## 誰可以使用

只有管理員。老師與學生看不到這一頁；他們在 [防火牆](/zh-TW/manual/firewall) 頁發布服務、或用桌面端連線時，用到的就是這裡設定好的功能。

## 開始之前

- 準備一台 Debian 13（trixie）主機當作 Gateway，SkyLab 後端要能以 SSH 連到它。一鍵安裝會拒絕其他系統，Debian 12 也不行。
- 主機上要有一個 SSH 帳號，必須是 root，或能免密碼 sudo。
- Gateway VM 的 IP 應與 [IP 管理](/zh-TW/manual/ip-management) 頁填寫的一致。
- 要用 HTTPS 時，請自行準備憑證（fullchain 與私鑰）；SkyLab 不會簽發憑證。

第一次設定請依序完成「連線設定」與「安裝服務」兩個分頁，需要時再設定「HTTPS 憑證」與「平台入口」。

## 操作步驟

### 以 SSH 連上 Gateway

1. 開啟「連線設定」分頁。
2. 在「SSH 連線設定」填入「Host / IP」、「SSH Port」（預設 22）與「SSH 使用者」（預設 `root`），按「儲存設定」。
3. 在「SSH 公鑰」卡按「重新產生 Keypair」，產生平台專用的金鑰，公鑰會顯示在卡片中。
4. 按「複製」，把公鑰加到 Gateway 上該使用者的 `~/.ssh/authorized_keys`。
5. 回到「SSH 連線設定」按「測試連線」。測試的是已儲存的設定，改過的內容要先儲存。

重新產生 Keypair 後舊金鑰會立即失效；系統會先請你確認，之後必須把新公鑰再加到 Gateway 一次。

![以 SSH 連上 Gateway（圖中編號對應上方步驟）](/manual-shots/zh-TW/gateway/s01.webp)

### Gateway 重灌後重設 Host Key

1. 只在 Gateway 重灌或換機、因 host key 改變導致連線被拒時使用。
2. 按「重設 Host Key」，再按「重設」確認。平台記錄的 host key 會被清除，下次連線時重新記錄。

如果 host key 在你不知情的情況下改變，請先查明原因，這可能是中間人攻擊的跡象。

![Gateway 重灌後重設 Host Key（圖中編號對應上方步驟）](/manual-shots/zh-TW/gateway/s02.webp)

### 安裝 Gateway 服務

1. 開啟「安裝服務」分頁，頁面會經 SSH 讀取 Gateway 的安裝狀態。
2. 檢查狀態卡：狀態徽章、「nginx」、「WireGuard」、「UFW」哪些已安裝、WireGuard 介面與網段、「作業系統」，以及上次安裝的開始與結束時間。
3. 檢查「安裝參數」，欄位已帶入在 Gateway 上偵測到的值；按「套用偵測值」可恢復成偵測值。
4. 按「一鍵安裝」（服務已存在時為「重新安裝」），再按「開始安裝」確認。
5. 安裝會在 Gateway 背景執行，約需數分鐘，可以離開此頁。安裝期間「安裝日誌」每 3 秒更新一次。
6. 完成後會自動把網域與 Port 轉發規則同步回 Gateway。之後需要重新推送時，按「同步規則到 Gateway」。

安裝會安裝 nginx、WireGuard 與監控 exporter，重寫 `nginx.conf` 並啟用 UFW（先放行 SSH）。原有設定會先備份到 `/root/skylab-backups`；nginx 重啟期間，既有的轉發連線會中斷。

![安裝 Gateway 服務（圖中編號對應上方步驟）](/manual-shots/zh-TW/gateway/s03.webp)

### 設定 HTTPS 憑證

1. 把憑證（fullchain）與私鑰放到 Gateway 主機上，建議放在 `/etc/ssl/skylab/`，且私鑰只給 root 讀。建議使用涵蓋整個網域的萬用憑證（例如 `example.com` 與 `*.example.com`）。
2. 開啟「HTTPS 憑證」分頁。
3. 填入「憑證路徑（fullchain）」與「私鑰路徑」，按「儲存並套用」。平台會在 Gateway 上檢查檔案並重新載入 nginx。
4. 查看「Gateway 上的憑證」卡：「到期日」、「私鑰」（是否與憑證相符）、「憑證涵蓋」與「使用中的 HTTPS 網域」，下方會列出所有警告。

換新憑證時，直接覆蓋同路徑的檔案，再按「重新套用」。不再使用憑證時，把兩個路徑都清空後儲存；確認後所有 HTTPS 網域會改用自簽憑證，瀏覽器會顯示警告。

![設定 HTTPS 憑證（圖中編號對應上方步驟）](/manual-shots/zh-TW/gateway/s04.webp)

### 讓主系統經 Gateway 對外（平台入口）

1. 開啟「平台入口」分頁。
2. 勾選「讓 SkyLab 主系統經 Gateway 的 nginx 對外」。
3. 填入「主系統網域」（例如 `skylab.example.com`）、「主系統位址」（Gateway 連得到的部署機，填 IPv4 位址或主機名稱，不含 Port）與「主系統 Port」（預設 8082）。
4. 保留勾選「啟用 HTTPS」即可使用「HTTPS 憑證」分頁設定的憑證；尚未設定憑證時，警告訊息會提供「前往 HTTPS 憑證」。
5. 使用者要經 Cloudflare 連到 Gateway 時，勾選「經由 Cloudflare Proxy（橘色雲）」。
6. 可以先按「測試上游」確認 Gateway 連得到主系統，這一步不會儲存。
7. 按「儲存並套用」。Gateway 會先連一次主系統，連不到就不會套用；連得到才寫進 nginx，並只重新載入 nginx，進行中的連線不會中斷。
8. 查看「Gateway 套用狀態」卡，並逐項完成「啟用後還要手動完成」中的事項。

如果網域屬於 Cloudflare 管理的 zone（在 [網域管理](/zh-TW/manual/domain) 設定），儲存時也會把它的 DNS 紀錄指向預設 DNS 目標；否則請自己把網域的 DNS 指到 Gateway 的對外 IP。

要停用平台入口，取消勾選第一個選項後儲存，再按「停用」確認。之後就無法從該網域連到 SkyLab，SkyLab 在 Cloudflare 建立的 DNS 紀錄也會一併刪除。停用前請先確認還能直接連到部署機。

![讓主系統經 Gateway 對外（平台入口）（圖中編號對應上方步驟）](/manual-shots/zh-TW/gateway/s05.webp)

### 管理 nginx 與編輯主設定檔

1. 開啟「nginx」分頁。
2. 狀態卡顯示「運行中」或「已停止」與服務狀態文字，可按「啟動」、「停止」、「重啟」或「Reload」。停止 nginx 會中斷所有轉發連線。
3. 編輯器只開放 `/etc/nginx/nginx.conf`。修改後按「寫入設定檔」（或按 Ctrl+S）。寫入前狀態列顯示「● 未寫入」，寫入後顯示「已同步」。
4. 檔案會先經 `nginx -t` 檢查，設定有誤會被還原。寫入後不會自動生效，要再按「Reload」。
5. 下方的「服務日誌（最近 100 行）」每 10 秒更新一次。

SkyLab 自動產生的轉發規則放在另外的檔案，不在這裡編輯。讀不到遠端設定檔時，編輯與寫入會被停用；SSH 連線恢復後，在編輯器中按「重新載入」。

![管理 nginx 與編輯主設定檔（圖中編號對應上方步驟）](/manual-shots/zh-TW/gateway/s06.webp)

### 查看 WireGuard VPN

1. 開啟「WireGuard VPN」分頁。
2. 狀態卡顯示服務是否運行，可按「啟動」、「停止」與「重啟」。
3. 四個數字分別是「有效授權連線」、「已載入 Peer」、「3 分鐘內握手」與「接收 / 傳送流量」。有已到期的工作階段等待清理時會出現提示。
4. 「VPN 設定摘要」列出「通道模式」、「介面」、「Systemd 服務」、「公開端點」、「用戶端網段」、「VM 網段」、「工作階段期限」、「Peer 自動同步」與「監聽 Port」。
5. 最下方是「服務日誌（最近 100 行）」。

這個分頁只顯示彙整數字，不會向瀏覽器回傳 Gateway 私鑰或任何 Peer 公鑰，也不會列出個別裝置。

![查看 WireGuard VPN（圖中編號對應上方步驟）](/manual-shots/zh-TW/gateway/s07.webp)

## 欄位與選項

| 欄位 | 意義 | 備註 |
| --- | --- | --- |
| Host / IP | SSH 連線用的 Gateway 位址 | 必填 |
| SSH Port | Gateway 的 SSH Port | 預設 22 |
| SSH 使用者 | SSH 使用的帳號 | 預設 `root`；要安裝必須是 root 或能免密碼 sudo |
| 對外網卡（WireGuard 入口） | 面向網際網路的網卡 | 不能與 VM 內網網卡相同 |
| VM 內網網卡 | 接在實驗室子網的網卡 | 選定後會自動帶入 SNAT 位址 |
| SNAT 位址 | VM 內網網卡上的 IP | 必須是 IPv4 位址 |
| WireGuard 監聽 Port（UDP） | VPN 監聽的 Port | 預設 51821；不能落在 Port 轉發範圍內 |
| Port 轉發起始／結束 | 轉發給機器的對外 Port 範圍 | 預設 30000–39999；起始不能大於結束 |
| 監控放行來源 | 允許讀取 exporter 的 Prometheus 主機 IP 或 CIDR | 以逗號分隔 |
| 憑證路徑／私鑰路徑 | 檔案在 Gateway 上的完整路徑 | 兩個一起填或一起清空；以 `/` 開頭，不可含空白、分號或引號 |
| 主系統網域 | 使用者要開啟的網域 | 啟用時必填 |
| 主系統位址／主系統 Port | Gateway 轉送主系統流量的目標 | 啟用時位址必填；Port 1–65535，預設 8082 |
| 啟用 HTTPS | 平台入口以 HTTPS 提供 | 預設勾選；需要先設定憑證 |
| 經由 Cloudflare Proxy（橘色雲） | 使用者先連到 Cloudflare，Gateway 的 IP 不公開 | 有開 HTTPS 時，Cloudflare 的 SSL/TLS 模式請設為 Full (strict) |

## 限制與注意事項

- 在儲存主機位址並產生 Keypair 之前，除了「連線設定」以外的分頁都只會顯示空狀態與「前往連線設定」。
- 安裝需要 root 或免密碼 sudo；不符合時會顯示警告，「一鍵安裝」維持停用。
- nginx 編輯器、憑證表單或平台入口表單有未儲存的變更時切換分頁，會先要求你按「捨棄變更」確認。
- 憑證卡會在以下情況發出警告：尚未設定憑證、讀不到檔案或不是有效的 PEM、私鑰與憑證不符、憑證已過期或 14 天內到期，以及使用中的 HTTPS 網域不在涵蓋範圍內。
- Gateway 上的設定和這裡儲存的不一致時（例如重裝過），平台入口徽章會顯示「與 Gateway 不一致」，按「儲存並套用」重新寫入即可。
- 啟用平台入口後，要在部署機的 `.env` 把 `SKYLAB_TRUSTED_PROXY` 設為 Gateway 的位址並重啟 nginx 容器。沒設定的話，後端看到的來源 IP 全是 Gateway，限流與稽核日誌都會失準。
- 也要把 `FRONTEND_HOST`、Google 登入的授權來源與 Turnstile 的網域清單改成新的 https 網址，並保留 Gateway 故障時能直連部署機的方式。

## 相關頁面

- [IP 管理](/zh-TW/manual/ip-management) — Gateway VM IP 與實驗室子網
- [網域管理](/zh-TW/manual/domain) — Cloudflare 帳號、Zone 與預設 DNS 目標
- [防火牆](/zh-TW/manual/firewall) — 以網址或對外 Port 發布機器上的服務
- [部署指南](/zh-TW/guides/deployment) — 平台入口、HTTPS 憑證與 CI 部署
- [WireGuard 桌面端架構](/zh-TW/guides/wireguard-desktop-architecture) — 桌面端如何使用 VPN
