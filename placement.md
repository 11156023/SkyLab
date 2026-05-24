# SkyLab 分配與重排說明

SkyLab 是一套建置在 Proxmox 叢集上的全端平台，用來處理 VM/LXC 的申請、審核、時段分配、啟動、搬移與自動關機。

## 系統目標

- 使用者以「使用時段」為核心送出 VM 或 LXC 申請
- 系統只允許選擇目前仍可排入的時段
- 管理者在審核時可以看到該時段的目前在線資源、已核准申請與預測分配結果
- 核准後先保留與規劃，不在審核當下就把最終執行位置定死
- 到 `start_at` 時，系統對該時段的 active cohort 做全域重排
- 依重排結果執行 `create / migrate / start`
- 到 `end_at` 時自動關機

## 核心流程

### 1. 使用者送出申請

1. 使用者選擇資源類型、規格與使用時段
2. 前端呼叫 availability API 取得可選時段
3. 後端在送單時再次驗證該時段是否仍可安排
4. 申請以 `pending` 狀態寫入資料庫

相關檔案：

- [backend/app/services/vm_request_service.py](backend/app/services/vm_request_service.py)
- [backend/app/services/vm_request_availability_service.py](backend/app/services/vm_request_availability_service.py)
- [frontend/src/components/Applications/ApplicationRequestPage.tsx](frontend/src/components/Applications/ApplicationRequestPage.tsx)

### 2. 管理者審核

1. 管理者開啟申請審核頁
2. 後端回傳該申請時段的 review context，包含：
   - 目前在線資源
   - 與此時段重疊的已核准申請
   - 若核准這筆申請後的預測分配結果
3. 管理者核准後，系統會重建重疊時段內所有已核准申請的 reservation，並寫入對應的 `desired_node`

相關檔案：

- [backend/app/services/vm_request_service.py](backend/app/services/vm_request_service.py)
- [backend/app/api/routes/vm_requests.py](backend/app/api/routes/vm_requests.py)
- [frontend/src/components/Applications/VMRequestReviewPage.tsx](frontend/src/components/Applications/VMRequestReviewPage.tsx)
- [frontend/src/services/vmRequestReview.ts](frontend/src/services/vmRequestReview.ts)

### 3. 到 `start_at` 時進行時段重排

當某個申請時段正式開始時，scheduler 不會只是把原本預留的機器直接開機，而是會：

1. 找出目前這個時段內所有 active 的已核准申請
2. 對整個 active cohort 重新計算一次較平衡的 placement
3. 寫入新的 `desired_node`
4. 對尚未建出的 request，直接在 `desired_node` 建立資源
5. 對已 provision 且 `actual_node != desired_node` 的 request 建立或執行 migration job
6. 確保該時段內的最終資源是 running

這代表系統的平衡點是在「真正開始使用的時刻」，而不是只在審核當下決定。

相關檔案：

- [backend/app/services/vm_request_schedule_service.py](backend/app/services/vm_request_schedule_service.py)
- [backend/app/services/vm_request_placement_service.py](backend/app/services/vm_request_placement_service.py)
- [backend/app/services/provisioning_service.py](backend/app/services/provisioning_service.py)
- [backend/app/services/proxmox_service.py](backend/app/services/proxmox_service.py)

### 4. 到 `end_at` 時自動關機

當 `end_at` 到達時，scheduler 會觸發對應資源的自動關機。

相關檔案：

- [backend/app/services/vm_request_schedule_service.py](backend/app/services/vm_request_schedule_service.py)

## 分配模型

每一筆 VM request 現在同時追蹤「規劃狀態」與「實際執行狀態」：

- `assigned_node`
  目前記錄中的保留節點或最近一次選定節點
- `desired_node`
  演算法認為該 request 在目前 active 時段最應該運行的節點
- `actual_node`
  該資源目前實際運行中的 Proxmox 節點
- `migration_status`
  migration 狀態，例如穩定、待搬移、搬移中、阻擋、失敗
- `rebalance_epoch`
  最近一次時段重排的版本號
- `last_rebalanced_at`
  最近一次重排的時間

相關檔案：

- [backend/app/models/vm_request.py](backend/app/models/vm_request.py)
- [backend/app/alembic/versions/t2u3v4w5x6y7_add_rebalance_fields_to_vm_requests.py](backend/app/alembic/versions/t2u3v4w5x6y7_add_rebalance_fields_to_vm_requests.py)

## 分配輸入來源

目前 placement 主要吃兩類由管理者維護的設定：

### 節點優先級

- `node priority`
- 數字越小代表優先級越高
- 在同等條件下，這是節點排序的重要 tie-break
- 設計方向與 `pve_ resource_simulator` 一致

### 儲存池設定

- `enabled`
  未啟用的 storage 不參與 VM/LXC 分配
- `speed_tier`
  預設偏好順序為 `nvme > ssd > hdd > unknown`
- `user_priority`
  在同一 speed tier 下，數字越小越優先

storage 設定同時影響：

- 分配可行性
  如果某個 node 上沒有可用且相容的 storage pool，該 node 不會被視為有效候選
- 實際落盤
  建機或 clone 時，系統會優先使用管理者設定出的最佳 storage pool，而不是只使用 request payload 裡的預設 storage 名稱

相關檔案：

- [backend/app/services/vm_request_placement_service.py](backend/app/services/vm_request_placement_service.py)
- [backend/app/services/provisioning_service.py](backend/app/services/provisioning_service.py)
- [backend/app/models/proxmox_storage.py](backend/app/models/proxmox_storage.py)

## 分配與 Migration 設定

### 已經在系統設定中的參數

這些參數已經適合交給系統管理員在日常營運中調整：

- `node priority`
- storage `enabled`
- storage `speed_tier`
- storage `user_priority`
- `cpu_overcommit_ratio`
- `disk_overcommit_ratio`
- `migration_enabled`
- `migration_max_per_rebalance`
- `migration_min_interval_minutes`
- `migration_retry_limit`
- `rebalance_migration_cost`
- `rebalance_peak_cpu_margin`
- `rebalance_peak_memory_margin`
- `rebalance_loadavg_warn_per_core`
- `rebalance_loadavg_max_per_core`
- `rebalance_loadavg_penalty_weight`
- `rebalance_disk_contention_warn_share`
- `rebalance_disk_contention_high_share`
- `rebalance_disk_penalty_weight`
- `rebalance_search_max_relocations`
- `rebalance_search_depth`
- `migration_worker_concurrency`
- `migration_job_claim_timeout_seconds`
- `migration_retry_backoff_seconds`

### 建議未來再放進系統設定的參數

這些參數目前還沒有正式暴露成系統設定，但若要讓主系統更接近 `pve_ resource_simulator`，會是很好的下一步：

- `rebalance_cpu_peak_warn_share`
  控制 CPU peak 壓力從什麼點開始產生明顯懲罰

- `rebalance_cpu_peak_high_share`
  控制 CPU 高風險門檻，超過後節點應明顯被降低偏好

- `rebalance_memory_peak_warn_share`
  控制記憶體 peak 壓力從什麼點開始影響分配

- `rebalance_memory_peak_high_share`
  控制記憶體高風險門檻

- `rebalance_resource_weights`
  用來調整 CPU、RAM、disk，未來也可延伸到 GPU 的整體權重

- `migration_allowed_resource_profiles`
  可用來定義政策，例如只允許 VM 搬移、LXC 可否搬、GPU 工作負載禁止搬移、local-disk 需人工批准等

- `rebalance_require_shared_storage_for_live_migration`
  可明確規定 live migration 是否一定要求 shared storage

### 比較適合先留在程式常數的參數

這些參數偏底層，不需要常改，先保留在程式裡通常更穩：

- VM hypervisor overhead 預設值
- storage speed tier 的內部排序規則
- penalty curve 的插值細節
- guest pressure 的低階公式
- telemetry normalization 細節，例如 load average parsing

### 建議下一批優先曝光的順序

如果只打算再增加少量設定，建議優先順序如下：

1. `rebalance_cpu_peak_warn_share` 與 `rebalance_cpu_peak_high_share`
2. `rebalance_memory_peak_warn_share` 與 `rebalance_memory_peak_high_share`
3. `rebalance_resource_weights`
4. `migration_allowed_resource_profiles`
5. `rebalance_require_shared_storage_for_live_migration`

## 完整度評估

以目前「申請 -> 審核 -> 到點重排 -> 自動啟停」這條主線來看，完整度大約可評估為 `85%` 左右。

### 已經完成的部分

- 使用者以時段為核心送出申請
- 前端先看可選時段，後端送單再做一次時段驗證
- 管理者可查看 review context 再決定是否核准
- 核准後會重建重疊時段 reservation
- 到 `start_at` 時會對 active cohort 做全域重排
- 對尚未建出的資源執行 create
- 對已存在且位置不同的資源建立 migration job
- migration 已有基本 eligibility check
- 到 `end_at` 自動 shutdown
- storage 與 node priority 已經真的影響分配結果

### 已部分完成但還不算最終版的部分

- migration queue 已有 `claim / timeout / backoff`，但仍偏 scheduler 內部流程，還不是完整的獨立 worker 架構
- `peak / loadavg / migration cost / disk contention` 已進入分配模型，local rebalance search 也已存在，但還未完全達到 simulator 的細緻程度
- review 頁已有預測資料，但可解釋性還能再加強，例如更完整的分數來源與原因說明
- 管理員可調整的參數已增加，但還沒有把所有 simulator 級參數都暴露出來

### 尚未完成的部分

- 更完整的 queue 監看與營運面板
- 更完整的自動搬移政策，例如依 workload profile 決定是否允許搬移

## 可能問題與風險

### 1. 並非所有資源都適合自動搬移

雖然系統已做基本 eligibility check，但仍要注意：

- GPU / PCI passthrough 資源通常不適合任意搬移
- local disk、特殊 mount、bind mount 可能限制 migration
- LXC 的搬移能力與 VM 不完全相同

若未來開放更積極的自動搬移，這塊風險會放大。

### 2. 分配結果與實際叢集狀態仍可能有時間差

系統是在 `start_at` 時重排，但若：

- Proxmox 節點狀態突然改變
- storage 臨時不可用
- cluster 上出現非本系統管理的額外工作負載

則演算法算出的最佳解不一定能完全落地。

### 3. migration queue 目前仍偏單體流程

現在 queue/job table 已經存在，但執行仍高度依賴 scheduler 內部流程。這代表：

- 大量 migration 時的可觀測性仍有限
- 若未來要水平擴充 worker，還需要再拆分
- claim timeout、backoff、concurrency control 還能再做完整

### 4. 模型已接近 simulator，但還不是同等級求解器

目前主系統已經納入：

- node priority
- storage profile
- loadavg penalty
- peak margin
- migration cost

但還沒有完全移植 simulator 那種更深的 local search、disk contention 與多步 relocation 規劃，因此在複雜壓力情境下，未必能找到和 simulator 一樣好的解。

### 5. 參數越多，誤設風險越高

把更多權重與門檻暴露成系統設定雖然更彈性，但也代表：

- 管理員更容易調出不合理組合
- 不同 cluster 間的行為可能變得更難預測
- 若沒有預設值與說明，可能讓分配結果看起來「突然變差」

因此建議把常調參數和底層常數明確分層，不要一次全部暴露。

## 本文件對照重點

若要快速理解目前系統，可用下面三句話記住：

1. 審核時先看時段上下文與預測分配
2. 真正的平衡發生在 `start_at`
3. 分配不是只選 node，也會同時考慮 storage 與 migration 成本

## 本機開發

### Backend

```bash
cd backend
uv sync
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### 使用 Docker Compose 啟動完整環境

請參考 [development.md](development.md) 與 [deployment.md](deployment.md)。

## 注意事項

- 使用最新排程流程前，請先執行 Alembic migration：

```bash
cd backend
alembic upgrade head
```

- 若 backend schema 有變動，frontend 需要同步更新 API 使用方式
- scheduler 預設以 SkyLab 管理的 Proxmox pool 作為 runtime source of truth

## 相關測試

VM request 主流程測試集中在：

- [backend/tests/test_backend_workflows.py](backend/tests/test_backend_workflows.py)

常用聚焦測試指令：

```bash
pytest backend/tests/test_backend_workflows.py -k "vm_request or process_due_request_starts"
```

## 其他文件

- [backend/README.md](backend/README.md)
- [frontend/README.md](frontend/README.md)
- [development.md](development.md)
- [deployment.md](deployment.md)

## 上線前清單

### 目前完整度評估

- 產品流程完整度：約 `88%`
- 分配與重排演算法完整度：約 `82%`
- 營運與可觀測性完整度：約 `72%`
- 與 `pve simulator` 的流程相似度：約 `85%`
- 與 `pve simulator` 的演算法細緻度：約 `78%`

目前已經具備：

- 使用者選配置與時段
- 系統提供可申請時段，且後端再次驗證
- 管理員審核時查看同時段申請、在線資源、preview cohort 預測結果
- 核准後建立 reservation 與 `desired_node`
- 到 `start_at` 進行 active cohort 重排
- 必要時建立 migration job 並自動搬移
- 到 `end_at` 自動關機

### 上線前還差什麼

- 將新的 rebalance / migration tuning 參數完整接到系統管理員設定 UI
- 補 migration queue 的監看畫面，至少能看到 `pending / running / failed / blocked`
- 補 scheduler / migration / provisioning 的告警與失敗通知
- 做真實 Proxmox cluster 的長時間壓力驗證，確認搬移、重排、關機不會互相打架
- 定義人工介入流程，包含 job 卡住、搬移失敗、PVE 節點異常時的處理方式
- 檢查並清理前端既有型別錯誤，避免正式 build 或 typecheck 被其他頁面卡住

### 哪些是必做

這些建議視為正式上線前必做：

- 完整驗證 migration queue 在真實叢集上的行為
  - 包含 `claim / timeout / backoff / retry limit`
  - 確認不會重複搬移、無限重試或 claim 過期後重入異常
- 補營運可見性
  - 至少要能看到哪一筆 request 正在等待搬移、失敗原因是什麼、下一次何時重試
- 補錯誤告警
  - scheduler 失敗
  - migration 失敗
  - provision 失敗
  - auto-shutdown 失敗
- 做真實叢集 smoke test
  - 一般 VM 建立與啟動
  - active window 重排
  - VM migration
  - end_at 自動關機
- 確認系統設定預設值
  - `migration_enabled`
  - `migration_max_per_rebalance`
  - `migration_min_interval_minutes`
  - `migration_worker_concurrency`
  - `migration_job_claim_timeout_seconds`
  - `migration_retry_backoff_seconds`
  - `rebalance_migration_cost`
  - `rebalance_loadavg_*`
  - `rebalance_peak_*`
  - `rebalance_disk_*`
- 確認審核頁與後端行為一致
  - 管理員看到的 preview cohort 說明要能對應實際 `start_at` 時的重排行為

### 哪些可以第二階段再補

這些不會阻止第一版上線，但會明顯提升系統成熟度：

- migration queue 的完整營運面板
  - 篩選
  - 手動重試
  - 手動取消
  - job 歷史查詢
- 更細的 workload profile 搬移政策
  - 例如 GPU、考試機、特殊課程機、不可中斷工作負載
- 更細的 resource policy 設定
  - `migration_allowed_resource_profiles`
  - `rebalance_require_shared_storage_for_live_migration`
  - `rebalance_resource_weights`
- 更完整的 explainability
  - 在審核頁直接顯示每台 node 的分數差異
  - 顯示 peak / loadavg / disk contention 對決策的影響
- 更完整的自動化觀測
  - queue 指標
  - migration 成功率
  - 平均搬移耗時
  - 每個時段的 rebalance 次數
- 與 `pve simulator` 更進一步對齊
  - 更細的 scoring 係數調優
  - 更多 relocation search 策略
  - 更細的 storage / contention 模型校正

### 建議的上線判準

若要判斷「這版是否可以正式上線」，至少應符合以下條件：

- 管理員可在 UI 中完成必要的 migration 與 rebalance 參數設定
- 一整輪申請 -> 審核 -> 到點重排 -> 搬移 -> 開機 -> 到點關機可在真實叢集穩定成功
- migration job 失敗時，管理員能知道原因並追蹤狀態
- scheduler 異常時，不會默默失敗而無人察覺
- preview cohort 顯示的推薦節點與實際 `start_at` 行為大致一致

若以上條件已達成，這版可以視為「第一版可正式上線」；若尚未達成，則仍建議維持在內部試跑或有限範圍上線。
