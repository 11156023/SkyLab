# 治理設定欄位

治理設定是一筆設定紀錄，控制排程器每 60 秒套用的自動政策。管理員在[治理](/zh-TW/manual/governance)頁編輯，API 是 `GET`／`PUT /api/v1/governance/config`。本頁列出每個欄位的預設值、允許範圍與作用。改完在下一輪排程就生效，不必重新啟動。

## 資源告警

機器持續超過閾值時會發出告警，顯示在監控頁並寄信給擁有者。

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `alerts_enabled` | `true` | | 資源告警總開關。 |
| `alert_cpu_threshold` | `90` | 50–100（%） | 超過此 CPU 使用率就告警。 |
| `alert_memory_threshold` | `90` | 50–100（%） | 記憶體使用率閾值。 |
| `alert_disk_threshold` | `90` | 50–100（%） | 磁碟使用率閾值。 |
| `alert_cooldown_minutes` | `30` | 1–1440 | 同一台機器同一指標兩次告警的最短間隔。 |
| `alert_check_interval_seconds` | `60` | 15–3600 | 取樣使用率的頻率。 |
| `alert_email_enabled` | `true` | | 同時寄信（需要 SMTP）。 |

## TTL 生命週期

每台機器都有到期日。生命週期任務在到期前提醒、到期時停機、寬限期過後刪除。從機器詳情頁延期會重新計算。

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `ttl_enabled` | `true` | | 總開關。關閉時到期的機器會繼續運行。 |
| `expiry_warn_days` | `3` | 1–30 | 到期前幾天通知擁有者。 |
| `expiry_grace_delete_days` | `7` | 0–90 | 到期後幾天把已停機的機器排入刪除佇列。`0` 代表到期即刪。 |

## 閒置偵測

整個觀察視窗內 CPU 都低於閾值的機器視為閒置。先通知擁有者，寬限期過後停機。

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `idle_detection_enabled` | `true` | | 總開關。 |
| `idle_cpu_threshold_percent` | `1.0` | 0.1–20（%） | 平均 CPU 低於此值算閒置。 |
| `idle_window_hours` | `48` | 1–720 | 觀察視窗長度。 |
| `idle_notify_after_hours` | `12` | 1–720 | 連續閒置幾小時後通知擁有者。必須小於 `idle_grace_hours`，否則 API 拒絕更新。 |
| `idle_grace_hours` | `24` | 1–720 | 通知後幾小時停機。 |
| `idle_scan_batch_size` | `20` | 1–200 | 每輪排程檢查的機器數。大叢集可以調高。 |

## 工作負載建議

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `workload_advisor_enabled` | `true` | | 在申請表單顯示「VM 還是容器？」的建議（`POST /api/v1/vm-requests/advise`）。 |

## 挖礦偵測

整個視窗內 CPU 持續飽和視為疑似挖礦事件：先把機器狀態存證、暫停機器，並通知擁有者與管理員。停權帳號一律由管理員在監控頁人工決定。

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `mining_detection_enabled` | `true` | | 總開關。 |
| `mining_cpu_threshold_percent` | `90` | 50–100（%） | 算作飽和的 CPU 使用率。 |
| `mining_window_hours` | `6` | 1–72 | 連續飽和幾小時後開立事件。 |
| `mining_scan_batch_size` | `20` | 1–200 | 每輪檢查的機器數。 |
| `mining_auto_suspend` | `true` | | 開立事件時自動暫停機器。關閉時只存證與通知。 |

## 建置

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `provision_max_concurrency` | `4` | 1–16 | worker 同時執行的克隆工作數。班級批次建置會在這個上限後排隊。 |

## 快照與備份

| 欄位 | 預設 | 範圍 | 作用 |
| --- | --- | --- | --- |
| `snapshot_cleanup_enabled` | `true` | | 自動刪除超過保留天數的快照。 |
| `snapshot_retention_days` | `7` | 1–90 | 快照超過此天數就被清理。 |
| `student_snapshot_max_count` | `3` | 1–10 | 學生每台機器可保留的快照數。 |
| `student_backup_max_count` | `2` | 1–10 | 學生每台機器可保留的備份數。storage 不支援快照的機器以備份當還原點，備份佔的空間大得多，所以上限獨立設定。 |

## 相關

- [治理頁](/zh-TW/manual/governance)：編輯這些值的表單
- [概念與架構](/zh-TW/concepts#背景工作)：套用這些政策的排程迴圈
- [監控](/zh-TW/manual/monitoring)：告警與挖礦事件顯示的地方
