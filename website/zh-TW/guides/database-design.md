# SkyLab 資料庫設計與正規化

本文件說明 SkyLab 的 PostgreSQL schema 如何正規化、哪些冗餘是刻意保留的，以及這些冗餘如何保持一致。資料表模型在 `backend/app/models/`；正規化由 migration `norm3nf01_normalize_schema` 完成。

## 目標：第三正規化

schema 維持在第三正規化（3NF）。每個階段各解決一類問題：

| 正規化 | 去除 | 在 SkyLab 的規則 |
| --- | --- | --- |
| 1NF | 重複群組與多值欄位 | 會逐項讀取、比對或編輯的清單一律拆成子表；欄位裡不放逗號分隔的清單。 |
| 2NF | 對複合鍵一部分的部分相依 | 資料表使用單欄的代理鍵（UUID 或整數）；複合的自然鍵以 `UNIQUE` 約束表示，其他欄位都相依於整個鍵。 |
| 3NF | 遞移相依（非鍵欄位由另一個非鍵欄位決定） | 能經由外鍵取得的值就經由該外鍵讀取，不另外複製到這一列。 |
| BCNF | 決定因子不是候選鍵時殘留的異常 | 不逐表追求；上面的 3NF 規則已涵蓋這裡會遇到的情況。 |

未正規化的資料表會造成三種異常：

- **新增異常**：要記錄一件事時，非得連帶記錄另一件無關的事。例如班級機器對應若同時存機器的 vmid，在 vmid 確定之前這筆對應就無法存在。
- **刪除異常**：刪掉一件事時連帶失去另一件事。例如要移除一個已完成項目，就得改寫整個 JSON 清單。
- **更新異常**：同一件事存了兩份，卻只更新其中一份。這是實務上最常見的問題。在 `norm3nf01` 之前，刪除班級機器時 `batch_provision_tasks.vmid` 會經由外鍵被清空，但 `teaching_class_student_machines.vmid` 那份複本仍然指向已刪除的 VM。

## `norm3nf01_normalize_schema` 改了什麼

### 1NF：多值欄位改成子表

| 改前 | 改後 | 說明 |
| --- | --- | --- |
| `subnet_config.dns_servers`（逗號分隔字串） | `subnet_dns_servers (subnet_config_id, position, address)` | 順序存在 `position`。API 仍收送逗號分隔字串，每一項都必須是 IP 位址。 |
| `subnet_config.extra_blocked_subnets`（逗號／換行分隔文字） | `subnet_blocked_subnets (subnet_config_id, position, cidr)` | 重複的項目會移除，保留第一次出現的位置。 |
| `teacher_judge_student_submissions.completed_item_ids`（JSON 陣列） | `teacher_judge_submission_items (submission_id, item_id)` | 每個已完成的評分項目一列。 |
| `wireguard_peers.allowed_endpoints`（物件組成的 JSON 陣列） | `wireguard_peer_endpoints (peer_id, position, vmid, name, service, host, port)` | `WireGuardPeer.allowed_endpoints` 仍是 property，讀取與整批替換都經由子表。 |

### 3NF：移除能經由外鍵取得的複製欄位

| 移除的欄位 | 現在從哪裡讀 | 原本造成的異常 |
| --- | --- | --- |
| `teacher_judge_script_runs.teaching_class_id` | `artifact.teaching_class_id`（run 會一併載入它的 artifact） | run 記的班級可能和它的腳本不同。 |
| `teacher_judge_student_submissions.teaching_class_id` | `artifact.teaching_class_id` | 同上。 |
| `teacher_judge_student_submissions.is_ready` | `ready_at IS NOT NULL` | 旗標和時間可能互相矛盾。 |
| `teacher_judge_session_attachments.message_id` | `teacher_judge_message_attachments (attachment_id, message_id)` | 訊息已經決定了 session，附件列不再同時存兩者。還沒送出的附件沒有連結列。 |
| `quick_practice_session_machines.name / role / resource_type / sort_order` | Session 所屬、已發佈（不可再改）的環境版本中，`node_key` 相同的節點 | 這些是不會變動的來源的複本。 |
| `teaching_class_student_machines.vmid / status / error` | `batch_task_id` 指向的 batch task | 已刪除的機器會留下過時的 vmid。「已回收」改為推導：task 已完成但 vmid 已被清空。 |

### 讓保留下來的冗餘保持安全的約束

| 約束 | 保證 |
| --- | --- |
| `audit_logs`、`deletion_requests`、`spec_change_requests`、`ip_allocation` 上的 `ck_<table>_resource_vmid_matches` | `resource_vmid` 只能是 `NULL` 或等於 `vmid`（見下方「vmid 快照加資源連結」）。 |
| `fk_teacher_judge_artifacts_session_same_class`、`fk_teacher_judge_artifacts_file_same_class` | artifact 選填的 session 與來源檔案都屬於 artifact 所在的班級。 |
| `fk_teacher_judge_sessions_week_same_class`、`fk_teacher_judge_sessions_file_same_class` | session 選填的週次與選定檔案都屬於 session 所在的班級。 |
| `fk_ai_api_usage_credential_owner` | 用 API 金鑰記下的用量一定算在金鑰擁有者名下。 |

這些複合外鍵參照上層資料表新增的 `UNIQUE (id, <上層>)` 約束。原本的單欄外鍵仍然保留，所以刪除被參照的列時，選填欄位照樣會被設成 `NULL`。選填欄位是 `NULL` 的列不會被複合外鍵檢查。

## 刻意保留的冗餘

為了效能或保存歷史而反正規化是可以接受的，前提是重複的值只有單一寫入者，或有資料庫約束把關。以下列出每一項保留的冗餘與控管方式。

| 位置 | 為什麼保留 | 如何保持一致 |
| --- | --- | --- |
| `batch_provision_jobs.total / done / failed_count` | 每次更新班級狀態都會讀的計數（類似商品庫存或剩餘點數） | 由該工作的 task 以 `sum(...)` 重新計算，不是盲目累加（`class_provision_service`、`batch_provision_service`）。 |
| `ai_api_usage.source` | 可由 `credential_id IS NOT NULL` 推導，但它是每個用量儀表板都會用到的熱點索引 `ix_ai_usage_user_source_created` 的開頭欄位 | `ck_ai_api_usage_source_credential` 讓兩者一致。 |
| 金鑰呼叫的 `ai_api_usage.user_id` | 用量表資料量大，查詢都依使用者過濾 | `fk_ai_api_usage_credential_owner`。 |
| vmid 快照加資源連結（`audit_logs`、`deletion_requests`、`spec_change_requests`、`ip_allocation`） | Proxmox 會回收 VMID。`vmid` 記下當時用的編號；`resource_vmid` 連到那一台資源，資源刪除後變成 `NULL`。`resource_vmid` 無法由 `vmid` 推導。 | `ck_<table>_resource_vmid_matches`；寫入一律經由 `linked_resource_vmid()`。 |
| 快照欄位：`spec_change_requests.current_*`、`deletion_requests.name / node / resource_type`、`mining_incidents.node / resource_type`、`vm_requests.vmid` | 記錄申請或事件建立當下的狀態；來源之後可能改變或消失 | 只寫一次、之後不更新；它們是歷史紀錄，不是快取。 |
| `nat_rule.vm_ip`、`reverse_proxy_rule.vm_ip` | 已對照 IP 配發紀錄驗證過、並寫進 Gateway 的位址。若改成每次重新推導，就會再次信任 guest agent 回報的 IP，Gateway 同步也會依賴 Proxmox 連得上。`vmid → vm_ip` 在設計上本來就不成立。 | 建立時由 `publish_target_policy` 驗證。 |
| `teaching_class_machine_nodes`（從課程環境節點複製） | 班級會調整版本的節點，例如把 `disk_gb` 拉到範本的大小、記下自己的建機工作 | 班級切換課程版本時整批重建。 |
| `class_capacity_reservations` 的總量 | 保留量是某個時間點做出的承諾 | 重新保留時整批替換。 |
| 逐節點的 `proxmox_storages` | Proxmox storage 清單的同步副本，以 `(node_name, storage)` 為鍵供放置查詢使用 | 每次同步連線時由 `upsert_storages`（`repositories/proxmox_storage.py`）整批改寫，保留使用者設定、刪掉已消失的 storage；共享 storage 的設定由 `update_storage_settings` 一次套用到所有節點。 |
| `ai_api_credentials.api_key_name / rate_limit` | 核發金鑰時從申請單複製，之後可以單獨修改，所以是金鑰自己的屬性 | 不相依於申請單。 |
| `resources.expiry_date` 與 `vm_requests.end_at` | 兩個不同的期限（資源使用期限與核准使用時段），實際生效的是較早的那個 | 延期時兩者一起更新（見申請流程）。 |
| `resource_networks.ip_address` 與 `ip_allocation.ip_address` | 觀測到的位址（guest agent 快取）和配發的位址是兩件不同的事 | `resource_networks` 是帶 `cached_at` 的快取。 |
| `resources.allocation_scope` | 班級連結被清除後仍保持 `teaching_class`，因此不是由 `teaching_class_id` 決定 | 由 resource repository 連同 `control_policy` 一起設定。 |
| JSON 文件（`task_records.payload / result`、`teacher_judge_*_json`、`batch_provision_jobs.template_params`、`course_environment_versions.draft_data`、`resources.guest_os`） | 整份存取的不透明文件，不會逐項查詢 | 視為單一值；一旦需要逐項查詢就改成子表。 |

單列設定表（`governance_config`、`proxmox_config`、`quota_config`、`ldap_config` 等）欄位很多，但只有一列 `id = 1`，列與列之間不存在相依。

## schema 變更檢查清單

1. 新增欄位前，先確認這個值能不能經由既有外鍵取得；能的話就經由外鍵讀取。
2. 如果一定要複製某個值（保存歷史或熱點路徑），把它列進上面的表格，並加上約束或單一寫入者來維持一致。
3. 只要有任何地方會過濾、比對或編輯單一元素，就不要把清單存在字串或 JSON 欄位裡。改用子表，需要順序時加 `position` 欄位。
4. 選填參照若指向同一個上層底下的東西，使用 `(子參照, 上層 id)` 的複合外鍵。
5. 每次修改 model 都要建立 Alembic migration（revision id 不超過 32 字元）。會搬移資料的 migration 必須先檢查既有資料，有問題時以清楚的訊息失敗，不可以默默改寫。

## 既有資料庫升級到 `norm3nf01`

migration 在單一交易內執行。修改任何東西之前會先檢查既有資料，遇到下列情況時會停止並列出問題：

- quick practice 的機器在它的環境版本中找不到對應節點；
- 班級機器對應沒有 batch task，但它的 vmid 是現存的資源；
- 存放的 JSON 清單不是陣列；
- 附件被綁到其他 session 的訊息上；
- 有任何一列會違反新的約束。

沒有 batch task、vmid 也找不到資源的班級機器對應原本就是懸空資料，不會被當成錯誤。migration 會以警告記下筆數，升級後這些列會顯示為尚未建機。`downgrade` 會從新的資料表還原舊的欄位。

## Teacher Judge 執行紀錄（`tjrec01`）

| 層級 | 資料表與來源 | 寫入時機 |
| --- | --- | --- |
| 整班 | `teacher_judge_run_batches` | 接受執行後先提交主紀錄，再準備 child runs；前置失敗仍保留失敗紀錄。 |
| 節點腳本 | `teacher_judge_script_runs` | 保存 artifact、目標快照、進度與摘要；班級仍經 artifact 取得。 |
| 執行目標 | `teacher_judge_run_targets` | 執行前建立每個目標，包括沒有 VMID 的前置失敗；由協調執行緒約每秒批次提交狀態與已完成結果，結束時強制提交剩餘結果。 |

target 以 `(run_id, target_key)` 唯一識別；有學生時 key 包含學生與邏輯節點，否則使用 VMID。學生識別與 VMID 是執行當時的快照。SSH key、host 與 runtime context 不複製到 target metadata。新執行與教師覆核以 target 表為結果來源，`TeacherJudgeScriptRun.results_document` 組回既有 API 的 `target_results_json` 格式；舊欄位只作歷史回退，不持續雙寫。

target 保存 `queued_at`、`started_at`、`script_finished_at`、`received_at`、`finished_at`，以及準備、腳本、下載、清理、驗證的耗時。`finished_at` 是後端觀察到處理完成或決定失敗的時間；失聯時不能推定遠端腳本的實際結束時間。失敗紀錄包含 `reason_code` 與 `failure_stage`。`total_seconds` 不含排隊；`run_wait_seconds` 是 target 完成到 run 收尾的間隔，batch API 另外推算 `completion_wait_seconds`（target 完成到整班收尾）。舊 `finalize_wait_seconds` 與 `batch_wait_seconds` 保留原本的回傳語意。

batch 的 `timing.total_seconds` 為接受執行到所有 child runs 進入終態的經過時間；不是各 target 耗時相加。`preparation_seconds`、`queue_seconds`、`execution_seconds` 分別對應前置準備、等待第一個 run、整個執行跨度；尚未完成時總耗時為 `null`，`elapsed_seconds` 可查目前經過時間。run 的排隊時間從 `prepared_at` 起算；舊 run 缺少此時間時沿用 `created_at`。收尾時間在最終交易提交前取值，不包含最後一次 commit、HTTP 傳輸或畫面輪詢。

教師端沿用 `/teaching-classes/{class_id}/judge/sessions/{session_id}` 權限邊界：

- `GET /run-batches`：以 `skip`／`limit` 分頁讀取整班摘要，SQL 聚合數量，不載入核查結果內容。
- `GET /run-batches/{run_batch_id}`：整班時間、數量、run 與學生結果。
- `GET /runs/{run_id}/targets`：以 `skip`／`limit` 分頁，可依 `status`、`student_id`、`vmid` 篩選；回傳 `data`、`total`。

程序取消、背景執行器拒收與 stale 回收只收尾尚未完成的目標，保留已提交結果。硬中斷仍可能遺失尚未提交的短週期結果；已接受但尚未建立 child runs 的 batch 也由既有 stale 排程回收。AI 分析訊息保存與 `ai_api_usage` 相同的 request ID，可經來源檔案／分析版本連回 artifact、batch、run 與 target。

升級會搬移既有目標快照、前置失敗與結果，不推造未觀察到的 target 完成時間或 batch 請求時間；無結果的歷史終態目標標記 `legacy_result_missing`。舊 JSON 保留原始內容，downgrade 會先將新 target 結果與教師覆核還原到 run JSON。資料仍遵循既有 class／artifact／run 刪除關聯，並非永久保留的稽核封存。
