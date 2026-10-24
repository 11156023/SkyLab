# 治理設定欄位

治理設定是一筆設定紀錄，控制排程器每 60 秒套用的自動政策。管理員在[治理](/zh-TW/manual/governance)頁編輯，API 是 `GET`／`PUT /api/v1/governance/config`。本頁列出每個欄位的預設值、允許範圍與作用。改完在下一輪排程就生效，不必重新啟動。

## 資源告警

叢集、節點或機器的取樣值達到閾值時開立告警，顯示在監控頁並寄信給所有啟用中的管理員。總開關、冷卻時間與取樣間隔也套用在平台健康告警（排程器、worker、Redis、PVE 連線）。

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `alerts_enabled` | `true` | | 資源告警與平台健康告警的總開關。 |
| `alert_cpu_threshold` | `90` | 50–100（%） | 超過此 CPU 使用率就告警。 |
| `alert_memory_threshold` | `90` | 50–100（%） | 記憶體使用率閾值。 |
| `alert_disk_threshold` | `90` | 50–100（%） | 磁碟使用率閾值。 |
| `alert_cooldown_minutes` | `30` | 1–1440 | 同一個目標同一指標兩次告警的最短間隔。 |
| `alert_check_interval_seconds` | `60` | 15–3600 | 取樣使用率的頻率。 |
| `alert_email_enabled` | `true` | | 同時寄信給管理員（需要 SMTP）。 |

## TTL 生命週期

對每台有到期日的機器，生命週期任務在到期前提醒擁有者、到期時停機、寬限期過後排入刪除佇列。延期後會重新提醒。

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `ttl_enabled` | `true` | | 總開關。關閉時到期的機器會繼續運行。 |
| `expiry_warn_days` | `3` | 1–30 | 到期前幾天通知擁有者。 |
| `expiry_grace_delete_days` | `7` | 0–90 | 到期後幾天把已停機的機器排入刪除佇列。`0` 代表到期即刪。 |

## 閒置偵測

開機時間已涵蓋整個觀察視窗、且視窗內平均 CPU 低於閾值的運行中機器會被標記為閒置。下面兩個時數都從標記當下起算：先通知擁有者，寬限期滿就停機。只要有負載或重開機，標記就清除。有未結案挖礦事件的機器不判閒置。

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `idle_detection_enabled` | `true` | | 總開關。 |
| `idle_cpu_threshold_percent` | `1.0` | 0.1–20（%） | 平均 CPU 低於此值算閒置。 |
| `idle_window_hours` | `48` | 1–720 | 觀察視窗長度。 |
| `idle_notify_after_hours` | `12` | 1–720 | 連續閒置幾小時後通知擁有者。必須小於 `idle_grace_hours`，否則 API 拒絕更新。 |
| `idle_grace_hours` | `24` | 1–720 | 連續閒置幾小時後停機。 |
| `idle_scan_batch_size` | `20` | 1–200 | 每輪排程檢查的機器數。大叢集可以調高。 |

## 工作負載建議

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `workload_advisor_enabled` | `true` | | 在申請表單顯示「VM 還是容器？」的建議（`POST /api/v1/vm-requests/advise`）。關閉時這支 API 與自動模式的申請都會被拒絕。 |

## 挖礦偵測

整個視窗內 CPU 持續飽和視為疑似挖礦事件：拍存證快照、暫停機器、開立告警，並寄信給管理員與擁有者所屬班級的老師。課程機與 GPU 機只拍存證快照，一律不自動暫停。停權帳號一律由管理員在監控頁人工決定。

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `mining_detection_enabled` | `true` | | 總開關。 |
| `mining_cpu_threshold_percent` | `90` | 50–100（%） | 算作飽和的 CPU 使用率。 |
| `mining_window_hours` | `6` | 1–72 | 連續飽和幾小時後開立事件。 |
| `mining_scan_batch_size` | `20` | 1–200 | 每輪檢查的機器數。 |
| `mining_auto_suspend` | `true` | | 開立事件時自動拍存證快照並暫停機器。關閉時事件只發告警與寄信，處置全部人工。 |

## 建置

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `provision_max_concurrency` | `4` | 1–16 | 每個 worker 行程同時執行的機器申請與快速練習克隆數（開多個 worker 行程時上限會倍增），其餘的在佇列中等待。班級批次建置的工作是一台接一台建機，不受這個上限影響。 |

## 快照與備份

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `snapshot_cleanup_enabled` | `true` | | 自動刪除超過保留天數的快照。初始快照 `skylab-init` 永遠不刪；挖礦存證快照保留到事件結案後 30 天。 |
| `snapshot_retention_days` | `7` | 1–90 | 快照超過此天數就被清理。 |
| `student_snapshot_max_count` | `3` | 1–10 | 非管理員（學生或老師）每台機器可保留的快照數。`skylab-init` 與挖礦存證快照不計入。 |
| `student_backup_max_count` | `2` | 1–10 | 非管理員每台機器可保留的備份數。storage 不支援快照的機器以備份當還原點，備份佔的空間大得多，所以上限獨立設定。 |

## 相關

- [治理頁](/zh-TW/manual/governance)：編輯這些值的表單
- [概念與架構](/zh-TW/concepts#背景工作)：套用這些政策的排程迴圈
- [監控](/zh-TW/manual/monitoring)：告警與挖礦事件顯示的地方
