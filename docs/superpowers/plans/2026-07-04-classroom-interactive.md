# 模組 B：虛擬教室互動系統 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 老師可實時觀看/接管學生 VM 畫面、可把自己的畫面直播給全班（唯讀），以單上游 RFB 連線 + 後端 fan-out 實現。

**Architecture:** 新增 `infrastructure/vnc/`（純 RFB 協議：上游 client 握手含 VNC-DES 認證、下游 server 握手、雙向訊息切框器）與 `services/classroom/`（VncSessionManager fan-out + 控制權 token + presence 信令 hub），REST 路由管 session 生命週期，兩條新 WebSocket（信令 / VNC 資料面）。學生自己的主控台仍走既有 1:1 `vnc_proxy`，僅在被接管時攔截其輸入。

**Tech Stack:** FastAPI WebSocket、websockets（上游 PVE）、cryptography（DES via TripleDES K1=K2=K3）、React 19 + react-vnc（下游 security=None）。

## Global Constraints

- 遵循 Routes → Services → Infrastructure 分層；PVE 呼叫只出現在 infrastructure。
- 所有 WS 連線先 JWT 驗證（`get_ws_current_user`）再 accept。
- 上游 RFB 編碼固定協商 `Hextile(5), CopyRect(1), Raw(0), DesktopSize(-223)`；像素格式固定 32bpp true color little-endian（bpp=32, depth=24, bigendian=0, truecolor=1, rgb max 255, shift r16 g8 b0）。
- 下游 security type 只提供 None(1)；下游的 SetPixelFormat/SetEncodings 讀掉即丟。
- 無控制權訂閱者的 KeyEvent/PointerEvent/ClientCutText 一律丟棄（不轉發）。
- 訂閱者佇列滿 → 斷開該訂閱者，不丟個別訊息。
- 僅支援 QEMU VM；廣播上限 `settings.CLASSROOM_MAX_SUBSCRIBERS`（預設 250）。
- 測試不依賴真實 PVE/DB/Redis；mypy strict + ruff 必須全過。
- commit 訊息格式沿用「範本系統2.0後續: <內容> (Bn)」。

---

### Task B1: RFB 協議基礎 `infrastructure/vnc/`

**Files:**
- Create: `backend/app/infrastructure/vnc/__init__.py`
- Create: `backend/app/infrastructure/vnc/des.py`
- Create: `backend/app/infrastructure/vnc/messages.py`
- Create: `backend/app/infrastructure/vnc/handshake.py`
- Test: `backend/tests/infrastructure/test_vnc_rfb.py`

**Interfaces (Produces):**
```python
# des.py
def vnc_auth_response(password: str, challenge: bytes) -> bytes  # 16B challenge -> 16B response

# messages.py
PIXEL_BYTES = 4
class RfbStreamError(Exception): ...
@dataclass
class FramebufferSize: width: int; height: int
class ServerMessageSplitter:
    def __init__(self, width: int, height: int) -> None
    def feed(self, data: bytes) -> list[bytes]   # 回傳 0..n 個「完整」server->client 訊息
    @property
    def size(self) -> FramebufferSize            # DesktopSize rect 會更新
class ClientMessageSplitter:
    def feed(self, data: bytes) -> list[tuple[int, bytes]]  # (msg_type, full_message_bytes)
CLIENT_INPUT_TYPES = {4, 5, 6}  # KeyEvent, PointerEvent, ClientCutText

# handshake.py
@dataclass
class ServerInitInfo: width: int; height: int; pixel_format: bytes; name: bytes
class ByteStream:  # websockets client connection 包成位元組流
    def __init__(self, ws) -> None
    async def recv_exact(self, n: int) -> bytes
async def upstream_handshake(ws, password: str) -> ServerInitInfo
    # RFB 3.8: version -> security(必須含 2/VNC auth) -> DES 認證 -> ClientInit(shared=1)
    # -> ServerInit -> 送 SetPixelFormat(固定32bpp) -> SetEncodings([5,1,0,-223])
def full_update_request(width: int, height: int, *, incremental: bool) -> bytes  # 10B FBUR
async def downstream_handshake(websocket: FastAPIWebSocket, init: ServerInitInfo) -> None
    # server 端：version 3.8 -> security [None] -> SecurityResult OK -> ClientInit -> ServerInit
```

**訊息長度規則（messages.py 實作核心）：**

server→client（type byte 開頭）：
- `0` FramebufferUpdate：`1+1+2`(hdr) + Σ rect（每 rect `12B` 頭 = x,y,w,h,encoding(i32)）：
  - Raw(0)：`w*h*4`；CopyRect(1)：`4`；DesktopSize(-223)：`0`（並更新 `size`）
  - Hextile(5)：逐 16×16 tile 走訪 — 每 tile 1B subencoding：
    bit0 Raw → `tw*th*4`；否則 bit1 Background → `+4`、bit2 Foreground → `+4`、
    bit3 AnySubrects → `+1`(nsubrects) + 每 subrect (`4` if bit4 SubrectsColoured else `0`)+`2`
  - 未知 encoding → raise `RfbStreamError`
- `1` SetColourMapEntries：`6 + 6*ncolours`；`2` Bell：`1`；`3` ServerCutText：`8 + len`

client→server：SetPixelFormat(0)=20、SetEncodings(2)=`4+4n`、FBUR(3)=10、KeyEvent(4)=8、PointerEvent(5)=6、ClientCutText(6)=`8+len`。

兩個 splitter 都必須容忍任意分片（內部 bytearray 緩衝、狀態機推進）。

**DES 細節：** key = ticket 前 8 bytes（不足補 0），每個 key byte **位元反轉**後
以 `TripleDES(key*3)` + ECB 加密 16B challenge（等價單 DES）。
`from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES`。

- [ ] **Step 1:** 寫 `test_vnc_rfb.py` 失敗測試：DES 已知向量（password="test"、全零 challenge，期望值先用參考實作算出寫死）、ServerMessageSplitter 各編碼 + 跨分片、Hextile tile 走訪含 subrects、ClientMessageSplitter 各型別、非法 encoding raise。
- [ ] **Step 2:** `uv run pytest tests/infrastructure/test_vnc_rfb.py -v` → 全 FAIL（module 不存在）。
- [ ] **Step 3:** 實作 des.py / messages.py / handshake.py。handshake 測試用 in-memory fake ws（`recv()` 回傳預先排好的 frames、`send()` 收集輸出）雙向驗證握手位元組序列。
- [ ] **Step 4:** pytest 全 PASS；`uv run ruff check . && uv run mypy app/infrastructure/vnc`。
- [ ] **Step 5:** Commit `範本系統2.0後續: RFB 協議基礎 infrastructure/vnc (B1)`。

---

### Task B2: VncSessionManager `services/classroom/`

**Files:**
- Create: `backend/app/services/classroom/__init__.py`
- Create: `backend/app/services/classroom/vnc_session_manager.py`
- Modify: `backend/app/core/config.py`（新增 `CLASSROOM_MAX_SUBSCRIBERS: int = 250`、`CLASSROOM_SUBSCRIBER_QUEUE_SIZE: int = 256`）
- Test: `backend/tests/services/test_vnc_session_manager.py`

**Interfaces (Consumes):** B1 全部。上游連線建立重用 `services/proxmox` 的
`get_session_ticket` / `get_vnc_ticket_with_session` 與 `infrastructure/proxmox` 的
`build_ws_ssl_context/get_active_host/get_proxmox_settings`（與 vnc.py 相同 URL 組法）。

**Interfaces (Produces):**
```python
class SessionMode(str, Enum): monitor = "monitor"; broadcast = "broadcast"
@dataclass
class ClassroomSession:
    id: str; vmid: int; mode: SessionMode; group_id: uuid.UUID | None
    started_by: uuid.UUID; controller_user_id: uuid.UUID | None; subscriber_count: int

class VncSessionManager:
    async def start_session(self, *, vmid, mode, group_id, started_by) -> ClassroomSession
        # 每 vmid 至多一個 session（重複 -> AppError 409）；背景 task 建上游+pump
    async def stop_session(self, session_id, *, reason: str = "ended") -> None
    def get_session(self, session_id) -> ClassroomSession | None
    def list_sessions(self) -> list[ClassroomSession]
    def find_broadcast_for_groups(self, group_ids: set[uuid.UUID]) -> ClassroomSession | None
    async def attach_subscriber(self, session_id, *, user_id, websocket) -> None
        # downstream_handshake -> 註冊 queue -> 請求 keyframe -> 迴圈:
        #   consumer task: queue -> websocket.send_bytes
        #   reader  task: websocket.receive -> ClientMessageSplitter
        #     有控制權: 輸入訊息轉發上游；無: CLIENT_INPUT_TYPES 丟棄；FBUR/SetXxx 一律吞掉
    async def set_controller(self, session_id, user_id: uuid.UUID | None) -> None
    def is_input_blocked(self, vmid: int) -> bool   # monitor session 有 controller 時 True
    def on_session_end(self, callback) -> None      # B3 presence hub 訂閱
```
單例：`vnc_session_manager = VncSessionManager()`。

**上游 pump 行為：** `upstream_handshake` 後送一次全量 FBUR；此後每收到一個完整
FramebufferUpdate（splitter 產出）→ 廣播給所有 subscriber queue → 立即回發
incremental FBUR。Bell/ServerCutText 也廣播。queue `put_nowait` 滿 → 關閉該訂閱者
WS（code 1013）並移除。上游例外/關閉 → `stop_session(reason="upstream_closed")`。

- [ ] **Step 1:** 失敗測試（fake 上游：monkeypatch `VncSessionManager._connect_upstream` 回傳 fake duplex + 固定 ServerInitInfo）：fan-out 順序一致、新訂閱者觸發 keyframe request、無控制權輸入被丟棄、控制權者輸入轉發、queue 滿斷開、上游關閉結束 session、同 vmid 重複 start 409、`is_input_blocked`。
- [ ] **Step 2:** pytest FAIL。
- [ ] **Step 3:** 實作 manager + config 設定。
- [ ] **Step 4:** pytest PASS + ruff + mypy。
- [ ] **Step 5:** Commit `範本系統2.0後續: VncSessionManager fan-out 與控制權 (B2)`。

---

### Task B3: 教室信令 hub + 既有 vnc_proxy 接管攔截

**Files:**
- Create: `backend/app/services/classroom/presence.py`
- Modify: `backend/app/api/websocket/vnc.py`（forward_to_proxmox 輸入攔截）
- Test: `backend/tests/services/test_classroom_presence.py`

**Interfaces (Produces):**
```python
class ClassroomPresenceHub:
    async def register(self, *, user_id, group_ids: set[uuid.UUID], websocket) -> None  # 常駐直到斷線
    def online_user_ids(self, group_id) -> set[uuid.UUID]
    async def broadcast_to_group(self, group_id, event: dict) -> None   # send_json，死連線自動清
    async def send_to_user(self, user_id, event: dict) -> None
classroom_presence_hub = ClassroomPresenceHub()
# 事件 payload: {"type": "live_started"|"live_stopped"|"takeover_started"|"takeover_stopped"
#               |"watch_force_closed", "session_id": ..., "vmid": ..., "group_id": ...}
```

**vnc.py 攔截：** `forward_to_proxmox` 內建立 `ClientMessageSplitter`；收到 bytes 時
若 `vnc_session_manager.is_input_blocked(vmid)` 且訊息 type ∈ `CLIENT_INPUT_TYPES`
→ 丟棄，否則原樣轉發（text frame 照舊直送）。旗標未啟用時 splitter 仍持續 feed
（維持訊息邊界同步），但不擋任何訊息。

- [ ] **Step 1:** 失敗測試：register/斷線清理、group 廣播只送本群組、online 名單、send_to_user；vnc 攔截以單元測試直接測「blocked 時 KeyEvent 被丟、FBUR 照發」（把 forward 邏輯抽成可測函式 `filter_client_bytes(splitter, data, blocked) -> list[bytes]` 放 messages.py 或 vnc.py 頂層）。
- [ ] **Step 2:** FAIL → **Step 3:** 實作 → **Step 4:** PASS + ruff/mypy。
- [ ] **Step 5:** Commit `範本系統2.0後續: 教室信令 hub 與接管輸入攔截 (B3)`。

---

### Task B4: REST 路由 + RBAC + WS 註冊

**Files:**
- Create: `backend/app/schemas/classroom.py`
- Create: `backend/app/services/classroom/classroom_service.py`（權限與編排）
- Create: `backend/app/api/routes/classroom.py`
- Create: `backend/app/api/websocket/classroom.py`（兩個 WS handler）
- Modify: `backend/app/core/permissions.py`（`CLASSROOM_MONITOR`；teacher matrix 加入）
- Modify: `backend/app/core/authorizers.py`（`require_classroom_monitor`）
- Modify: `backend/app/api/main.py`（router 註冊）、`backend/app/main.py`（WS 註冊）
- Test: `backend/tests/services/test_classroom_service.py`

**Interfaces (Produces):**
```python
# classroom_service.py（DB session 由呼叫端注入）
def require_can_watch(session, user, vmid) -> Resource
    # admin/RESOURCE_OWNERSHIP_BYPASS 直接過；teacher: resource.user_id ∈ 自己擁有群組的成員
    # 其他 -> PermissionDeniedError；resource 不存在 -> NotFoundError
def require_can_broadcast(session, user, vmid, group_id) -> None
    # CLASSROOM_MONITOR + group.owner_id == user.id (admin bypass) + vmid 可存取
def get_group_ids_of_user(session, user_id) -> set[uuid.UUID]
def list_classroom_students(session, group_id, user) -> list[ClassroomStudent]
async def start_watch(...) / start_broadcast(...) / stop(...) / set_control(...)
    # 編排 vnc_session_manager + presence 事件推播
# WS: /ws/classroom  -> get_ws_current_user -> get_group_ids_of_user -> hub.register
#     /ws/classroom/{session_id}/watch -> 驗證: monitor 需 require_can_watch；
#     broadcast 需 user ∈ session.group 成員或發起者/admin -> attach_subscriber
```
REST（`/api/v1/classroom`）：
- `GET /groups/{group_id}/students`（teacher/admin）→ `[{user_id, email, full_name, vms:[{vmid,name,status}], online}]`
- `POST /sessions` body `{vmid, mode, group_id?}` → `ClassroomSessionPublic`
- `DELETE /sessions/{id}`（發起者或 admin）
- `POST /sessions/{id}/control` body `{action: "take"|"release"}`（monitor 發起者）→ 推播 takeover 事件
- `GET /sessions`（teacher/admin: 自己發起的；admin 全部）
- `GET /live`（student）→ 自己群組進行中的 broadcast session 或 null

- [ ] **Step 1:** 失敗測試：`require_can_watch`（teacher 同群組過/跨群組拒/admin 過/不存在 404）、`require_can_broadcast`、student `GET /live` 過濾——service 層以 in-memory sqlite session 或 mock repo 測。
- [ ] **Step 2:** FAIL → **Step 3:** 實作 schemas/service/routes/WS + 註冊。
- [ ] **Step 4:** PASS + ruff/mypy + 冒煙：`python -c "from app.main import app; print([r.path for r in app.routes if 'classroom' in r.path])"`。
- [ ] **Step 5:** Commit `範本系統2.0後續: 教室 API 路由與 RBAC (B4)`。

---

### Task B5: 前端教室頁 + 直播觀看

**Files:**
- Create: `frontend/src/hooks/useClassroomSocket.ts`（信令 WS：自動重連、事件 dispatch）
- Create: `frontend/src/components/Classroom/ClassroomWatchDialog.tsx`（重用 VncScreen，url=`/ws/classroom/{sessionId}/watch?token=...`，**不帶 credentials**、viewOnly、無 Ctrl+Alt+Del/貼上；含「接管/釋放」鈕——僅 monitor 模式發起者可見）
- Create: `frontend/src/components/Classroom/LiveBanner.tsx`（學生收到 live_started 顯示、點擊開 watch dialog；live_stopped 自動關）
- Create: `frontend/src/routes/_layout/classroom.tsx`（teacher/admin：群組下拉 → 學生卡片格 [VM 狀態/online 徽章/觀看鈕] → 頂部直播開關選自己的 VM）
- Modify: `_layout.tsx` 或全域掛載點：學生角色掛 `useClassroomSocket` + LiveBanner；資源頁 console 加「老師接管中」覆蓋（takeover_started/stopped 事件）
- Modify: sidebar 導航加「教室」項（teacher/admin 可見）
- 執行 `bash ./scripts/generate-client.sh` 重生 client

- [ ] **Step 1:** 後端起本地 dev（或用既有 openapi 匯出流程）→ regenerate client。
- [ ] **Step 2:** 依序實作 hook → dialog → banner → 教室頁 → 掛載/導航。
- [ ] **Step 3:** `cd frontend && bun run build && bun run lint` 全過。
- [ ] **Step 4:** Commit `範本系統2.0後續: 前端教室頁與直播觀看 (B5)`。

---

### Task B6: 收尾

**Files:**
- Modify: `CLAUDE.md`（架構樹加 `infrastructure/vnc/`、`services/classroom/`、新 WS 路徑說明）
- Modify: `progress.md`（模組 B 進度表 + 實作備註）

- [ ] **Step 1:** 後端全量 `uv run ruff check . && uv run mypy . && uv run pytest tests`（容忍既有無 Redis 環境錯誤，新增測試必須全過）。
- [ ] **Step 2:** 前端 `bun run build`、`bun run lint`。
- [ ] **Step 3:** 更新 CLAUDE.md 與 progress.md。
- [ ] **Step 4:** Commit `範本系統2.0後續: 模組B收尾與文件 (B6)`。
