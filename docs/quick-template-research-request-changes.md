# 快速模板與研究申請流程調整

## 目標

本次調整把 VM/LXC 申請拆成兩種用途：

- 快速模板：學生選擇服務模板後，若容量足夠就自動核准並立即建立，固定使用 3 小時。
- 研究申請：以天為主要單位，送出後維持待審核，由管理員審核後才進入排程與供應流程。

## 後端變更

### 新增申請類型

`vm_requests` 新增 `request_kind` 欄位：

- `quick_template`：快速模板自助環境。
- `research`：一般研究或正式資源申請。

新增 Alembic migration：

- `backend/app/alembic/versions/qt01_add_vm_request_kind.py`

### 新增 quick_template mode

`VMRequestCreate.mode` 新增：

```py
"quick_template"
```

快速模板送出時，後端會覆寫使用時間：

```txt
start_at = now
end_at = now + 3 hours
```

前端不控制快速模板的開始與結束時間，避免學生自行延長。

### 快速模板限制

目前快速模板限制集中在：

`backend/app/services/vm/vm_request_service.py`

規則如下：

- 僅支援 LXC。
- 必須帶 `service_template_slug`。
- 只能使用白名單模板：
  - `postgresql`
  - `mongodb`
  - `grafana`
  - `homepage`
  - `wordpress`
- 禁止 GPU。
- CPU 最多 2 cores。
- Memory 最多 4096 MB。
- Disk 最多 32 GB。
- 每位使用者同時最多 1 個快速模板。
- 每位使用者 24 小時內最多建立 3 個快速模板。
- 必須通過既有容量檢查。

### 自動核准

`quick_template` 通過後會自動核准，並沿用既有背景供應流程：

```py
submit_sync(vm_request_schedule_service.process_single_request_start, ...)
```

到期關機沿用既有 scheduler：

```py
process_due_request_stops()
```

### 分配與重平衡邏輯

快速模板採用「只使用現有空間」策略：

- 核准快速模板時，只檢查同時段已核准資源後剩下的容量。
- 不會為了快速模板重新分配既有研究申請的 `assigned_node`。
- 不會在 preview placement 中模擬 cohort rebalance 來替快速模板找位置。
- active rebalance 時，已建立的快速模板會固定在目前節點，不主動遷移。

研究申請維持原本邏輯：審核後可依照 reservation rebuild 與 active rebalance 做整體最佳化，適合較長期且需要管理員控管的資源。

## 前端變更

### 快速模板

在 `ApplicationRequestPage` 中，學生選擇服務模板後會自動切換為：

```ts
mode: "quick_template"
```

前端送出的 quick template payload 不包含可控的 `start_at` / `end_at`。

學生快速模板畫面會顯示固定規則：

```txt
有可用容量時會自動核准並立即建立，使用時間固定 3 小時，到期後由系統自動關機。
```

### 研究申請

一般 `scheduled` 申請改成以日期區間為單位：

- 開始日期
- 結束日期

前端會轉換為：

```txt
start_at = 開始日期 00:00 Asia/Taipei
end_at = 結束日期隔天 00:00 Asia/Taipei
```

因此使用期間會包含完整的結束日期。

送出後仍維持待審核，不會自動建立。

## 測試項目

新增/更新測試：

- 學生 `quick_template` 可自動核准。
- `quick_template` 會固定產生 3 小時使用區間。
- 非白名單模板會被拒絕。
- 學生仍不能使用一般 `immediate`。
- 前端 quick template payload 不傳 client-controlled dates。

## 後續建議

下一步可以補管理後台分頁：

- 研究申請：只顯示待審核的 `research`。
- 快速模板紀錄：顯示 `quick_template` 的使用者、模板、開始時間、到期時間、狀態與強制關機操作。

也可以再加到期後自動刪除策略，例如關機後保留 24 小時，超過後自動刪除資源。
