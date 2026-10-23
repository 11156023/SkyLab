# WebSocket 端點

主控台與即時更新使用直接註冊在應用程式上的 WebSocket 連線（不屬於 `/api/v1` router，因此不在 OpenAPI schema 裡）。nginx 會把 `/ws` 下的所有連線轉給後端。

| 路徑 | 用途 | 認證 |
| --- | --- | --- |
| `/ws/vnc/{vmid}` | VNC 主控台。後端向 Proxmox 開一張 VNC ticket 並代理 RFB 串流，瀏覽器端是 noVNC。機器的顯示設定為 `clipboard=vnc` 時可以傳剪貼簿。 | 存取 token 放在 `token` 查詢參數 |
| `/ws/terminal/{vmid}` | LXC 容器的終端機。後端向容器所在的 Proxmox 節點開一張終端機 ticket（termproxy），再把那條 websocket 橋接到 xterm.js。 | 存取 token 放在 `token` 查詢參數 |
| `/ws/jobs` | 呼叫者自己的任務紀錄（克隆、批次建置、重置、刪除）即時狀態，供任務頁使用。 | 存取 token 放在 `token` 查詢參數 |
| `/ws/classroom` | 教室會話的信令通道：老師廣播、學生畫面分送與控制。 | 存取 token 放在 `token` 查詢參數 |
| `/ws/classroom/{session_id}/watch` | 把一位學生的畫面以 VNC 分送到老師的教室畫面。 | 存取 token 放在 `token` 查詢參數 |
| `/ws/courses/paths/{path_id}/progress` | 某條課程路徑的進度更新，供老師儀表板使用。 | 存取 token 放在 `token` 查詢參數 |

注意事項：

- token 與 REST 呼叫用的是同一個 JWT。因為瀏覽器無法在 WebSocket 交握時設定標頭，所以放在查詢字串。
- 呼叫者對機器沒有使用權限時，後端拒絕主控台連線；個人機器已到期或不在核准的使用時段內時也一樣拒絕。機器仍在啟動中（GPU 機器在 QEMU monitor 就緒前回報 `starting`）時，介面會停用主控台按鈕。
- 瀏覽器與後端之間的代理必須允許長連線；隨附的 nginx 設定已經處理。
- 後端裡每個代理跑兩個 pump 任務，共用一個 `asyncio.Event` 通知斷線。新增 WebSocket 端點的貢獻者請沿用同一模式，見[後端](/zh-TW/guides/backend#api-概覽)。
