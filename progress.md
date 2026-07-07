# 開發進度

## 模組 B：虛擬教室互動系統（2026-07-04 完成）

> 設計文件：`docs/superpowers/specs/2026-07-04-classroom-interactive-design.md`
> 實作計畫：`docs/superpowers/plans/2026-07-04-classroom-interactive.md`

| 任務 | 內容 | 狀態 | Commit |
|------|------|------|--------|
| B1 | RFB 協議基礎 `infrastructure/vnc/`（VNC-DES、訊息切框器、上下游握手） | ✅ | `355ae3f` |
| B2 | `VncSessionManager` 單上游 fan-out、控制權 token、佇列滿斷開 | ✅ | `a4aaaa7` |
| B3 | 教室信令 hub（presence）+ 既有 vnc_proxy 接管輸入攔截 | ✅ | `22ac124` |
| B4 | REST `/api/v1/classroom` + RBAC（CLASSROOM_MONITOR）+ 兩條 WS 註冊 | ✅ | `d3111ac` |
| B5 | 前端教室頁、直播橫幅、觀看視窗（react-vnc）、接管覆蓋 | ✅ | `1e99c91` |
| B6 | 全量驗證 + 文件收尾 | ✅ | 本 commit |

### 實作備註

- **DES 已知向量**：以獨立純 Python DES 參考實作（經典 FIPS 46 向量自我驗證）產生，
  與正式實作（cryptography TripleDES K1=K2=K3）交叉驗證，寫死於 `tests/infrastructure/test_vnc_rfb.py`。
- **QEMU Extended Key Event（client type 255）**：PVE 的 noVNC 實際會送，
  `ClientMessageSplitter` 已支援（submessage 0），接管攔截時同樣視為輸入丟棄；
  未知型別失去同步時 vnc_proxy fail-open 原樣轉發，不影響學生主控台。
- **事件推播**：`live_started/live_stopped/takeover_started/takeover_stopped` 經
  `classroom_presence_hub`；session 結束（含上游關閉）統一由 `on_session_end` callback 發送。
- **上限設定**：`CLASSROOM_MAX_SUBSCRIBERS=250`、`CLASSROOM_SUBSCRIBER_QUEUE_SIZE=256`（滿→斷開該訂閱者 1013）。
- **僅支援 QEMU VM**；學生 LXC 卡片觀看鈕停用（`vm_type` 判斷）。
- **驗證**：模組 B 測試 74 例全過（ruff + mypy strict 乾淨）；後端全量 513 passed，
  57 errors 為既有 DB/Redis 環境相依測試（無 Redis/DB 的本機環境）；前端 `bun run build` + `bun run lint` 全過。
