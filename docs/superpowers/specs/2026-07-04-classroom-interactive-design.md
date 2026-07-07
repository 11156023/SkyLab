# 模組 B：虛擬教室互動系統 — 設計文件

日期：2026-07-04
狀態：已核准（使用者確認：單上游 fan-out、接管封鎖學生輸入、專用 classroom WebSocket）

## 目標

打破 VM 孤島：老師可實時觀看/接管任何在線學生的 VM 畫面；老師可將自己的 VM 設為
「直播模式」，全班學生端同步唯讀顯示。後端強制寫入攔截（write-block），所有連線經
JWT 驗證。

## 架構總覽

核心為 `VncSessionManager`（`services/classroom/`）：每個「被觀看的 VM」只開一條
上游 RFB 連線到 PVE，後端解析 RFB 訊息邊界後把 framebuffer 更新複製給 N 個下游訂閱
者。下游（瀏覽器 noVNC）不直連 PVE，而是與後端完成簡化 RFB server 握手
（security type = None；JWT 已在 WS 層驗完身分）。

```
學生A noVNC ─┐
學生B noVNC ─┼─ /ws/classroom/{session}/watch ─→ VncSessionManager ─→ 單一 RFB client ─→ PVE vncwebsocket ─→ QEMU
老師  noVNC ─┘      (下游 RFB server 握手)         (fan-out + 控制權 token)   (Hextile 編碼)
```

### 編碼選擇（關鍵取捨）

上游一律協商 **Hextile + CopyRect + Raw + DesktopSize**，明確不用 Tight/ZRLE：
Tight/ZRLE 的 zlib 壓縮流跨訊息有狀態，中途加入的訂閱者無法解碼；Hextile 每個矩形
無狀態、可安全 fan-out、noVNC 原生支援。新訂閱者加入時管理器向上游發一次全量
FramebufferUpdateRequest（keyframe），新加入者最多有 <1 秒的畫面補齊期（期間收到的
增量更新只是畫面不完整，不會破壞協議）。

不協商 cursor pseudo-encoding：讓 QEMU 把游標畫進 framebuffer，觀看者能看到老師
的游標位置（教學需求）。

## 元件劃分（Routes → Services → Infrastructure）

### Infrastructure — `infrastructure/vnc/`（純 RFB 協議，不含業務）

- `rfb_client.py`：上游握手 — RFB 3.8 版本協商、VNC auth（用 PVE vncticket 做 DES
  challenge-response）、ClientInit(shared=1)、接收 ServerInit、送 SetPixelFormat
  （固定 32bpp true color，同 noVNC 預設）與 SetEncodings。
- `rfb_server.py`：下游握手 — 版本協商、security types 只提供 None(1)、
  SecurityResult OK、讀 ClientInit、用上游快取的寬高/像素格式/名稱送 ServerInit。
- `messages.py`：兩個方向的串流訊息切框器：
  - server→client：解析 FramebufferUpdate（Raw/CopyRect/Hextile/DesktopSize 各編碼
    的矩形長度）、SetColourMapEntries、Bell、ServerCutText，輸出完整訊息位元組串。
  - client→server：解析固定長度訊息 — SetPixelFormat(20B)、SetEncodings(4+4n)、
    FramebufferUpdateRequest(10B)、KeyEvent(8B)、PointerEvent(6B)、
    ClientCutText(8+len)，供輸入攔截與轉發判斷。
- DES：優先用現有 `cryptography` 套件（TripleDES 以 K1=K2=K3 等價單 DES；VNC 變體
  key byte 需 bit-reverse）；若不可用則 vendor ~150 行 d3des。

### Services — `services/classroom/`

- `vnc_session_manager.py`：
  - session 註冊表：session_id → {vmid, mode(monitor|broadcast), group_id, owner}
  - 每 VM 至多一條上游；上游 pump：收到 FramebufferUpdate 即回發 incremental
    FramebufferUpdateRequest（連續拉流）。
  - 訂閱者管理：加入（觸發 keyframe request）、離開、廣播上限
    （settings.CLASSROOM_MAX_SUBSCRIBERS，預設 250）。
  - 控制權 token：同一時間至多一人可輸入；無 token 的下游 client→server 訊息中
    KeyEvent/PointerEvent/ClientCutText 一律丟棄（結構性 write-block）；
    FramebufferUpdateRequest 由管理器統一處理，不轉發下游的。
  - 背壓：每訂閱者一個有上限的 asyncio.Queue；佇列滿即斷開該訂閱者（不能丟單一
    更新訊息 — 會造成畫面區域永久過期；斷線重連拿到新 keyframe）。
  - 上游斷線/PVE 錯誤：結束 session、通知信令 hub、關閉所有下游。
- `classroom_presence.py`（信令 hub）：
  - 使用者 ↔ 群組頻道註冊；在線名單查詢。
  - 事件推播：live_started / live_stopped / takeover_started / takeover_stopped。
- 接管旗標：per-vmid「被接管」註冊表；既有 1:1 `vnc_proxy`（學生自己的主控台，
  路徑不變）在 client→server 方向掛訊息解析器，旗標存在時丟棄輸入訊息。

### Routes / WebSocket

- 新增 `Permission.CLASSROOM_MONITOR`（teacher/admin）。
- 權限規則：
  - teacher：僅可觀看/接管「自己擁有的群組的成員」名下 VM；broadcast 目標須為
    自己可存取的 VM 且指定自己擁有的群組。
  - admin：不限。
  - student：僅可訂閱自己所屬群組的進行中 broadcast session。
- `api/routes/classroom.py`：
  - `GET /classroom/groups/{gid}/students` — 成員 + VM + 在線狀態（teacher/admin）
  - `POST /classroom/sessions` (vmid, mode, group_id) → session 資訊
  - `DELETE /classroom/sessions/{id}` — 結束
  - `POST /classroom/sessions/{id}/control` (action=take|release) — 接管切換
  - `GET /classroom/live` — 學生查自己群組目前的直播
- WebSocket（main.py 註冊）：
  - `/ws/classroom` — 信令（JWT）
  - `/ws/classroom/{session_id}/watch` — VNC 資料面（JWT + session 權限）
- 所有 PVE 失敗轉 `AppError`（HTTP 面）或 close code + 信令事件（WS 面）。

## 前端

- 老師教室頁 `/classroom`：群組選擇 → 學生卡片格（VM 狀態、在線）→「觀看」開唯讀
  dialog、「接管」按鈕、頂部「開始/結束直播」。
- 學生端：全域掛 classroom WS；收到 live_started 顯示橫幅 → 點擊開唯讀觀看 dialog
  （viewOnly、隱藏 Ctrl+Alt+Del / 貼上）；被接管時自己 console 顯示「老師接管中」。
- 觀看 dialog 重用 `VncScreen`（react-vnc），指向 watch WS（security None，無需
  ticket）。
- 完成後 regenerate OpenAPI client。

## 範圍限制與假設

- 僅支援 QEMU VM（LXC 為文字終端無 framebuffer，監控留待後續）。
- SessionManager 與 presence hub 為行程內記憶體狀態（與現有 VNC proxy 同前提：
  單 backend 行程；多 worker 水平擴展屬模組 D 範圍）。
- 上游固定 32bpp 像素格式；忽略下游 SetPixelFormat/SetEncodings（下游固定為
  noVNC，行為確定）。
- 廣播上限預設 250 訂閱者（settings 可調）。

## 交付切分

| 批次 | 內容 |
|---|---|
| B1 | `infrastructure/vnc/`：RFB 握手 + 訊息切框器 + DES（單元測試密集覆蓋） |
| B2 | VncSessionManager：fan-out、控制權、背壓、keyframe（fake transport 測試） |
| B3 | classroom 信令 hub + 接管旗標接入既有 vnc_proxy |
| B4 | REST 路由 + RBAC + main.py WS 註冊 |
| B5 | 前端老師教室頁 + 學生直播觀看 + regenerate client |
| B6 | 整合收尾：CLAUDE.md 更新、進度文件、全量 lint/mypy/測試 |

## 測試策略

- RFB parser：手工位元組流單元測試（分片邊界、各編碼矩形、非法輸入）。
- 握手狀態機：fake transport（in-memory duplex）雙端對測。
- SessionManager：fake 上游/下游，驗證 fan-out 順序、控制權轉移、queue 滿斷開、
  keyframe 觸發。
- RBAC：teacher 跨群組拒絕、student 訂閱非本群組直播拒絕。
- 不需真實 PVE / DB / Redis。
