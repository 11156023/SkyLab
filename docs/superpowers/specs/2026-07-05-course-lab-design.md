# 互動式實作教學系統（Course Lab）設計文件

日期：2026-07-05
狀態：已與需求方逐段確認

## 1. 目標

為 SkyLab 增加 TryHackMe 式學習管理系統：老師定義「學習路徑 → 房間 → 任務 → 題目」，
學生免審核一鍵部署實驗機（VM 或 LXC），透過提交 Flag 驗證學習成果，環境到期自動回收。

## 2. 已確認的需求決策

| 決策點 | 結論 |
|---|---|
| 秒開部署底層 | 擴充既有 VMRequest 快速通道（新增 `mode="course"`），不另建獨立管線 |
| 實驗環境型態 | VM + LXC 都支援，統一綁定模板系統 2.0 的 `VMTemplate`（`resource_type` qemu/lxc）；房間亦可不綁模板（純理論房） |
| Flag 設計 | 靜態共用 Flag（SHA-256 hash 儲存）+ `no_answer` 閱讀型題目 |
| 可見性 | 發布制：路徑 draft/published，published 後全站學生可見（第一版不綁 group） |
| 範圍 | 一份完整設計，按四階段實作到完 |
| 架構方案 | 方案 A：`mode="course"` + 輕量 `CourseDeployment` 關聯表 |
| 進度監控 | 老師端以 WebSocket 即時推播（見 §7a），非輪詢 |

## 3. 現況依賴（複用而非新建）

- `vm_request_service.create` 已有 `quick_template` 免審核模式：自動核准、背景 provision、
  HTTP 立即返回（[vm_request_service.py:341](../../../backend/app/services/vm/vm_request_service.py)）。
- `provision_pool`（`asyncio.Semaphore`，上限 `GovernanceConfig.provision_max_concurrency`）
  已提供克隆併發控制與防重複三層。
- VM 以學生本人 `VMRequest` 建立時，`/ws/vnc/{vmid}` ownership 授權自動成立。
- Governance 排程已有到期關機與刪除佇列，課程 VM 以 `end_at` 接上即可。
- `VMTemplate`（模板系統 2.0）含 `resource_type`、`default_cores/memory/disk`、`ready` 狀態。

## 4. 資料模型（`backend/app/models/course.py`，一支 Alembic migration）

| 表 | 欄位 |
|---|---|
| `CoursePath` | `id`, `title`, `description`, `status`(draft/published), `created_by`(FK user), `created_at`, `updated_at` |
| `CourseRoom` | `id`, `path_id`(FK CASCADE), `title`, `description`, `difficulty`(easy/medium/hard), `category`, `template_id`(FK → `vm_templates.id`，**nullable：NULL = 純理論房**), `order` |
| `CourseTask` | `id`, `room_id`(FK CASCADE), `title`, `content`(Markdown 原文), `order` |
| `CourseQuestion` | `id`, `task_id`(FK CASCADE), `prompt`, `question_type`(flag/no_answer), `flag_hash`(SHA-256；no_answer 為 NULL), `points`, `order` |
| `UserCourseProgress` | `id`, `user_id`, `question_id`, `completed_at`；UNIQUE(user_id, question_id) |
| `CourseDeployment` | `id`, `room_id`, `user_id`, `vm_request_id`(FK → vm_requests), `created_at`, `expires_at`（= 對應 VMRequest 的 `end_at`，冗餘存放以利課程側查詢） |

要點：

1. 進度記在 **question 層**（修正計畫書原案的 task 層）：任務完成 = 所有題目完成，
   房間/路徑百分比為衍生查詢，不存冗餘欄位。
2. Flag：老師輸入明文 → 後端 `strip()` 正規化 → SHA-256 入庫；API 永不回傳 hash。
3. `CourseDeployment` 是課程域與 VM 域唯一接點；部署狀態一律 join `vm_requests` 即時取得，
   不雙寫。
4. `GovernanceConfig` 新增：`course_ttl_hours`（default 3，1–24）、
   `course_max_active_per_user`（default 1）。沿用 `/governance/config` 管理。

## 5. 服務層（`backend/app/services/course/`）

- `course_service.py` — 路徑/房間/任務/題目 CRUD、發布狀態機
- `flag_service.py` — 純函式：答案正規化 + hash 比對、進度計算
- `deployment_service.py` — 秒開編排（見 §7）
- `progress_service.py` — 學生自身進度、老師端全班統計、進度事件發布（見 §7a）

## 6. API 設計

管理端 `routes/course_admin.py`（新增 `require_course_manage`，teacher/admin）：

```
POST/PUT/DELETE  /admin/courses/paths|rooms|tasks|questions
PUT              /admin/courses/paths/{id}/publish
GET              /admin/courses/paths/{id}/progress     # 每學生每房間完成 %
```

學生端 `routes/courses.py`（登入即可）：

```
GET    /courses/paths                    # published + 我的進度 %
GET    /courses/paths/{id}               # 房間清單 + 進度
GET    /courses/rooms/{id}               # 任務+題目（無 flag_hash）+ 我的部署狀態
POST   /courses/rooms/{id}/deploy        # → 202 {deployment_id}
GET    /courses/deployments/{id}         # 輪詢 provision 狀態 + vmid
DELETE /courses/deployments/{id}         # 提前歸還：將 VMRequest end_at 提前為 now，交由既有回收流程銷毀
POST   /courses/questions/{id}/submit    # {answer} → {correct, task_completed, progress}
```

（計畫書原案 `tasks/{id}/submit` 改為 question 層提交。）

## 7. 秒開部署流程（`deployment_service.deploy`）

1. 驗證：房間屬 published 路徑、房間有綁模板（純理論房呼叫 deploy 回 400）、
   `VMTemplate` 為 `ready`、使用者無進行中課程部署（`course_max_active_per_user`）。
2. 由模板組 `VMRequestCreate`：`resource_type` 依模板 qemu/lxc；hostname 自動命名
   `course-{room短碼}-{user短碼}`；規格取模板 default；密碼自動產生
   （實驗帳密以模板內烘焙為準）；`start_at=now`、`end_at=now+course_ttl_hours`。
3. 呼叫 `vm_request_service.create(mode="course")`：
   - `can_auto_approve_vm_request` 加入 course（student/teacher/admin 皆自動核准）
   - 立即背景 provision 的 mode 集合加入 `"course"`
   - 配額、audit、防重複三層、`provision_pool` 併發原樣繼承
4. 同交易寫入 `CourseDeployment`，回 202。

Flag 提交：`no_answer` 直接記完成；`flag` 型 `strip()` 後 SHA-256 比對 →
正確 upsert `UserCourseProgress`（冪等）→ 回任務是否全數完成。
錯誤僅回 `correct: false`；提交行為記 audit log（暴力猜測可觀察）。

TTL 回收：`end_at` 到期走既有 governance 排程（到期關機 → 刪除佇列），課程側不寫新排程；
部署是否過期由 `expires_at` 判定。

## 7a. 進度即時推播（WebSocket）

- 新增 `/ws/courses/paths/{path_id}/progress`，比照 `/ws/classroom` 模式直接註冊在
  FastAPI app 上（不經 API router），連線時驗證 token + `require_course_manage`。
- `services/course/progress_hub.py`：in-memory hub（path_id → 訂閱連線集合），
  比照 classroom 信令 hub 的實作模式。
- 學生 `submit` 答對後，`progress_service` 發布事件
  `{user_id, room_id, task_id, question_id, room_progress_percent}` 到該 path 的 hub，
  訂閱中的老師端即時收到增量更新。
- 老師端 `CourseCmsPage` 進度 tab：進頁先 `GET /admin/courses/paths/{id}/progress`
  取全量快照，再掛 WS 收增量；WS 斷線自動重連並重拉快照。
- 學生自身頁面不走 WS（自己的提交結果由 HTTP 回應直接更新）。

## 8. 前端

| 頁面 | 說明 |
|---|---|
| `pages/courses/paths/CoursePathsPage.jsx` | 路徑卡片 + 進度條，展開房間清單（難度/分類/完成 %） |
| `pages/courses/room/CourseRoomPage.jsx` | 三欄：任務導航｜Markdown 內容 + Flag 輸入｜內嵌 VNC；頂部啟動按鈕 + 剩餘時間倒數。純理論房（無模板）自動收合 VNC 欄與啟動按鈕，呈兩欄 |
| `pages/teaching/course-cms/CourseCmsPage.jsx` | 樹狀編輯器、發布開關、模板下拉（既有 templates service，可選「不綁模板」）、學生進度 tab（WS 即時更新，見 §7a） |

- VNC 內嵌：抽用 `VncDialog.jsx` 連線邏輯（react-vnc + `/ws/vnc/{vmid}`）改嵌入式面板。
- 部署輪詢：`GET /courses/deployments/{id}` 3 秒間隔；成功掛 VNC、失敗顯示原因可重試。
- Markdown：新增 `react-markdown` + `rehype-sanitize`（老師輸入仍需防 XSS）。
- 答對打勾動畫 + sonner toast，任務全對自動展開下一任務。
- 新增 `services/courses.js`（走 `api.js`）+ vitest 測試；路由集中 `App.jsx`、側欄 `Sidebar.jsx`。

## 9. 測試

- 後端：`flag_service` 純函式單測；`deployment_service` 整合測試（單人單機、published 檢查、
  純理論房 deploy 回 400、TTL 寫入）；API 測試（權限 403、flag_hash 不外洩、submit 冪等）；
  `progress_hub` 單測（訂閱/發布/斷線清理）。
- 前端：`courses.js` vitest。
- 壓測：沿用 `tests/performance/` 兩層模式驗證多學生同時秒開。

## 10. 實作階段

1. **後端骨架**：models + migration + `mode="course"` 擴充 + `deployment_service` +
   governance 欄位（驗證：curl 全流程）
2. **管理端**：CMS CRUD API + `CourseCmsPage` + 進度監控
3. **學生端**：paths/room 頁 + 三欄工作區 + Flag 提交
4. **壓測收尾**：併發秒開壓測、回收精準度、進度面板優化

## 11. 明確不做（YAGNI）

- 動態 per-student Flag（防拄答案）— 留待後續迭代
- 課程綁 group 可見性 — 第一版全站公開
- 排行榜/積分商城 — `points` 欄位先存，不做 UI
