# Teacher Judge Managed Script Executor Plan

## 背景

目前 Teacher Judge 已經把第一階段收斂成：

```text
老師選擇 template
-> 上傳評分表
-> LLM 分析 rubric
-> 產出含 check_steps 的評分計劃書
-> 前端顯示結果，老師可再調整
```

這一階段只做「評分計劃書」，不實際執行檢查。

下一步要補的是：老師確認評分計劃書後，系統能把它轉成一份可重複使用的受管檢測腳本，存到群組內，再由老師選擇 VM/LXC 批次執行，回收結果並交給 LLM 做證據解讀與評分建議。

## 問題理解

評分表通常不只是一兩個 shell command。實務上會需要：

- 多個檢查步驟。
- 整理服務狀態、port、process、HTTP 回應。
- 彙整 stdout / stderr / log。
- 將結果整理成老師與 LLM 都能理解的 evidence。

如果每個 rubric item 都單獨打一次 SSH，會有幾個問題：

- SSH call 太碎，效能與錯誤處理都複雜。
- 很難保證同一台機器上的檢查脈絡一致。
- log 分散，不利於後續回放、審核、匯出。
- 前端很難呈現清楚的進度。

因此更合理的方向是：AI 先根據 rubric 產生一份受管檢測腳本，系統檢查腳本安全性與輸出格式，老師審核後保存，最後由 Executor 批次丟到目標機器執行並回收結果。

## 設計目標

- 將一次性的 rubric 判斷結果轉成群組可重複使用的受管腳本。
- 腳本可被老師審核、保存、版本化與重新執行。
- Executor 批次執行腳本，減少多次 SSH round-trip。
- 腳本輸出 structured JSON，方便後端保存與 LLM 評斷。
- LLM 可以產生與審查腳本，但不能跳過系統檢查直接執行。
- 執行結果保留 evidence，LLM 評斷只作為老師確認前的建議。

## 非目標

- 第一版不做完全自動正式給分。
- 第一版不允許 AI 生成腳本後直接執行。
- 第一版不允許腳本進行破壞性操作、安裝套件或修改學生環境。
- 第一版不支援任意語言腳本；建議先支援 Python managed script。
- 第一版不讓 LLM 直接輸出 raw SSH command 給 Executor 執行。

## 建議核心流程

```text
老師在群組 Judge 頁上傳/調整評分表
-> LLM 產生 rubric analysis
-> 老師按「製作檢測腳本」
-> 後端保存 rubric snapshot，並建立對應 script artifact draft
-> 後端根據 rubric snapshot 產生 managed script draft
-> deterministic policy checker 檢查腳本
-> AI reviewer 依 script policy 再審查一次
-> 前端顯示腳本內容、檢查結果、AI 審查意見
-> 老師確認保存
-> 腳本進入群組腳本頁，成為 reusable group script
-> 老師在腳本頁選擇目標 VM/LXC 執行
-> Executor 上傳腳本、執行、回收 JSON/log
-> 後端保存 run results
-> LLM 根據 rubric + evidence 產生 pass/fail/manual-review 建議
-> 老師審核與套用結果
```

## 已確認的產品決策

1. AI 情境分析完成後需要保存。第一版把 rubric 分析結果與 AI 產生的檢測腳本合併成同一個 artifact 紀錄，方便老師以「一份 AI 檢測腳本」管理。
2. 老師按「製作檢測腳本」後自動跳轉到群組腳本頁，不停留在 AI 情境分析頁。
3. 老師可以查看腳本內容，也可以退回並重新生成；第一版不提供直接手動編輯腳本。未核准前重新產生可覆蓋同一筆 draft，核准後重新產生則開新 version。
4. 腳本由老師審核即可，但 hard policy 一票否決；AI reviewer 通過也不能覆蓋 hard policy 的阻擋結果。
5. 執行目標選擇預留三種：全群組有 VM 的成員、只選 running VM、手動勾選成員/VMID。
6. 第一版不把執行結果自動回填 rubric，也不先實作 VM 檢測；等腳本保存與審查流程完整後再接 executor。
7. 後續執行進度採用 polling，不先做 WebSocket。
8. 老師刪除腳本時採硬刪除，直接移除該群組腳本紀錄；第一版不做封存/停用流程。
9. 腳本 artifact 必須保存 rubric snapshot 與 artifact version；不另外加 `rubric_hash`，因為新評分表會產生新腳本。
10. `source` 欄位需要保留，第一版支援 `ai_generated` 與 `regenerated`。
11. 第一版腳本語言固定使用 Python；資料欄位可預留語言值，但 UI 不開 shell / bat。
12. 執行任務與結果先收斂成單一 run 紀錄，未來若查詢壓力或結果量變大，再拆出 per-target result table。
13. 第一版 API 先提供 artifact 生命週期：create from analysis、list、detail、regenerate、approve、archive。
14. artifact `name` 直接引用上傳的評分表檔名；若後續重新產生腳本，沿用同一名稱並透過 version 區分。
15. 腳本輸出 JSON schema 由後端硬編碼嚴格驗證，不交給 LLM 自由解釋。
16. hard policy 第一版先擋明顯破壞性與反向危險指令，尤其是刪除資料、刪庫、刪檔、清空目錄、關機重啟或 AI 產生的反向操作指令。
17. 前端第一版先沿用群組頁 `activeView` 新增「AI 檢測腳本」；保留未來收斂成子路由的資料與元件邊界。

## 第一版實作範圍

本輪重點是先完成「腳本產出並驗證通過」，不執行 VM 檢測。

第一版包含：

- 在群組 AI 情境分析結果上新增「製作檢測腳本」入口。
- 保存目前分析結果與腳本草稿為同一筆 `teacher_judge_script_artifacts`。
- artifact 內保存 rubric snapshot、script content、source、policy check 與 AI review。
- 對 script draft 執行 deterministic policy check。
- 對 script draft 執行 AI reviewer check。
- 自動跳轉到群組腳本 view/page。
- 顯示腳本內容、policy check 結果、AI review 結果。
- 老師可以核准保存，或退回重新生成。

第一版不包含：

- 不選擇 VM/LXC 執行。
- 不上傳腳本到學生機器。
- 不建立實際 run/result。
- 不做 LLM evidence judgement。
- 不把結果回填 rubric checked。
- 不做單台重跑或重跑失敗項目。

## 頁面流程建議

### 1. 群組 Judge 頁

定位：建立與調整評分依據。

主要操作：

- 上傳評分表。
- 選擇評分環境 template。
- 顯示 rubric analysis。
- 老師可用聊天或表單調整 rubric。
- 按「製作檢測腳本」。

建議不要在這一頁直接做大量批次執行。Judge 頁的心智模型是「把評分表整理好」，不是「管理批次任務」。

### 2. 新增群組 AI 檢測腳本頁

定位：管理群組共用 AI 檢測腳本。

第一版先沿用群組頁目前的 `activeView` 模式，和「AI 情境分析」、「AI PVE 訊息」並列。未來當腳本詳情、run history、深連結需求變強，再收斂成子路由。

未來建議 route：

```text
/groups/:groupId/judge/scripts
```

主要功能：

- 腳本列表。
- 腳本狀態：draft / review_failed / reviewed / approved / archived。
- 腳本版本。
- 腳本來源：ai_generated / regenerated。
- 對應 rubric snapshot / artifact version。
- 查看腳本內容。
- 查看 policy check 與 AI reviewer 結果。
- 老師確認/退回修改。
- 選擇 VM/LXC 執行。
- 查看歷史執行紀錄。

### 3. 腳本執行頁或抽屜

定位：選擇目標、監看進度、查看結果。

可以是腳本頁內的執行抽屜，也可以是子頁：

```text
/groups/:groupId/judge/scripts/:scriptId/runs/:runId
```

第一版建議先做頁內 run detail panel，若歷史紀錄與結果很多，再拆成獨立 run detail page。

## 可視化進度

執行進度應以 run 為單位呈現：

```text
Run status:
pending -> preparing -> uploading -> running -> collecting -> judging -> completed
```

每台 VM/LXC 也要有自己的狀態：

```text
queued
uploading
running
collecting
llm_judging
completed
failed
timeout
skipped
```

前端可顯示：

- 總進度：完成幾台 / 總共幾台。
- 目前階段。
- 每台 VM 的狀態 badge。
- stdout / stderr 摘要。
- parsed result JSON。
- AI 建議結果。
- 老師是否已確認。

## 腳本輸出契約

受管腳本必須輸出 JSON。建議格式：

```json
{
  "schema_version": "teacher_judge_result.v1",
  "summary": "n8n service check completed",
  "checks": [
    {
      "id": "n8n_port",
      "title": "n8n port is listening",
      "status": "pass",
      "evidence": "Port 5678 is listening on 127.0.0.1",
      "raw": "tcp LISTEN 0 4096 127.0.0.1:5678"
    },
    {
      "id": "n8n_http",
      "title": "n8n HTTP endpoint is reachable",
      "status": "fail",
      "evidence": "HTTP request timed out after 5 seconds",
      "raw": ""
    }
  ],
  "errors": []
}
```

允許狀態：

```text
pass
fail
warning
unknown
skipped
```

後端處理原則：

- 優先解析 JSON。
- 解析後必須用後端硬編碼 schema 嚴格驗證欄位與狀態值。
- JSON 無法解析時，保存 raw stdout / stderr 並標記 `parse_failed`。
- JSON 可解析但 schema 不合格時，標記 `schema_invalid`，不可進入 approved-ready 狀態。
- stdout / stderr 需截斷保存，避免過大 log。
- 對可能敏感的內容做遮罩。

## Script Policy

AI 產生腳本後必須經過兩層審查。

### deterministic policy checker

不靠 LLM，直接掃描 AST / token / pattern。

第一版禁止：

- 刪除或破壞性操作，例如 `rm -rf`、`del /s`。
- 刪庫或清空資料，例如 `drop database`、`truncate table`、`delete from` 未帶安全條件。
- 刪除大量檔案或清空目錄，例如 `find ... -delete`、`Remove-Item -Recurse`。
- 關機或重啟，例如 `shutdown`、`reboot`。
- 修改系統設定。
- 安裝套件，例如 `apt install`、`pip install`、`npm install`。
- 讀取敏感檔案，例如 `.ssh`、`.env`、private key。
- 對外送資料。
- 無 timeout 的網路請求。
- 無限制迴圈或大量掃描檔案系統。
- AI 產生的反向操作指令，例如「修復」、「清除」、「重設」、「刪除」、「停用」等非 read-only 行為。

### AI reviewer

AI reviewer 根據固定 policy 審查腳本，不執行腳本。

輸出格式：

```json
{
  "approved": false,
  "risk_level": "high",
  "issues": [
    "script attempts to read /home/*/.ssh"
  ],
  "suggested_fix": "remove SSH key inspection and only check service status"
}
```

只有 deterministic check 與 AI reviewer 都通過後，老師才可以核准腳本進入可執行狀態。

hard policy 是一票否決。若 deterministic policy checker 判定 blocked，即使 AI reviewer 認為可接受，artifact 仍不能進入 approved 狀態。

## 後端資料模型草案

第一版以簡單管理為主，將「評分表分析結果」與「AI 產生的檢測腳本」合併成同一個 artifact。執行任務與結果也先收斂成單一 run 紀錄。

### teacher_judge_script_artifacts

```text
id
group_id
name
template_key
rubric_snapshot_json
script_language     python | shell | bat
script_content
source              ai_generated | regenerated
version
status              draft | review_failed | reviewed | approved | archived
policy_check_result_json
ai_review_result_json
created_by
approved_by
created_at
updated_at
approved_at
```

欄位語意：

- `name`：直接引用上傳的評分表檔名，方便老師從原始評分表辨識腳本來源。
- `rubric_snapshot_json`：保存 AI 情境分析完成後的 rubric items、summary、raw_text、template_key 等快照。
- `script_content`：保存 AI 產生的 managed script。
- `script_language`：第一版固定為 `python`；`shell` / `bat` 僅作為未來保留值，不在 UI 開放。
- `source`：保存腳本來源，第一版包含 `ai_generated` 與 `regenerated`。
- `policy_check_result_json`：保存 deterministic policy checker 的結果。
- `ai_review_result_json`：保存 AI reviewer 的審查結果。
- `version`：未核准 draft 重新產生時可覆蓋同一筆；已核准 artifact 若重新產生，需建立新 version。

第一版不新增 `rubric_hash`。流程上新評分表會產生新 artifact；同一 artifact 內的 `rubric_snapshot_json` 就是該腳本的評分依據。

### teacher_judge_script_runs

```text
id
group_id
artifact_id
target_scope        all_with_vm | running_only | manual
target_snapshot_json
status              pending | running | completed | failed | cancelled
progress_json
result_summary_json
target_results_json
started_by
started_at
finished_at
created_at
updated_at
```

欄位語意：

- `target_snapshot_json`：保存本次執行選到的成員、resource_id、vmid、當時 VM 狀態。
- `progress_json`：保存 polling UI 需要的總進度、每台狀態、錯誤摘要。
- `result_summary_json`：保存整體執行摘要。
- `target_results_json`：保存每台 VM/LXC 的 stdout/stderr 摘要、parsed result、AI judgement。第一版不執行 VM，因此先預留欄位，不深入實作。

### 未來可能拆分

```text
teacher_judge_script_run_results
```

當單次 run 的 target 很多、結果量很大、或需要針對單台 VM 查詢與重跑時，再拆成 per-target result table。

## Executor 設計

第一版建議沿用 AI-PVE 的 SSH 能力與 group VMID 邊界，但新增 Teacher Judge 專用 executor service，不要把腳本執行責任塞進 chat flow。

單台 VM 執行流程：

```text
resolve VM/LXC by group_id + resource_id/vmid
-> 建立 /tmp/campus-cloud-judge/<run_id>
-> 上傳 managed script
-> 執行 python3 check.py
-> 收集 stdout / stderr / result JSON
-> 清理暫存檔
-> 保存 result
-> 呼叫 LLM judgement
```

第一版建議限制：

- 只執行 approved script。
- 只允許 group 內 VM/LXC。
- 預設 timeout。
- 限制 stdout / stderr 大小。
- 限制同時執行數量。
- 所有 run/result 都可追蹤 started_by。

## LLM 評斷

LLM 不直接決定事實，只根據 evidence 給建議。

輸入：

```text
rubric snapshot
managed script metadata
parsed result JSON
stdout/stderr excerpt
```

輸出：

```json
{
  "overall_status": "manual_review",
  "items": [
    {
      "item_id": "item-1",
      "judgement": "pass",
      "confidence": 0.9,
      "reason": "n8n HTTP endpoint returned 200 OK.",
      "evidence_refs": ["n8n_http"],
      "teacher_review_required": false
    }
  ],
  "summary": "大部分檢查通過，但仍有一項需要老師確認。"
}
```

老師可選擇：

- 接受 AI 建議。
- 修改單項判斷。
- 標記需人工評閱。
- 匯出結果。

## 流程可行性判斷

這個流程可行，而且比在 Judge 頁直接完成所有事情更清楚。

建議保留兩個頁面邊界：

- Judge 頁：負責 rubric analysis，並觸發生成 script artifact。
- Script 頁：負責腳本審查、保存、執行與結果。

這樣有幾個好處：

- 老師不會在同一頁同時面對 rubric 編輯、腳本審查、機器選擇、執行進度、結果審核。
- 腳本變成群組資產，可以重複使用。
- run history 有清楚入口。
- 腳本審查與執行權限比較容易控管。
- 未來可以把腳本頁擴充成「群組自動檢測中心」。

## 優化點

### 1. Judge 頁按鈕只做「製作腳本」

Judge 頁不要直接跳到執行，而是：

```text
製作檢測腳本 -> 產生 draft -> 跳轉腳本頁
```

跳轉到：

```text
/groups/:groupId/judge/scripts/:scriptId
```

老師在腳本頁看審查結果，再決定是否核准與執行。

### 2. 腳本要 versioned

同一份 rubric 可以重新生成腳本。每次重新生成都產生新版本，舊 run result 不被覆蓋。

### 3. 執行結果不要直接覆蓋 rubric checked

第一版建議顯示為「AI 建議」。老師確認後才套用。

### 4. 先支援 Python script

Python 比 shell / bat 更容易規範輸出 JSON，也比較好做靜態檢查。shell 和 bat 可以放第二階段。

### 5. 腳本頁要顯示審核原因，不只顯示通過/失敗

老師需要知道腳本為什麼不能執行，例如：

- 讀取敏感檔案。
- 缺少 JSON output。
- 使用外部網路。
- 可能修改環境。

## 建議實作順序

### 第一版

1. 新增 script artifact model/API，讓一次性的 rubric 分析結果與 managed script 可存成群組版本。
2. 新增 script draft generation API。
3. 新增 Python script generation prompt 與 JSON output contract。
4. 新增硬編碼 Pydantic schema，嚴格驗證腳本輸出 JSON contract。
5. 新增 deterministic script policy checker，先擋刪除、刪庫、清空、關機重啟、修改環境與反向操作指令。
6. 新增 AI reviewer prompt。
7. 新增群組腳本頁，顯示 draft、policy check、AI review。
8. 新增 approve script API。
9. 新增 artifact list/detail/regenerate/archive API。
10. 在群組側欄用 `activeView` 新增「AI 檢測腳本」，並讓 AI 情境分析完成後可跳轉到此 view。
11. 補上 artifact 生命週期測試。

### 後續 Executor 階段

1. 新增 script run model/API。
2. 新增 Teacher Judge executor service。
3. 新增 VM/LXC target selection：全群組有 VM 的成員、只選 running VM、手動勾選。
4. 新增 polling 進度 UI。
5. 新增 LLM evidence judgement。
6. 新增老師確認與匯出流程。

## 第一版垂直切片

建議先用 `n8n` 做最小可驗證版本：

```text
rubric: n8n Web UI 可存取
script: Python managed script
checks:
  - process check
  - port 5678 check
  - localhost HTTP check
targets:
  - group 內 1-2 台 VM/LXC
result:
  - JSON parsed successfully
  - AI judgement generated
  - teacher can review
```

這條切片跑通後，再擴大到 Python / Linux 一般作業。
